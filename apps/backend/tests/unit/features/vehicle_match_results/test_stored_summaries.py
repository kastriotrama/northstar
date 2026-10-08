"""A count over many cars is answered from a kept copy and counted again behind it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from api.app.features.vehicle_match_results.summaries import (
    SUMMARY_COUNTS,
    SUMMARY_OVERVIEW,
    StoredSummaries,
    StoredSummary,
    canonical_filter,
    filter_key,
)
from api.app.features.vehicles.schemas import VehicleCondition, VehicleFilter

START = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


class _Store:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], StoredSummary] = {}
        self.filters: dict[tuple[str, str], dict[str, Any]] = {}
        self.token = "runs-1"
        self.clock = START

    def read(self, kind: str, filter_key: str) -> StoredSummary | None:
        return self.rows.get((kind, filter_key))

    def write(
        self, kind: str, filter_key: str, *, vehicle_filter: dict[str, Any],
        payload: dict[str, Any], data_token: str, took_ms: int,
    ) -> StoredSummary:
        stored = StoredSummary(payload, data_token, self.clock, took_ms)
        self.rows[(kind, filter_key)] = stored
        self.filters[(kind, filter_key)] = vehicle_filter
        return stored

    def data_token(self) -> str:
        return self.token

    def now(self) -> datetime:
        return self.clock


class _Counter:
    """Counts the cars: each call is one read of every car of the filter."""

    def __init__(self, store: _Store | None = None) -> None:
        self.calls = 0
        self.cars = 100
        self._store = store

    def __call__(self) -> dict[str, Any]:
        self.calls += 1
        return {"total": self.cars}


def _summaries(store: _Store, **options: Any) -> StoredSummaries:
    return StoredSummaries(store, run_in_background=False, **options)


def test_the_first_look_counts_and_the_next_reads_the_kept_copy() -> None:
    store, count = _Store(), _Counter()
    summaries = _summaries(store)

    first = summaries.get(SUMMARY_COUNTS, VehicleFilter(), count)
    store.clock += timedelta(minutes=3)
    second = summaries.get(SUMMARY_COUNTS, VehicleFilter(), count)

    assert first.payload == second.payload == {"total": 100}
    assert count.calls == 1
    assert second.counted_at == START and second.updating is False


def test_cars_matched_since_the_count_have_it_counted_again() -> None:
    store, count = _Store(), _Counter()
    summaries = _summaries(store)
    summaries.get(SUMMARY_COUNTS, VehicleFilter(), count)

    store.token, count.cars = "runs-2", 120
    store.clock += timedelta(minutes=1)
    after = summaries.get(SUMMARY_COUNTS, VehicleFilter(), count)

    assert after.payload == {"total": 120}
    assert count.calls == 2


def test_a_copy_just_counted_is_not_counted_again_but_says_it_is_behind() -> None:
    store, count = _Store(), _Counter()
    summaries = _summaries(store, min_age=timedelta(seconds=30))
    summaries.get(SUMMARY_COUNTS, VehicleFilter(), count)

    store.token, count.cars = "runs-2", 120
    store.clock += timedelta(seconds=5)
    soon = summaries.get(SUMMARY_COUNTS, VehicleFilter(), count)

    assert soon.payload == {"total": 100} and soon.updating is True
    assert count.calls == 1


def test_a_slow_count_rests_longer_before_it_runs_again() -> None:
    store, count = _Store(), _Counter()
    summaries = _summaries(store, min_age=timedelta(seconds=30), cost_factor=5)
    summaries.get(SUMMARY_OVERVIEW, VehicleFilter(), count)
    key = next(iter(store.rows))
    kept = store.rows[key]
    # Counting took a minute: five minutes pass before it is counted again.
    store.rows[key] = StoredSummary(kept.payload, kept.data_token, kept.computed_at, 60_000)
    store.token = "runs-2"

    store.clock += timedelta(minutes=4)
    assert summaries.get(SUMMARY_OVERVIEW, VehicleFilter(), count).updating is True
    assert count.calls == 1
    store.clock += timedelta(minutes=2)
    assert summaries.get(SUMMARY_OVERVIEW, VehicleFilter(), count).updating is False
    assert count.calls == 2


def test_an_old_copy_is_counted_again_even_when_no_run_was_recorded() -> None:
    store, count = _Store(), _Counter()
    summaries = _summaries(store, max_age=timedelta(minutes=10))
    summaries.get(SUMMARY_COUNTS, VehicleFilter(), count)

    store.clock += timedelta(minutes=11)
    summaries.get(SUMMARY_COUNTS, VehicleFilter(), count)

    assert count.calls == 2


def test_each_filter_and_each_kind_keeps_its_own_count() -> None:
    store, count = _Store(), _Counter()
    summaries = _summaries(store)
    diesel = VehicleFilter(conditions=[VehicleCondition(field="fuel", values=["diesel"])])

    summaries.get(SUMMARY_COUNTS, VehicleFilter(), count)
    summaries.get(SUMMARY_COUNTS, diesel, count)
    summaries.get(SUMMARY_OVERVIEW, VehicleFilter(), count)
    summaries.get(SUMMARY_COUNTS, VehicleFilter(text="  "), count)

    assert count.calls == 3
    assert len(store.rows) == 3


def test_the_key_ignores_the_order_of_conditions_and_outer_blanks() -> None:
    fuel = VehicleCondition(field="fuel", values=["diesel"])
    make = VehicleCondition(field="manufacturer", values=["Volvo"])
    one = canonical_filter(VehicleFilter(conditions=[fuel, make], text=" v70 "))
    other = canonical_filter(VehicleFilter(conditions=[make, fuel], text="v70"))

    assert one == other
    assert filter_key(SUMMARY_COUNTS, one) == filter_key(SUMMARY_COUNTS, other)
    assert filter_key(SUMMARY_COUNTS, one) != filter_key(SUMMARY_OVERVIEW, one)


def test_recount_replaces_the_copy_whatever_its_age() -> None:
    store, count = _Store(), _Counter()
    summaries = _summaries(store)
    summaries.get(SUMMARY_COUNTS, VehicleFilter(), count)

    count.cars = 130
    fresh = summaries.recount(SUMMARY_COUNTS, VehicleFilter(), count)

    assert fresh.payload == {"total": 130}
    assert summaries.get(SUMMARY_COUNTS, VehicleFilter(), count).payload == {"total": 130}
    assert count.calls == 2


def test_a_count_behind_an_answer_runs_once_and_its_failure_keeps_the_copy() -> None:
    store, count = _Store(), _Counter()
    summaries = StoredSummaries(store, run_in_background=True, min_age=timedelta(0))
    summaries.get(SUMMARY_COUNTS, VehicleFilter(), count)
    store.token = "runs-2"
    store.clock += timedelta(seconds=1)

    def failing() -> dict[str, Any]:
        raise RuntimeError("database went away")

    answer = summaries.get(SUMMARY_COUNTS, VehicleFilter(), failing)
    summaries._executor.shutdown(wait=True)

    assert answer.payload == {"total": 100} and answer.updating is True
    assert next(iter(store.rows.values())).payload == {"total": 100}
    assert summaries._counting == set()
