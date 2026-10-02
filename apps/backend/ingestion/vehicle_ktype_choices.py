"""Read and append a person's KType choice per car, and keep the vehicle's copy.

`core.vehicle_ktype_choices` is the truth: an append-only chain per vehicle
whose head -- the row nothing supersedes -- is the car's current choice. A
`withdraw` head means "no choice". `core.vehicles.ktype` / `match_state` and
their two `field_sources` keys are a derived copy of that head, written by
`project_choices` in the same transaction, so the Vehicles list can filter on
them; nothing reads the copy to answer a lookup.

Sync, PostgreSQL only. Callers own the transaction: nothing here commits.
Nothing here writes Neo4j, aliases, canonical ids, the enrichment ledger or
the match decision tables.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.vehicle_core_fields import SOURCE_REVIEW
from ingestion.vehicle_core_migrations import VEHICLES_TABLE
from ingestion.vehicle_ktype_choice_migrations import COLUMNS, VEHICLE_KTYPE_CHOICES_TABLE

ChoiceAction = Literal["choose", "none", "withdraw"]

#: `core.vehicles.match_state` for a car whose head is a chosen KType / "none of these".
MATCH_STATE_MANUAL = "manual"
MATCH_STATE_MANUAL_NONE = "manual_none"

_SELECT = ", ".join(f"c.{column}" for column in COLUMNS)


class OperationReusedError(Exception):
    """The operation id already recorded a different choice."""


class ChoiceChangedError(Exception):
    """The car's current choice is not the one the caller saw."""


class NothingToWithdrawError(Exception):
    """The car has no choice in force to withdraw."""


@dataclass(frozen=True)
class NewChoice:
    """One choice to append. `choice_id` is the client's operation UUID."""

    choice_id: UUID
    vehicle_id: str
    action: ChoiceAction
    ktype: str | None
    supersedes_choice_id: UUID | None
    reviewer: str
    reason: str | None
    catalog_batch: str
    automatic_terminal: str
    automatic_ktype: str | None
    code_version: str
    evidence_fingerprint: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class StoredChoice:
    choice_id: UUID
    vehicle_id: str
    action: ChoiceAction
    ktype: str | None
    supersedes_choice_id: UUID | None
    reviewer: str
    reason: str | None
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
        action: str,
        ktype: str | None,
        reviewer: str,
        reason: str | None,
        supersedes_choice_id: UUID | None,
    ) -> bool:
        """True when a request with this content is a replay of this row.

        The evidence is the server's own and is left out: a replay after the
        car's data changed must still be recognized as the same operation.
        """

        return (
            self.vehicle_id == vehicle_id
            and self.action == action
            and self.ktype == ktype
            and self.reviewer == reviewer
            and self.reason == reason
            and self.supersedes_choice_id == supersedes_choice_id
        )


def _stored(row: Sequence[Any]) -> StoredChoice:
    record = dict(zip(COLUMNS, row, strict=True))
    return StoredChoice(
        choice_id=record["choice_id"],
        vehicle_id=str(record["vehicle_id"]),
        action=record["action"],
        ktype=record["ktype"],
        supersedes_choice_id=record["supersedes_choice_id"],
        reviewer=str(record["reviewer"]),
        reason=record["reason"],
        catalog_batch=str(record["catalog_batch"]),
        automatic_terminal=str(record["automatic_terminal"]),
        automatic_ktype=record["automatic_ktype"],
        code_version=str(record["code_version"]),
        evidence_fingerprint=str(record["evidence_fingerprint"]),
        evidence=dict(record["evidence"]),
        created_at=record["created_at"],
    )


def fetch_choice(connection: Connection[Any], choice_id: UUID) -> StoredChoice | None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {_SELECT} FROM {VEHICLE_KTYPE_CHOICES_TABLE} AS c WHERE c.choice_id = %s",
            (choice_id,),
        )
        row = cursor.fetchone()
    return _stored(row) if row else None


def current_choice(connection: Connection[Any], vehicle_id: str) -> tuple[StoredChoice, int] | None:
    """The car's head row and how many rows its chain holds; None without a chain."""

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {_SELECT}, "
            f"(SELECT count(*) FROM {VEHICLE_KTYPE_CHOICES_TABLE} AS h "
            "WHERE h.vehicle_id = c.vehicle_id) AS history_count "
            f"FROM {VEHICLE_KTYPE_CHOICES_TABLE} AS c "
            "WHERE c.vehicle_id = %s AND NOT EXISTS ("
            f"SELECT 1 FROM {VEHICLE_KTYPE_CHOICES_TABLE} AS s "
            "WHERE s.supersedes_choice_id = c.choice_id)",
            (vehicle_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return _stored(row[:-1]), int(row[-1])


def chain(connection: Connection[Any], vehicle_id: str) -> list[StoredChoice]:
    """The car's rows from the head backwards."""

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {_SELECT} FROM {VEHICLE_KTYPE_CHOICES_TABLE} AS c WHERE c.vehicle_id = %s",
            (vehicle_id,),
        )
        rows = [_stored(row) for row in cursor.fetchall()]
    superseded = {row.supersedes_choice_id for row in rows}
    by_id = {row.choice_id: row for row in rows}
    ordered: list[StoredChoice] = []
    cursor_row = next((row for row in rows if row.choice_id not in superseded), None)
    while cursor_row is not None:
        ordered.append(cursor_row)
        previous = cursor_row.supersedes_choice_id
        cursor_row = by_id.get(previous) if previous is not None else None
    return ordered


