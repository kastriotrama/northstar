"""PostgreSQL reads for the normalization review workspace."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

from psycopg import Connection, sql

from api.app.features.normalization_review.schemas import NormalizationReviewFilters
from ingestion.normalization_migrations import NORMALIZATION_RESULTS_TABLE

ALL_PARTS_SUFFIX = "-all-parts"
PART_MARKER = "-part-"


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


class NormalizationReviewRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def get_latest_batch_id(self) -> str | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT source_batch_id FROM {NORMALIZATION_RESULTS_TABLE} "
                "GROUP BY source_batch_id ORDER BY max(created_at) DESC, source_batch_id DESC "
                "LIMIT 1"
            )
            row = cursor.fetchone()
        if row is None:
            return None
        batch_id = str(row[0])
        if PART_MARKER in batch_id:
            return f"{batch_id.rsplit(PART_MARKER, maxsplit=1)[0]}{ALL_PARTS_SUFFIX}"
        return batch_id

    @staticmethod
    def _batch_condition(batch_id: str) -> tuple[sql.Composable, tuple[object, ...]]:
        if batch_id.endswith(ALL_PARTS_SUFFIX):
            prefix = batch_id.removesuffix(ALL_PARTS_SUFFIX)
            return (
                sql.SQL("source_batch_id = ANY(%s)"),
                ([f"{prefix}{PART_MARKER}{part:03d}" for part in range(1, 1000)],),
            )
        return sql.SQL("source_batch_id = %s"), (batch_id,)

    def fetch_page(
        self,
        *,
        batch_id: str,
        filters: NormalizationReviewFilters,
    ) -> tuple[int, list[dict[str, Any]]]:
        conditions, parameters = self._filter_conditions(filters)
        where_clause = sql.SQL(" AND ").join(conditions) if conditions else sql.SQL("TRUE")
        batch_condition, batch_parameters = self._batch_condition(batch_id)
        cte = (
            sql.SQL(
            f"""
            WITH latest AS (
                SELECT DISTINCT ON (source_record_id)
                    source_record_id,
                    source_batch_id,
                    status,
                    confidence,
                    normalized_payload,
                    applied_rule_ids,
                    review_reasons,
                    (
                        SELECT raw.raw_record
                        FROM staging.transportstyrelsen_raw AS raw
                        WHERE raw.id = source_record_id
                    ) AS source_evidence,
                        (
                        SELECT raw.raw_record->>'brand'
                        FROM staging.transportstyrelsen_raw AS raw
                        WHERE raw.id = source_record_id
                    ) AS source_brand
                FROM {NORMALIZATION_RESULTS_TABLE}
                WHERE
            """
            )
            + batch_condition
            + sql.SQL(
                """
                ORDER BY source_record_id, updated_at DESC, id DESC
            )
            """
            )
        )
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                cte + sql.SQL("SELECT count(*) FROM latest WHERE ") + where_clause,
                (*batch_parameters, *parameters),
            )
            count_row = cursor.fetchone()
            filtered_total = int(count_row[0]) if count_row is not None else 0
            cursor.execute(
                cte
                + sql.SQL(
                    "SELECT source_record_id, status, confidence, normalized_payload, "
                    "applied_rule_ids, review_reasons, source_evidence, source_brand, source_batch_id FROM latest WHERE "
                )
                + where_clause
                + sql.SQL(" ORDER BY source_record_id LIMIT %s OFFSET %s"),
                (*batch_parameters, *parameters, filters.limit, filters.offset),
            )
            rows = cursor.fetchall()
        return filtered_total, [
            {
                "source_record_id": int(row[0]),
                "status": str(row[1]),
                "confidence": float(row[2]),
                "normalized_payload": dict(row[3]),
                "applied_rule_ids": list(row[4]),
                "review_reasons": list(row[5]),
                "source_evidence": dict(row[6] or {}),
                "source_brand": str(row[7]) if row[7] is not None else None,
                "source_batch_id": str(row[8]),
            }
            for row in rows
        ]

    def fetch_summary(self, *, batch_id: str) -> dict[str, int]:
        counts = {"resolved": 0, "provisional": 0, "review_required": 0, "failed": 0}
        batch_condition, batch_parameters = self._batch_condition(batch_id)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    f"""
                WITH latest AS (
                    SELECT DISTINCT ON (source_record_id) source_record_id, status
                    FROM {NORMALIZATION_RESULTS_TABLE}
                    WHERE
                """
                )
                + batch_condition
                + sql.SQL(
                    """
                    ORDER BY source_record_id, updated_at DESC, id DESC
                )
                SELECT status, count(*) FROM latest GROUP BY status
                """
                ),
                batch_parameters,
            )
            for status, count in cursor.fetchall():
                counts[str(status)] = int(count)
        counts["total"] = sum(counts.values())
        return counts

    def fetch_facets(self, *, batch_id: str) -> dict[str, list[str]]:
        batch_condition, batch_parameters = self._batch_condition(batch_id)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    f"""
                WITH latest AS (
                    SELECT DISTINCT ON (source_record_id)
                        source_record_id,
                        normalized_payload
                    FROM {NORMALIZATION_RESULTS_TABLE}
                    WHERE
                """
                )
                + batch_condition
                + sql.SQL(
                    """
                    ORDER BY source_record_id, updated_at DESC, id DESC
                ), values AS (
                    SELECT
                        coalesce(
                            normalized_payload #>> '{normalized,manufacturer}',
                            normalized_payload #>> '{candidates,manufacturer}'
                        ) AS manufacturer,
                        normalized_payload #>> '{normalized,bodywork_form}' AS bodywork,
                        normalized_payload #>> '{normalized,transmission_type}' AS transmission,
                        normalized_payload #> '{normalized,energy_sources}' AS fuels
                    FROM latest
                )
                SELECT
                    coalesce(array_agg(DISTINCT manufacturer)
                        FILTER (WHERE manufacturer IS NOT NULL), '{}'),
                    coalesce(array_agg(DISTINCT bodywork)
                        FILTER (WHERE bodywork IS NOT NULL), '{}'),
                    coalesce(array_agg(DISTINCT transmission)
                        FILTER (WHERE transmission IS NOT NULL), '{}'),
                    coalesce(
                        (
                            SELECT array_agg(DISTINCT fuel ORDER BY fuel)
                            FROM values, jsonb_array_elements_text(coalesce(fuels, '[]')) AS fuel
                        ),
                        '{}'
                    )
                FROM values
                """
                ),
                batch_parameters,
            )
            row = cursor.fetchone()
        if row is None:
            return {
                "manufacturers": [],
                "bodywork_forms": [],
                "transmissions": [],
                "fuels": [],
            }
        return {
            "manufacturers": sorted(str(value) for value in row[0]),
            "bodywork_forms": sorted(str(value) for value in row[1]),
            "transmissions": sorted(str(value) for value in row[2]),
            "fuels": sorted(str(value) for value in row[3]),
        }

    def _filter_conditions(
        self, filters: NormalizationReviewFilters
    ) -> tuple[list[sql.Composable], list[object]]:
        conditions: list[sql.Composable] = []
        parameters: list[object] = []
        payload = "normalized_payload"
        if filters.query.strip():
            conditions.append(
                sql.SQL(
                    f"""
                    concat_ws(' ',
                        {payload} #>> '{{normalized,manufacturer}}',
                        {payload} #>> '{{candidates,manufacturer}}',
                        {payload} #>> '{{normalized,model_family}}',
                        {payload} #>> '{{candidates,model_family}}',
                        {payload} #>> '{{normalized,engine_code}}',
                        {payload} #>> '{{normalized,bodywork_form}}',
                        {payload} #>> '{{normalized,transmission_type}}',
                        {payload} #> '{{normalized,energy_sources}}',
                        source_brand,
                        source_evidence->>'plate'
                    ) ILIKE %s
                    """
                )
            )
            parameters.append(f"%{filters.query.strip()}%")
        simple_filters = (
            (filters.status, sql.SQL("status = %s")),
            (
                filters.manufacturer,
                sql.SQL(
                    "coalesce(normalized_payload #>> '{normalized,manufacturer}', "
                    "normalized_payload #>> '{candidates,manufacturer}') = %s"
                ),
            ),
            (
                filters.bodywork,
                sql.SQL("normalized_payload #>> '{normalized,bodywork_form}' = %s"),
            ),
            (
                filters.transmission,
                sql.SQL("normalized_payload #>> '{normalized,transmission_type}' = %s"),
            ),
        )
        for value, condition in simple_filters:
            if value is not None:
                conditions.append(condition)
                parameters.append(value)
        if filters.fuel is not None:
            conditions.append(sql.SQL("normalized_payload #> '{normalized,energy_sources}' ? %s"))
            parameters.append(filters.fuel)
        return conditions, parameters


RepositoryFactory = Callable[[], NormalizationReviewRepository]
