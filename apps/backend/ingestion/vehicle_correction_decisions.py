"""Read and append a person's decisions that correct many cars.

`core.vehicle_correction_decisions` is the truth about a decision: an
append-only chain of events whose head -- the event with the highest
`chain_position` -- says where the decision stands. The root says what was
decided and for which cars; it is a proposal (`propose`, no car written) or an
application (`apply`). A `withdraw` on top takes the whole decision back.

The cars a decision wrote carry ordinary rows in `core.vehicle_fact_corrections`
(`vehicle_fact_corrections.append_many` writes them, `project_many` the copies
on the vehicles). Each names the event that wrote it in `group_id`, and its id
is derived from that event and the car (`member_correction_id`), so the same
operation can never write a car twice.

This module stores and reads events; which cars an event writes, and the checks
made under their row locks, are the caller's (the API's decision repository).

Sync, PostgreSQL only. Callers own the transaction: nothing here commits.
Nothing here writes Neo4j, aliases, canonical ids, the enrichment ledger or the
match decision tables.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.vehicle_correction_decision_migrations import (
    COLUMNS,
    VEHICLE_CORRECTION_DECISIONS_TABLE,
)
from ingestion.vehicle_fact_correction_migrations import VEHICLE_FACT_CORRECTIONS_TABLE
from ingestion.vehicle_fact_corrections import (
    NothingToWithdrawError,
    OperationReusedError,
    group_sizes,
)

DecisionEventKind = Literal["propose", "apply", "withdraw"]
DecisionAction = Literal["set", "ignore"]
DecisionStatus = Literal["proposed", "applied", "withdrawn"]

#: Where a decision stands, by the kind of its head event.
STATUS_OF: dict[str, DecisionStatus] = {
    "propose": "proposed",
    "apply": "applied",
    "withdraw": "withdrawn",
}

_SELECT = ", ".join(f"d.{column}" for column in COLUMNS)


class DecisionChangedError(Exception):
    """The decision's current event is not the one the caller built on."""


class DecisionNotFoundError(LookupError):
    """No decision has that id."""


@dataclass(frozen=True)
class NewDecisionEvent:
    """One event to append. `event_id` is the client's operation UUID.

    The root (`event_id == decision_id`, no predecessor) carries what was
    decided; every later event leaves those fields empty.
    """

    event_id: UUID
    decision_id: UUID
    event: DecisionEventKind
    supersedes_event_id: UUID | None
    field: str | None
    action: DecisionAction | None
    value: str | None
    scope: dict[str, Any] | None
    scope_label: str | None
    manufacturer: str | None
    model_family: str | None
    reviewer: str
    reason: str | None
    catalog_batch: str
    code_version: str
    measurement: dict[str, Any]


@dataclass(frozen=True)
class StoredDecisionEvent:
    event_id: UUID
    decision_id: UUID
    #: 0 for the decision's root; each later event is one higher.
    chain_position: int
    event: DecisionEventKind
    supersedes_event_id: UUID | None
    field: str | None
    action: DecisionAction | None
    value: str | None
    scope: dict[str, Any] | None
    scope_label: str | None
    manufacturer: str | None
    model_family: str | None
    reviewer: str
    reason: str | None
    catalog_batch: str
    code_version: str
    measurement: dict[str, Any]
    created_at: datetime

    def same_request(self, new: NewDecisionEvent) -> bool:
        """True when `new` is a replay of this event.

        The measurement and the provenance are the server's own and are left
        out: a replay must be recognized whatever the cars look like by then.
        """

        return (
            self.decision_id == new.decision_id
            and self.event == new.event
            and self.field == new.field
            and self.action == new.action
            and self.value == new.value
            and self.scope == new.scope
            and self.scope_label == new.scope_label
            and self.reviewer == new.reviewer
            and self.reason == new.reason
        )


