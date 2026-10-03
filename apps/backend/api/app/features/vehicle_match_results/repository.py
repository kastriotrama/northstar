"""Reads of stored match results joined to `core.vehicles`. Nothing here runs the matcher.

Every query starts from `core.vehicles` under the Vehicles filter and left-joins
the car's stored result, so a car without a result is counted (`not_evaluated`)
rather than lost. A person's choice lives on the vehicle (`match_state`, kept by
the choice feature) and outranks the matcher's state.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol

from psycopg import Connection

from ingestion.vehicle_core_migrations import VEHICLES_TABLE
from ingestion.vehicle_core_query import (
    ALIAS,
    VehicleTerm,
    compile_vehicle_filter,
    resolve_search,
)
from ingestion.vehicle_facts_query import CompiledPredicate
from ingestion.vehicle_ktype_choices import MATCH_STATE_MANUAL, MATCH_STATE_MANUAL_NONE
from ingestion.vehicle_match_result_migrations import (
    MATCH_STATES,
    VEHICLE_MATCH_RESULTS_TABLE,
)
from ingestion.vehicle_match_results import StoredRun, latest_run

STATE_CHOSEN = "chosen"
STATE_CHOSEN_NONE = "chosen_none"
STATE_NOT_EVALUATED = "not_evaluated"
OVERVIEW_STATES: tuple[str, ...] = (
    *MATCH_STATES,
    STATE_CHOSEN,
    STATE_CHOSEN_NONE,
    STATE_NOT_EVALUATED,
)

_JOIN = (
    f"FROM {VEHICLES_TABLE} AS {ALIAS} "
    f"LEFT JOIN {VEHICLE_MATCH_RESULTS_TABLE} AS m ON m.vehicle_id = {ALIAS}.vehicle_id"
)
_UNDECIDED = (
    f"({ALIAS}.match_state IS NULL OR {ALIAS}.match_state NOT IN "
    f"('{MATCH_STATE_MANUAL}', '{MATCH_STATE_MANUAL_NONE}'))"
)
#: The overview state of a joined row.
STATE_SQL = (
    f"CASE WHEN {ALIAS}.match_state = '{MATCH_STATE_MANUAL}' THEN '{STATE_CHOSEN}' "
    f"WHEN {ALIAS}.match_state = '{MATCH_STATE_MANUAL_NONE}' THEN '{STATE_CHOSEN_NONE}' "
    f"WHEN m.vehicle_id IS NULL THEN '{STATE_NOT_EVALUATED}' ELSE m.state END"
)
_CHANGED = f"(m.vehicle_id IS NOT NULL AND {ALIAS}.updated_at > m.evaluated_at)"

#: The cars list's columns after the vehicle's own.
_CAR_COLUMNS = (
    f"{ALIAS}.vehicle_id, {ALIAS}.plate, {ALIAS}.vin, {ALIAS}.manufacturer, "
    f"{ALIAS}.model_family, {ALIAS}.production_year, {STATE_SQL}, m.state, m.terminal, "
    f"{ALIAS}.ktype, m.ktype, m.best_candidate_ktype, m.confidence, m.candidate_ktypes, "
    "m.candidate_confidences, m.separating_fields, m.missing_fields, m.conflicting_fields, "
    f"m.reason_codes, m.evaluated_at, {_CHANGED}, {ALIAS}.match_state"
)
CAR_FIELDS: tuple[str, ...] = (
    "vehicle_id", "plate", "vin", "manufacturer", "model_family", "production_year", "state",
    "automatic_state", "terminal", "vehicle_ktype", "automatic_ktype", "best_candidate_ktype",
    "confidence", "candidate_ktypes", "candidate_confidences", "separating_fields",
    "missing_fields", "conflicting_fields", "reason_codes", "evaluated_at",
    "changed_since_matched", "vehicle_match_state",
)


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


class UnknownStateError(ValueError):
    """Not one of the overview's states."""


def state_predicate(state: str) -> str:
    """The rows of one overview state, written so the state index can serve it."""

    if state == STATE_CHOSEN:
        return f"{ALIAS}.match_state = '{MATCH_STATE_MANUAL}'"
    if state == STATE_CHOSEN_NONE:
        return f"{ALIAS}.match_state = '{MATCH_STATE_MANUAL_NONE}'"
    if state == STATE_NOT_EVALUATED:
        return f"m.vehicle_id IS NULL AND {_UNDECIDED}"
    if state in MATCH_STATES:
        return f"m.state = '{state}' AND {_UNDECIDED}"
    raise UnknownStateError(f"{state!r} is not a match result state")


class MatchResultRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def overview(self, terms: Sequence[VehicleTerm], text: str) -> dict[str, Any]:
        """Every count of the overview under one filter, on one connection."""

        with self._connection_factory() as connection:
            predicate = compile_vehicle_filter(terms, resolve_search(connection, text))
            where, parameters = predicate.sql, predicate.parameters

            def grouped(expression: str, extra: str = "true") -> list[tuple[str, int]]:
                rows = connection.execute(
                    f"SELECT {expression}, count(*) {_JOIN} WHERE {where} AND {extra} "
                    "GROUP BY 1 ORDER BY 2 DESC, 1",
                    parameters,
                ).fetchall()
                return [(str(value), int(count)) for value, count in rows if value is not None]

            def unnested(column: str, state: str) -> list[tuple[str, int]]:
                rows = connection.execute(
                    f"SELECT item, count(*) {_JOIN} CROSS JOIN LATERAL unnest(m.{column}) AS item "
                    f"WHERE {where} AND {state_predicate(state)} GROUP BY 1 ORDER BY 2 DESC, 1",
                    parameters,
                ).fetchall()
                return [(str(value), int(count)) for value, count in rows]

            states = grouped(STATE_SQL)
            row = connection.execute(
                f"SELECT count(*) FILTER (WHERE {_CHANGED}), "
                f"count(*) FILTER (WHERE {state_predicate('none')} "
                f"AND m.best_candidate_ktype IS NULL) {_JOIN} WHERE {where}",
                parameters,
            ).fetchone()
            return {
                "states": states,
                "terminals": grouped("m.terminal"),
                "several_candidate_counts": grouped(
                    "m.candidate_count::text", state_predicate("several")
                ),
                "several_separating_fields": unnested("separating_fields", "several"),
                "several_missing_fields": unnested("missing_fields", "several"),
                "none_conflicting_fields": unnested("conflicting_fields", "none"),
                "not_matchable_reasons": unnested("reason_codes", "not_matchable"),
                "changed_since_matched": int(row[0]) if row else 0,
                "none_without_candidates": int(row[1]) if row else 0,
                "catalog_batches": grouped("m.catalog_batch"),
                "matcher_versions": grouped("m.matcher_version"),
                "latest_run": latest_run(connection),
            }

    def cars(
        self,
        terms: Sequence[VehicleTerm],
        text: str,
        *,
        state: str,
        narrowing: CompiledPredicate,
        after: str | None,
        limit: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        """How many cars the state holds under the filter, and one keyset page of them."""

        with self._connection_factory() as connection:
            predicate = compile_vehicle_filter(terms, resolve_search(connection, text))
            where = f"{predicate.sql} AND {state_predicate(state)} AND {narrowing.sql}"
            parameters = [*predicate.parameters, *narrowing.parameters]
            counted = connection.execute(
                f"SELECT count(*) {_JOIN} WHERE {where}", parameters
            ).fetchone()
            cursor_sql = f" AND {ALIAS}.vehicle_id > %s" if after else ""
            rows = connection.execute(
                f"SELECT {_CAR_COLUMNS} {_JOIN} WHERE {where}{cursor_sql} "
                f"ORDER BY {ALIAS}.vehicle_id LIMIT %s",
                [*parameters, *([after] if after else []), limit],
            ).fetchall()
        total = int(counted[0]) if counted else 0
        return total, [dict(zip(CAR_FIELDS, row, strict=True)) for row in rows]

    def latest_run(self) -> StoredRun | None:
        with self._connection_factory() as connection:
            return latest_run(connection)


def narrowing_predicate(
    *,
    missing_field: str | None = None,
    separating_field: str | None = None,
    conflicting_field: str | None = None,
    reason: str | None = None,
    ktype: str | None = None,
    candidate_count: int | None = None,
) -> CompiledPredicate:
    """The optional narrowing of a state list; `true` when nothing is asked for."""

    fragments: list[str] = []
    parameters: list[Any] = []
    for column, value in (
        ("missing_fields", missing_field),
        ("separating_fields", separating_field),
        ("conflicting_fields", conflicting_field),
        ("reason_codes", reason),
    ):
        if value is not None:
            fragments.append(f"m.{column} @> ARRAY[%s]::text[]")
            parameters.append(value)
    if ktype is not None:
        fragments.append(
            f"(m.ktype = %s OR {ALIAS}.ktype = %s OR m.candidate_ktypes @> ARRAY[%s]::text[])"
        )
        parameters += [ktype, ktype, ktype]
    if candidate_count is not None:
        fragments.append("m.candidate_count = %s")
        parameters.append(candidate_count)
    return CompiledPredicate(" AND ".join(fragments) if fragments else "true", parameters)
