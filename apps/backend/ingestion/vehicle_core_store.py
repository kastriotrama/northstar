"""Read and write `core.vehicles` and its identifiers, links and ledger entries.

Shared by every writer. Pages of tens of thousands of vehicles are written with
COPY into a temporary table and one statement from there, not row by row: the TS
backfill alone writes 6.5M vehicles.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.ledger_migrations import LEDGER_TABLE
from ingestion.vehicle_core_fields import CORE_FIELDS, FIELD_NAMES
from ingestion.vehicle_core_merge import VehicleState
from ingestion.vehicle_core_migrations import (
    VEHICLE_IDENTIFIERS_TABLE,
    VEHICLE_SOURCE_LINKS_TABLE,
    VEHICLES_TABLE,
)
from northstar.node_ids import NodeIdGenerator, NodeIdPrefix, is_valid_node_id

# Ledger event ids are derived, not random: the same import touching the same
# vehicle always produces the same id, so a retried import cannot record twice.
LEDGER_NAMESPACE = UUID("8f0f3a52-2f1e-4c8b-9d7e-5a1b6c3d2e10")

_META_COLUMNS: tuple[str, ...] = (
    "vehicle_id",
    "origin_source",
    "origin_observed_on",
    "ts_record_id",
)
_JSON_COLUMNS: tuple[str, ...] = ("field_sources", "field_alternatives")
ALL_COLUMNS: tuple[str, ...] = _META_COLUMNS + FIELD_NAMES + _JSON_COLUMNS
_SELECT_COLUMNS = ", ".join(ALL_COLUMNS)
_ARRAY_FIELDS = frozenset(field.name for field in CORE_FIELDS if field.sql_type == "text[]")


def mint_vehicle_id(generator: NodeIdGenerator | None = None) -> str:
    return (generator or NodeIdGenerator()).mint(NodeIdPrefix.VEHICLE)


def ledger_event_id(*parts: str) -> UUID:
    return uuid5(LEDGER_NAMESPACE, "\x1f".join(parts))


def _state_from_row(row: Sequence[Any]) -> VehicleState:
    record = dict(zip(ALL_COLUMNS, row, strict=True))
    values = {name: record[name] for name in FIELD_NAMES}
    for name in _ARRAY_FIELDS:
        if values.get(name) is not None:
            values[name] = list(values[name])
    return VehicleState(
        vehicle_id=str(record["vehicle_id"]),
        origin_source=str(record["origin_source"]),
        origin_observed_on=record["origin_observed_on"],
        values=values,
        field_sources=dict(record["field_sources"] or {}),
        field_alternatives={
            key: list(entries) for key, entries in dict(record["field_alternatives"] or {}).items()
        },
        ts_record_id=record["ts_record_id"],
    )


def load_vehicles(connection: Connection, vehicle_ids: Iterable[str]) -> dict[str, VehicleState]:
    ids = sorted(set(vehicle_ids))
    if not ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {_SELECT_COLUMNS} FROM {VEHICLES_TABLE} WHERE vehicle_id = ANY(%s)",
            (ids,),
        )
        return {state.vehicle_id: state for state in map(_state_from_row, cursor.fetchall())}


def load_vehicle(connection: Connection, vehicle_id: str) -> VehicleState | None:
    return load_vehicles(connection, [vehicle_id]).get(vehicle_id)


def _row(state: VehicleState) -> tuple[Any, ...]:
    values = [state.vehicle_id, state.origin_source, state.origin_observed_on, state.ts_record_id]
    for name in FIELD_NAMES:
        value = state.values.get(name)
        if name == "registry_status" and value is None:
            value = "registered"
        values.append(value)
    values.append(Jsonb(state.field_sources))
    values.append(Jsonb(state.field_alternatives))
    return tuple(values)


def save_vehicles(connection: Connection, states: Iterable[VehicleState]) -> int:
    """Insert new vehicles and overwrite existing ones, in one statement per page.

    Callers commit. `updated_at` moves only for rows whose content changed, so it
    still says when the vehicle last changed rather than when a job last ran.
    """

    rows = [_row(state) for state in states]
    if not rows:
        return 0
    columns = ", ".join(ALL_COLUMNS)
    updates = ", ".join(
        f"{column} = EXCLUDED.{column}" for column in ALL_COLUMNS if column != "vehicle_id"
    )
    changed = " OR ".join(
        f"{VEHICLES_TABLE.split('.')[1]}.{column} IS DISTINCT FROM EXCLUDED.{column}"
        for column in ALL_COLUMNS
        if column != "vehicle_id"
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TEMP TABLE IF NOT EXISTS vehicle_core_page "
            f"(LIKE {VEHICLES_TABLE} INCLUDING DEFAULTS) ON COMMIT DELETE ROWS"
        )
        cursor.execute("TRUNCATE vehicle_core_page")
        with cursor.copy(f"COPY vehicle_core_page ({columns}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
        cursor.execute(
            f"""
            INSERT INTO {VEHICLES_TABLE} ({columns})
            SELECT {columns} FROM vehicle_core_page
            ON CONFLICT (vehicle_id) DO UPDATE SET {updates}, updated_at = now()
            WHERE {changed}
            """
        )
        return cursor.rowcount


@dataclass(frozen=True)
class IdentifierRow:
    vehicle_id: str
    kind: str
    value: str
    source: str
    source_ref: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None


class IdentifierPlan:
    """Identifier opens and closes for one page, resolved in memory first.

    Within a page a plate can be opened and closed again -- two TS records of one
    car, the older one first. Resolving that against the table would close a row
    that has not been written yet, so the plan settles it before anything is sent.
    """

    def __init__(self) -> None:
        self._pending: dict[tuple[str, str, str, bool], IdentifierRow] = {}
        self.closures: list[tuple[str, str, str, date]] = []

    def open(self, row: IdentifierRow) -> None:
        key = (row.vehicle_id, row.kind, row.value, row.valid_to is None)
        self._pending.setdefault(key, row)

    def close(self, vehicle_id: str, kind: str, value: str, on: date) -> None:
        key = (vehicle_id, kind, value, True)
        pending = self._pending.pop(key, None)
        if pending is not None:
            closed_on = max(on, pending.valid_from) if pending.valid_from else on
            self.open(
                IdentifierRow(pending.vehicle_id, pending.kind, pending.value, pending.source,
                              pending.source_ref, pending.valid_from, closed_on)
            )
            return
        self.closures.append((vehicle_id, kind, value, on))

    def rows(self) -> list[IdentifierRow]:
        return list(self._pending.values())

    def write(self, connection: Connection) -> None:
        close_identifiers(connection, self.closures)
        add_identifiers(connection, self.rows())


def add_identifiers(connection: Connection, rows: Iterable[IdentifierRow]) -> int:
    """Record identifiers, skipping ones the vehicle already holds as current."""

    batch = list(rows)
    if not batch:
        return 0
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TEMP TABLE IF NOT EXISTS vehicle_identifier_page ("
            "vehicle_id TEXT, kind TEXT, value TEXT, valid_from DATE, valid_to DATE, "
            "source TEXT, source_ref TEXT) ON COMMIT DELETE ROWS"
        )
        cursor.execute("TRUNCATE vehicle_identifier_page")
        with cursor.copy(
            "COPY vehicle_identifier_page (vehicle_id, kind, value, valid_from, valid_to, "
            "source, source_ref) FROM STDIN"
        ) as copy:
            for row in batch:
                copy.write_row(
                    (row.vehicle_id, row.kind, row.value, row.valid_from, row.valid_to,
                     row.source, row.source_ref)
                )
        cursor.execute(
            f"""
            INSERT INTO {VEHICLE_IDENTIFIERS_TABLE}
                (vehicle_id, kind, value, valid_from, valid_to, source, source_ref)
            SELECT DISTINCT ON (page.vehicle_id, page.kind, page.value, page.valid_to IS NULL)
                   page.vehicle_id, page.kind, page.value, page.valid_from, page.valid_to,
                   page.source, page.source_ref
            FROM vehicle_identifier_page AS page
            WHERE NOT EXISTS (
                SELECT 1 FROM {VEHICLE_IDENTIFIERS_TABLE} AS existing
                WHERE existing.vehicle_id = page.vehicle_id
                  AND existing.kind = page.kind
                  AND existing.value = page.value
                  AND (existing.valid_to IS NULL) = (page.valid_to IS NULL)
            )
            ORDER BY page.vehicle_id, page.kind, page.value, page.valid_to IS NULL,
                     page.valid_to DESC NULLS LAST
            """
        )
        return cursor.rowcount


def close_identifiers(
    connection: Connection,
    closures: Iterable[tuple[str, str, str, date]],
) -> list[tuple[str, str, str]]:
    """Close current identifiers (vehicle_id, kind, value) as of a date.

    Returns what was actually closed. A validity period may not end before it
    began, so a closure dated before the identifier's `valid_from` ends it on
    its first day instead.
    """

    batch = list(closures)
    if not batch:
        return []
    closed: list[tuple[str, str, str]] = []
    with connection.cursor() as cursor:
        for vehicle_id, kind, value, on in batch:
            cursor.execute(
                f"UPDATE {VEHICLE_IDENTIFIERS_TABLE} "
                "SET valid_to = GREATEST(%s::date, coalesce(valid_from, %s::date)) "
                "WHERE vehicle_id = %s AND kind = %s AND value = %s AND valid_to IS NULL "
                "RETURNING vehicle_id, kind, value",
                (on, on, vehicle_id, kind, value),
            )
            closed.extend((str(r[0]), str(r[1]), str(r[2])) for r in cursor.fetchall())
    return closed


def current_owners(connection: Connection, kind: str, values: Iterable[str]) -> dict[str, str]:
    """Which vehicle currently holds each identifier value."""

    unique = sorted(set(values))
    if not unique:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT value, vehicle_id FROM {VEHICLE_IDENTIFIERS_TABLE} "
            "WHERE kind = %s AND value = ANY(%s) AND valid_to IS NULL",
            (kind, unique),
        )
        owners: dict[str, str] = {}
        for value, vehicle_id in cursor.fetchall():
            owners.setdefault(str(value), str(vehicle_id))
        return owners


def holders(connection: Connection, kind: str, values: Iterable[str]) -> dict[str, list[str]]:
    """Every vehicle currently holding each value -- chassis numbers repeat."""

    unique = sorted(set(values))
    if not unique:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT value, vehicle_id FROM {VEHICLE_IDENTIFIERS_TABLE} "
            "WHERE kind = %s AND value = ANY(%s) AND valid_to IS NULL",
            (kind, unique),
        )
        found: dict[str, list[str]] = {}
        for value, vehicle_id in cursor.fetchall():
            found.setdefault(str(value), []).append(str(vehicle_id))
        return found


@dataclass(frozen=True)
class LinkRow:
    source_system: str
    source_record_key: str
    vehicle_id: str
    observed_on: date | None
    link_method: str


def add_links(connection: Connection, rows: Iterable[LinkRow]) -> int:
    batch = list(rows)
    if not batch:
        return 0
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TEMP TABLE IF NOT EXISTS vehicle_link_page ("
            "source_system TEXT, source_record_key TEXT, vehicle_id TEXT, observed_on DATE, "
            "link_method TEXT) ON COMMIT DELETE ROWS"
        )
        cursor.execute("TRUNCATE vehicle_link_page")
        with cursor.copy(
            "COPY vehicle_link_page (source_system, source_record_key, vehicle_id, observed_on, "
            "link_method) FROM STDIN"
        ) as copy:
            for row in batch:
                copy.write_row(
                    (row.source_system, row.source_record_key, row.vehicle_id, row.observed_on,
                     row.link_method)
                )
        cursor.execute(
            f"""
            INSERT INTO {VEHICLE_SOURCE_LINKS_TABLE}
                (source_system, source_record_key, vehicle_id, observed_on, link_method)
            SELECT DISTINCT ON (source_system, source_record_key)
                   source_system, source_record_key, vehicle_id, observed_on, link_method
            FROM vehicle_link_page
            ORDER BY source_system, source_record_key
            ON CONFLICT (source_system, source_record_key) DO UPDATE
                SET observed_on = GREATEST({VEHICLE_SOURCE_LINKS_TABLE.split('.')[1]}.observed_on,
                                           EXCLUDED.observed_on)
                WHERE {VEHICLE_SOURCE_LINKS_TABLE.split('.')[1]}.vehicle_id = EXCLUDED.vehicle_id
            """
        )
        return cursor.rowcount


def linked_vehicles(
    connection: Connection, source_system: str, keys: Iterable[str]
) -> dict[str, str]:
    unique = sorted(set(keys))
    if not unique:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT source_record_key, vehicle_id FROM {VEHICLE_SOURCE_LINKS_TABLE} "
            "WHERE source_system = %s AND source_record_key = ANY(%s)",
            (source_system, unique),
        )
        return {str(key): str(vehicle_id) for key, vehicle_id in cursor.fetchall()}


@dataclass(frozen=True)
class LedgerRow:
    event_id: UUID
    source: str
    target_node_id: str
    attributes_added: tuple[str, ...]
    confidence: float
    evidence: dict[str, Any]
    source_batch_id: str | None


class LedgerConflictError(RuntimeError):
    """An event id was already recorded with different content."""


def record_ledger_rows(connection: Connection, rows: Iterable[LedgerRow]) -> int:
    """Append many ledger entries at once, with `record_ledger_entry`'s guarantees.

    Same validation, same idempotency: replaying an event with identical content
    is a no-op, and replaying it with different content is rejected rather than
    silently kept, exactly as the single-entry writer does.
    """

    batch = list(rows)
    if not batch:
        return 0
    for row in batch:
        if not row.source.strip():
            raise ValueError("ledger source must not be empty")
        if not is_valid_node_id(row.target_node_id):
            raise ValueError(f"not a canonical node id: {row.target_node_id!r}")
        if not 0.0 <= row.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TEMP TABLE IF NOT EXISTS vehicle_ledger_page ("
            "event_id UUID, source TEXT, target_node_id TEXT, attributes_added TEXT[], "
            "confidence DOUBLE PRECISION, evidence JSONB, source_batch_id TEXT) "
            "ON COMMIT DELETE ROWS"
        )
        cursor.execute("TRUNCATE vehicle_ledger_page")
        with cursor.copy(
            "COPY vehicle_ledger_page (event_id, source, target_node_id, attributes_added, "
            "confidence, evidence, source_batch_id) FROM STDIN"
        ) as copy:
            for row in batch:
                copy.write_row(
                    (row.event_id, row.source.strip(), row.target_node_id,
                     list(row.attributes_added), row.confidence,
                     Jsonb(row.evidence), row.source_batch_id)
                )
        cursor.execute(
            f"""
            INSERT INTO {LEDGER_TABLE}
                (event_id, source, target_node_id, attributes_added, nodes_benefited,
                 cost_eur, confidence, evidence, source_batch_id)
            SELECT event_id, source, target_node_id, attributes_added, 1, %s,
                   confidence, evidence, source_batch_id
            FROM vehicle_ledger_page
            ON CONFLICT (event_id) DO NOTHING
            """,
            (Decimal(0),),
        )
        inserted = cursor.rowcount
        cursor.execute(
            f"""
            SELECT count(*) FROM vehicle_ledger_page AS page
            JOIN {LEDGER_TABLE} AS existing ON existing.event_id = page.event_id
            WHERE (existing.source, existing.target_node_id, existing.attributes_added,
                   existing.evidence, existing.source_batch_id)
                  IS DISTINCT FROM
                  (page.source, page.target_node_id, page.attributes_added,
                   page.evidence, page.source_batch_id)
            """
        )
        conflicts = int(cursor.fetchone()[0])
    if conflicts:
        raise LedgerConflictError(
            f"{conflicts} ledger event ids are already used by different events"
        )
    return inserted


def json_value(value: Any) -> Any:
    """What a value looks like inside ledger evidence and alternatives."""

    if isinstance(value, date):
        return value.isoformat()
    return json.loads(json.dumps(value, default=str))
