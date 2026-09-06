"""PostgreSQL access for the chunk review workspace."""

from __future__ import annotations

from collections.abc import Callable, Sequence
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
    MATCH_FIELD_RESOLUTIONS_TABLE,
    MATCH_RESOLUTION_RULES_TABLE,
    OEM_VIN_EVIDENCE_TABLE,
    run_match_chunk_migrations,
)
from ingestion.match_run_migrations import (
    MATCH_REVIEW_RULE_DECISIONS_TABLE,
    MATCH_RUN_PATTERN_MEMBERS_TABLE,
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
_RESOLUTION_RULE_COLUMNS = (
    "rule_id, build_id, source_field, source_value, target_field, "
    "target_value, conditions, author, note, matched_rows, would_resolve, "
    "already_resolved, status, resolved_rows, created_at, applied_at, "
    "applied_by, retired_at, retired_by"
)

# A row counts as still unresolved only while nothing has filled the gap: not
# the signature, and not a rule a reviewer has already run. Without the second
# half, applying a rule would leave the screen reporting the same population it
# just resolved.
_UNRESOLVED_ROW = f"""nullif(btrim(chunks.signature ->> %s), '') IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM {MATCH_FIELD_RESOLUTIONS_TABLE} AS res
                      WHERE res.source_record_id = mem.source_record_id
                        AND res.target_field = %s
                        AND res.superseded_at IS NULL
                  )"""


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


def _resolution_rule_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "rule_id": row[0],
        "build_id": row[1],
        "source_field": str(row[2]),
        "source_value": str(row[3]),
        "target_field": str(row[4]),
        "target_value": str(row[5]),
        "conditions": list(row[6]),
        "author": str(row[7]),
        "note": str(row[8]) if row[8] is not None else None,
        "matched_rows": int(row[9]),
        "would_resolve": int(row[10]),
        "already_resolved": int(row[11]),
        "status": str(row[12]),
        "resolved_rows": int(row[13]),
        "created_at": row[14],
        "applied_at": row[15],
        "applied_by": str(row[16]) if row[16] is not None else None,
        "retired_at": row[17],
        "retired_by": str(row[18]) if row[18] is not None else None,
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
        chunk_ids: Sequence[UUID] | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        conditions = ["build_id = %s"]
        parameters: list[object] = [build_id]
        if chunk_ids is not None:
            if not chunk_ids:
                return 0, []
            conditions.append("chunk_id = ANY(%s)")
            parameters.append(list(chunk_ids))
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
                f"SELECT count(*) FROM {MATCH_CHUNKS_TABLE} WHERE {where_clause}",
                parameters,
            )
            count_row = cursor.fetchone()
            total = int(count_row[0]) if count_row is not None else 0
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
        return total, items

    def fetch_build_progress(self, build_id: UUID) -> dict[str, int]:
        """How much of one build has actually been worked, build-wide.

        Deliberately independent of the list's filters: a search box narrowing
        the worklist must not appear to undo the progress made. The two kinds
        of work are counted apart because they are different claims — a chunk
        decision settles what a group of cars *matches*, a resolution rule
        fills a field the register left uninterpretable.
        """

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT coalesce(sum(member_count) FILTER (
                           WHERE status IN ('approved', 'split')), 0),
                       coalesce(sum(member_count) FILTER (
                           WHERE status = 'proposed'), 0),
                       coalesce(sum(member_count), 0)
                FROM {MATCH_CHUNKS_TABLE}
                WHERE build_id = %s
                """,
                (build_id,),
            )
            chunk_row = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT count(*), count(DISTINCT rule_id)
                FROM {MATCH_FIELD_RESOLUTIONS_TABLE}
                WHERE build_id = %s AND superseded_at IS NULL
                """,
                (build_id,),
            )
            resolution_row = cursor.fetchone()
        return {
            "decided_rows": int(chunk_row[0]) if chunk_row else 0,
            "in_review_rows": int(chunk_row[1]) if chunk_row else 0,
            "member_rows": int(chunk_row[2]) if chunk_row else 0,
            "resolved_rows": int(resolution_row[0]) if resolution_row else 0,
            "applied_rules": int(resolution_row[1]) if resolution_row else 0,
        }

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
                  AND {_UNRESOLVED_ROW}
                  AND nullif(btrim(raw.raw_record ->> %s), '') IS NOT NULL
                GROUP BY 1
                ORDER BY 2 DESC
                LIMIT %s
                """,
                (
                    source_field,
                    build_id,
                    signature_field,
                    signature_field,
                    source_field,
                    limit,
                ),
            )
            rows = cursor.fetchall()
        return [
            {"source_value": str(row[0]), "row_count": int(row[1])} for row in rows
        ]

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
        """Facets for the whole unresolved population (no extra narrowing)."""

        return self.fetch_refined_discriminators(
            build_id,
            signature_field=signature_field,
            conditions=[
                PredicateTerm("source", source_field, "equals", (source_value,))
            ],
            candidate_fields=candidate_fields,
            pinned_fields=(source_field,),
            top_values=top_values,
        )

    def fetch_refined_discriminators(
        self,
        build_id: UUID,
        *,
        signature_field: str,
        conditions: list[PredicateTerm],
        candidate_fields: tuple[str, ...],
        pinned_fields: tuple[str, ...] = (),
        top_values: int = 8,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Break down the rows matching a predicate, by each candidate field.

        Counts are recomputed against the *current* predicate, so the numbers a
        reviewer clicks on describe the population they have actually narrowed
        to. One exception makes multi-value rules possible: a field the rule
        already constrains is counted with **its own clause lifted**, every
        other clause still applied. Picking `model = E 220 D` therefore leaves
        the other Mercedes models visible and clickable — they are what the
        rule would cover if that clause were widened — instead of collapsing
        the field to the single value already chosen, which is a facet that can
        never be widened again.

        `pinned_fields` are exempt: the population's own anchor field defines
        which cars are in scope at all, so lifting its clause would offer
        values from outside the population being resolved.
        """

        pinned = set(pinned_fields)
        # Clauses that always apply: the anchor's, and anything keyed on a
        # field with no facet of its own. Filtering on them early keeps the
        # scanned set as small as the old single-predicate query did.
        early_terms = [
            term
            for term in conditions
            if term.field in pinned or term.field not in candidate_fields
        ]
        # Clauses that one facet each must ignore, carried as boolean columns.
        liftable_terms = [
            term
            for term in conditions
            if term.field not in pinned and term.field in candidate_fields
        ]

        early_sql, early_parameters = (
            self._condition_sql(early_terms) if early_terms else ("true", [])
        )
        flag_columns: list[str] = []
        flag_parameters: list[object] = []
        for position, term in enumerate(liftable_terms, start=1):
            clause, clause_parameters = self._condition_sql([term])
            flag_columns.append(f"({clause}) AS c{position}")
            flag_parameters.extend(clause_parameters)

        def mask(*, lifted_field: str | None) -> str:
            """AND of every flag except the ones keyed on `lifted_field`."""

            flags = [
                f"c{position}"
                for position, term in enumerate(liftable_terms, start=1)
                if term.field != lifted_field
            ]
            return " AND ".join(flags) if flags else "true"

        value_expressions = ", ".join(
            ["nullif(btrim(raw.raw_record ->> %s), '')"] * len(candidate_fields)
        )
        constrained_fields = {term.field for term in liftable_terms}
        open_indexes = [
            index
            for index, field in enumerate(candidate_fields, start=1)
            if field not in constrained_fields
        ]
        # One arm per constrained field, each reading the shared scan with its
        # own clause lifted. There are at most a handful; the rule builder caps
        # conditions at six.
        lifted_arms = "".join(
            f"""
                    UNION ALL
                    SELECT {index}, base.field_values[{index}]
                    FROM base
                    WHERE base.field_values[{index}] IS NOT NULL
                      AND {mask(lifted_field=candidate_fields[index - 1])}"""
            for index, field in enumerate(candidate_fields, start=1)
            if field in constrained_fields
        )

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                WITH base AS MATERIALIZED (
                    SELECT ARRAY[{value_expressions}] AS field_values
                           {"," if flag_columns else ""}
                           {", ".join(flag_columns)}
                    FROM {MATCH_CHUNK_MEMBERS_TABLE} AS mem
                    JOIN {MATCH_CHUNKS_TABLE} AS chunks USING (chunk_id)
                    JOIN staging.transportstyrelsen_raw AS raw
                        ON raw.id = mem.source_record_id
                    WHERE chunks.build_id = %s
                      AND {_UNRESOLVED_ROW}
                      AND {early_sql}
                ), matched AS (
                    SELECT field_values FROM base WHERE {mask(lifted_field=None)}
                ), total AS (
                    SELECT count(*) AS population FROM matched
                ), pairs AS (
                    SELECT term.field_index, term.value
                    FROM matched,
                         unnest(matched.field_values)
                             WITH ORDINALITY AS term(value, field_index)
                    WHERE term.value IS NOT NULL
                      AND term.field_index = ANY(%s::int[]){lifted_arms}
                ), counted AS (
                    SELECT field_index, value, count(*) AS occurrences
                    FROM pairs
                    GROUP BY field_index, value
                )
                SELECT (SELECT population FROM total),
                       field_index,
                       count(*) AS distinct_count,
                       sum(occurrences) AS present_count,
                       (array_agg(value ORDER BY occurrences DESC, value))[1:%s],
                       (array_agg(occurrences ORDER BY occurrences DESC, value))[1:%s]
                FROM counted
                GROUP BY field_index
                """,
                (
                    *candidate_fields,
                    *flag_parameters,
                    build_id,
                    signature_field,
                    signature_field,
                    *early_parameters,
                    open_indexes,
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
                "field": candidate_fields[int(row[1]) - 1],
                "distinct_count": int(row[2]),
                "present_count": int(row[3]),
                "constrained": candidate_fields[int(row[1]) - 1]
                in constrained_fields,
                "top_values": [
                    {"value": str(value), "count": int(count)}
                    for value, count in zip(row[4], row[5], strict=True)
                ],
            }
            for row in rows
        ]
        return population, breakdown

    def fetch_narrowing_trail(
        self,
        build_id: UUID,
        *,
        signature_field: str,
        conditions: list[PredicateTerm],
    ) -> list[int]:
        """Row count after each successive condition, in one pass.

        Shows which term did the work (191,921 -> 6,550 -> 926) rather than
        only the final number.
        """

        if not conditions:
            return []
        filters: list[str] = []
        parameters: list[object] = []
        for index in range(1, len(conditions) + 1):
            clause, clause_parameters = self._condition_sql(conditions[:index])
            filters.append(f"count(*) FILTER (WHERE {clause})")
            parameters.extend(clause_parameters)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {", ".join(filters)}
                FROM {MATCH_CHUNK_MEMBERS_TABLE} AS mem
                JOIN {MATCH_CHUNKS_TABLE} AS chunks USING (chunk_id)
                JOIN staging.transportstyrelsen_raw AS raw
                    ON raw.id = mem.source_record_id
                WHERE chunks.build_id = %s
                  AND {_UNRESOLVED_ROW}
                """,
                (*parameters, build_id, signature_field, signature_field),
            )
            row = cursor.fetchone()
        return [int(value) for value in row] if row else []

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

        `already_resolved` rows already carry a value — from the signature, or
        from a resolution rule someone has already run — so the rule would be
        asserting over an existing decision rather than filling a gap.
        """

        clauses, parameters = self._condition_sql(conditions)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                WITH matched AS (
                    SELECT raw.raw_record ->> 'plate' AS plate,
                           coalesce(
                               nullif(btrim(chunks.signature ->> %s), ''),
                               applied.target_value
                           ) AS existing_value
                    FROM {MATCH_CHUNK_MEMBERS_TABLE} AS mem
                    JOIN {MATCH_CHUNKS_TABLE} AS chunks USING (chunk_id)
                    JOIN staging.transportstyrelsen_raw AS raw
                        ON raw.id = mem.source_record_id
                    LEFT JOIN {MATCH_FIELD_RESOLUTIONS_TABLE} AS applied
                        ON applied.source_record_id = mem.source_record_id
                       AND applied.target_field = %s
                       AND applied.superseded_at IS NULL
                    WHERE chunks.build_id = %s AND {clauses}
                )
                SELECT count(*),
                       count(*) FILTER (WHERE existing_value IS NULL),
                       count(*) FILTER (WHERE existing_value IS NOT NULL),
                       (array_agg(plate) FILTER (WHERE plate IS NOT NULL))[1:%s]
                FROM matched
                """,
                (
                    signature_field,
                    signature_field,
                    build_id,
                    *parameters,
                    sample_limit,
                ),
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

    def insert_resolution_rule(
        self,
        *,
        rule_id: UUID,
        build_id: UUID,
        source_field: str,
        source_value: str,
        target_field: str,
        target_value: str,
        conditions: list[dict[str, Any]],
        author: str,
        note: str | None,
        matched_rows: int,
        would_resolve: int,
        already_resolved: int,
    ) -> dict[str, Any]:
        """Persist a saved rule. Definition columns are immutable thereafter."""

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {MATCH_RESOLUTION_RULES_TABLE}
                    (rule_id, build_id, source_field, source_value,
                     target_field, target_value, conditions, author, note,
                     matched_rows, would_resolve, already_resolved)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_RESOLUTION_RULE_COLUMNS}
                """,
                (
                    rule_id,
                    build_id,
                    source_field,
                    source_value,
                    target_field,
                    target_value,
                    Jsonb(conditions),
                    author,
                    note,
                    matched_rows,
                    would_resolve,
                    already_resolved,
                ),
            )
            row = cursor.fetchone()
            connection.commit()
        if row is None:
            raise RuntimeError("resolution rule row missing after insert")
        return _resolution_rule_row(row)

    def fetch_resolution_rules(
        self,
        build_id: UUID,
        *,
        source_field: str | None = None,
        source_value: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        filters = ["build_id = %s"]
        parameters: list[object] = [build_id]
        if source_field is not None:
            filters.append("source_field = %s")
            parameters.append(source_field)
        if source_value is not None:
            filters.append("source_value = %s")
            parameters.append(source_value)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_RESOLUTION_RULE_COLUMNS} "
                f"FROM {MATCH_RESOLUTION_RULES_TABLE} "
                f"WHERE {' AND '.join(filters)} "
                f"ORDER BY created_at DESC LIMIT %s",
                (*parameters, limit),
            )
            rows = cursor.fetchall()
        return [_resolution_rule_row(row) for row in rows]

    def fetch_resolution_rule(self, rule_id: UUID) -> dict[str, Any] | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_RESOLUTION_RULE_COLUMNS} "
                f"FROM {MATCH_RESOLUTION_RULES_TABLE} WHERE rule_id = %s",
                (rule_id,),
            )
            row = cursor.fetchone()
        return None if row is None else _resolution_rule_row(row)

    def apply_resolution_rule(
        self,
        rule_id: UUID,
        *,
        build_id: UUID,
        conditions: list[PredicateTerm],
        target_field: str,
        target_value: str,
        applied_by: str,
    ) -> dict[str, Any]:
        """Write one resolution per matched car that still lacks the field.

        Rows whose signature already carries a value are skipped, so running a
        rule can only fill gaps — never overwrite a decision normalization
        already made. Re-running is idempotent: the partial unique index makes
        a second pass insert only rows the first pass did not reach.
        """

        clauses, parameters = self._condition_sql(conditions)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                WITH matched AS (
                    SELECT DISTINCT mem.source_record_id
                    FROM {MATCH_CHUNK_MEMBERS_TABLE} AS mem
                    JOIN {MATCH_CHUNKS_TABLE} AS chunks USING (chunk_id)
                    JOIN staging.transportstyrelsen_raw AS raw
                        ON raw.id = mem.source_record_id
                    WHERE chunks.build_id = %s
                      AND {_UNRESOLVED_ROW}
                      AND {clauses}
                )
                INSERT INTO {MATCH_FIELD_RESOLUTIONS_TABLE}
                    (rule_id, build_id, source_record_id, target_field,
                     target_value)
                SELECT %s, %s, source_record_id, %s, %s FROM matched
                ON CONFLICT DO NOTHING
                """,
                (
                    build_id,
                    target_field,
                    target_field,
                    *parameters,
                    rule_id,
                    build_id,
                    target_field,
                    target_value,
                ),
            )
            resolved_now = cursor.rowcount
            cursor.execute(
                f"""
                UPDATE {MATCH_RESOLUTION_RULES_TABLE}
                SET status = 'applied',
                    resolved_rows = resolved_rows + %s,
                    applied_at = now(),
                    applied_by = %s
                WHERE rule_id = %s
                RETURNING {_RESOLUTION_RULE_COLUMNS}
                """,
                (resolved_now, applied_by, rule_id),
            )
            row = cursor.fetchone()
            connection.commit()
        if row is None:
            raise RuntimeError(f"resolution rule {rule_id} vanished while applying")
        applied = _resolution_rule_row(row)
        applied["resolved_now"] = max(resolved_now, 0)
        return applied

    def retire_resolution_rule(
        self, rule_id: UUID, *, retired_by: str
    ) -> dict[str, Any]:
        """Supersede every resolution this rule wrote, and close the rule.

        The rows stay — they record what was asserted and when — but they stop
        counting as resolved, so the population they came from reopens.
        """

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {MATCH_FIELD_RESOLUTIONS_TABLE}
                SET superseded_at = now()
                WHERE rule_id = %s AND superseded_at IS NULL
                """,
                (rule_id,),
            )
            superseded = cursor.rowcount
            cursor.execute(
                f"""
                UPDATE {MATCH_RESOLUTION_RULES_TABLE}
                SET status = 'retired',
                    resolved_rows = 0,
                    retired_at = now(),
                    retired_by = %s
                WHERE rule_id = %s
                RETURNING {_RESOLUTION_RULE_COLUMNS}
                """,
                (retired_by, rule_id),
            )
            row = cursor.fetchone()
            connection.commit()
        if row is None:
            raise RuntimeError(f"resolution rule {rule_id} vanished while retiring")
        retired = _resolution_rule_row(row)
        retired["superseded_rows"] = max(superseded, 0)
        return retired

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

    def fetch_pattern_chunks(
        self, *, operation_id: UUID, pattern_key: str, build_id: UUID
    ) -> dict[str, Any]:
        """Resolve a blocker pattern to the chunks holding its rows.

        Both sides key members on ``source_record_id`` in
        ``staging.transportstyrelsen_raw``, so the pattern is a lens over
        chunks rather than a second grouping. Rows the build never chunked are
        counted, not hidden: a pattern is only fully actionable here when
        ``matched_rows`` equals ``pattern_rows``.
        """

        with self._connection_factory() as connection, connection.cursor() as cursor:
            # The blocker workspace owns these tables and migrates them on its
            # own path. Until a matcher run has created them there is nothing
            # to bridge, and that is a state to report, not a server error.
            cursor.execute(
                "SELECT to_regclass(%s)", (MATCH_RUN_PATTERN_MEMBERS_TABLE,)
            )
            if (cursor.fetchone() or (None,))[0] is None:
                return {"pattern_rows": 0, "matched_rows": 0, "chunks": []}
            cursor.execute(
                f"""
                SELECT count(*)
                FROM {MATCH_RUN_PATTERN_MEMBERS_TABLE}
                WHERE operation_id = %s AND pattern_key = %s
                """,
                (operation_id, pattern_key),
            )
            pattern_rows = int((cursor.fetchone() or (0,))[0])
            cursor.execute(
                f"""
                SELECT chunks.chunk_id, chunks.signature, chunks.member_count,
                       chunks.status, count(*) AS overlap
                FROM {MATCH_RUN_PATTERN_MEMBERS_TABLE} AS pattern
                JOIN {MATCH_CHUNK_MEMBERS_TABLE} AS members
                  ON members.source_record_id = pattern.source_record_id
                JOIN {MATCH_CHUNKS_TABLE} AS chunks
                  ON chunks.chunk_id = members.chunk_id
                WHERE pattern.operation_id = %s
                  AND pattern.pattern_key = %s
                  AND chunks.build_id = %s
                GROUP BY chunks.chunk_id, chunks.signature, chunks.member_count,
                         chunks.status
                ORDER BY overlap DESC, chunks.chunk_id
                """,
                (operation_id, pattern_key, build_id),
            )
            rows = cursor.fetchall()
        chunks = [
            {
                "chunk_id": row[0],
                "signature": dict(row[1]),
                "member_count": int(row[2]),
                "status": str(row[3]),
                "overlap_rows": int(row[4]),
            }
            for row in rows
        ]
        return {
            "pattern_rows": pattern_rows,
            "matched_rows": sum(chunk["overlap_rows"] for chunk in chunks),
            "chunks": chunks,
        }

    def fetch_pattern_decisions(
        self, *, operation_id: UUID, pattern_key: str
    ) -> list[dict[str, Any]]:
        """Prior pattern-level rulings, newest first, for read-only history."""

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass(%s)", (MATCH_REVIEW_RULE_DECISIONS_TABLE,)
            )
            if (cursor.fetchone() or (None,))[0] is None:
                return []
            cursor.execute(
                f"""
                SELECT decision_id, action, reviewer, reason, created_at
                FROM {MATCH_REVIEW_RULE_DECISIONS_TABLE}
                WHERE operation_id = %s AND pattern_key = %s
                ORDER BY created_at DESC, decision_id DESC
                """,
                (operation_id, pattern_key),
            )
            rows = cursor.fetchall()
        return [
            {
                "decision_id": str(row[0]),
                "action": str(row[1]),
                "reviewer": str(row[2]),
                "reason": str(row[3]),
                "created_at": row[4],
            }
            for row in rows
        ]

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