def _replay(existing: StoredChoice, new: NewChoice) -> StoredChoice:
    if not existing.same_request(
        vehicle_id=new.vehicle_id,
        action=new.action,
        ktype=new.ktype,
        reviewer=new.reviewer,
        reason=new.reason,
        supersedes_choice_id=new.supersedes_choice_id,
    ):
        raise OperationReusedError(
            "This operation id already recorded a different choice."
        )
    return existing


def append_choice(
    connection: Connection[Any], new: NewChoice, expected_head: UUID | None
) -> tuple[StoredChoice, bool, int]:
    """Append one row; returns `(row, created, history_count)`.

    Idempotent by `choice_id`: the same operation and content returns the row
    already stored with `created=False`; different content for that id raises
    `OperationReusedError`. Otherwise the car's head must be `expected_head`
    (`ChoiceChangedError`), and a withdrawal needs a choice in force
    (`NothingToWithdrawError`). The database enforces the same chain rules; a
    caller racing past these reads gets a unique violation instead.
    """

    existing = fetch_choice(connection, new.choice_id)
    if existing is not None:
        row = _replay(existing, new)
        found = current_choice(connection, row.vehicle_id)
        return row, False, found[1] if found else 1

    found = current_choice(connection, new.vehicle_id)
    head, history_count = found if found else (None, 0)
    if (head.choice_id if head else None) != expected_head:
        raise ChoiceChangedError("The car's current choice is not the one that was shown.")
    if new.supersedes_choice_id != expected_head:
        raise ChoiceChangedError("A choice must supersede the car's current choice.")
    if new.action == "withdraw" and (head is None or head.action == "withdraw"):
        raise NothingToWithdrawError("There is no choice to withdraw.")

    columns = [column for column in COLUMNS if column != "created_at"]
    values: list[Any] = [
        Jsonb(new.evidence) if column == "evidence" else getattr(new, column) for column in columns
    ]
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {VEHICLE_KTYPE_CHOICES_TABLE} AS c ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))}) "
            f"ON CONFLICT (choice_id) DO NOTHING RETURNING {_SELECT}",
            values,
        )
        inserted = cursor.fetchone()
    if inserted is None:
        # Another transaction committed this operation id between the read and the insert.
        raced = fetch_choice(connection, new.choice_id)
        if raced is None:  # pragma: no cover - the conflicting row cannot vanish (append-only)
            raise OperationReusedError("This operation id is already in use.")
        return _replay(raced, new), False, history_count + 1
    return _stored(inserted), True, history_count + 1


_HEAD = f"""
    SELECT c.vehicle_id, c.action, c.ktype,
           '{SOURCE_REVIEW}:' || c.choice_id::text || '@'
             || to_char(c.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS ref
    FROM {VEHICLE_KTYPE_CHOICES_TABLE} AS c
    WHERE NOT EXISTS (
        SELECT 1 FROM {VEHICLE_KTYPE_CHOICES_TABLE} AS s
        WHERE s.supersedes_choice_id = c.choice_id)
"""

_WANTED = f"""
    SELECT vehicle.vehicle_id,
           CASE WHEN head.action = 'choose' THEN head.ktype END AS ktype,
           CASE head.action WHEN 'choose' THEN '{MATCH_STATE_MANUAL}'
                            WHEN 'none' THEN '{MATCH_STATE_MANUAL_NONE}' END AS match_state,
           (coalesce(vehicle.field_sources, '{{}}'::jsonb) - 'ktype' - 'match_state')
             || CASE head.action
                  WHEN 'choose' THEN jsonb_build_object('ktype', head.ref, 'match_state', head.ref)
                  WHEN 'none' THEN jsonb_build_object('match_state', head.ref)
                  ELSE '{{}}'::jsonb END AS field_sources
    FROM {VEHICLES_TABLE} AS vehicle
    LEFT JOIN ({_HEAD}) AS head ON head.vehicle_id = vehicle.vehicle_id
"""

# Without ids: every car that has a chain, or whose copy claims a person's choice.
_SCOPE_ALL = (
    f"(vehicle.vehicle_id IN (SELECT vehicle_id FROM {VEHICLE_KTYPE_CHOICES_TABLE}) "
    f"OR vehicle.match_state IN ('{MATCH_STATE_MANUAL}', '{MATCH_STATE_MANUAL_NONE}'))"
)


def project_choices(connection: Connection[Any], vehicle_ids: Sequence[str] | None) -> int:
    """Make `core.vehicles` carry each car's head; returns the vehicles changed.

    A pure function of the head, so repair is a recompute. Touches only `ktype`,
    `match_state`, their two `field_sources` keys and `updated_at`, and only
    where something differs.
    """

    if vehicle_ids is not None and not vehicle_ids:
        return 0
    scope = _SCOPE_ALL if vehicle_ids is None else "vehicle.vehicle_id = ANY(%s)"
    parameters: tuple[Any, ...] = () if vehicle_ids is None else (list(vehicle_ids),)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH wanted AS ({_WANTED} WHERE {scope})
            UPDATE {VEHICLES_TABLE} AS target
            SET ktype = wanted.ktype,
                match_state = wanted.match_state,
                field_sources = wanted.field_sources,
                updated_at = now()
            FROM wanted
            WHERE target.vehicle_id = wanted.vehicle_id
              AND (target.ktype IS DISTINCT FROM wanted.ktype
                   OR target.match_state IS DISTINCT FROM wanted.match_state
                   OR target.field_sources IS DISTINCT FROM wanted.field_sources)
            """,
            parameters,
        )
        return cursor.rowcount
