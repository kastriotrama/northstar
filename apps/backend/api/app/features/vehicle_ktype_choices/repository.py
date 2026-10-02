"""Database access for KType choices: one short transaction per write.

The append and the vehicle's derived copy commit together. The vehicle row is
locked first, so two people deciding the same car are serialized and the second
is told the choice changed; the lock waits a bounded time, never a request's
lifetime.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol
from uuid import UUID

import psycopg
from psycopg import Connection

from ingestion.vehicle_core_migrations import VEHICLES_TABLE
from ingestion.vehicle_ktype_choice_migrations import (
    POSITION_KEY,
    SUPERSEDES_FOREIGN_KEY,
    VEHICLE_FOREIGN_KEY,
)
from ingestion.vehicle_ktype_choices import (
    ChoiceChangedError,
    NewChoice,
    StoredChoice,
    append_choice,
    chain,
    current_choice,
    fetch_choice,
    project_choices,
)


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


class ChoiceVehicleNotFoundError(LookupError):
    """No vehicle has that id."""


class VehicleBusyError(RuntimeError):
    """The vehicle's row is locked by another writer; retry with the same operation id."""


class ChoiceRejectedError(Exception):
    """The database refused the row for good: sending the same request again cannot succeed."""


class KTypeChoiceRepository:
    def __init__(self, connection_factory: ConnectionFactory, *, lock_timeout: str = "3s") -> None:
        self._connection_factory = connection_factory
        self._lock_timeout = lock_timeout

    def fetch(self, choice_id: UUID) -> StoredChoice | None:
        with self._connection_factory() as connection:
            try:
                return fetch_choice(connection, choice_id)
            finally:
                connection.rollback()

    def current(self, vehicle_id: str) -> tuple[StoredChoice, int] | None:
        """The lookup's choice read: the head and the chain length, one indexed query."""

        with self._connection_factory() as connection:
            try:
                return current_choice(connection, vehicle_id)
            finally:
                connection.rollback()

    def chain(self, vehicle_id: str) -> list[StoredChoice]:
        with self._connection_factory() as connection:
            try:
                return chain(connection, vehicle_id)
            finally:
                connection.rollback()

    def record(self, new: NewChoice) -> tuple[StoredChoice, bool, int]:
        """Append `new` on top of the head it names and refresh the vehicle's copy.

        Returns `(row, created, history_count)`. Raises the append's own errors,
        `ChoiceVehicleNotFoundError`, `VehicleBusyError` (retry), or
        `ChoiceRejectedError` when the database refuses the content itself (a
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
                        raise ChoiceVehicleNotFoundError(new.vehicle_id)
                result = append_choice(connection, new, new.supersedes_choice_id)
                if result[1]:
                    project_choices(connection, [new.vehicle_id])
                connection.commit()
                return result
            except psycopg.errors.LockNotAvailable as error:
                connection.rollback()
                raise VehicleBusyError("The vehicle is being changed; try again.") from error
            except psycopg.errors.IntegrityError as error:
                connection.rollback()
                constraint = error.diag.constraint_name
                if constraint in (POSITION_KEY, SUPERSEDES_FOREIGN_KEY):
                    raise ChoiceChangedError(
                        "The car's current choice is not the one that was shown."
                    ) from error
                if constraint == VEHICLE_FOREIGN_KEY:
                    raise ChoiceVehicleNotFoundError(new.vehicle_id) from error
                raise ChoiceRejectedError("The database refused this choice.") from error
            except psycopg.errors.DataError as error:
                connection.rollback()
                raise ChoiceRejectedError("The request holds a value that cannot be stored.") from error
            except BaseException:
                connection.rollback()
                raise
