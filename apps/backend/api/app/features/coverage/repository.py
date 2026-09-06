"""PostgreSQL reads for field-level normalization coverage.

Scope contract
--------------
Coverage is always computed **for one batch at a time**. ``core.normalization_results``
holds ~7.25M rows across ~550 batches; the largest logical run is split into 261 parts of
25k rows. A single part aggregates in ~1s, while a collapsed "all parts" scan over 6.5M
rows is a known unsolved timeout in this codebase. Nothing here accepts an all-parts
selector -- callers pick a concrete ``batch_id``.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

from psycopg import Connection

NORMALIZATION_RESULTS_TABLE = "core.normalization_results"
TECDOC_CANDIDATES_TABLE = "core.tecdoc_canonical_candidates"

COVERAGE_TIMEOUT_MS = 60_000


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


class CoverageRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def fetch_batches(self) -> list[dict[str, Any]]:
        """Normalization batches, read from the job ledger rather than the results table.

        ``GROUP BY source_batch_id`` over ``core.normalization_results`` measured at ~14s
        because it touches all 7.25M rows. ``core.ingest_job_runs`` answers the same
        question from ~550 rows.
        """
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT batch_id,
                       max(records_processed) AS records,
                       max(finished_at) AS finished_at
                FROM core.ingest_job_runs
                WHERE batch_id IS NOT NULL AND job_name = 'normalize'
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
                "finished_at": row[2],
            }
            for row in rows
        ]

    def fetch_ts_coverage(self, *, batch_id: str) -> dict[str, Any]:
        """Per-field coverage for one normalization batch.

        ``latest`` collapses re-runs to the newest result per source record, and every
        aggregate below reads from that CTE -- there is no correlated per-row lookup.
        """
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL statement_timeout = {COVERAGE_TIMEOUT_MS}")
            cursor.execute(
                f"""
                WITH latest AS (
                    SELECT DISTINCT ON (source_record_id)
                        source_record_id, status, normalized_payload, review_reasons
                    FROM {NORMALIZATION_RESULTS_TABLE}
                    WHERE source_batch_id = %s
                    ORDER BY source_record_id, updated_at DESC, id DESC
                )
                SELECT
                    (SELECT count(*) FROM latest) AS rows,
                    (
                        SELECT coalesce(jsonb_object_agg(status, count), '{{}}'::jsonb)
                        FROM (SELECT status, count(*) AS count FROM latest GROUP BY status) s
                    ) AS status_counts,
                    (
                        SELECT coalesce(jsonb_agg(row_to_json(f)), '[]'::jsonb)
                        FROM (
                            -- Counted per section, then reconciled. The "in both sections"
                            -- overlap is resolved on the candidates side only: candidate
                            -- objects hold a handful of keys while normalized objects hold
                            -- ~60, so probing the small side keeps this at ~1s. Walking the
                            -- union of both key sets instead measured at ~14s for the same
                            -- numbers.
                            SELECT coalesce(n.field, c.field) AS field,
                                   coalesce(n.normalized, 0) AS normalized,
                                   coalesce(c.candidates_only, 0) AS candidates_only
                            FROM (
                                SELECT entry.key AS field, count(*) AS normalized
                                FROM latest l
                                CROSS JOIN LATERAL jsonb_each(
                                    coalesce(l.normalized_payload -> 'normalized', '{{}}'::jsonb)
                                ) AS entry
                                GROUP BY entry.key
                            ) n
                            FULL OUTER JOIN (
                                SELECT entry.key AS field,
                                       count(*) FILTER (
                                           WHERE NOT (
                                               coalesce(
                                                   l.normalized_payload -> 'normalized',
                                                   '{{}}'::jsonb
                                               ) ? entry.key
                                           )
                                       ) AS candidates_only
                                FROM latest l
                                CROSS JOIN LATERAL jsonb_each(
                                    coalesce(l.normalized_payload -> 'candidates', '{{}}'::jsonb)
                                ) AS entry
                                GROUP BY entry.key
                            ) c ON c.field = n.field
                        ) f
                    ) AS field_counts,
                    (
                        SELECT coalesce(jsonb_agg(row_to_json(r)), '[]'::jsonb)
                        FROM (
                            SELECT reason, count(*) AS records
                            FROM latest
                            CROSS JOIN LATERAL unnest(review_reasons) AS reason
                            GROUP BY reason
                            ORDER BY count(*) DESC
                            LIMIT 40
                        ) r
                    ) AS reason_counts
                """,
                (batch_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return {"rows": 0, "status": {}, "fields": [], "review_reasons": []}
        return {
            "rows": int(row[0] or 0),
            "status": dict(row[1] or {}),
            "fields": list(row[2] or []),
            "review_reasons": list(row[3] or []),
        }

    def fetch_tecdoc_coverage(self) -> dict[str, Any]:
        """Attribute fill rates per TecDoc entity type for the newest promoted batch."""
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL statement_timeout = {COVERAGE_TIMEOUT_MS}")
            cursor.execute(
                f"""
                SELECT batch_id
                FROM {TECDOC_CANDIDATES_TABLE}
                GROUP BY batch_id
                ORDER BY count(*) DESC
                LIMIT 1
                """
            )
            batch_row = cursor.fetchone()
            if batch_row is None:
                return {"batch_id": None, "entities": []}
            batch_id = str(batch_row[0])
            cursor.execute(
                f"""
                WITH scoped AS (
                    SELECT entity_type, attributes
                    FROM {TECDOC_CANDIDATES_TABLE}
                    WHERE batch_id = %s
                ), totals AS (
                    SELECT entity_type, count(*) AS rows FROM scoped GROUP BY entity_type
                ), fields AS (
                    SELECT s.entity_type, entry.key AS field,
                           count(*) FILTER (
                               WHERE entry.value IS NOT NULL
                                 AND entry.value <> 'null'::jsonb
                                 AND entry.value <> '""'::jsonb
                           ) AS present
                    FROM scoped s
                    CROSS JOIN LATERAL jsonb_each(coalesce(s.attributes, '{{}}'::jsonb)) AS entry
                    GROUP BY s.entity_type, entry.key
                )
                SELECT t.entity_type, t.rows, f.field, coalesce(f.present, 0)
                FROM totals t
                LEFT JOIN fields f ON f.entity_type = t.entity_type
                ORDER BY t.rows DESC, t.entity_type, coalesce(f.present, 0) DESC, f.field
                """,
                (batch_id,),
            )
            rows = cursor.fetchall()
        entities: dict[str, dict[str, Any]] = {}
        for entity_type, total, field, present in rows:
            entity = entities.setdefault(
                str(entity_type),
                {"entity_type": str(entity_type), "rows": int(total or 0), "fields": []},
            )
            if field is None:
                continue
            total_rows = int(total or 0)
            present_count = int(present or 0)
            entity["fields"].append(
                {
                    "entity_type": str(entity_type),
                    "field": str(field),
                    "present": present_count,
                    "missing": max(total_rows - present_count, 0),
                    "coverage": (present_count / total_rows) if total_rows else 0.0,
                }
            )
        return {"batch_id": batch_id, "entities": list(entities.values())}


RepositoryFactory = Callable[[], CoverageRepository]
