"""Read and append a person's corrections of a car's data, and keep the vehicle's copy.

`core.vehicle_fact_corrections` is the truth: an append-only chain per field of
a vehicle whose head -- the row with the highest `chain_position` -- says what
is in force. A `set` head is the value to use, an `ignore` head means the car's
own value is not used, and a `withdraw` head (or no chain) means no correction.

The matcher is handed the heads straight from this table (`correction_heads`),
so a correction counts at once. `core.vehicles` carries a copy of every `set` for
the Vehicles list, the search and the record view. `project_correction` writes
that copy in the same transaction, through the merge code every other writer
uses and under its own source (`correction`, above a reviewer's rule); nothing
reads the copy to match a car.

Which fields can be corrected, what a value looks like and which vehicle columns
carry its copy is defined in one place, with the API
(`api.app.features.vehicle_corrections.fields`). This module stores and links
rows whatever the field; the functions that write the copy are handed that
definition's `vehicle_copy` as `copy_of`.

A decision that corrects many cars (`vehicle_correction_decisions`) writes one
ordinary row per car through `append_many` and `project_many`: the same rows and
the same copy as one car's, in one round trip each.

Sync, PostgreSQL only. Callers own the transaction: nothing here commits.
Nothing here writes Neo4j, aliases, canonical ids, the enrichment ledger or the
match decision tables.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, NamedTuple
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.vehicle_core_fields import (
    FIELDS_BY_NAME,
    SOURCE_CORRECTION,
    SourceRef,
    parse_source_ref,
)
from ingestion.vehicle_core_merge import Observation, VehicleState, merge, retract
from ingestion.vehicle_core_migrations import VEHICLES_TABLE
from ingestion.vehicle_core_store import load_vehicles, save_vehicles
from ingestion.vehicle_fact_correction_migrations import COLUMNS, VEHICLE_FACT_CORRECTIONS_TABLE

CorrectionAction = Literal["set", "ignore", "withdraw"]

#: What a `set` puts on the vehicle: each column that carries the copy and its
#: value. None says the correction leaves that column empty (a single fuel has
#: no second one).
VehicleCopy = Mapping[str, Any]
#: `(field, stored value) -> VehicleCopy`: the field definition's own answer.
CopyOf = Callable[[str, str], VehicleCopy]

_SELECT = ", ".join(f"c.{column}" for column in COLUMNS)


class OperationReusedError(Exception):
    """The operation id already recorded a different correction."""


class CorrectionChangedError(Exception):
    """The field's current correction is not the one the caller saw."""


class NothingToWithdrawError(Exception):
    """The field has no correction in force to withdraw."""


@dataclass(frozen=True)
class NewCorrection:
    """One correction to append. `correction_id` is the client's operation UUID."""

    correction_id: UUID
    vehicle_id: str
    field: str
    action: CorrectionAction
    value: str | None
    supersedes_correction_id: UUID | None
    group_id: UUID | None
    reviewer: str
    reason: str | None
    previous_value: str | None
    previous_source: str | None
    catalog_batch: str
    automatic_terminal: str
    automatic_ktype: str | None
    code_version: str
    evidence_fingerprint: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class StoredCorrection:
    correction_id: UUID
    vehicle_id: str
    field: str
    #: 0 for the field's first row on this vehicle; each later row is one higher.
    chain_position: int
    action: CorrectionAction
    value: str | None
    supersedes_correction_id: UUID | None
    group_id: UUID | None
    reviewer: str
    reason: str | None
    previous_value: str | None
    previous_source: str | None
    catalog_batch: str
    automatic_terminal: str
    automatic_ktype: str | None
    code_version: str
    evidence_fingerprint: str
    evidence: dict[str, Any]
    created_at: datetime

    def same_request(
        self,
        *,
        vehicle_id: str,
        field: str,
        action: str,
        value: str | None,
        reviewer: str,
        reason: str | None,
        supersedes_correction_id: UUID | None,
    ) -> bool:
        """True when a request with this content is a replay of this row.

        The evidence and the previous value are the server's own and are left
        out: a replay after the car's data changed must still be recognized as
        the same operation.
        """

        return (
            self.vehicle_id == vehicle_id
            and self.field == field
            and self.action == action
            and self.value == value
            and self.reviewer == reviewer
            and self.reason == reason
            and self.supersedes_correction_id == supersedes_correction_id
        )


