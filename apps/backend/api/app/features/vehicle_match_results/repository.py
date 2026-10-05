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

from ingestion.match_chunk_migrations import (
    MATCH_FIELD_RESOLUTIONS_TABLE,
    MATCH_RESOLUTION_RULES_TABLE,
)
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
        """Every count of the overview under one filter, in one pass over the cars.

        The filtered cars are read once, each with its overview state, and every
        count is taken from that set: a dozen separate scans made the overview
        of all cars take seconds.
        """

        with self._connection_factory() as connection:
            predicate = compile_vehicle_filter(terms, resolve_search(connection, text))
            rows = connection.execute(
                f"""
                WITH cars AS MATERIALIZED (
                    SELECT {STATE_SQL} AS state, m.terminal, m.candidate_count,
                           m.separating_fields, m.missing_fields, m.conflicting_fields,
                           m.reason_codes, m.catalog_batch, m.matcher_version,
                           m.best_candidate_ktype, {_CHANGED} AS changed
                    {_JOIN} WHERE {predicate.sql}
                )
                SELECT 'states', state, count(*) FROM cars GROUP BY 2
                UNION ALL
                SELECT 'terminals', terminal, count(*) FROM cars
                WHERE terminal IS NOT NULL GROUP BY 2
                UNION ALL
                SELECT 'catalog_batches', catalog_batch, count(*) FROM cars
                WHERE catalog_batch IS NOT NULL GROUP BY 2
                UNION ALL
                SELECT 'matcher_versions', matcher_version, count(*) FROM cars
                WHERE matcher_version IS NOT NULL GROUP BY 2
                UNION ALL
                SELECT 'several_candidate_counts', candidate_count::text, count(*) FROM cars
                WHERE state = 'several' GROUP BY 2
                UNION ALL
                SELECT 'several_separating_fields', item, count(*)
                FROM cars CROSS JOIN LATERAL unnest(separating_fields) AS item
                WHERE state = 'several' GROUP BY 2
                UNION ALL
                SELECT 'several_missing_fields', item, count(*)
                FROM cars CROSS JOIN LATERAL unnest(missing_fields) AS item
                WHERE state = 'several' GROUP BY 2
                UNION ALL
                SELECT 'none_conflicting_fields', item, count(*)
                FROM cars CROSS JOIN LATERAL unnest(conflicting_fields) AS item
                WHERE state = 'none' GROUP BY 2
                UNION ALL
                SELECT 'not_matchable_reasons', item, count(*)
                FROM cars CROSS JOIN LATERAL unnest(reason_codes) AS item
                WHERE state = 'not_matchable' GROUP BY 2
                UNION ALL
                SELECT 'changed_since_matched', '', count(*) FROM cars WHERE changed
                UNION ALL
                SELECT 'none_without_candidates', '', count(*) FROM cars
                WHERE state = 'none' AND best_candidate_ktype IS NULL
                """,
                predicate.parameters,
            ).fetchall()
            run = latest_run(connection)
        grouped: dict[str, list[tuple[str, int]]] = {}
        for kind, value, count in rows:
            grouped.setdefault(str(kind), []).append((str(value), int(count)))

        def ranked(kind: str) -> list[tuple[str, int]]:
            return sorted(grouped.get(kind, []), key=lambda item: (-item[1], item[0]))

        def single(kind: str) -> int:
            return sum(count for _, count in grouped.get(kind, []))

        return {
            "states": ranked("states"),
            "terminals": ranked("terminals"),
            "several_candidate_counts": ranked("several_candidate_counts"),
            "several_separating_fields": ranked("several_separating_fields"),
            "several_missing_fields": ranked("several_missing_fields"),
            "none_conflicting_fields": ranked("none_conflicting_fields"),
            "not_matchable_reasons": ranked("not_matchable_reasons"),
            "changed_since_matched": single("changed_since_matched"),
            "none_without_candidates": single("none_without_candidates"),
            "catalog_batches": ranked("catalog_batches"),
            "matcher_versions": ranked("matcher_versions"),
            "latest_run": run,
        }

    def counts(self, terms: Sequence[VehicleTerm], text: str) -> dict[str, Any]:
        """Cars per overview state under the filter, and how many may be out of date.

        One grouped query: what the strip above the car list needs, without the
        breakdowns the full overview also computes.
        """

        with self._connection_factory() as connection:
            predicate = compile_vehicle_filter(terms, resolve_search(connection, text))
            rows = connection.execute(
                f"SELECT {STATE_SQL}, count(*), count(*) FILTER (WHERE {_CHANGED}) "
                f"{_JOIN} WHERE {predicate.sql} GROUP BY 1",
                predicate.parameters,
            ).fetchall()
        return {
            "states": [(str(state), int(cars)) for state, cars, _ in rows],
            "changed_since_matched": sum(int(changed) for _, _, changed in rows),
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

    def reviewer_rules(self, limit: int) -> list[dict[str, Any]]:
        """The latest reviewer rules, each with the vehicles it reached and how many are stale.

        A rule's vehicles are the ones its records created. A retired rule's
        resolutions are superseded, so for it every resolution it ever wrote counts:
        retiring it changed those vehicles again.
        """

        names = (
            "rule_id", "status", "author", "applied_by", "retired_by", "created_at", "applied_at",
            "retired_at", "conditions", "target_field", "target_value", "override", "note",
            "records_written", "vehicles", "out_of_date",
        )
        with self._connection_factory() as connection:
            rows = connection.execute(
                f"""
                SELECT rule.rule_id::text, rule.status, rule.author, rule.applied_by,
                       rule.retired_by, rule.created_at, rule.applied_at, rule.retired_at,
                       rule.conditions, rule.target_field, rule.target_value, rule.override,
                       rule.note, rule.resolved_rows, reached.vehicles, reached.out_of_date
                FROM (
                    SELECT * FROM {MATCH_RESOLUTION_RULES_TABLE}
                    ORDER BY coalesce(retired_at, applied_at, created_at) DESC, rule_id
                    LIMIT %s
                ) AS rule
                CROSS JOIN LATERAL (
                    SELECT count(DISTINCT {ALIAS}.vehicle_id) AS vehicles,
                           count(DISTINCT {ALIAS}.vehicle_id) FILTER (WHERE {_CHANGED})
                               AS out_of_date
                    FROM {MATCH_FIELD_RESOLUTIONS_TABLE} AS written
                    JOIN {VEHICLES_TABLE} AS {ALIAS}
                      ON {ALIAS}.ts_record_id = written.source_record_id
                    LEFT JOIN {VEHICLE_MATCH_RESULTS_TABLE} AS m
                      ON m.vehicle_id = {ALIAS}.vehicle_id
                    WHERE written.rule_id = rule.rule_id
                      AND (rule.status = 'retired' OR written.superseded_at IS NULL)
                ) AS reached
                ORDER BY coalesce(rule.retired_at, rule.applied_at, rule.created_at) DESC,
                         rule.rule_id
                """,
                (limit,),
            ).fetchall()
        return [dict(zip(names, row, strict=True)) for row in rows]

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
