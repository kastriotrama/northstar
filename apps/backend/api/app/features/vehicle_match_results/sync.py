"""Keep a car's stored match result current at the moment a person changes the car.

A saved correction or KType choice calls in here after its own transaction
committed. The car is read again and, when the matcher would be handed
something else than its row was computed from, matched again and stored.

This never fails the save it follows: the correction or choice is already
stored, and a row left behind is picked up by the next refresh run (the car is
newer than its row). So every error is logged and swallowed here, and nowhere
else.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol
from uuid import UUID

from psycopg import Connection

from ingestion.vehicle_fact_correction_migrations import VEHICLE_FACT_CORRECTIONS_TABLE

logger = logging.getLogger(__name__)


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


class Refresher(Protocol):
    def refresh_vehicles(self, vehicle_ids: Sequence[str]) -> object: ...

    def refresh_scope(self) -> object: ...


class MatchResultSync:
    def __init__(
        self,
        refresher: Callable[[], Refresher],
        connection_factory: ConnectionFactory,
        *,
        inline_limit: int = 5,
        run_in_background: bool = True,
    ) -> None:
        self._refresher = refresher
        self._connection_factory = connection_factory
        self._inline_limit = inline_limit
        self._run_in_background = run_in_background
        self._lock = threading.Lock()
        self._running = False
        self._again = False

    @property
    def refreshing(self) -> bool:
        """A refresh of every changed car is running in this process."""

        with self._lock:
            return self._running

    def population_changed(self) -> bool:
        """Many cars may have changed -- a reviewer rule ran or was retired, or a person asks.

        Every car that is new or changed since its row was written is matched
        again, on a thread. One such refresh runs at a time: a call that arrives
        while one is running makes it run once more when it ends, so nothing
        that changed in between is left behind. Returns False for such a call.
        """

        with self._lock:
            if self._running:
                self._again = True
                return False
            self._running = True
        if self._run_in_background:
            threading.Thread(target=self._refresh_changed, daemon=True).start()
        else:
            self._refresh_changed()
        return True

    def _refresh_changed(self) -> None:
        while True:
            try:
                self._refresher().refresh_scope()
            except Exception:
                logger.exception(
                    "Stored match results were not refreshed after a change to many cars"
                )
            with self._lock:
                if self._again:
                    self._again = False
                    continue
                self._running = False
                return

    def vehicle_changed(self, vehicle_id: str) -> None:
        """One car was corrected or decided: its row is brought up to date before the answer."""

        self._refresh([vehicle_id])

    def decision_changed(self, decision_id: UUID) -> None:
        """A decision was applied to, or taken back from, its cars.

        The cars are the ones whose corrections name the decision. A handful is
        refreshed at once; more are refreshed on a thread so the request that
        saved the decision does not wait for them.
        """

        try:
            with self._connection_factory() as connection:
                rows = connection.execute(
                    f"SELECT DISTINCT vehicle_id FROM {VEHICLE_FACT_CORRECTIONS_TABLE} "
                    "WHERE group_id = %s",
                    (decision_id,),
                ).fetchall()
        except Exception:
            logger.exception("Could not read the cars of a decision for their match results")
            return
        ids = sorted(str(row[0]) for row in rows)
        if len(ids) <= self._inline_limit or not self._run_in_background:
            self._refresh(ids)
        else:
            threading.Thread(target=self._refresh, args=(ids,), daemon=True).start()

    def _refresh(self, vehicle_ids: Sequence[str]) -> None:
        if not vehicle_ids:
            return
        try:
            self._refresher().refresh_vehicles(vehicle_ids)
        except Exception:
            logger.exception(
                "Stored match results were not refreshed for %d cars; the next refresh run "
                "picks them up",
                len(vehicle_ids),
            )
