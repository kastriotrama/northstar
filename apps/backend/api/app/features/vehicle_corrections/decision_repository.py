"""Database access for corrections that cover many cars.

Three kinds of work, each a short transaction of its own:

- reads for choosing a scope and checking it: the anchor car's vehicle values,
  how many cars a scope holds, a page of cars as the matcher is handed them.
  Every one runs under a statement timeout, so a scope that is too broad ends
  in a refusal, never in a query that holds the server.
- applying a decision: the event and one correction row per car, with the
  vehicles' copies, in one transaction. The cars' rows are locked in id order
  first -- the lock one car's correction takes -- and each car is then read
  again through the matcher's own seam: a car that is no longer what was
  checked, or that a person has corrected in the meantime, is left out and
  counted. Nothing is written when any lock cannot be had in time.
- withdrawing a decision: the event and a withdrawal for every car whose
  correction is still the decision's own, under the same locks.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from typing import Any, Protocol
from uuid import UUID

import psycopg
from psycopg import Connection

from api.app.features.vehicle_corrections.fields import vehicle_copy
from api.app.features.vehicle_corrections.scope import ANCHOR_COLUMNS
from api.app.features.vehicle_matching.repository import (
    SAMPLE_SEED,
    CarRecord,
    matcher_input_hash,
    read_vehicle_car_records,
)
from ingestion.vehicle_core_migrations import VEHICLES_TABLE
from ingestion.vehicle_core_query import ALIAS, VehicleTerm, compile_vehicle_filter
from ingestion.vehicle_correction_decision_migrations import POSITION_KEY, SUPERSEDES_FOREIGN_KEY
from ingestion.vehicle_correction_decisions import (
    DecisionChangedError,
    DecisionNotFoundError,
    DecisionRecord,
    NewDecisionEvent,
    StoredDecisionEvent,
    append_event,
    decision,
    fetch_event,
    member_correction_id,
    recent_decisions,
)
from ingestion.vehicle_fact_corrections import (
    NewCorrection,
    NothingToWithdrawError,
    OperationReusedError,
    StoredCorrection,
    append_many,
    field_heads,
    group_rows,
    lock_vehicles,
    project_many,
)
from ingestion.vehicle_ktype_choice_migrations import VEHICLE_KTYPE_CHOICES_TABLE

#: The matcher's verdict stored with a row a withdrawn decision wrote: the cars
#: are not evaluated again to undo what was done to them.
NOT_EVALUATED = "not_evaluated"
WITHDRAWAL_EVIDENCE_SCHEMA = "vehicle-correction-decision-withdrawal-evidence-v1"
#: Cars read through the matcher's seam per query, under the locks.
_PAGE = 200


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


class ScopeTimeoutError(RuntimeError):
    """Reading a scope took longer than allowed: it is too broad to check."""


class VehiclesBusyError(RuntimeError):
    """A car's row is locked by another writer; retry with the same operation id."""


class NothingToApplyError(Exception):
    """No car is left to write."""


class DecisionRejectedError(Exception):
    """The database refused the content for good: the same request cannot succeed."""


@dataclass(frozen=True)
class PlannedMember:
    """One car an application sets out to write, as the check left it."""

    #: The row to append; what it supersedes is read under the car's lock.
    row: NewCorrection
    #: The hash of the matcher input the car was checked on.
    input_hash: str
    #: Where the check sorted the car.
    outcome: str


@dataclass(frozen=True)
class CheckPage:
    """A page of cars as the matcher is handed them, with the choices people made for them."""

    cars: list[CarRecord]
    #: The head of each decided car's choice chain: its action and KType.
    choices: dict[str, tuple[str, str | None]]


def _withdrawal_evidence(decision_id: UUID, withdrawn: StoredCorrection, input_hash: str) -> dict[str, Any]:
    """What a withdrawal of one car's row stores: no evaluation was made, and it says so."""

    return {
        "schema": WITHDRAWAL_EVIDENCE_SCHEMA,
        "automatic": {"terminal": NOT_EVALUATED, "top_ktype": None, "reason_codes": []},
        "decision_id": str(decision_id),
        "withdraws": str(withdrawn.correction_id),
        "matcher_input_hash": input_hash,
    }


