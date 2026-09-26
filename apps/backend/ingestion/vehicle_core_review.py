"""Reviewer resolution rules, carried onto the vehicles they describe.

The TS data screen's rules resolve TS records -- that is where a reviewer sees the
registry text and decides. What they assert is a fact about the car, so it must
reach `core.vehicles` too, in the same transaction as the resolution itself: a
rule must never be visible on one and missing on the other.

A review outranks every source. The value it displaces is kept, so retiring the
rule restores exactly what the vehicle said before.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from psycopg import Connection

from ingestion.match_chunk_migrations import MATCH_FIELD_RESOLUTIONS_TABLE
from ingestion.vehicle_core_fields import (
    FIELDS_BY_NAME,
    INTEGER_TYPES,
    SOURCE_REVIEW,
    SOURCE_TS,
    SourceRef,
    clean_int,
    clean_text,
)
from ingestion.vehicle_core_merge import Observation, merge, retract
from ingestion.vehicle_core_migrations import VEHICLE_SOURCE_LINKS_TABLE, VEHICLES_TABLE
from ingestion.vehicle_core_store import load_vehicles, save_vehicles


def _typed(field: str, value: str) -> object:
    if FIELDS_BY_NAME[field].sql_type in INTEGER_TYPES:
        return clean_int(value, zero_is_absent=False)
    return clean_text(value)


def _vehicles_for_records(connection: Connection, record_ids: Iterable[int]) -> dict[int, str]:
    ids = sorted({str(record_id) for record_id in record_ids})
    if not ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT source_record_key, vehicle_id FROM {VEHICLE_SOURCE_LINKS_TABLE} "
            "WHERE source_system = %s AND source_record_key = ANY(%s)",
            (SOURCE_TS, ids),
        )
        return {int(key): str(vehicle_id) for key, vehicle_id in cursor.fetchall()}


def _core_exists(connection: Connection) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (VEHICLES_TABLE,))
        row = cursor.fetchone()
    # A database that has not run the vehicle-core migration yet simply has no
    # vehicles to carry the rule onto.
    return bool(row and row[0])


def sync_applied_review(
    connection: Connection,
    *,
    rule_id: UUID,
    target_field: str,
    target_value: str,
    after_id: int,
    through_id: int,
) -> int:
    """Carry one applied batch of a rule onto its vehicles. Does not commit.

    The batch is read back from the resolution ledger -- the rows this rule holds
    live between the batch's cursors -- so the vehicles get exactly what was
    written, whether the rule filled gaps or overrode values.
    """

    if target_field not in FIELDS_BY_NAME or not _core_exists(connection):
        return 0
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT source_record_id FROM {MATCH_FIELD_RESOLUTIONS_TABLE} "
            "WHERE rule_id = %s AND superseded_at IS NULL "
            "AND source_record_id > %s AND source_record_id <= %s",
            (rule_id, after_id, through_id),
        )
        record_ids = [int(row[0]) for row in cursor.fetchall()]
    vehicles = _vehicles_for_records(connection, record_ids)
    states = load_vehicles(connection, vehicles.values())
    observation = Observation(_typed(target_field, target_value), SourceRef(SOURCE_REVIEW, str(rule_id)))
    changed = [
        state for state in states.values() if merge(state, {target_field: observation}).touched
    ]
    save_vehicles(connection, changed)
    return len(changed)


def sync_retired_review(connection: Connection, *, rule_id: UUID, target_field: str) -> int:
    """Take a retired rule's value back off every vehicle, wherever it sits. Does not commit."""

    if target_field not in FIELDS_BY_NAME or not _core_exists(connection):
        return 0
    marker = SourceRef(SOURCE_REVIEW, str(rule_id)).encode()
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT vehicle_id FROM {VEHICLES_TABLE} "
            "WHERE field_sources ->> %s = %s "
            "OR field_alternatives -> %s @> jsonb_build_array(jsonb_build_object('source', %s::text))",
            (target_field, marker, target_field, marker),
        )
        vehicle_ids = [str(row[0]) for row in cursor.fetchall()]
    states = load_vehicles(connection, vehicle_ids)
    for state in states.values():
        retract(state, target_field, SOURCE_REVIEW, str(rule_id))
    save_vehicles(connection, states.values())
    return len(states)
