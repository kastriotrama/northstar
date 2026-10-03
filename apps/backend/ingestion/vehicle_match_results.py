"""Write and refresh the stored outcome of matching per car (`core.vehicle_match_results`).

A row is a cache of what the matcher concluded for a car from what it was
handed (`input_hash`), on one catalog batch and matcher version. It is
overwritten when the car is matched again; nothing here is a decision and
nothing here is append-only.

A car needs matching again when it has no row, when its row is from another
catalog batch, or when something the matcher reads may have changed since the
row was written: the vehicle itself, its origin record's normalization, or a
person's correction. That is a cheap, generous test on timestamps; the caller
settles it with the car's `input_hash` and only evaluates a car whose hash
differs.

Sync, PostgreSQL only. Callers own the transaction: nothing here commits.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import astuple, dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection

from ingestion.normalization_migrations import NORMALIZATION_RESULTS_TABLE
from ingestion.vehicle_core_migrations import VEHICLES_TABLE
from ingestion.vehicle_fact_correction_migrations import VEHICLE_FACT_CORRECTIONS_TABLE
from ingestion.vehicle_facts import STAGING_TABLE
from ingestion.vehicle_match_result_migrations import (
    RESULT_COLUMNS,
    VEHICLE_MATCH_RESULTS_TABLE,
    VEHICLE_MATCH_RUNS_TABLE,
)

STATE_RESOLVED = "resolved"
STATE_SEVERAL = "several"
STATE_ONE_UNCONFIRMED = "one_unconfirmed"
STATE_NONE = "none"
STATE_NOT_MATCHABLE = "not_matchable"


@dataclass(frozen=True)
class MatchResult:
    """What is stored for one car; the fields are the table's columns, in order."""

    vehicle_id: str
    state: str
    terminal: str
    ktype: str | None
    best_candidate_ktype: str | None
    confidence: float | None
    candidate_count: int
    candidate_ktypes: list[str]
    candidate_confidences: list[float]
    separating_fields: list[str]
    missing_fields: list[str]
    conflicting_fields: list[str]
    reason_codes: list[str]
    catalog_batch: str
    matcher_version: str
    input_hash: str
    run_id: UUID
    evaluated_at: datetime


@dataclass(frozen=True)
class RunPins:
    """What a run evaluates with; stored on the run and on every row it writes."""

    catalog_batch: str
    matcher_version: str
    rule_set_version: str | None = None


@dataclass(frozen=True)
class StoredRun:
    run_id: UUID
    mode: str
    catalog_batch: str
    matcher_version: str
    rule_set_version: str | None
    status: str
    target: int
    evaluated: int
    unchanged: int
    error: str | None
    started_at: datetime
    finished_at: datetime | None


_RUN_COLUMNS = (
    "run_id, mode, catalog_batch, matcher_version, rule_set_version, status, target, "
    "evaluated, unchanged, error, started_at, finished_at"
)


def database_now(connection: Connection[Any]) -> datetime:
    """The database's clock, read before a car is: a row's `evaluated_at`.

    Taken before the read so that a change committed while the car is being
    matched is newer than the row and the car is picked up again.
    """

    row = connection.execute("SELECT clock_timestamp()").fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


def start_run(
    connection: Connection[Any], run_id: UUID, mode: str, pins: RunPins, *, target: int
) -> None:
    connection.execute(
        f"INSERT INTO {VEHICLE_MATCH_RUNS_TABLE} "
        "(run_id, mode, catalog_batch, matcher_version, rule_set_version, target) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (run_id, mode, pins.catalog_batch, pins.matcher_version, pins.rule_set_version, target),
    )


def add_progress(
    connection: Connection[Any], run_id: UUID, *, evaluated: int, unchanged: int
) -> None:
    connection.execute(
        f"UPDATE {VEHICLE_MATCH_RUNS_TABLE} "
        "SET evaluated = evaluated + %s, unchanged = unchanged + %s WHERE run_id = %s",
        (evaluated, unchanged, run_id),
    )


def finish_run(
    connection: Connection[Any], run_id: UUID, status: str, *, error: str | None = None
) -> None:
    connection.execute(
        f"UPDATE {VEHICLE_MATCH_RUNS_TABLE} "
        "SET status = %s, error = %s, finished_at = clock_timestamp() WHERE run_id = %s",
        (status, error, run_id),
    )


def latest_run(connection: Connection[Any]) -> StoredRun | None:
    row = connection.execute(
        f"SELECT {_RUN_COLUMNS} FROM {VEHICLE_MATCH_RUNS_TABLE} "
        "ORDER BY started_at DESC, run_id DESC LIMIT 1"
    ).fetchone()
    return StoredRun(*row) if row else None