class CorrectionHead(NamedTuple):
    """The head of one field's chain, as the matcher needs it."""

    action: CorrectionAction
    value: str | None
    correction_id: UUID


def _stored(row: Sequence[Any]) -> StoredCorrection:
    record = dict(zip(COLUMNS, row, strict=True))
    return StoredCorrection(
        correction_id=record["correction_id"],
        vehicle_id=str(record["vehicle_id"]),
        field=str(record["field"]),
        chain_position=int(record["chain_position"]),
        action=record["action"],
        value=record["value"],
        supersedes_correction_id=record["supersedes_correction_id"],
        group_id=record["group_id"],
        reviewer=str(record["reviewer"]),
        reason=record["reason"],
        previous_value=record["previous_value"],
        previous_source=record["previous_source"],
        catalog_batch=str(record["catalog_batch"]),
        automatic_terminal=str(record["automatic_terminal"]),
        automatic_ktype=record["automatic_ktype"],
        code_version=str(record["code_version"]),
        evidence_fingerprint=str(record["evidence_fingerprint"]),
        evidence=dict(record["evidence"]),
        created_at=record["created_at"],
    )


def fetch_correction(connection: Connection[Any], correction_id: UUID) -> StoredCorrection | None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {_SELECT} FROM {VEHICLE_FACT_CORRECTIONS_TABLE} AS c "
            "WHERE c.correction_id = %s",
            (correction_id,),
        )
        row = cursor.fetchone()
    return _stored(row) if row else None


