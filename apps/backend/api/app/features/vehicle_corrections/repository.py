"""Database access for corrections of one car's data: one short transaction per write.

The append and the vehicle's copy commit together. The vehicle row is locked
first -- the same lock a KType choice takes -- so two people correcting the same
car are serialized and the second is told the correction changed; the lock waits
a bounded time, never a request's lifetime.

The service decides on a lookup it made before that lock. Once the row is
locked the car is read again through the matcher's own seam, and the write goes
ahead only when the matcher would still be handed what that lookup was made on.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol
from uuid import UUID

import psycopg
from psycopg import Connection

from api.app.features.vehicle_corrections.fields import vehicle_copy
from api.app.features.vehicle_matching.repository import (
    matcher_input_hash,
    read_vehicle_car_records,
)
from ingestion.vehicle_core_migrations import VEHICLES_TABLE
from ingestion.vehicle_correction_decisions import DecisionRef, decision_refs
from ingestion.vehicle_fact_correction_migrations import (
    POSITION_KEY,
    SUPERSEDES_FOREIGN_KEY,
    VEHICLE_FOREIGN_KEY,
)
from ingestion.vehicle_fact_corrections import (
    CorrectionChangedError,
    NewCorrection,
    StoredCorrection,
    append_correction,
    chains,
    current_corrections,
    fetch_correction,
    project_correction,
)


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


class CorrectionVehicleNotFoundError(LookupError):
    """No vehicle has that id."""


class VehicleBusyError(RuntimeError):
    """The vehicle's row is locked by another writer; retry with the same operation id."""


class CorrectionRejectedError(Exception):
    """The database refused the row for good: sending the same request again cannot succeed."""


class EvidenceChangedError(Exception):
    """The car's matching is no longer what the screen showed."""


class CorrectionRepository:
    def __init__(self, connection_factory: ConnectionFactory, *, lock_timeout: str = "3s") -> None:
        self._connection_factory = connection_factory
        self._lock_timeout = lock_timeout

    def fetch(self, correction_id: UUID) -> StoredCorrection | None:
        with self._connection_factory() as connection:
            try:
                return fetch_correction(connection, correction_id)
            finally:
                connection.rollback()

    def current(self, vehicle_id: str) -> dict[str, tuple[StoredCorrection, int]]:
        """The lookup's detail read: every field's head and chain length, one indexed query.

        Only asked for a car that has corrections; the car's own read says so.
        """

        with self._connection_factory() as connection:
            try:
                return current_corrections(connection, vehicle_id)
            finally:
                connection.rollback()

    def chains(self, vehicle_id: str) -> dict[str, list[StoredCorrection]]:
        with self._connection_factory() as connection:
            try:
                return chains(connection, vehicle_id)
            finally:
                connection.rollback()

    def decisions(self, group_ids: Sequence[UUID]) -> dict[UUID, DecisionRef]:
        """The decisions behind the rows that carry these group ids, by group id.

        Only asked for a car one of whose heads a decision about many cars wrote.
        """

        with self._connection_factory() as connection:
            try:
                return decision_refs(connection, group_ids)
            finally:
                connection.rollback()

    def record(
        self, new: NewCorrection, *, checked_on: str | None = None
    ) -> tuple[StoredCorrection, bool]:
        """Append `new` on top of the head it names and refresh the vehicle's copy.

        `checked_on` is the hash of the matcher input the correction was decided
        on (`lookup.matcher_input_hash`). Under the vehicle's lock the car is
        read again through the matcher's seam -- no matcher run -- and a car
        that is handed anything else by then is refused (`EvidenceChangedError`).
        A replay of an operation already stored is answered without that check.
        `None` skips it: a caller that decided on nothing it read before.

        Returns `(row, created)`. Raises the append's own errors,
        `CorrectionVehicleNotFoundError`, `VehicleBusyError` (retry), or
        `CorrectionRejectedError` when the database refuses the content itself (a
        value it cannot store, a constraint) -- a retry would fail the same way.
        Any other database error propagates. Nothing is written on any error.
        """

        with self._connection_factory() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('lock_timeout', %s, true)", (self._lock_timeout,)
                    )
                    cursor.execute(
                        f"SELECT 1 FROM {VEHICLES_TABLE} WHERE vehicle_id = %s FOR NO KEY UPDATE",
                        (new.vehicle_id,),
                    )
                    if cursor.fetchone() is None:
                        raise CorrectionVehicleNotFoundError(new.vehicle_id)
                if (
                    checked_on is not None
                    and fetch_correction(connection, new.correction_id) is None
                ):
                    cars = read_vehicle_car_records(connection, [new.vehicle_id])
                    if not cars or matcher_input_hash(cars[0].record) != checked_on:
                        raise EvidenceChangedError(
                            "The car's matching changed since it was shown."
                        )
                row, created, superseded = append_correction(
                    connection, new, new.supersedes_correction_id
                )
                if created:
                    project_correction(connection, new.vehicle_id, row, superseded, vehicle_copy)
                connection.commit()
                return row, created
            except psycopg.errors.LockNotAvailable as error:
                connection.rollback()
                raise VehicleBusyError("The vehicle is being changed; try again.") from error
            except psycopg.errors.IntegrityError as error:
                connection.rollback()
                constraint = error.diag.constraint_name
                if constraint in (POSITION_KEY, SUPERSEDES_FOREIGN_KEY):
                    raise CorrectionChangedError(
                        "The field's current correction is not the one that was shown."
                    ) from error
                if constraint == VEHICLE_FOREIGN_KEY:
                    raise CorrectionVehicleNotFoundError(new.vehicle_id) from error
                raise CorrectionRejectedError("The database refused this correction.") from error
            except psycopg.errors.DataError as error:
                connection.rollback()
                raise CorrectionRejectedError(
                    "The request holds a value that cannot be stored."
                ) from error
            except BaseException:
                connection.rollback()
                raise
