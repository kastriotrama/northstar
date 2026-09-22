"""Reads over the flat vehicle projection.

Every statement here is built by the shared predicate compiler, so the SQL a
browse list runs is the SQL a rule preview runs. Field names are whitelisted by
the compiler; values are always bound.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol

from psycopg import Connection

from ingestion.normalization_migrations import NORMALIZATION_RESULTS_TABLE
from ingestion.vehicle_facts import STAGING_TABLE
from ingestion.vehicle_facts_migrations import (
    RESOLVABLE_FIELDS,
    VEHICLE_FACTS_TABLE,
    effective_value,
    unresolved_predicate,
)
from ingestion.vehicle_facts_query import (
    CompiledPredicate,
    UnknownFieldError,
    canonical_page_statement,
    compile_predicate,
    compile_search_text,
    count_statement,
    facet_statement,
    group_statement,
    page_statement,
)

# Which registry field a reviewer would look at to explain each canonical one.
# Only used for display, so an absent entry costs a hint, not correctness.
SOURCE_FOR_FIELD: dict[str, str] = {
    "manufacturer": "brand",
    "model_family": "model",
    "drive_type": "is_4wd",
    "bodywork_form": "body_code",
    "power_kw": "kw",
    "displacement_cc": "ccm",
    "production_year": "vehicle_year",
    "engine_code": "type_text",
}


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


def _terms(conditions: Sequence[Any]) -> list[tuple[str, str, str, tuple[str, ...]]]:
    """Flatten API conditions into the compiler's structural tuples."""

    return [
        (condition.layer, condition.field, condition.operator, condition.terms)
        for condition in conditions
    ]


class VehicleFilterRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def _predicate(
        self, conditions: Sequence[Any], unresolved_field: str | None
    ) -> CompiledPredicate:
        terms = _terms(conditions)
        if not terms and unresolved_field is None:
            # "Everything" is a legitimate starting point for a filter box, but
            # `compile_predicate` refuses an empty conjunction so that a rule can
            # never be saved without one.
            return CompiledPredicate("true", [])
        if not terms:
            return CompiledPredicate(f"({unresolved_predicate(str(unresolved_field))})", [])
        return compile_predicate(terms, unresolved_field=unresolved_field)

    def total_rows(self) -> int:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT count(*) FROM {VEHICLE_FACTS_TABLE}")
            row = cursor.fetchone()
        return int(row[0]) if row else 0

    def count(self, conditions: Sequence[Any], unresolved_field: str | None) -> int:
        predicate = self._predicate(conditions, unresolved_field)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(count_statement(predicate), predicate.parameters)
            row = cursor.fetchone()
        return int(row[0]) if row else 0

    def unresolved_summary(
        self, conditions: Sequence[Any]
    ) -> tuple[int, list[tuple[str, int]]]:
        """How many matched cars still lack each resolvable field.

        One scan answers every field: asking them separately would repeat the
        same filter eight times.
        """

        predicate = self._predicate(conditions, None)
        # One scan of the matched set with a counter per field. Measured at 0.20s
        # filtered and 0.65s unfiltered; a version using one indexed subquery per
        # field was slower at both, because eight index scans over overlapping
        # populations cost more than a single pass that counts them all at once.
        counters = ", ".join(
            f"count(*) FILTER (WHERE {unresolved_predicate(field)}) AS {field}"
            for field in RESOLVABLE_FIELDS
        )
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT count(*)::bigint, {counters} "
                f"FROM {VEHICLE_FACTS_TABLE} WHERE {predicate.sql}",
                predicate.parameters,
            )
            row = cursor.fetchone()
        if row is None:
            return 0, []
        matched = int(row[0])
        return matched, [
            (field, int(value)) for field, value in zip(RESOLVABLE_FIELDS, row[1:])
        ]

    def facet(
        self,
        conditions: Sequence[Any],
        unresolved_field: str | None,
        *,
        field: str,
        limit: int,
    ) -> list[tuple[str, int]]:
        """Top values of one field inside the filter, with its own clause lifted.

        A field the filter already pins would otherwise report that one value at
        100%, which is true and useless: it hides every sibling value the
        reviewer might want to add. Counting with this field's own conditions
        removed keeps the alternatives visible and their counts honest, so
        picking a second model is one click rather than a restart.
        """

        others = [
            condition
            for condition in conditions
            if not (condition.field == field and condition.layer == "source")
        ]
        predicate = self._predicate(others, unresolved_field)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                facet_statement(predicate, field, limit=limit), predicate.parameters
            )
            rows = cursor.fetchall()
        return [(str(value), int(count)) for value, count in rows]

    def page(
        self,
        conditions: Sequence[Any],
        unresolved_field: str | None,
        *,
        cursor_id: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        predicate = self._predicate(conditions, unresolved_field)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                page_statement(predicate, limit=limit),
                [*predicate.parameters, cursor_id, limit],
            )
            rows = cursor.fetchall()
        return [
            {
                "source_record_id": int(row[0]),
                "plate": row[1],
                "brand": row[2],
                "model": row[3],
                "variant": row[4],
                "version": row[5],
                "vehicle_year": row[6],
                "kw": row[7],
                "norm_status": row[8],
            }
            for row in rows
        ]

    def search_page(
        self,
        conditions: Sequence[Any],
        text: str,
        *,
        cursor_id: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Cars matching the filter and the free text, as canonical values."""

        base = self._predicate(conditions, None)
        search = compile_search_text(text)
        predicate = (
            base
            if search is None
            else CompiledPredicate(
                f"({base.sql}) AND ({search.sql})",
                [*base.parameters, *search.parameters],
            )
        )
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                canonical_page_statement(predicate, limit=limit),
                [*predicate.parameters, cursor_id, limit],
            )
            rows = cursor.fetchall()
        fields = RESOLVABLE_FIELDS
        results = []
        for row in rows:
            canonical = {
                field: row[3 + index * 2] for index, field in enumerate(fields)
            }
            rule_filled = [
                field for index, field in enumerate(fields) if row[4 + index * 2]
            ]
            tail = 3 + len(fields) * 2
            results.append(
                {
                    "source_record_id": int(row[0]),
                    "plate": row[1],
                    "vin": row[2],
                    **canonical,
                    "rule_filled": rule_filled,
                    "fuel": row[tail],
                    "transmission": row[tail + 1],
                    "euro_class": row[tail + 2],
                    "norm_status": row[tail + 3],
                }
            )
        return results

    def search_count(self, conditions: Sequence[Any], text: str) -> int:
        base = self._predicate(conditions, None)
        search = compile_search_text(text)
        predicate = (
            base
            if search is None
            else CompiledPredicate(
                f"({base.sql}) AND ({search.sql})",
                [*base.parameters, *search.parameters],
            )
        )
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(count_statement(predicate), predicate.parameters)
            row = cursor.fetchone()
        return int(row[0]) if row else 0

    def full_record(self, source_record_id: int) -> dict[str, Any] | None:
        """Everything known about one car: the registry row, every normalized value.

        `detail` answers "which canonical fields did rules or normalization settle";
        this answers "what is this car". It reads the raw registry row and the latest
        normalization result by primary key, so nothing is scanned.
        """

        facts = self.detail(source_record_id)
        if facts is None:
            return None
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT source_batch_id, ingested_at, raw_record "
                f"FROM {STAGING_TABLE} WHERE id = %s",
                (source_record_id,),
            )
            raw = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT status, confidence, normalized_payload, applied_rule_ids,
                       review_reasons, mapping_version, rule_version, pipeline_version,
                       updated_at
                FROM {NORMALIZATION_RESULTS_TABLE}
                WHERE source_table = %s AND source_record_id = %s
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (STAGING_TABLE, source_record_id),
            )
            norm = cursor.fetchone()

        payload = dict(norm[2] or {}) if norm else {}
        return {
            **facts,
            "ingested_at": raw[1] if raw else None,
            "registry": dict(raw[2] or {}) if raw else {},
            "normalized": dict(payload.get("normalized") or {}),
            "normalization": (
                {
                    "status": norm[0],
                    "confidence": float(norm[1] or 0.0),
                    "applied_rule_ids": [str(v) for v in (norm[3] or [])],
                    "review_reasons": [str(v) for v in (norm[4] or [])],
                    "mapping_version": norm[5],
                    "rule_version": norm[6],
                    "pipeline_version": norm[7],
                    "updated_at": norm[8],
                }
                if norm
                else None
            ),
        }

    def detail(self, source_record_id: int) -> dict[str, Any] | None:
        """One car, with each canonical field's origin and outcome."""

        source_columns = sorted({name for name in SOURCE_FOR_FIELD.values()})
        normalized = ", ".join(
            f"n_{field}, r_{field}" for field in RESOLVABLE_FIELDS
        )
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT plate, vin, norm_status, source_batch_id, "
                f"{', '.join(source_columns)}, {normalized} "
                f"FROM {VEHICLE_FACTS_TABLE} WHERE source_record_id = %s",
                (source_record_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None

        plate, vin, status, batch = row[0], row[1], row[2], row[3]
        offset = 4
        sources = dict(zip(source_columns, row[offset : offset + len(source_columns)]))
        offset += len(source_columns)

        fields = []
        for index, field in enumerate(RESOLVABLE_FIELDS):
            derived = row[offset + index * 2]
            resolved = row[offset + index * 2 + 1]
            source_field = SOURCE_FOR_FIELD.get(field)
            source_value = sources.get(source_field) if source_field else None
            # A reviewer's assertion outranks the derivation it corrects, so
            # the row reports the rule whenever one has spoken -- otherwise the
            # panel would keep showing the estate this car was corrected out of.
            if resolved is not None:
                state = "rule_resolved"
            elif derived is not None:
                state = "resolved"
            else:
                state = "unresolved"
            fields.append(
                {
                    "field": field,
                    "source_field": source_field,
                    "source_value": None if source_value is None else str(source_value),
                    "normalized_value": None if derived is None else str(derived),
                    "resolved_value": None if resolved is None else str(resolved),
                    "status": state,
                }
            )
        return {
            "source_record_id": source_record_id,
            "plate": plate,
            "vin": vin,
            "norm_status": status,
            "source_batch_id": batch,
            "fields": fields,
        }


    def advisor_evidence(
        self, conditions: Sequence[Any], *, target_field: str, candidate_fields: Sequence[str]
    ) -> tuple[int, list[dict[str, Any]], dict[str, list[tuple[str, int]]]]:
        """Population size, scored candidate fields, and their value spreads.

        Shaped for the rule advisor, which asks the same three questions of any
        population: how big is it, what could split it, and how do those values
        fall. Scoped by the filter rather than by a build, so the advisor reasons
        about the cars the reviewer is actually looking at.
        """

        population = self.count(conditions, target_field)
        if population == 0:
            return 0, [], {}

        discriminators: list[dict[str, Any]] = []
        field_values: dict[str, list[tuple[str, int]]] = {}
        for field in candidate_fields:
            try:
                values = self.facet(conditions, target_field, field=field, limit=40)
            except UnknownFieldError:
                continue
            if not values:
                continue
            present = sum(count for _, count in values)
            distinct = len(values)
            largest = max(count for _, count in values)
            # A field that is one value across the whole population cannot split
            # it, and one that is near-unique splits it into noise.
            separation = 1.0 - (largest / present) if present else 0.0
            discriminators.append(
                {
                    "field": field,
                    "distinct_count": distinct,
                    "present_count": present,
                    "coverage": round(present / population, 4),
                    "separation": round(separation, 4),
                    "usable": distinct > 1 and separation > 0.02,
                }
            )
            field_values[field] = values

        discriminators.sort(key=lambda item: item["separation"], reverse=True)
        return population, discriminators, field_values


    def resolved_profile(self, conditions: Sequence[Any]) -> dict[str, Any]:
        """What is already settled about the matched cars.

        The advisor was only ever shown the registry columns it might filter on,
        so it had to infer the vehicle from raw spellings while the normalized
        identity -- manufacturer, model family, power, displacement, year -- sat
        one table over, already derived and already trusted. A field is reported
        only when it is uniform across the population, because a fact about the
        block is the only kind of fact worth reasoning from.
        """

        predicate = self._predicate(conditions, None)
        fields = [f for f in RESOLVABLE_FIELDS]
        selects = ", ".join(
            f"count(DISTINCT {effective_value(f, cast='text')}) AS d_{f}, "
            f"min({effective_value(f, cast='text')}) AS v_{f}"
            for f in fields
        )
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {selects} FROM {VEHICLE_FACTS_TABLE} WHERE {predicate.sql}",
                predicate.parameters,
            )
            row = cursor.fetchone()
        if row is None:
            return {}
        profile: dict[str, Any] = {}
        for index, field in enumerate(fields):
            distinct, value = row[index * 2], row[index * 2 + 1]
            if distinct == 1 and value is not None:
                profile[field] = value
        return profile


    def gap_groups(
        self,
        conditions: Sequence[Any],
        unresolved_field: str,
        *,
        field: str,
        mode: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Where the gap actually lives, grouped by the shape of the value.

        Answers "where is the leverage" rather than "which cars are these", which
        is a different question and the one an exact-value list cannot answer.
        """

        predicate = self._predicate(conditions, unresolved_field)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                group_statement(predicate, field, mode, limit=limit),
                predicate.parameters,
            )
            rows = cursor.fetchall()
        return [
            {
                "label": str(row[0]),
                "rows": int(row[1]),
                "distinct_values": int(row[2]),
                "samples": [str(value) for value in (row[3] or [])],
            }
            for row in rows
        ]
