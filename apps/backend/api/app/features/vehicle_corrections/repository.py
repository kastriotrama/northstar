"""Database access for corrections of one car's data: one short transaction per write.

The append and the vehicle's copy commit together. The vehicle row is locked
first -- the same lock a KType choice takes -- so two people correcting the same
car are serialized and the second is told the correction changed; the lock waits
a bounded time, never a request's lifetime.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol
from uuid import UUID

import psycopg
from psycopg import Connection

from api.app.features.vehicle_corrections.fields import vehicle_copy
from ingestion.vehicle_core_migrations import VEHICLES_TABLE
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

    def record(self, new: NewCorrection) -> tuple[StoredCorrection, bool]:
        """Append `new` on top of the head it names and refresh the vehicle's copy.

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
