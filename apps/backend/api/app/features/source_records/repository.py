"""PostgreSQL reads for raw Transportstyrelsen staging rows.

Performance contract for this module
------------------------------------
``staging.transportstyrelsen_raw`` holds ~7.25M rows and carries only a primary-key index
on ``id``. Two rules follow from that and must not be broken:

1. **Keyset pagination only.** ``OFFSET 5_000_000`` measured at ~7.3s against this table
   while the equivalent ``WHERE id > ?`` keyset read measured at ~0.01s. Every page read
   here seeks on the primary key and returns a cursor; there is no offset paging.
2. **No per-row correlated lookups.** Rows are cut to the page first; anything that needs
   a companion table (normalization results) is joined afterwards, against the page only.

Filtered searches have no index to lean on and degrade to a sequential scan (~8s worst
case for a term that matches nothing). Those run under a short ``statement_timeout`` and
surface a ``timed_out`` flag rather than hanging the request.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

import psycopg
from psycopg import Connection, sql

from api.app.features.source_records.schemas import (
    TS_SEARCH_FIELDS,
    SourceRecordFilters,
)

STAGING_TABLE = "staging.transportstyrelsen_raw"
NORMALIZATION_RESULTS_TABLE = "core.normalization_results"

# Ceiling for unindexed scans (search / attribute filters). A full no-match scan of the
# table measured at ~8s, so this leaves headroom without letting a request hang.
SEARCH_TIMEOUT_MS = 20_000
# Sampling window for the field inventory. Reading every row to compute fill rates would
# be a full-table scan for a number that only needs to be indicative.
FIELD_SAMPLE_ROWS = 20_000


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


class SourceRecordRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    @staticmethod
    def _conditions(
        filters: SourceRecordFilters,
    ) -> tuple[list[sql.Composable], list[object]]:
        conditions: list[sql.Composable] = []
        parameters: list[object] = []

        if filters.cursor is not None:
            conditions.append(sql.SQL("id > %s"))
            parameters.append(filters.cursor)

        if filters.batch_id:
            conditions.append(sql.SQL("source_batch_id = %s"))
            parameters.append(filters.batch_id)

        if filters.field and filters.value:
            # Key is parameterised as a value, never interpolated, so an arbitrary
            # raw_record key is safe to filter on.
            conditions.append(sql.SQL("raw_record ->> %s = %s"))
            parameters.append(filters.field)
            parameters.append(filters.value)

        term = filters.query.strip()
        if term:
            search = sql.SQL(" OR ").join(
                sql.SQL("raw_record ->> {} ILIKE %s").format(sql.Literal(field))
                for field in TS_SEARCH_FIELDS
            )
            conditions.append(sql.SQL("({})").format(search))
            parameters.extend([f"%{term}%"] * len(TS_SEARCH_FIELDS))

        return conditions, parameters

    def fetch_page(
        self, filters: SourceRecordFilters
    ) -> tuple[list[dict[str, Any]], bool]:
        """Return one keyset page plus a flag saying whether the scan timed out."""
        conditions, parameters = self._conditions(filters)
        where_clause = (
            sql.SQL(" AND ").join(conditions) if conditions else sql.SQL("TRUE")
        )
        # Fetch one extra row to learn whether a further page exists without counting.
        statement = (
            sql.SQL(
                f"SELECT id, source_batch_id, ingested_at, raw_record FROM {STAGING_TABLE} WHERE "
            )
            + where_clause
            + sql.SQL(" ORDER BY id LIMIT %s")
        )
        needs_scan = bool(filters.query.strip() or (filters.field and filters.value))
        with self._connection_factory() as connection, connection.cursor() as cursor:
            if needs_scan:
                cursor.execute(f"SET LOCAL statement_timeout = {SEARCH_TIMEOUT_MS}")
            try:
                cursor.execute(statement, (*parameters, filters.limit + 1))
                rows = cursor.fetchall()
            except psycopg.errors.QueryCanceled:
                return [], True
        return [
            {
                "id": int(row[0]),
                "source_batch_id": str(row[1]),
                "ingested_at": row[2],
                "raw_record": dict(row[3] or {}),
            }
            for row in rows
        ], False

    def estimated_total(self) -> int:
        """Planner estimate for the whole table -- avoids a 7.25M-row count on every page."""
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT reltuples::bigint FROM pg_class WHERE oid = %s::regclass",
                (STAGING_TABLE,),
            )
            row = cursor.fetchone()
        estimate = int(row[0]) if row is not None and row[0] is not None else 0
        return max(estimate, 0)

    def fetch_record(self, record_id: int) -> dict[str, Any] | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id, source_batch_id, ingested_at, raw_record "
                f"FROM {STAGING_TABLE} WHERE id = %s",
                (record_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            record = {
                "id": int(row[0]),
                "source_batch_id": str(row[1]),
                "ingested_at": row[2],
                "raw_record": dict(row[3] or {}),
            }
            # Joined only after the single row is pinned, never across a page.
            cursor.execute(
                f"""
                SELECT source_batch_id, status, confidence, normalized_payload,
                       applied_rule_ids, review_reasons, updated_at
                FROM {NORMALIZATION_RESULTS_TABLE}
                WHERE source_record_id = %s
                ORDER BY updated_at DESC, id DESC
                LIMIT 25
                """,
                (record_id,),
            )
            normalizations = []
            for result in cursor.fetchall():
                payload = dict(result[3] or {})
                normalizations.append(
                    {
                        "source_batch_id": str(result[0]),
                        "status": str(result[1]),
                        "confidence": float(result[2] or 0.0),
                        "normalized": dict(payload.get("normalized") or {}),
                        "candidates": dict(payload.get("candidates") or {}),
                        "applied_rule_ids": [str(v) for v in (result[4] or [])],
                        "review_reasons": [str(v) for v in (result[5] or [])],
                        "updated_at": result[6],
                    }
                )
        record["normalizations"] = normalizations
        return record

    def fetch_batches(self) -> list[dict[str, Any]]:
        """Batch catalogue from the job-run ledger.

        ``GROUP BY source_batch_id`` straight off the staging table measured at ~21s
        because it scans all 7.25M rows. ``core.ingest_job_runs`` holds one row per batch
        (~550 rows) and answers the same question instantly.
        """
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT batch_id,
                       max(records_processed) AS records,
                       max(status) AS status,
                       max(finished_at) AS finished_at
                FROM core.ingest_job_runs
                WHERE batch_id IS NOT NULL
                GROUP BY batch_id
                ORDER BY max(coalesce(finished_at, started_at)) DESC NULLS LAST
                LIMIT 1000
                """
            )
            rows = cursor.fetchall()
        return [
            {
                "batch_id": str(row[0]),
                "records": int(row[1] or 0),
                "status": str(row[2]) if row[2] is not None else None,
                "finished_at": row[3],
            }
            for row in rows
        ]

    def fetch_field_inventory(self) -> tuple[int, list[dict[str, Any]]]:
        """Key inventory and fill rates over a bounded sample of the newest rows."""
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL statement_timeout = {SEARCH_TIMEOUT_MS}")
            cursor.execute(
                f"""
                WITH sample AS (
                    SELECT raw_record FROM {STAGING_TABLE} ORDER BY id DESC LIMIT %s
                ), pairs AS (
                    SELECT entry.key AS field, entry.value AS value
                    FROM sample, LATERAL jsonb_each(sample.raw_record) AS entry
                )
                SELECT field,
                       count(*) AS present,
                       count(*) FILTER (
                           WHERE value IS NOT NULL
                             AND value <> 'null'::jsonb
                             AND value <> '""'::jsonb
                       ) AS non_null,
                       (array_agg(DISTINCT trim(both '"' from value::text))
                            FILTER (WHERE value IS NOT NULL AND value <> 'null'::jsonb))[1:5]
                           AS examples
                FROM pairs
                GROUP BY field
                ORDER BY non_null DESC, field
                """,
                (FIELD_SAMPLE_ROWS,),
            )
            rows = cursor.fetchall()
        sampled = FIELD_SAMPLE_ROWS
        return sampled, [
            {
                "field": str(row[0]),
                "present": int(row[1]),
                "non_null": int(row[2]),
                "fill_rate": (int(row[2]) / sampled) if sampled else 0.0,
                "examples": [str(v) for v in (row[3] or [])][:5],
            }
            for row in rows
        ]


RepositoryFactory = Callable[[], SourceRecordRepository]