def stored_hashes(connection: Connection[Any], vehicle_ids: Sequence[str]) -> dict[str, tuple[str, str]]:
    """For the cars that have a row: its `input_hash` and catalog batch."""

    if not vehicle_ids:
        return {}
    rows = connection.execute(
        f"SELECT vehicle_id, input_hash, catalog_batch FROM {VEHICLE_MATCH_RESULTS_TABLE} "
        "WHERE vehicle_id = ANY(%s)",
        (list(vehicle_ids),),
    ).fetchall()
    return {str(row[0]): (str(row[1]), str(row[2])) for row in rows}


def mark_checked(
    connection: Connection[Any], vehicle_ids: Sequence[str], checked_at: datetime
) -> int:
    """These cars were read again and the matcher would be handed the same thing.

    Their rows stand; only `evaluated_at` moves, so the timestamp test stops
    selecting them.
    """

    if not vehicle_ids:
        return 0
    cursor = connection.execute(
        f"UPDATE {VEHICLE_MATCH_RESULTS_TABLE} SET evaluated_at = %s "
        "WHERE vehicle_id = ANY(%s) AND evaluated_at < %s",
        (checked_at, list(vehicle_ids), checked_at),
    )
    return cursor.rowcount


def upsert_results(connection: Connection[Any], results: Sequence[MatchResult]) -> int:
    """Store these rows, replacing each car's earlier row."""

    if not results:
        return 0
    columns = ", ".join(RESULT_COLUMNS)
    placeholders = ", ".join(["%s"] * len(RESULT_COLUMNS))
    updates = ", ".join(
        f"{column} = EXCLUDED.{column}" for column in RESULT_COLUMNS if column != "vehicle_id"
    )
    with connection.cursor() as cursor:
        cursor.executemany(
            f"INSERT INTO {VEHICLE_MATCH_RESULTS_TABLE} ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT (vehicle_id) DO UPDATE SET {updates}",
            [astuple(result) for result in results],
        )
    return len(results)


def _scope(scope: str | None, registered_only: bool) -> tuple[str, list[Any]]:
    fragments: list[str] = []
    parameters: list[Any] = []
    if scope is not None:
        fragments.append("v.vehicle_scope = %s")
        parameters.append(scope)
    if registered_only:
        fragments.append("v.registry_status = 'registered'")
    return "".join(f" AND {fragment}" for fragment in fragments), parameters


#: A car whose row may no longer describe it. Generous on purpose: a car picked
#: here and found unchanged costs one read, a changed car missed stays wrong.
STALE_PREDICATE = f"""(
    m.vehicle_id IS NULL
    OR m.catalog_batch <> %s
    OR v.updated_at > m.evaluated_at
    OR EXISTS (SELECT 1 FROM {NORMALIZATION_RESULTS_TABLE} AS n
               WHERE n.source_table = '{STAGING_TABLE}'
                 AND n.source_record_id = v.ts_record_id AND n.updated_at > m.evaluated_at)
    OR EXISTS (SELECT 1 FROM {VEHICLE_FACT_CORRECTIONS_TABLE} AS c
               WHERE c.vehicle_id = v.vehicle_id AND c.created_at > m.evaluated_at)
)"""


def vehicle_ids_to_match(
    connection: Connection[Any],
    *,
    catalog_batch: str,
    only_stale: bool,
    after: str | None,
    limit: int,
    scope: str | None = "passenger",
    registered_only: bool = True,
) -> list[str]:
    """The next page of cars to match, in NOR ID order after `after`.

    `only_stale` leaves out cars whose row is current; without it every car in
    the scope is returned (a rebuild).
    """

    scope_sql, parameters = _scope(scope, registered_only)
    stale_sql = ""
    if only_stale:
        stale_sql = f" AND {STALE_PREDICATE}"
        parameters.append(catalog_batch)
    cursor_sql = ""
    if after:
        cursor_sql = " AND v.vehicle_id > %s"
        parameters.append(after)
    parameters.append(limit)
    rows = connection.execute(
        f"SELECT v.vehicle_id FROM {VEHICLES_TABLE} AS v "
        f"LEFT JOIN {VEHICLE_MATCH_RESULTS_TABLE} AS m ON m.vehicle_id = v.vehicle_id "
        f"WHERE true{scope_sql}{stale_sql}{cursor_sql} ORDER BY v.vehicle_id LIMIT %s",
        parameters,
    ).fetchall()
    return [str(row[0]) for row in rows]


def count_to_match(
    connection: Connection[Any],
    *,
    catalog_batch: str,
    only_stale: bool,
    scope: str | None = "passenger",
    registered_only: bool = True,
) -> int:
    scope_sql, parameters = _scope(scope, registered_only)
    stale_sql = ""
    if only_stale:
        stale_sql = f" AND {STALE_PREDICATE}"
        parameters.append(catalog_batch)
    row = connection.execute(
        f"SELECT count(*) FROM {VEHICLES_TABLE} AS v "
        f"LEFT JOIN {VEHICLE_MATCH_RESULTS_TABLE} AS m ON m.vehicle_id = v.vehicle_id "
        f"WHERE true{scope_sql}{stale_sql}",
        parameters,
    ).fetchone()
    return int(row[0]) if row else 0
