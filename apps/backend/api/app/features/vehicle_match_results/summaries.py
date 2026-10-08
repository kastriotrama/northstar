"""Counts over every car, answered from a stored copy and counted again behind it.

Counting the cars of a filter by match state reads each of them: at the size of
the full register that is many seconds for the strip above the list and longer
for the breakdown. So a count is kept once made. A look is answered from the
kept copy at once, and when the copy is behind -- cars were matched since, or it
is simply old -- it is counted again in the background and the next look gets
the new numbers. The first look at a filter nobody counted yet counts it there
and then.

A kept copy always covers every car of its filter; only its age differs, and
the answer says how old it is (`counted_at`) and whether a new count is on its
way (`updating`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from api.app.features.vehicles.schemas import VehicleFilter

logger = logging.getLogger(__name__)

SUMMARY_COUNTS = "counts"
SUMMARY_OVERVIEW = "overview"
#: Part of every key: raise it when what a kind stores changes shape, and the
#: copies made before are simply never read again.
SUMMARY_VERSION = 1


@dataclass(frozen=True)
class StoredSummary:
    payload: dict[str, Any]
    data_token: str
    computed_at: datetime
    took_ms: int


@dataclass(frozen=True)
class Summary:
    payload: dict[str, Any]
    counted_at: datetime
    #: The copy is behind and is being counted again, or will be on a next look.
    updating: bool


class SummaryStore(Protocol):
    def read(self, kind: str, filter_key: str) -> StoredSummary | None: ...

    def write(
        self,
        kind: str,
        filter_key: str,
        *,
        vehicle_filter: dict[str, Any],
        payload: dict[str, Any],
        data_token: str,
        took_ms: int,
    ) -> StoredSummary: ...

    def data_token(self) -> str: ...

    def now(self) -> datetime: ...


def canonical_filter(vehicle_filter: VehicleFilter) -> dict[str, Any]:
    """The filter as the counts read it: condition order and outer blanks do not matter."""

    conditions = sorted(
        (condition.model_dump(mode="json") for condition in vehicle_filter.conditions),
        key=lambda item: json.dumps(item, sort_keys=True, default=str),
    )
    return {"conditions": conditions, "text": vehicle_filter.text.strip()}


def filter_key(kind: str, canonical: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"version": SUMMARY_VERSION, "kind": kind, "filter": canonical},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


class StoredSummaries:
    def __init__(
        self,
        store: SummaryStore,
        *,
        min_age: timedelta = timedelta(seconds=30),
        max_age: timedelta = timedelta(minutes=10),
        cost_factor: int = 5,
        run_in_background: bool = True,
    ) -> None:
        self._store = store
        #: A copy younger than this is not counted again, however the data moved.
        self._min_age = min_age
        #: A copy older than this is counted again even if no run was recorded:
        #: rules and imports change cars without one.
        self._max_age = max_age
        #: ... nor one younger than this many times what counting it took, so a
        #: count that takes a minute is not started again every half minute.
        self._cost_factor = cost_factor
        self._run_in_background = run_in_background
        self._lock = threading.Lock()
        self._counting: set[str] = set()
        # One at a time: a count reads every car of its filter.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="match-summary")

    def get(
        self, kind: str, vehicle_filter: VehicleFilter, count: Callable[[], dict[str, Any]]
    ) -> Summary:
        canonical = canonical_filter(vehicle_filter)
        key = filter_key(kind, canonical)
        stored = self._store.read(kind, key)
        if stored is None:
            fresh = self._count(kind, key, canonical, count)
            return Summary(fresh.payload, fresh.computed_at, updating=False)
        age = self._store.now() - stored.computed_at
        behind = age >= self._max_age or stored.data_token != self._store.data_token()
        if not behind:
            return Summary(stored.payload, stored.computed_at, updating=False)
        rest = max(self._min_age, timedelta(milliseconds=stored.took_ms * self._cost_factor))
        if age >= rest:
            if not self._run_in_background:
                fresh = self._count(kind, key, canonical, count)
                return Summary(fresh.payload, fresh.computed_at, updating=False)
            self._count_behind(kind, key, canonical, count)
        return Summary(stored.payload, stored.computed_at, updating=True)

    def recount(
        self, kind: str, vehicle_filter: VehicleFilter, count: Callable[[], dict[str, Any]]
    ) -> Summary:
        """Count now and keep it, whatever is stored: after a load or a long run."""

        canonical = canonical_filter(vehicle_filter)
        fresh = self._count(kind, filter_key(kind, canonical), canonical, count)
        return Summary(fresh.payload, fresh.computed_at, updating=False)

    def _count(
        self, kind: str, key: str, canonical: dict[str, Any], count: Callable[[], dict[str, Any]]
    ) -> StoredSummary:
        # Read before counting: cars matched while the count runs leave the copy behind.
        token = self._store.data_token()
        started = time.perf_counter()
        payload = count()
        took_ms = int((time.perf_counter() - started) * 1000)
        return self._store.write(
            kind, key, vehicle_filter=canonical, payload=payload, data_token=token, took_ms=took_ms
        )

    def _count_behind(
        self, kind: str, key: str, canonical: dict[str, Any], count: Callable[[], dict[str, Any]]
    ) -> None:
        with self._lock:
            if key in self._counting:
                return
            self._counting.add(key)

        def work() -> None:
            try:
                self._count(kind, key, canonical, count)
            except Exception:
                logger.exception("Counting a stored match summary again failed")
            finally:
                with self._lock:
                    self._counting.discard(key)

        self._executor.submit(work)