@dataclass(frozen=True)
class DecisionRecord:
    """A decision as it stands: its events from the root on, and the cars it wrote."""

    events: tuple[StoredDecisionEvent, ...]
    #: Cars the decision's application wrote; 0 for a proposal.
    member_count: int

    @property
    def root(self) -> StoredDecisionEvent:
        return self.events[0]

    @property
    def head(self) -> StoredDecisionEvent:
        return self.events[-1]

    @property
    def status(self) -> DecisionStatus:
        return STATUS_OF[self.head.event]

    @property
    def applied(self) -> StoredDecisionEvent | None:
        """The event that wrote the decision's cars, if it was ever applied."""

        return next((event for event in reversed(self.events) if event.event == "apply"), None)


@dataclass(frozen=True)
class DecisionRef:
    """What a car's lookup says about the decision that wrote one of its rows."""

    decision_id: UUID
    scope_label: str
    #: Cars the decision's application wrote.
    member_count: int
    #: Who made the event that wrote the row: the application, or its withdrawal.
    reviewer: str


def member_correction_id(event_id: UUID, vehicle_id: str, field: str) -> UUID:
    """The id of the row a decision event writes for one car: the same event, the same id."""

    return uuid5(event_id, f"{vehicle_id}:{field}")


def _stored(row: Sequence[Any]) -> StoredDecisionEvent:
    record = dict(zip(COLUMNS, row, strict=True))
    scope = record["scope"]
    return StoredDecisionEvent(
        event_id=record["event_id"],
        decision_id=record["decision_id"],
        chain_position=int(record["chain_position"]),
        event=record["event"],
        supersedes_event_id=record["supersedes_event_id"],
        field=record["field"],
        action=record["action"],
        value=record["value"],
        scope=None if scope is None else dict(scope),
        scope_label=record["scope_label"],
        manufacturer=record["manufacturer"],
        model_family=record["model_family"],
        reviewer=str(record["reviewer"]),
        reason=record["reason"],
        catalog_batch=str(record["catalog_batch"]),
        code_version=str(record["code_version"]),
        measurement=dict(record["measurement"]),
        created_at=record["created_at"],
    )


def fetch_event(connection: Connection[Any], event_id: UUID) -> StoredDecisionEvent | None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {_SELECT} FROM {VEHICLE_CORRECTION_DECISIONS_TABLE} AS d "
            "WHERE d.event_id = %s",
            (event_id,),
        )
        row = cursor.fetchone()
    return _stored(row) if row else None


def decision_events(connection: Connection[Any], decision_id: UUID) -> list[StoredDecisionEvent]:
    """The decision's events from its root on; empty when there is no such decision."""

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {_SELECT} FROM {VEHICLE_CORRECTION_DECISIONS_TABLE} AS d "
            "WHERE d.decision_id = %s ORDER BY d.chain_position",
            (decision_id,),
        )
        return [_stored(row) for row in cursor.fetchall()]


def _replay(existing: StoredDecisionEvent, new: NewDecisionEvent) -> StoredDecisionEvent:
    if not existing.same_request(new):
        raise OperationReusedError("This operation id already recorded something else.")
    return existing


def append_event(
    connection: Connection[Any], new: NewDecisionEvent
) -> tuple[StoredDecisionEvent, bool]:
    """Append one event; returns `(event, created)`.

    Idempotent by `event_id`: the same operation and content returns the event
    already stored with `created=False`; different content for that id raises
    `OperationReusedError`. Otherwise the decision's head must be the event
    `new` supersedes (`DecisionChangedError`); an event on a decision that does
    not exist raises `DecisionNotFoundError`, and a withdrawal needs a decision
    that is not withdrawn already (`NothingToWithdrawError`). The event takes
    the position after the head. The database enforces the same chain rules; a
    caller racing past these reads gets a unique violation on the position.
    """

    existing = fetch_event(connection, new.event_id)
    if existing is not None:
        return _replay(existing, new), False

    events = decision_events(connection, new.decision_id)
    head = events[-1] if events else None
    if head is None and new.event_id != new.decision_id:
        raise DecisionNotFoundError(str(new.decision_id))
    if (head.event_id if head else None) != new.supersedes_event_id:
        raise DecisionChangedError("The decision is no longer as it was shown.")
    if new.event == "withdraw" and (head is None or head.event == "withdraw"):
        raise NothingToWithdrawError("There is no decision to withdraw.")

    columns = [column for column in COLUMNS if column != "created_at"]
    given: dict[str, Any] = {
        "chain_position": head.chain_position + 1 if head else 0,
        "scope": None if new.scope is None else Jsonb(new.scope),
        "measurement": Jsonb(new.measurement),
    }
    values: list[Any] = [
        given[column] if column in given else getattr(new, column) for column in columns
    ]
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {VEHICLE_CORRECTION_DECISIONS_TABLE} AS d ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))}) "
            f"ON CONFLICT (event_id) DO NOTHING RETURNING {_SELECT}",
            values,
        )
        inserted = cursor.fetchone()
    if inserted is None:
        # Another transaction committed this operation id between the read and the insert.
        raced = fetch_event(connection, new.event_id)
        if raced is None:  # pragma: no cover - the conflicting row cannot vanish (append-only)
            raise OperationReusedError("This operation id is already in use.")
        return _replay(raced, new), False
    return _stored(inserted), True


