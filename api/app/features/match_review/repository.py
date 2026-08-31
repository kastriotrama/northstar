"""PostgreSQL access for the chunk review workspace."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from api.app.features.match_review.field_resolution import PredicateTerm
from ingestion.match_chunk_migrations import (
    MATCH_CHUNK_BUILDS_TABLE,
    MATCH_CHUNK_MEMBERS_TABLE,
    MATCH_CHUNK_PROPOSALS_TABLE,
    MATCH_CHUNK_SAMPLES_TABLE,
    MATCH_CHUNKS_TABLE,
    OEM_VIN_EVIDENCE_TABLE,
    run_match_chunk_migrations,
)
from ingestion.normalization_migrations import NORMALIZATION_RESULTS_TABLE

_BUILD_COLUMNS = (
    "build_id, source_batch_id, signature_version, status, "
    "row_count, chunk_count, started_at, finished_at"
)
_PROPOSAL_COLUMNS = (
    "proposal_id, chunk_id, proposal_source, adjudicator_version, "
    "recommendation, target_ktype_reference, confidence, evidence, reasoning, "
    "status, reviewed_by, review_note, reviewed_at, created_at"
)


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


def _build_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "build_id": row[0],
        "source_batch_id": str(row[1]),
        "signature_version": str(row[2]),
        "status": str(row[3]),
        "row_count": int(row[4]),
        "chunk_count": int(row[5]),
        "started_at": row[6],
        "finished_at": row[7],
    }


def _proposal_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "proposal_id": row[0],
        "chunk_id": row[1],
        "proposal_source": str(row[2]),
        "adjudicator_version": str(row[3]),
        "recommendation": str(row[4]),
        "target_ktype_reference": str(row[5]) if row[5] is not None else None,
        "confidence": float(row[6]),
        "evidence": dict(row[7]),
        "reasoning": str(row[8]),
        "status": str(row[9]),
        "reviewed_by": str(row[10]) if row[10] is not None else None,
        "review_note": str(row[11]) if row[11] is not None else None,
        "reviewed_at": row[12],
        "created_at": row[13],
    }


class MatchReviewRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def ensure_schema(self) -> None:
        with self._connection_factory() as connection:
            run_match_chunk_migrations(connection)

    def fetch_builds(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_BUILD_COLUMNS} FROM {MATCH_CHUNK_BUILDS_TABLE} "
                "ORDER BY started_at DESC, build_id LIMIT %s",
                (limit,),
            )
            rows = cursor.fetchall()
        return [_build_row(row) for row in rows]

    def fetch_build(self, build_id: UUID) -> dict[str, Any] | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_BUILD_COLUMNS} FROM {MATCH_CHUNK_BUILDS_TABLE} "
                "WHERE build_id = %s",
                (build_id,),
            )
            row = cursor.fetchone()
        return _build_row(row) if row is not None else None

    def fetch_latest_build(self) -> dict[str, Any] | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_BUILD_COLUMNS} FROM {MATCH_CHUNK_BUILDS_TABLE} "
                "WHERE status = 'completed' "
                "ORDER BY started_at DESC, build_id LIMIT 1"
            )
            row = cursor.fetchone()
        return _build_row(row) if row is not None else None

    def fetch_chunk_page(
        self,
        *,
        build_id: UUID,
        status: str | None,
        query: str,
        limit: int,
        offset: int,
    ) -> tuple[int, int, list[dict[str, Any]]]:
        conditions = ["build_id = %s"]
        parameters: list[object] = [build_id]
        if status is not None:
            conditions.append("status = %s")
            parameters.append(status)
        if query.strip():
            conditions.append(
                "concat_ws(' ', signature->>'manufacturer', "
                "signature->>'model_family') ILIKE %s"
            )
            parameters.append(f"%{query.strip()}%")
        where_clause = " AND ".join(conditions)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT count(*), "
                f"coalesce(sum(member_count) FILTER "
                f"(WHERE status IN ('approved', 'rejected', 'split')), 0) "
                f"FROM {MATCH_CHUNKS_TABLE} WHERE {where_clause}",
                parameters,
            )
            count_row = cursor.fetchone()
            total = int(count_row[0]) if count_row is not None else 0
            decided_members = int(count_row[1]) if count_row is not None else 0
            cursor.execute(
                f"""
                SELECT chunk_id, signature, member_count, reason_profile, status
                FROM {MATCH_CHUNKS_TABLE}
                WHERE {where_clause}
                ORDER BY member_count DESC, chunk_id
                LIMIT %s OFFSET %s
                """,
                (*parameters, limit, offset),
            )
            rows = cursor.fetchall()
        items = [
            {
                "chunk_id": row[0],
                "signature": dict(row[1]),
                "member_count": int(row[2]),
                "reason_profile": {
                    str(reason): int(count) for reason, count in dict(row[3]).items()
                },
                "status": str(row[4]),
            }
            for row in rows
        ]
        return total, decided_members, items

    def fetch_chunk(self, chunk_id: UUID) -> dict[str, Any] | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT chunk_id, build_id, signature, member_count,
                       reason_profile, status
                FROM {MATCH_CHUNKS_TABLE} WHERE chunk_id = %s
                """,
                (chunk_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return {
            "chunk_id": row[0],
            "build_id": row[1],
            "signature": dict(row[2]),
            "member_count": int(row[3]),
            "reason_profile": {
                str(reason): int(count) for reason, count in dict(row[4]).items()
            },
            "status": str(row[5]),
        }

    def fetch_members(
        self, chunk_id: UUID, *, limit: int = 25
    ) -> list[dict[str, Any]]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT members.source_record_id, members.source_batch_id,
                       members.normalization_status, members.review_reasons,
                       raw.raw_record->>'plate',
                       coalesce(raw.raw_record->>'manufacturer',
                                raw.raw_record->>'brand'),
                       raw.raw_record->>'model',
                       raw.raw_record->>'vehicle_year'
                FROM {MATCH_CHUNK_MEMBERS_TABLE} AS members
                LEFT JOIN staging.transportstyrelsen_raw AS raw
                    ON raw.id = members.source_record_id
                WHERE members.chunk_id = %s
                ORDER BY members.source_record_id
                LIMIT %s
                """,
                (chunk_id, limit),
            )
            rows = cursor.fetchall()
        return [
            {
                "source_record_id": int(row[0]),
                "source_batch_id": str(row[1]),
                "normalization_status": str(row[2]),
                "review_reasons": [str(reason) for reason in row[3]],
                "plate": str(row[4]) if row[4] is not None else None,
                "source_manufacturer": str(row[5]) if row[5] is not None else None,
                "source_model": str(row[6]) if row[6] is not None else None,
                "source_year": str(row[7]) if row[7] is not None else None,
            }
            for row in rows
        ]

    def fetch_unresolved_populations(
        self,
        build_id: UUID,
        *,
        source_field: str,
        signature_field: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Rows whose source states something the signature could not interpret."""

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT nullif(btrim(raw.raw_record ->> %s), '') AS source_value,
                       count(*) AS row_count
                FROM {MATCH_CHUNK_MEMBERS_TABLE} AS mem
                JOIN {MATCH_CHUNKS_TABLE} AS chunks USING (chunk_id)
                JOIN staging.transportstyrelsen_raw AS raw
                    ON raw.id = mem.source_record_id
                WHERE chunks.build_id = %s
                  AND nullif(btrim(chunks.signature ->> %s), '') IS NULL
                  AND nullif(btrim(raw.raw_record ->> %s), '') IS NOT NULL
                GROUP BY 1
                ORDER BY 2 DESC
                LIMIT %s
                """,
                (source_field, build_id, signature_field, source_field, limit),
            )
            rows = cursor.fetchall()
        return [
            {"source_value": str(row[0]), "row_count": int(row[1])} for row in rows
        ]

    def fetch_discriminators(
        self,
        build_id: UUID,
        *,
        source_field: str,
        source_value: str,
        signature_field: str,
        candidate_fields: tuple[str, ...],
        top_values: int = 8,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Break an unresolved population down by each candidate predicate field."""

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                WITH population AS (
                    SELECT raw.raw_record AS record
                    FROM {MATCH_CHUNK_MEMBERS_TABLE} AS mem
                    JOIN {MATCH_CHUNKS_TABLE} AS chunks USING (chunk_id)
                    JOIN staging.transportstyrelsen_raw AS raw
                        ON raw.id = mem.source_record_id
                    WHERE chunks.build_id = %s
                      AND nullif(btrim(chunks.signature ->> %s), '') IS NULL
                      AND btrim(raw.raw_record ->> %s) = %s
                ), total AS (
                    SELECT count(*) AS population FROM population
                ), pairs AS (
                    SELECT field.name AS field,
                           nullif(btrim(population.record ->> field.name), '')
                               AS value
                    FROM population, unnest(%s::text[]) AS field(name)
                ), counted AS (
                    SELECT field, value, count(*) AS occurrences
                    FROM pairs WHERE value IS NOT NULL
                    GROUP BY field, value
                )
                SELECT (SELECT population FROM total),
                       field,
                       count(*) AS distinct_count,
                       sum(occurrences) AS present_count,
                       (array_agg(value ORDER BY occurrences DESC, value))[1:%s],
                       (array_agg(occurrences ORDER BY occurrences DESC, value))[1:%s]
                FROM counted
                GROUP BY field
                """,
                (
                    build_id,
                    signature_field,
                    source_field,
                    source_value,
                    list(candidate_fields),
                    top_values,
                    top_values,
                ),
            )
            rows = cursor.fetchall()
        if not rows:
            return 0, []
        population = int(rows[0][0])
        breakdown = [
            {
                "field": str(row[1]),
                "distinct_count": int(row[2]),
                "present_count": int(row[3]),
                "top_values": [
                    {"value": str(value), "count": int(count)}
                    for value, count in zip(row[4], row[5], strict=True)
                ],
            }
            for row in rows
        ]
        return population, breakdown

    @staticmethod
    def _condition_sql(
        conditions: list[PredicateTerm],
    ) -> tuple[str, list[object]]:
        """Build the predicate. Field names and values are always parameters.

        Values inside a term are OR-ed, terms are AND-ed. Numeric operators
        cast the registry's text through a regex guard so a non-numeric value
        becomes NULL (row simply does not match) instead of raising.
        """

        clauses: list[str] = []
        parameters: list[object] = []
        for term in conditions:
            column = (
                "raw.raw_record" if term.layer == "source" else "chunks.signature"
            )
            expr = f"btrim({column} ->> %s)"
            numeric = (
                f"(CASE WHEN {expr} ~ '^-?[0-9]+(\\.[0-9]+)?$' "
                f"THEN {expr}::numeric END)"
            )
            if term.operator == "equals":
                clauses.append(f"{expr} = ANY(%s)")
                parameters.extend((term.field, list(term.values)))
            elif term.operator == "not_equals":
                clauses.append(f"({expr} IS NULL OR NOT ({expr} = ANY(%s)))")
                parameters.extend((term.field, term.field, list(term.values)))
            elif term.operator in {"starts_with", "contains"}:
                pattern = "{}%" if term.operator == "starts_with" else "%{}%"
                parts = []
                for value in term.values:
                    parts.append(f"{expr} LIKE %s")
                    parameters.extend((term.field, pattern.format(value)))
                clauses.append("(" + " OR ".join(parts) + ")")
            elif term.operator in {"gte", "lte"}:
                comparison = ">=" if term.operator == "gte" else "<="
                parts = []
                for value in term.values:
                    parts.append(f"{numeric} {comparison} %s::numeric")
                    parameters.extend((term.field, term.field, value))
                clauses.append("(" + " OR ".join(parts) + ")")
            else:
                raise ValueError(f"unsupported operator: {term.operator}")
        return " AND ".join(clauses), parameters

    def fetch_population_attributes(
        self,
        build_id: UUID,
        *,
        source_field: str,
        source_value: str,
        signature_field: str,
        top_values: int = 6,
        sample_limit: int = 20_000,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Every source key present in the population, for free-form picking.

        Bounded by ``sample_limit``; the scanned count is returned so callers
        can disclose that the figures come from a sample.
        """

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                WITH population AS (
                    SELECT raw.raw_record AS record
                    FROM {MATCH_CHUNK_MEMBERS_TABLE} AS mem
                    JOIN {MATCH_CHUNKS_TABLE} AS chunks USING (chunk_id)
                    JOIN staging.transportstyrelsen_raw AS raw
                        ON raw.id = mem.source_record_id
                    WHERE chunks.build_id = %s
                      AND nullif(btrim(chunks.signature ->> %s), '') IS NULL
                      AND btrim(raw.raw_record ->> %s) = %s
                    LIMIT %s
                ), scanned AS (
                    SELECT count(*) AS scanned_members FROM population
                ), pairs AS (
                    SELECT entry.key AS field,
                           nullif(btrim(entry.value), '') AS value
                    FROM population,
                         jsonb_each_text(population.record) AS entry(key, value)
                ), counted AS (
                    SELECT field, value, count(*) AS occurrences
                    FROM pairs WHERE value IS NOT NULL
                    GROUP BY field, value
                )
                SELECT (SELECT scanned_members FROM scanned),
                       field,
                       count(*) AS distinct_count,
                       sum(occurrences) AS present_count,
                       (array_agg(value ORDER BY occurrences DESC, value))[1:%s],
                       (array_agg(occurrences ORDER BY occurrences DESC, value))[1:%s]
                FROM counted
                GROUP BY field
                ORDER BY field
                """,
                (
                    build_id,
                    signature_field,
                    source_field,
                    source_value,
                    sample_limit,
                    top_values,
                    top_values,
                ),
            )
            rows = cursor.fetchall()
        if not rows:
            return 0, []
        return int(rows[0][0]), [
            {
                "field": str(row[1]),
                "distinct_count": int(row[2]),
                "present_count": int(row[3]),
                "top_values": [
                    {"value": str(value), "count": int(count)}
                    for value, count in zip(row[4], row[5], strict=True)
                ],
            }
            for row in rows
        ]

    def fetch_value_expansion(
        self,
        build_id: UUID,
        *,
        source_field: str,
        source_value: str,
        signature_field: str,
        expand_field: str,
        raw_field: str,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Which raw spellings sit behind one normalized value in this population."""

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT nullif(btrim(raw.raw_record ->> %s), '') AS raw_value,
                       count(*) AS row_count
                FROM {MATCH_CHUNK_MEMBERS_TABLE} AS mem
                JOIN {MATCH_CHUNKS_TABLE} AS chunks USING (chunk_id)
                JOIN staging.transportstyrelsen_raw AS raw
                    ON raw.id = mem.source_record_id
                WHERE chunks.build_id = %s
                  AND nullif(btrim(chunks.signature ->> %s), '') IS NULL
                  AND btrim(raw.raw_record ->> %s) = %s
                  AND btrim(chunks.signature ->> %s) = %s
                GROUP BY 1
                HAVING nullif(btrim(raw.raw_record ->> %s), '') IS NOT NULL
                ORDER BY 2 DESC
                LIMIT %s
                """,
                (
                    raw_field,
                    build_id,
                    signature_field,
                    source_field,
                    source_value,
                    expand_field,
                    "",
                    raw_field,
                    limit,
                ),
            )
            rows = cursor.fetchall()
        return [
            {"value": str(row[0]), "count": int(row[1])} for row in rows
        ]

    def fetch_population_oem_samples(
        self,
        build_id: UUID,
        *,
        source_field: str,
        source_value: str,
        signature_field: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """OEM evidence already held for vehicles in this unresolved population.

        Evidence is stored per chunk member, while a population spans chunks,
        so this reaches through members to any cached response for a car that
        matches the population predicate. Nothing is fetched from the provider.
        """

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT DISTINCT evidence.response_payload
                FROM {MATCH_CHUNK_SAMPLES_TABLE} AS samples
                JOIN {OEM_VIN_EVIDENCE_TABLE} AS evidence
                    ON evidence.id = samples.evidence_id
                JOIN {MATCH_CHUNK_MEMBERS_TABLE} AS mem
                    ON mem.chunk_id = samples.chunk_id
                   AND mem.source_record_id = samples.source_record_id
                JOIN {MATCH_CHUNKS_TABLE} AS chunks
                    ON chunks.chunk_id = mem.chunk_id
                JOIN staging.transportstyrelsen_raw AS raw
                    ON raw.id = mem.source_record_id
                WHERE chunks.build_id = %s
                  AND nullif(btrim(chunks.signature ->> %s), '') IS NULL
                  AND btrim(raw.raw_record ->> %s) = %s
                LIMIT %s
                """,
                (build_id, signature_field, source_field, source_value, limit),
            )
            rows = cursor.fetchall()
        return [dict(row[0]) for row in rows]

    def fetch_signature_values(
        self, build_id: UUID, *, signature_field: str, limit: int = 60
    ) -> list[dict[str, Any]]:
        """Values this signature field already holds, as free-text suggestions.

        For an open vocabulary such as `model_family` there is no canonical
        list, so the best guidance is what the register has already resolved to
        elsewhere in the same build — spelled exactly as normalization spells
        it, so an authored rule matches existing data instead of inventing a
        near-miss variant.
        """

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT nullif(btrim(signature ->> %s), '') AS value,
                       sum(member_count) AS rows
                FROM {MATCH_CHUNKS_TABLE}
                WHERE build_id = %s
                  AND nullif(btrim(signature ->> %s), '') IS NOT NULL
                GROUP BY 1
                ORDER BY 2 DESC
                LIMIT %s
                """,
                (signature_field, build_id, signature_field, limit),
            )
            rows = cursor.fetchall()
        return [{"value": str(row[0]), "count": int(row[1])} for row in rows]

    def preview_rule(
        self,
        build_id: UUID,
        *,
        conditions: list[PredicateTerm],
        signature_field: str,
        sample_limit: int = 5,
    ) -> dict[str, Any]:
        """Count what a candidate rule would resolve, and what it would contradict.

        `already_resolved` rows already carry a signature value, so the rule
        would be asserting over an existing decision rather than filling a gap.
        """

        clauses, parameters = self._condition_sql(conditions)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                WITH matched AS (
                    SELECT raw.raw_record ->> 'plate' AS plate,
                           nullif(btrim(chunks.signature ->> %s), '')
                               AS existing_value
                    FROM {MATCH_CHUNK_MEMBERS_TABLE} AS mem
                    JOIN {MATCH_CHUNKS_TABLE} AS chunks USING (chunk_id)
                    JOIN staging.transportstyrelsen_raw AS raw
                        ON raw.id = mem.source_record_id
                    WHERE chunks.build_id = %s AND {clauses}
                )
                SELECT count(*),
                       count(*) FILTER (WHERE existing_value IS NULL),
                       count(*) FILTER (WHERE existing_value IS NOT NULL),
                       (array_agg(plate) FILTER (WHERE plate IS NOT NULL))[1:%s]
                FROM matched
                """,
                (signature_field, build_id, *parameters, sample_limit),
            )
            row = cursor.fetchone()
        if row is None:
            return {
                "matched_rows": 0,
                "would_resolve": 0,
                "already_resolved": 0,
                "sample_plates": [],
            }
        return {
            "matched_rows": int(row[0]),
            "would_resolve": int(row[1]),
            "already_resolved": int(row[2]),
            "sample_plates": [str(plate) for plate in (row[3] or [])],
        }

    def fetch_field_profile(
        self,
        chunk_id: UUID,
        *,
        fields: tuple[str, ...],
        sample_limit: int = 5_000,
        top_values: int = 5,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Distinct raw source values per field across the chunk's members.

        A chunk is uniform in its normalized signature by construction, so this
        exposes the source-level spread the signature collapsed. Bounded by
        ``sample_limit`` so very large chunks stay responsive.
        """

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                WITH members AS (
                    SELECT raw.raw_record AS record
                    FROM {MATCH_CHUNK_MEMBERS_TABLE} AS mem
                    JOIN staging.transportstyrelsen_raw AS raw
                        ON raw.id = mem.source_record_id
                    WHERE mem.chunk_id = %s
                    ORDER BY mem.source_record_id
                    LIMIT %s
                ), scanned AS (
                    SELECT count(*) AS member_count FROM members
                ), pairs AS (
                    SELECT field.name AS field,
                           nullif(btrim(members.record ->> field.name), '')
                               AS value
                    FROM members, unnest(%s::text[]) AS field(name)
                ), counted AS (
                    SELECT field, value, count(*) AS occurrences
                    FROM pairs
                    WHERE value IS NOT NULL
                    GROUP BY field, value
                )
                SELECT (SELECT member_count FROM scanned),
                       field,
                       count(*) AS distinct_count,
                       sum(occurrences) AS present_count,
                       (array_agg(value ORDER BY occurrences DESC, value))[1:%s],
                       (array_agg(occurrences ORDER BY occurrences DESC, value))[1:%s]
                FROM counted
                GROUP BY field
                ORDER BY count(*) DESC, field
                """,
                (chunk_id, sample_limit, list(fields), top_values, top_values),
            )
            rows = cursor.fetchall()
        if not rows:
            return 0, []
        scanned = int(rows[0][0])
        profile = [
            {
                "field": str(row[1]),
                "distinct_count": int(row[2]),
                "present_count": int(row[3]),
                "top_values": [
                    {"value": str(value), "count": int(count)}
                    for value, count in zip(row[4], row[5], strict=True)
                ],
            }
            for row in rows
        ]
        return scanned, profile

    def fetch_member_evidence(
        self, chunk_id: UUID, source_record_id: int
    ) -> dict[str, Any] | None:
        """One member's TS raw evidence and latest normalized payload.

        The VIN is deliberately excluded; it never leaves the server.
        """

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT raw.raw_record - 'vin',
                       (
                           SELECT results.normalized_payload
                           FROM {NORMALIZATION_RESULTS_TABLE} AS results
                           WHERE results.source_record_id = members.source_record_id
                             AND results.source_batch_id = members.source_batch_id
                           ORDER BY results.updated_at DESC, results.id DESC
                           LIMIT 1
                       )
                FROM {MATCH_CHUNK_MEMBERS_TABLE} AS members
                LEFT JOIN staging.transportstyrelsen_raw AS raw
                    ON raw.id = members.source_record_id
                WHERE members.chunk_id = %s AND members.source_record_id = %s
                """,
                (chunk_id, source_record_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return {
            "source_record": dict(row[0] or {}),
            "normalized_payload": dict(row[1] or {}),
        }

    def fetch_member_vin(
        self, chunk_id: UUID, source_record_id: int
    ) -> str | None:
        """Resolve a member's VIN server-side; the VIN never reaches clients."""

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT raw.raw_record->>'vin'
                FROM {MATCH_CHUNK_MEMBERS_TABLE} AS members
                JOIN staging.transportstyrelsen_raw AS raw
                    ON raw.id = members.source_record_id
                WHERE members.chunk_id = %s AND members.source_record_id = %s
                """,
                (chunk_id, source_record_id),
            )
            row = cursor.fetchone()
        if row is None or row[0] is None:
            return None
        vin = str(row[0]).strip()
        return vin or None

    def fetch_oem_evidence(
        self, *, provider: str, vin: str, dataset_version: str
    ) -> dict[str, Any] | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id, vin, response_payload, fetched_at
                FROM {OEM_VIN_EVIDENCE_TABLE}
                WHERE provider = %s AND vin = %s AND dataset_version = %s
                """,
                (provider, vin, dataset_version),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return {
            "id": int(row[0]),
            "vin": str(row[1]),
            "response_payload": dict(row[2]),
            "fetched_at": row[3],
        }

    def insert_oem_evidence(
        self,
        *,
        request_id: UUID,
        provider: str,
        vin: str,
        dataset_version: str,
        response_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Append-only insert; a concurrent duplicate returns the stored row."""

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {OEM_VIN_EVIDENCE_TABLE}
                    (request_id, provider, vin, dataset_version, response_payload)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (provider, vin, dataset_version) DO NOTHING
                """,
                (request_id, provider, vin, dataset_version, Jsonb(response_payload)),
            )
            connection.commit()
        stored = self.fetch_oem_evidence(
            provider=provider, vin=vin, dataset_version=dataset_version
        )
        if stored is None:
            raise RuntimeError("OEM evidence row missing after idempotent insert")
        return stored

    def link_sample(
        self, *, chunk_id: UUID, evidence_id: int, source_record_id: int
    ) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {MATCH_CHUNK_SAMPLES_TABLE}
                    (chunk_id, evidence_id, source_record_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (chunk_id, evidence_id) DO NOTHING
                """,
                (chunk_id, evidence_id, source_record_id),
            )
            connection.commit()

    def fetch_samples(self, chunk_id: UUID) -> list[dict[str, Any]]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT samples.id, samples.source_record_id, evidence.provider,
                       evidence.vin, evidence.dataset_version,
                       evidence.response_payload, evidence.fetched_at
                FROM {MATCH_CHUNK_SAMPLES_TABLE} AS samples
                JOIN {OEM_VIN_EVIDENCE_TABLE} AS evidence
                    ON evidence.id = samples.evidence_id
                WHERE samples.chunk_id = %s
                ORDER BY samples.created_at, samples.id
                """,
                (chunk_id,),
            )
            rows = cursor.fetchall()
        return [
            {
                "sample_id": int(row[0]),
                "source_record_id": int(row[1]),
                "provider": str(row[2]),
                "vin": str(row[3]),
                "dataset_version": str(row[4]),
                "response_payload": dict(row[5]),
                "fetched_at": row[6],
            }
            for row in rows
        ]

    def insert_proposal(
        self,
        *,
        proposal_id: UUID,
        chunk_id: UUID,
        proposal_source: str,
        adjudicator_version: str,
        recommendation: str,
        target_ktype_reference: str | None,
        confidence: float,
        evidence: dict[str, Any],
        reasoning: str,
    ) -> dict[str, Any]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {MATCH_CHUNK_PROPOSALS_TABLE}
                    (proposal_id, chunk_id, proposal_source, adjudicator_version,
                     recommendation, target_ktype_reference, confidence,
                     evidence, reasoning)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (proposal_id) DO NOTHING
                """,
                (
                    proposal_id,
                    chunk_id,
                    proposal_source,
                    adjudicator_version,
                    recommendation,
                    target_ktype_reference,
                    confidence,
                    Jsonb(evidence),
                    reasoning,
                ),
            )
            cursor.execute(
                f"UPDATE {MATCH_CHUNKS_TABLE} SET status = 'proposed', "
                "updated_at = now() WHERE chunk_id = %s AND status = 'open'",
                (chunk_id,),
            )
            connection.commit()
        proposal = self.fetch_proposal(proposal_id)
        if proposal is None:
            raise RuntimeError("proposal row missing after idempotent insert")
        return proposal

    def fetch_proposal(self, proposal_id: UUID) -> dict[str, Any] | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_PROPOSAL_COLUMNS} FROM {MATCH_CHUNK_PROPOSALS_TABLE} "
                "WHERE proposal_id = %s",
                (proposal_id,),
            )
            row = cursor.fetchone()
        return _proposal_row(row) if row is not None else None

    def fetch_proposals(self, chunk_id: UUID) -> list[dict[str, Any]]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_PROPOSAL_COLUMNS} FROM {MATCH_CHUNK_PROPOSALS_TABLE} "
                "WHERE chunk_id = %s ORDER BY created_at DESC, proposal_id",
                (chunk_id,),
            )
            rows = cursor.fetchall()
        return [_proposal_row(row) for row in rows]

    def review_proposal(
        self,
        *,
        proposal_id: UUID,
        status: str,
        chunk_status: str,
        reviewer: str,
        note: str | None,
    ) -> dict[str, Any] | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {MATCH_CHUNK_PROPOSALS_TABLE}
                SET status = %s, reviewed_by = %s, review_note = %s,
                    reviewed_at = now()
                WHERE proposal_id = %s AND status = 'proposed'
                RETURNING chunk_id
                """,
                (status, reviewer, note, proposal_id),
            )
            row = cursor.fetchone()
            if row is not None:
                cursor.execute(
                    f"UPDATE {MATCH_CHUNKS_TABLE} SET status = %s, "
                    "updated_at = now() WHERE chunk_id = %s",
                    (chunk_status, row[0]),
                )
            connection.commit()
        if row is None:
            return None
        return self.fetch_proposal(proposal_id)


RepositoryFactory = Callable[[], MatchReviewRepository]