def current_corrections(
    connection: Connection[Any], vehicle_id: str
) -> dict[str, tuple[StoredCorrection, int]]:
    """Each corrected field's head row and how many rows its chain holds, by field.

    A withdrawn head is included: the next correction of that field supersedes
    it. One read of the `(vehicle_id, field, chain_position)` key, backwards.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT DISTINCT ON (c.field) {_SELECT} "
            f"FROM {VEHICLE_FACT_CORRECTIONS_TABLE} AS c WHERE c.vehicle_id = %s "
            "ORDER BY c.field DESC, c.chain_position DESC",
            (vehicle_id,),
        )
        heads = [_stored(row) for row in cursor.fetchall()]
    return {
        head.field: (head, head.chain_position + 1)
        for head in sorted(heads, key=lambda head: head.field)
    }


def chains(connection: Connection[Any], vehicle_id: str) -> dict[str, list[StoredCorrection]]:
    """The car's rows by field, each field's from its head backwards."""

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {_SELECT} FROM {VEHICLE_FACT_CORRECTIONS_TABLE} AS c "
            "WHERE c.vehicle_id = %s ORDER BY c.field, c.chain_position DESC",
            (vehicle_id,),
        )
        rows = [_stored(row) for row in cursor.fetchall()]
    ordered: dict[str, list[StoredCorrection]] = {}
    for row in rows:
        ordered.setdefault(row.field, []).append(row)
    return ordered


def _head(connection: Connection[Any], vehicle_id: str, field: str) -> StoredCorrection | None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {_SELECT} FROM {VEHICLE_FACT_CORRECTIONS_TABLE} AS c "
            "WHERE c.vehicle_id = %s AND c.field = %s ORDER BY c.chain_position DESC LIMIT 1",
            (vehicle_id, field),
        )
        row = cursor.fetchone()
    return _stored(row) if row else None


def _replay(existing: StoredCorrection, new: NewCorrection) -> StoredCorrection:
    if not existing.same_request(
        vehicle_id=new.vehicle_id,
        field=new.field,
        action=new.action,
        value=new.value,
        reviewer=new.reviewer,
        reason=new.reason,
        supersedes_correction_id=new.supersedes_correction_id,
    ):
        raise OperationReusedError(
            "This operation id already recorded a different correction."
        )
    return existing


def append_correction(
    connection: Connection[Any], new: NewCorrection, expected_head: UUID | None
) -> tuple[StoredCorrection, bool, StoredCorrection | None]:
    """Append one row; returns `(row, created, superseded)`.

    Idempotent by `correction_id`: the same operation and content returns the
    row already stored with `created=False`; different content for that id
    raises `OperationReusedError`. Otherwise the field's head must be
    `expected_head` (`CorrectionChangedError`), and a withdrawal needs a
    correction in force (`NothingToWithdrawError`). The row takes the position
    after the head; `superseded` is that head, which the vehicle's copy is
    projected from. The database enforces the same chain rules; a caller racing
    past these reads gets a unique violation on the position instead.
    """

    existing = fetch_correction(connection, new.correction_id)
    if existing is not None:
        return _replay(existing, new), False, None

    head = _head(connection, new.vehicle_id, new.field)
    if (head.correction_id if head else None) != expected_head:
        raise CorrectionChangedError(
            "The field's current correction is not the one that was shown."
        )
    if new.supersedes_correction_id != expected_head:
        raise CorrectionChangedError("A correction must supersede the field's current correction.")
    if new.action == "withdraw" and (head is None or head.action == "withdraw"):
        raise NothingToWithdrawError("There is no correction to withdraw.")

    columns = [column for column in COLUMNS if column != "created_at"]
    given: dict[str, Any] = {
        "chain_position": head.chain_position + 1 if head else 0,
        "evidence": Jsonb(new.evidence),
    }
    values: list[Any] = [
        given[column] if column in given else getattr(new, column) for column in columns
    ]
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {VEHICLE_FACT_CORRECTIONS_TABLE} AS c ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))}) "
            f"ON CONFLICT (correction_id) DO NOTHING RETURNING {_SELECT}",
            values,
        )
        inserted = cursor.fetchone()
    if inserted is None:
        # Another transaction committed this operation id between the read and the insert.
        raced = fetch_correction(connection, new.correction_id)
        if raced is None:  # pragma: no cover - the conflicting row cannot vanish (append-only)
            raise OperationReusedError("This operation id is already in use.")
        return _replay(raced, new), False, None
    return _stored(inserted), True, head


def field_heads(
    connection: Connection[Any], vehicle_ids: Sequence[str], field: str
) -> dict[str, StoredCorrection]:
    """The head row of one field's chain, for each of these vehicles that has one.

    A withdrawn head is included: the next correction of the field supersedes
    it. One query, answered from the `(vehicle_id, field, chain_position)` key.
    """

    if not vehicle_ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT DISTINCT ON (c.vehicle_id) {_SELECT} "
            f"FROM {VEHICLE_FACT_CORRECTIONS_TABLE} AS c "
            "WHERE c.vehicle_id = ANY(%s) AND c.field = %s "
            "ORDER BY c.vehicle_id DESC, c.chain_position DESC",
            (list(vehicle_ids), field),
        )
        heads = [_stored(row) for row in cursor.fetchall()]
    return {head.vehicle_id: head for head in heads}


def append_many(
    connection: Connection[Any], rows: Sequence[tuple[NewCorrection, StoredCorrection | None]]
) -> int:
    """Append one row per pair, each on top of the head given with it; returns the rows written.

    For a decision that writes many cars: one round trip instead of three reads
    and a write per car. The caller holds every vehicle's row lock and read the
    heads under it. Each row takes the position after its head and supersedes
    it, whatever `supersedes_correction_id` it was built with. Nothing is
    skipped here: an id already stored, or a head that is no longer the field's,
    violates a key and fails the whole statement, so the caller's transaction
    writes all of its rows or none.
    """

    if not rows:
        return 0
    columns = [column for column in COLUMNS if column != "created_at"]
    values: list[list[Any]] = []
    for new, head in rows:
        given: dict[str, Any] = {
            "chain_position": head.chain_position + 1 if head else 0,
            "supersedes_correction_id": head.correction_id if head else None,
            "evidence": Jsonb(new.evidence),
        }
        values.append(
            [given[column] if column in given else getattr(new, column) for column in columns]
        )
    with connection.cursor() as cursor:
        cursor.executemany(
            f"INSERT INTO {VEHICLE_FACT_CORRECTIONS_TABLE} ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))})",
            values,
        )
    return len(values)


def group_rows(connection: Connection[Any], group_id: UUID) -> list[StoredCorrection]:
    """The rows one decision event wrote, by vehicle; read off the group index."""

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {_SELECT} FROM {VEHICLE_FACT_CORRECTIONS_TABLE} AS c "
            "WHERE c.group_id = %s ORDER BY c.vehicle_id, c.field",
            (group_id,),
        )
        return [_stored(row) for row in cursor.fetchall()]


def group_sizes(connection: Connection[Any], group_ids: Sequence[UUID]) -> dict[UUID, int]:
    """How many rows each of these decision events wrote; an event that wrote none is absent."""

    if not group_ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT c.group_id, count(*) FROM {VEHICLE_FACT_CORRECTIONS_TABLE} AS c "
            "WHERE c.group_id = ANY(%s) GROUP BY c.group_id",
            (list(group_ids),),
        )
        return {row[0]: int(row[1]) for row in cursor.fetchall()}


def correction_heads(
    connection: Connection[Any], vehicle_ids: Sequence[str] | None
) -> dict[str, dict[str, CorrectionHead]]:
    """The head of every chain these vehicles have, by vehicle and field.

    A withdrawn head is included, so a caller can tell a car nobody ever
    corrected from one whose corrections were taken back. One query, answered
    from the `(vehicle_id, field, chain_position)` key read backwards. `None`
    reads every vehicle (the repair helper's scope).
    """

    if vehicle_ids is not None and not vehicle_ids:
        return {}
    scope = "" if vehicle_ids is None else "WHERE c.vehicle_id = ANY(%s) "
    parameters: tuple[Any, ...] = () if vehicle_ids is None else (list(vehicle_ids),)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT ON (c.vehicle_id, c.field) "
            "c.vehicle_id, c.field, c.action, c.value, c.correction_id "
            f"FROM {VEHICLE_FACT_CORRECTIONS_TABLE} AS c {scope}"
            "ORDER BY c.vehicle_id DESC, c.field DESC, c.chain_position DESC",
            parameters,
        )
        rows = cursor.fetchall()
    heads: dict[str, dict[str, CorrectionHead]] = {}
    for vehicle_id, field, action, value, correction_id in rows:
        heads.setdefault(str(vehicle_id), {})[str(field)] = CorrectionHead(
            action, value, correction_id
        )
    return heads


def standing_corrections(
    connection: Connection[Any], vehicle_ids: Sequence[str] | None
) -> dict[str, dict[str, CorrectionHead]]:
    """The corrections in force on these vehicles, by vehicle and field.

    Heads that are a `set` or an `ignore` only: a withdrawn field has nothing in
    force, and a vehicle with nothing in force is not listed.
    """

    standing: dict[str, dict[str, CorrectionHead]] = {}
    for vehicle_id, fields in correction_heads(connection, vehicle_ids).items():
        in_force = {field: head for field, head in fields.items() if head.action != "withdraw"}
        if in_force:
            standing[vehicle_id] = in_force
    return standing


def _lock_vehicles(
    connection: Connection[Any], scope: str, parameters: tuple[Any, ...]
) -> list[str]:
    """Lock these vehicle rows the way a correction locks its vehicle, in id order."""

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT vehicle_id FROM {VEHICLES_TABLE} WHERE {scope} "
            "ORDER BY vehicle_id FOR NO KEY UPDATE",
            parameters,
        )
        return [str(row[0]) for row in cursor.fetchall()]


def lock_vehicles(connection: Connection[Any], vehicle_ids: Sequence[str]) -> list[str]:
    """Lock these vehicles' rows in id order; returns the ids that exist.

    The lock one car's correction and a KType choice take. Every writer of
    several cars takes them in the same order, so two of them cannot deadlock.
    """

    if not vehicle_ids:
        return []
    return _lock_vehicles(connection, "vehicle_id = ANY(%s)", (list(vehicle_ids),))


def _merge_copy(state: VehicleState, copy: VehicleCopy, correction_id: UUID) -> bool:
    """Merge a `set`'s copy as `correction:<id>`; a column it leaves empty is cleared."""

    ref = SourceRef(SOURCE_CORRECTION, str(correction_id))
    observations = {
        column: Observation(value, ref, clears=value is None)
        for column, value in copy.items()
        if column in FIELDS_BY_NAME
    }
    return merge(state, observations).touched


def _retract_copy(state: VehicleState, copy: VehicleCopy, correction_id: UUID) -> bool:
    """Take a `set`'s copy back off the vehicle: each column falls back to what it displaced."""

    touched = False
    for column, value in copy.items():
        if column not in FIELDS_BY_NAME:
            continue
        if retract(state, column, SOURCE_CORRECTION, str(correction_id)).touched:
            touched = True
        if value is None and state.values.get(column) is None:
            # The correction had emptied the column, which leaves no source on
            # it to retract. What it displaced waits among the alternatives:
            # let those sources speak again, in the merge's own precedence.
            for entry in list(state.field_alternatives.get(column, [])):
                again = Observation(entry["value"], parse_source_ref(str(entry["source"])))
                if merge(state, {column: again}).touched:
                    touched = True
    return touched


def project_correction(
    connection: Connection[Any],
    vehicle_id: str,
    head: StoredCorrection,
    superseded: StoredCorrection | None,
    copy_of: CopyOf,
) -> bool:
    """Make `core.vehicles` carry what the new head says; True when the row changed.

    A `set` is merged onto the columns `copy_of` names as a `correction`, the
    source that outranks every other. What it displaces -- a reviewer rule's
    value, else a provider's -- is kept behind it, and so is a rule or an import
    that arrives while the correction stands. An `ignore` or a `withdraw` takes
    a superseded `set` back off the vehicle, which brings the best of those
    back, and otherwise leaves the row alone: an ignored value stays visible on
    the vehicle, the matcher just is not handed it.

    The vehicle row is locked before it is read (a no-op when the caller holds
    the lock already), so the state written back is never an older one.
    """

    if not _lock_vehicles(connection, "vehicle_id = %s", (vehicle_id,)):
        return False
    state = load_vehicles(connection, [vehicle_id])[vehicle_id]
    touched = _project(state, head, superseded, copy_of)
    if touched:
        save_vehicles(connection, [state])
    return touched


def _project(
    state: VehicleState,
    head: NewCorrection | StoredCorrection,
    superseded: StoredCorrection | None,
    copy_of: CopyOf,
) -> bool:
    """Bring one vehicle's state in line with a new head; True when it changed."""

    if head.action == "set":
        return _merge_copy(state, copy_of(head.field, head.value or ""), head.correction_id)
    if superseded is not None and superseded.action == "set":
        return _retract_copy(
            state, copy_of(superseded.field, superseded.value or ""), superseded.correction_id
        )
    return False


def project_many(
    connection: Connection[Any],
    changes: Sequence[tuple[NewCorrection, StoredCorrection | None]],
    copy_of: CopyOf,
) -> int:
    """`project_correction` for many vehicles: one read and one write; returns the rows changed.

    Each pair is a row just appended and the head it superseded, as handed to
    `append_many`. The caller holds every vehicle's row lock, so the states
    written back are never older than the ones read.
    """

    states = load_vehicles(connection, [new.vehicle_id for new, _ in changes])
    changed: dict[str, VehicleState] = {}
    for new, superseded in changes:
        state = states.get(new.vehicle_id)
        if state is not None and _project(state, new, superseded, copy_of):
            changed[state.vehicle_id] = state
    save_vehicles(connection, changed.values())
    return len(changed)


def reproject_corrections(
    connection: Connection[Any], vehicle_ids: Sequence[str] | None, copy_of: CopyOf
) -> int:
    """Put every standing `set` back on its vehicle; returns the vehicles changed.

    The repair for a copy another writer overwrote (a job that held an older
    state and wrote it back). The vehicle rows are locked first, the way a
    correction locks its vehicle, and only then read: the repair and a person
    cannot interleave, and it never writes back a state older than the one it
    locked. `None` repairs every corrected vehicle. Does not commit.
    """

    if vehicle_ids is not None and not vehicle_ids:
        return 0
    if vehicle_ids is None:
        locked = _lock_vehicles(
            connection,
            f"vehicle_id IN (SELECT vehicle_id FROM {VEHICLE_FACT_CORRECTIONS_TABLE})",
            (),
        )
    else:
        locked = _lock_vehicles(connection, "vehicle_id = ANY(%s)", (list(vehicle_ids),))
    standing = standing_corrections(connection, locked)
    states = load_vehicles(connection, standing)
    changed: list[VehicleState] = []
    for vehicle_id, fields in standing.items():
        state = states[vehicle_id]
        touched = False
        for field, head in sorted(fields.items()):
            if head.action == "set" and _merge_copy(
                state, copy_of(field, head.value or ""), head.correction_id
            ):
                touched = True
        if touched:
            changed.append(state)
    save_vehicles(connection, changed)
    return len(changed)