def _records(
    connection: Connection[Any], events: Sequence[StoredDecisionEvent]
) -> list[DecisionRecord]:
    """Group events into decisions, each with the number of cars its application wrote."""

    chains: dict[UUID, list[StoredDecisionEvent]] = {}
    for event in events:
        chains.setdefault(event.decision_id, []).append(event)
    applied = {
        decision_id: event.event_id
        for decision_id, chain in chains.items()
        for event in chain
        if event.event == "apply"
    }
    sizes = group_sizes(connection, list(applied.values()))
    return [
        DecisionRecord(tuple(chain), sizes.get(applied[decision_id], 0) if decision_id in applied else 0)
        for decision_id, chain in chains.items()
    ]


def decision(connection: Connection[Any], decision_id: UUID) -> DecisionRecord | None:
    """One decision as it stands; None when there is no such decision."""

    records = _records(connection, decision_events(connection, decision_id))
    return records[0] if records else None


def recent_decisions(connection: Connection[Any], limit: int) -> list[DecisionRecord]:
    """The newest decisions first, by when each was first recorded."""

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {_SELECT} FROM {VEHICLE_CORRECTION_DECISIONS_TABLE} AS d "
            "WHERE d.decision_id IN ("
            f"  SELECT root.decision_id FROM {VEHICLE_CORRECTION_DECISIONS_TABLE} AS root "
            "  WHERE root.chain_position = 0 "
            "  ORDER BY root.created_at DESC, root.event_id DESC LIMIT %s) "
            "ORDER BY d.decision_id, d.chain_position",
            (limit,),
        )
        events = [_stored(row) for row in cursor.fetchall()]
    records = _records(connection, events)
    return sorted(
        records, key=lambda record: (record.root.created_at, str(record.root.event_id)), reverse=True
    )


def decision_refs(connection: Connection[Any], event_ids: Sequence[UUID]) -> dict[UUID, DecisionRef]:
    """The decision behind each of these events, by event id.

    What a correction row's `group_id` resolves to. The member count is the
    cars the decision's application wrote, also for a row its withdrawal wrote.
    """

    if not event_ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT event.event_id, event.decision_id, root.scope_label, event.reviewer,
                   (SELECT count(*) FROM {VEHICLE_FACT_CORRECTIONS_TABLE} AS member
                    WHERE member.group_id = applied.event_id)
            FROM {VEHICLE_CORRECTION_DECISIONS_TABLE} AS event
            JOIN {VEHICLE_CORRECTION_DECISIONS_TABLE} AS root ON root.event_id = event.decision_id
            LEFT JOIN LATERAL (
                SELECT other.event_id FROM {VEHICLE_CORRECTION_DECISIONS_TABLE} AS other
                WHERE other.decision_id = event.decision_id AND other.event = 'apply'
                ORDER BY other.chain_position DESC LIMIT 1
            ) AS applied ON true
            WHERE event.event_id = ANY(%s)
            """,
            (list(event_ids),),
        )
        return {
            row[0]: DecisionRef(row[1], str(row[2]), int(row[4]), str(row[3]))
            for row in cursor.fetchall()
        }
