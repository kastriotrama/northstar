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

from ingestion.vehicle_facts_migrations import (
    RESOLVABLE_FIELDS,
    VEHICLE_FACTS_TABLE,
    unresolved_predicate,
)
from ingestion.vehicle_facts_query import (
    CompiledPredicate,
    UnknownFieldError,
    compile_predicate,
    count_statement,
    facet_statement,
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
            if derived is not None:
                state = "resolved"
            elif resolved is not None:
                state = "rule_resolved"
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