class DecisionRepository:
    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        lock_timeout: str = "3s",
        count_timeout: str = "5s",
        read_timeout: str = "15s",
    ) -> None:
        self._connection_factory = connection_factory
        self._lock_timeout = lock_timeout
        self._count_timeout = count_timeout
        self._read_timeout = read_timeout

    # ------------------------------------------------------------ scopes and the check

    def anchor(self, vehicle_id: str) -> dict[str, Any] | None:
        """The vehicle values a scope is built from; None when there is no such vehicle."""

        with self._connection_factory() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT {', '.join(ANCHOR_COLUMNS)} FROM {VEHICLES_TABLE} "
                        "WHERE vehicle_id = %s",
                        (vehicle_id,),
                    )
                    row = cursor.fetchone()
            finally:
                connection.rollback()
        return None if row is None else dict(zip(ANCHOR_COLUMNS, row, strict=True))

    def count_scope(self, terms: Sequence[VehicleTerm]) -> int | None:
        """How many vehicles a scope holds; None when counting took too long."""

        predicate = compile_vehicle_filter(terms)
        with self._connection_factory() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('statement_timeout', %s, true)", (self._count_timeout,)
                    )
                    cursor.execute(
                        f"SELECT count(*) FROM {VEHICLES_TABLE} AS {ALIAS} WHERE {predicate.sql}",
                        predicate.parameters,
                    )
                    row = cursor.fetchone()
                return int(row[0]) if row else 0
            except psycopg.errors.QueryCanceled:
                return None
            finally:
                connection.rollback()

    def scope_population(
        self, terms: Sequence[VehicleTerm], *, limit: int, seed: str = SAMPLE_SEED
    ) -> tuple[int, list[str]]:
        """How many vehicles a scope holds, and a seeded random `limit` of them.

        One pass over the scope's rows, in the order `vehicle_population` uses,
        so a scope larger than `limit` is checked on a fair sample and the same
        scope picks the same cars. Raises `ScopeTimeoutError` when it takes
        longer than a count may.
        """

        predicate = compile_vehicle_filter(terms)
        with self._connection_factory() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('statement_timeout', %s, true)", (self._count_timeout,)
                    )
                    cursor.execute(
                        f"SELECT {ALIAS}.vehicle_id, count(*) OVER () "
                        f"FROM {VEHICLES_TABLE} AS {ALIAS} WHERE {predicate.sql} "
                        f"ORDER BY md5(%s || {ALIAS}.vehicle_id), {ALIAS}.vehicle_id LIMIT %s",
                        [*predicate.parameters, seed, limit],
                    )
                    rows = cursor.fetchall()
            except psycopg.errors.QueryCanceled as error:
                raise ScopeTimeoutError("The scope could not be read in time.") from error
            finally:
                connection.rollback()
        return (int(rows[0][1]) if rows else 0), [str(row[0]) for row in rows]

    def check_page(self, vehicle_ids: Sequence[str]) -> CheckPage:
        """These cars as the matcher is handed them, in the order asked for.

        Primary-key reads on one connection, under a statement timeout. The
        choice heads are read only for the cars a person decided.
        """

        if not vehicle_ids:
            return CheckPage([], {})
        with self._connection_factory() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('statement_timeout', %s, true)", (self._read_timeout,)
                    )
                cars = read_vehicle_car_records(connection, vehicle_ids)
                decided = [str(car.vehicle_id) for car in cars if car.has_choices]
                choices: dict[str, tuple[str, str | None]] = {}
                if decided:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT DISTINCT ON (c.vehicle_id) c.vehicle_id, c.action, c.ktype "
                            f"FROM {VEHICLE_KTYPE_CHOICES_TABLE} AS c "
                            "WHERE c.vehicle_id = ANY(%s) "
                            "ORDER BY c.vehicle_id DESC, c.chain_position DESC",
                            (decided,),
                        )
                        choices = {str(row[0]): (str(row[1]), row[2]) for row in cursor.fetchall()}
                return CheckPage(cars, choices)
            finally:
                connection.rollback()

    # ------------------------------------------------------------------- decisions

    def fetch_event(self, event_id: UUID) -> StoredDecisionEvent | None:
        with self._connection_factory() as connection:
            try:
                return fetch_event(connection, event_id)
            finally:
                connection.rollback()

    def decision(self, decision_id: UUID) -> DecisionRecord | None:
        with self._connection_factory() as connection:
            try:
                return decision(connection, decision_id)
            finally:
                connection.rollback()

    def recent(self, limit: int) -> list[DecisionRecord]:
        with self._connection_factory() as connection:
            try:
                return recent_decisions(connection, limit)
            finally:
                connection.rollback()

    def propose(self, event: NewDecisionEvent) -> tuple[StoredDecisionEvent, bool]:
        """Store a decision as a proposal: its root event and no car. Returns `(event, created)`."""

        with self._connection_factory() as connection:
            try:
                stored, created = append_event(connection, event)
                connection.commit()
                return stored, created
            except (psycopg.errors.IntegrityError, psycopg.errors.DataError) as error:
                connection.rollback()
                raise DecisionRejectedError("The database refused this decision.") from error
            except BaseException:
                connection.rollback()
                raise

    def apply(
        self, event: NewDecisionEvent, members: Sequence[PlannedMember]
    ) -> tuple[StoredDecisionEvent, bool]:
        """Apply a decision: its root event and one correction row per car, or nothing.

        Returns `(event, created)`; the stored measurement says how many cars
        were written and how many were left out. `created=False` is a replay of
        an operation already stored. Raises `VehiclesBusyError` (retry with the
        same operation id), `NothingToApplyError` when every car was left out,
        `OperationReusedError`, or `DecisionRejectedError` when the database
        refuses the content itself. Nothing is written on any error.
        """

        field = event.field or ""
        ids = sorted({member.row.vehicle_id for member in members})
        with self._connection_factory() as connection:
            try:
                if fetch_event(connection, event.event_id) is not None:
                    stored, _ = append_event(connection, event)
                    connection.rollback()
                    return stored, False
                hashes = self._lock(connection, ids)
                heads = field_heads(connection, ids, field)
                writing: list[tuple[PlannedMember, StoredCorrection | None]] = []
                skipped: Counter[str] = Counter()
                for member in sorted(members, key=lambda item: item.row.vehicle_id):
                    head = heads.get(member.row.vehicle_id)
                    if head is not None and head.action != "withdraw":
                        skipped["corrected_meanwhile"] += 1
                    elif hashes.get(member.row.vehicle_id) != member.input_hash:
                        skipped["changed_since_check"] += 1
                    else:
                        writing.append((member, head))
                if not writing:
                    raise NothingToApplyError(
                        "Every car changed or was corrected since the check. Check again."
                    )
                measured = {
                    **event.measurement,
                    "written": len(writing),
                    "written_by_outcome": dict(
                        sorted(Counter(member.outcome for member, _ in writing).items())
                    ),
                    "skipped": {
                        "changed_since_check": skipped["changed_since_check"],
                        "corrected_meanwhile": skipped["corrected_meanwhile"],
                    },
                }
                stored, created = append_event(connection, replace(event, measurement=measured))
                if not created:
                    # The same operation landed while this one waited for the locks.
                    connection.rollback()
                    return stored, False
                rows = [(member.row, head) for member, head in writing]
                append_many(connection, rows)
                project_many(connection, rows, vehicle_copy)
                connection.commit()
                return stored, True
            except psycopg.errors.LockNotAvailable as error:
                connection.rollback()
                raise VehiclesBusyError("Some of these cars are being changed; try again.") from error
            except (psycopg.errors.IntegrityError, psycopg.errors.DataError) as error:
                connection.rollback()
                raise DecisionRejectedError("The database refused this decision.") from error
            except BaseException:
                connection.rollback()
                raise

    def withdraw(
        self,
        decision_id: UUID,
        *,
        operation_id: UUID,
        reviewer: str,
        reason: str | None,
        code_version: str,
    ) -> tuple[StoredDecisionEvent, bool]:
        """Take a decision back: a `withdraw` event, and a withdrawal for each of its cars.

        A car whose correction is still the row the decision wrote gets a
        `withdraw` row on top and its vehicle copy retracted; a car a person
        changed since is left as it is and counted. Returns `(event, created)`.
        Raises `DecisionNotFoundError`, `NothingToWithdrawError` for a decision
        already withdrawn, `DecisionChangedError` when another event landed
        first, `VehiclesBusyError` (retry), `OperationReusedError`, or
        `DecisionRejectedError`. Nothing is written on any error.
        """

        with self._connection_factory() as connection:
            try:
                existing = fetch_event(connection, operation_id)
                if existing is not None:
                    connection.rollback()
                    if (
                        existing.event != "withdraw"
                        or existing.decision_id != decision_id
                        or existing.reviewer != reviewer
                        or existing.reason != reason
                    ):
                        raise OperationReusedError(
                            "This operation id already recorded something else."
                        )
                    return existing, False
                record = decision(connection, decision_id)
                if record is None:
                    raise DecisionNotFoundError(str(decision_id))
                if record.status == "withdrawn":
                    raise NothingToWithdrawError("This decision is already withdrawn.")
                applied = record.applied
                members = group_rows(connection, applied.event_id) if applied else []
                ids = sorted({member.vehicle_id for member in members})
                hashes = self._lock(connection, ids)
                heads = field_heads(connection, ids, str(record.root.field))
                standing = [
                    member
                    for member in members
                    if (head := heads.get(member.vehicle_id)) is not None
                    and head.correction_id == member.correction_id
                    and member.vehicle_id in hashes
                ]
                head_event = record.head
                event = NewDecisionEvent(
                    event_id=operation_id,
                    decision_id=decision_id,
                    event="withdraw",
                    supersedes_event_id=head_event.event_id,
                    field=None,
                    action=None,
                    value=None,
                    scope=None,
                    scope_label=None,
                    manufacturer=None,
                    model_family=None,
                    reviewer=reviewer,
                    reason=reason,
                    catalog_batch=head_event.catalog_batch,
                    code_version=code_version,
                    measurement={
                        **head_event.measurement,
                        "withdrawal": {
                            "members": len(members),
                            "withdrawn": len(standing),
                            "left_changed": len(members) - len(standing),
                        },
                    },
                )
                stored, created = append_event(connection, event)
                if not created:
                    connection.rollback()
                    return stored, False
                rows = [
                    (
                        NewCorrection(
                            correction_id=member_correction_id(
                                operation_id, member.vehicle_id, member.field
                            ),
                            vehicle_id=member.vehicle_id,
                            field=member.field,
                            action="withdraw",
                            value=None,
                            supersedes_correction_id=member.correction_id,
                            group_id=operation_id,
                            reviewer=reviewer,
                            reason=reason,
                            # What the matcher used just before: the decision's own value.
                            previous_value=member.value,
                            previous_source="correction",
                            catalog_batch=head_event.catalog_batch,
                            automatic_terminal=NOT_EVALUATED,
                            automatic_ktype=None,
                            code_version=code_version,
                            evidence_fingerprint=hashes[member.vehicle_id],
                            evidence=_withdrawal_evidence(
                                decision_id, member, hashes[member.vehicle_id]
                            ),
                        ),
                        member,
                    )
                    for member in standing
                ]
                append_many(connection, rows)
                project_many(connection, rows, vehicle_copy)
                connection.commit()
                return stored, True
            except psycopg.errors.LockNotAvailable as error:
                connection.rollback()
                raise VehiclesBusyError("Some of these cars are being changed; try again.") from error
            except psycopg.errors.IntegrityError as error:
                connection.rollback()
                if error.diag.constraint_name in (POSITION_KEY, SUPERSEDES_FOREIGN_KEY):
                    raise DecisionChangedError(
                        "The decision is no longer as it was shown."
                    ) from error
                raise DecisionRejectedError("The database refused this withdrawal.") from error
            except psycopg.errors.DataError as error:
                connection.rollback()
                raise DecisionRejectedError("The database refused this withdrawal.") from error
            except BaseException:
                connection.rollback()
                raise

    def _lock(self, connection: Connection[Any], vehicle_ids: Sequence[str]) -> dict[str, str]:
        """Lock these cars' rows in id order, then read each again through the matcher's seam.

        Returns the hash of what the matcher is handed for each car now, under
        the lock. No matcher runs. The wait for a lock is bounded.
        """

        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('lock_timeout', %s, true)", (self._lock_timeout,))
        locked = lock_vehicles(connection, vehicle_ids)
        hashes: dict[str, str] = {}
        for start in range(0, len(locked), _PAGE):
            for car in read_vehicle_car_records(connection, locked[start : start + _PAGE]):
                hashes[str(car.vehicle_id)] = matcher_input_hash(car.record)
        return hashes
