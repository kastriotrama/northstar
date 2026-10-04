"""The check of a correction over many cars: every outcome, and the job's limits."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from many_cars_test_support import (
    Page,
    ScriptedMatcher,
    by_drive,
    car,
    corrected,
    plan,
    vehicle_id,
)

from api.app.features.vehicle_corrections import preview
from api.app.features.vehicle_corrections.preview import (
    HARMED,
    OUTCOMES,
    WRITABLE,
    PreviewBusyError,
    PreviewJobs,
    PreviewNotFoundError,
    check_car,
    classify,
    harms_more_than_it_fixes,
    measurement,
    run_check,
)
from api.app.features.vehicle_matching.repository import CarRecord, matcher_input_hash
from api.app.features.vehicle_matching.service import MatchOutcome


class _Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _reader(cars: Sequence[CarRecord], choices: dict[str, Any] | None = None,
            pages: list[list[str]] | None = None) -> Any:
    by_id = {str(item.vehicle_id): item for item in cars}

    def read(ids: Sequence[str]) -> Page:
        if pages is not None:
            pages.append(list(ids))
        return Page([by_id[i] for i in ids], choices or {})

    return read


def _checked(cars: Sequence[CarRecord], *, matcher: ScriptedMatcher | None = None,
             choices: dict[str, Any] | None = None, affected: int | None = None,
             cap: int = 500, **planned: Any) -> tuple[preview.PreviewJob, PreviewJobs]:
    jobs = PreviewJobs(clock=_Clock())
    job = jobs.create(plan(**planned), affected=len(cars) if affected is None else affected, cap=cap)
    engine = matcher or ScriptedMatcher()
    run_check(job, [str(item.vehicle_id) for item in cars][:cap], read_page=_reader(cars, choices),
              matcher=lambda: engine, max_seconds=60, clock=jobs.now)
    return job, jobs


# ------------------------------------------------------------------- sorting one car


@pytest.mark.parametrize(
    ("before", "after", "outcome"),
    [
        (("review_required", None), ("resolved", "K1"), "gained"),
        (("unmatched", None), ("resolved", "K1"), "gained"),
        (("resolved", "K1"), ("review_required", None), "lost"),
        (("resolved", "K1"), ("hard_conflict", None), "lost"),
        (("resolved", "K1"), ("resolved", "K2"), "moved"),
        (("resolved", "K1"), ("resolved", "K1"), "same"),
        # Unresolved both times: worse only when the terminal got harder.
        (("review_required", None), ("hard_conflict", None), "worse"),
        (("review_required", None), ("unmatched", None), "worse"),
        (("unmatched", None), ("hard_conflict", None), "worse"),
        (("review_required", None), ("review_required", None), "still_unresolved"),
        (("hard_conflict", None), ("review_required", None), "still_unresolved"),
        (("hard_conflict", None), ("unmatched", None), "still_unresolved"),
        (("normalization_review", None), ("normalization_review", None), "still_unresolved"),
    ],
)
def test_an_evaluated_car_is_sorted_by_where_the_matcher_ends(
    before: tuple[str, str | None], after: tuple[str, str | None], outcome: str
) -> None:
    assert classify(MatchOutcome(*before), MatchOutcome(*after)) == outcome


def test_the_outcomes_an_application_writes_and_the_harmed_ones_never_overlap() -> None:
    assert set(OUTCOMES) == WRITABLE | HARMED | {"no_effect", "already_corrected", "not_like_this"}
    assert not WRITABLE & HARMED
    assert harms_more_than_it_fixes({"gained": 2, "lost": 1, "worse": 1})
    assert not harms_more_than_it_fixes({"gained": 3, "lost": 1, "moved": 1})
    # A check that harms nobody is never blocked for it, even when it fixes nobody.
    assert not harms_more_than_it_fixes({"gained": 0, "same": 4})


def test_every_car_gets_exactly_one_outcome_and_only_a_changed_car_is_evaluated() -> None:
    matcher = ScriptedMatcher()
    memo: dict[object, MatchOutcome] = {}
    setting = plan(value="fwd")

    def sort(item: CarRecord) -> preview.CheckedCar:
        return check_car(setting, item, matcher, memo)

    gained = sort(car(1))
    assert (gained.outcome, gained.before, gained.after) == (
        "gained", ("review_required", None), ("resolved", "K1"))
    assert (gained.vehicle_id, gained.plate) == (vehicle_id(1), "TST001")
    # What an application needs: the input it was checked on, and what it replaces.
    assert gained.input_hash == matcher_input_hash(car(1).record)
    assert (gained.previous_value, gained.previous_source) == (None, "registry")

    # A person's own correction of the field stands, whatever it says.
    for head in (corrected("set", "awd"), corrected("ignore")):
        own = sort(car(2, corrections={"drive_type": head}, drive_type="awd"))
        assert (own.outcome, own.before, own.after) == ("already_corrected", None, None)
    # Another field's correction does not make it one.
    assert sort(car(3, corrections={"power_kw": corrected("set", "120")})).outcome == "gained"
    # The SQL only picked candidates: this car has a drive type the matcher uses.
    other = sort(car(4, drive_type="rwd", overlaid={"drive_type": "ais"}))
    assert (other.outcome, other.previous_value, other.previous_source) == (
        "not_like_this", "rwd", "ais")
    evaluations = len(matcher.evaluated)
    assert sort(car(4, drive_type="fwd")).outcome == "not_like_this"
    assert len(matcher.evaluated) == evaluations  # neither was evaluated

    # A stopped car is evaluated; it ends where it was.
    stopped = sort(car(5, stopped=True))
    assert (stopped.outcome, stopped.after) == ("still_unresolved", ("normalization_review", None))


def test_a_car_whose_matcher_input_does_not_change_has_no_effect() -> None:
    matcher = ScriptedMatcher()
    # Ignoring the drive type of cars that have none: the anchor's is none too.
    ignoring = plan(action="ignore", anchor_value=None)

    unchanged = check_car(ignoring, car(1), matcher, {})

    assert (unchanged.outcome, unchanged.before, unchanged.after) == ("no_effect", None, None)
    assert matcher.evaluated == []


def test_an_ignore_takes_the_value_away_from_the_matcher() -> None:
    matcher = ScriptedMatcher()
    ignoring = plan(action="ignore", anchor_value="fwd")

    lost = check_car(ignoring, car(1, drive_type="fwd"), matcher, {})

    assert (lost.outcome, lost.before, lost.after) == (
        "lost", ("resolved", "K1"), ("review_required", None))
    assert lost.previous_value == "fwd"


# -------------------------------------------------------------------- a whole check


def test_a_check_counts_every_car_once_and_keeps_the_list_complete() -> None:
    def rule(values: Any) -> tuple[str, str | None]:
        if values.get("power_kw") == 150:  # resolved already; the drive type moves it
            return ("resolved", "K2" if values.get("drive_type") else "K3")
        if values.get("power_kw") == 110:  # a tie that becomes a conflict
            return ("hard_conflict", None) if values.get("drive_type") else ("review_required", None)
        if values.get("power_kw") == 90:  # resolved, and the drive type takes it away
            return ("review_required", None) if values.get("drive_type") else ("resolved", "K1")
        if values.get("power_kw") == 70:  # resolved either way
            return ("resolved", "K1")
        if values.get("power_kw") == 50:  # a tie either way
            return ("review_required", None)
        return by_drive(values)

    cars = [
        car(1), car(2), car(3, engine_code="D5244T"), car(4, engine_code=None),
        car(5, power_kw=150), car(6, power_kw=110), car(7, power_kw=90), car(8, power_kw=70),
        car(9, power_kw=50), car(10, stopped=True),
        car(11, corrections={"drive_type": corrected()}, drive_type="awd"),
        car(12, drive_type="rwd"),
    ]
    choices = {
        vehicle_id(1): ("choose", "K1"),   # the matcher would agree
        vehicle_id(3): ("choose", "K2"),   # ... and here resolve to K1 instead
        vehicle_id(9): ("none", None),     # "none of these": a choice too
        vehicle_id(2): ("withdraw", None),  # a withdrawn choice is no choice
        vehicle_id(12): ("choose", "K1"),  # not evaluated: not counted
    }

    job, jobs = _checked(cars, matcher=ScriptedMatcher(rule), choices=choices)
    result = jobs.snapshot(job)

    assert (result.status, result.affected, result.checked, result.complete, result.stopped_by) == (
        "done", 12, 12, True, None)
    counts = result.counts.model_dump()
    assert {name: counts[name] for name in OUTCOMES} == {
        "gained": 4, "lost": 1, "moved": 1, "worse": 1, "same": 1, "still_unresolved": 2,
        "no_effect": 0, "already_corrected": 1, "not_like_this": 1,
    }
    assert sum(counts[name] for name in OUTCOMES) == result.checked
    assert counts["still_unresolved_by_terminal"] == {
        "normalization_review": 1, "review_required": 1}
    assert (counts["with_choice"], counts["choice_would_disagree"]) == (3, 1)
    # The engine proxy on the four gained cars: K1 lists B4204T.
    assert result.engine_check.model_dump() == {"agree": 2, "differ": 1, "unchecked": 1}
    # Harmed cars are left out of what an application writes by default.
    assert (result.would_write, result.can_apply, result.blocked_by) == (7, True, [])
    assert (result.field, result.action, result.value) == ("drive_type", "set", "fwd")
    assert (result.scope.kind, result.scope.label) == (
        "like_this", "All VOLVO V70 cars with no drive type")
    assert result.catalog_batch == "batch-1"

    every = jobs.cars(job, None, offset=0, limit=500)
    assert [item.vehicle_id for item in every.cars] == [vehicle_id(n) for n in range(1, 13)]
    moved = jobs.cars(job, "moved", offset=0, limit=10)
    assert (moved.total, moved.outcome) == (1, "moved")
    assert moved.cars[0].model_dump() == {
        "vehicle_id": vehicle_id(5), "plate": "TST005", "outcome": "moved",
        "before": {"terminal": "resolved", "ktype": "K3"},
        "after": {"terminal": "resolved", "ktype": "K2"},
    }
    left = jobs.cars(job, "already_corrected", offset=0, limit=10).cars[0]
    assert (left.before, left.after) == (None, None)
    paged = jobs.cars(job, "gained", offset=3, limit=2)
    assert (paged.total, paged.offset, [item.plate for item in paged.cars]) == (4, 3, ["TST004"])

    stored = measurement(job)
    assert stored["complete"] is True and stored["preview_id"] == job.preview_id
    assert {name: stored[name] for name in ("affected", "checked", "gained", "lost", "moved", "worse")} == {
        "affected": 12, "checked": 12, "gained": 4, "lost": 1, "moved": 1, "worse": 1}


def test_each_distinct_matcher_input_is_evaluated_once_and_nothing_is_remembered() -> None:
    cars = [car(number) for number in range(1, 41)]
    matcher = ScriptedMatcher()

    job, jobs = _checked(cars, matcher=matcher)

    # Forty cars the matcher keys alike: one evaluation as they are, one with
    # the correction -- and the per-car list is still complete.
    assert len(matcher.evaluated) == 2
    assert all(remember is False for _, remember in matcher.evaluated)
    result = jobs.snapshot(job)
    assert (result.checked, result.counts.gained) == (40, 40)
    assert {item.outcome for item in job.cars} == {"gained"}
    assert len({item.input_hash for item in job.cars}) == 40  # each car's own hash is kept

    # Cars the matcher stops before keying share nothing: each is evaluated.
    stopped = ScriptedMatcher()
    _checked([car(1, stopped=True), car(2, stopped=True)], matcher=stopped)
    assert len(stopped.evaluated) == 4


def test_a_corrected_engine_code_is_never_checked_against_itself() -> None:
    def rule(values: Any) -> tuple[str, str | None]:
        return ("resolved", "K2") if values.get("engine_code") == "D5244T" else ("review_required", None)

    cars = [car(1), car(2), car(3)]

    job, jobs = _checked(cars, matcher=ScriptedMatcher(rule), field_name="engine_code",
                         value="D5244T", anchor_value="B4204T")
    result = jobs.snapshot(job)

    # K2 lists D5244T: the proxy would agree on every car, with the correction itself.
    assert result.counts.gained == 3
    assert result.engine_check.model_dump() == {"agree": 0, "differ": 0, "unchecked": 3}


def test_a_check_stops_at_the_cap_and_cannot_be_applied() -> None:
    cars = [car(number) for number in range(1, 6)]

    job, jobs = _checked(cars, affected=4275, cap=5)
    result = jobs.snapshot(job)

    assert (result.status, result.affected, result.cap, result.checked) == ("done", 4275, 5, 5)
    assert (result.complete, result.stopped_by) == (False, "cap")
    assert (result.can_apply, result.blocked_by) == (False, ["not_all_cars_checked"])
    assert measurement(job)["complete"] is False


def test_a_check_stops_at_the_time_limit_and_keeps_what_it_found() -> None:
    clock = _Clock()
    jobs = PreviewJobs(clock=clock)
    cars = [car(number) for number in range(1, 8)]
    job = jobs.create(plan(), affected=7, cap=500)
    pages: list[list[str]] = []

    class _Slow(ScriptedMatcher):
        def evaluate(self, record: Any, *, remember: bool = True) -> Any:
            clock.now += 20  # each evaluation takes twenty seconds
            return super().evaluate(record, remember=remember)

    matcher = _Slow(lambda values: ("review_required", None) if "x" in values else by_drive(values))
    # Give every car its own matcher input, so each is evaluated.
    cars = [car(number, power_kw=100 + number) for number in range(1, 8)]
    run_check(job, [str(item.vehicle_id) for item in cars], read_page=_reader(cars, pages=pages),
              matcher=lambda: matcher, max_seconds=90, clock=clock, page_size=2)
    result = jobs.snapshot(job)

    assert (result.status, result.stopped_by, result.complete) == ("done", "time_limit", False)
    assert 0 < result.checked < 7
    assert result.blocked_by == ["not_all_cars_checked"]
    assert all(len(page) <= 2 for page in pages)  # small pages: lookups stay responsive
    assert result.seconds_elapsed >= 90


def test_a_check_can_be_stopped_between_two_cars() -> None:
    jobs = PreviewJobs(clock=_Clock())
    cars = [car(number, power_kw=100 + number) for number in range(1, 6)]
    job = jobs.create(plan(), affected=5, cap=500)

    class _Stopping(ScriptedMatcher):
        def evaluate(self, record: Any, *, remember: bool = True) -> Any:
            if len(self.evaluated) == 3:
                job.cancel.set()
            return super().evaluate(record, remember=remember)

    run_check(job, [str(item.vehicle_id) for item in cars], read_page=_reader(cars),
              matcher=lambda: _Stopping(), max_seconds=60, clock=jobs.now)
    result = jobs.snapshot(job)

    assert (result.status, result.stopped_by, result.complete, result.checked) == (
        "cancelled", "stopped", False, 2)
    assert not jobs.busy()


def test_a_failing_check_says_so_without_the_details() -> None:
    jobs = PreviewJobs(clock=_Clock())
    job = jobs.create(plan(), affected=1, cap=500)

    def broken(ids: Sequence[str]) -> Page:
        raise RuntimeError("connection to 10.0.0.5 refused")

    run_check(job, [vehicle_id(1)], read_page=broken, matcher=lambda: ScriptedMatcher(),
              max_seconds=60, clock=jobs.now)
    result = jobs.snapshot(job)

    assert (result.status, result.error, result.complete, result.can_apply) == (
        "failed", "RuntimeError", False, False)


# ------------------------------------------------------------------------ the jobs


def test_one_check_at_a_time_and_the_last_ten_are_kept() -> None:
    clock = _Clock()
    jobs = PreviewJobs(clock=clock)
    first = jobs.create(plan(), affected=1, cap=500)

    assert jobs.busy()
    with pytest.raises(PreviewBusyError):
        jobs.create(plan(), affected=1, cap=500)
    first.finish("done", clock())
    assert not jobs.busy()

    later = []
    for _ in range(12):
        job = jobs.create(plan(), affected=0, cap=500)
        job.finish("done", clock())
        later.append(job)
    assert jobs.get(later[-1].preview_id) is later[-1]
    assert [jobs.get(job.preview_id) for job in later[-9:]] == later[-9:]
    for forgotten in (first, later[0]):
        with pytest.raises(PreviewNotFoundError):
            jobs.get(forgotten.preview_id)
    with pytest.raises(PreviewNotFoundError):
        jobs.get("nope")


def test_a_check_expires_thirty_minutes_after_it_ended() -> None:
    clock = _Clock()
    jobs = PreviewJobs(clock=clock)
    job = jobs.create(plan(), affected=1, cap=500)
    run_check(job, [vehicle_id(1)], read_page=_reader([car(1)]),
              matcher=lambda: ScriptedMatcher(), max_seconds=60, clock=clock)

    clock.now += 30 * 60
    assert not jobs.expired(job) and jobs.snapshot(job).can_apply
    clock.now += 1
    assert jobs.expired(job)
    result = jobs.snapshot(job)
    assert (result.can_apply, result.blocked_by) == (False, ["preview_expired"])
    # A running check is not expired: it is bounded by its own time limit.
    running = jobs.create(plan(), affected=1, cap=500)
    clock.now += 10_000
    assert not jobs.expired(running)


def test_what_blocks_an_application_is_named() -> None:
    # Nothing an application could write.
    job, jobs = _checked([car(1, drive_type="rwd")])
    assert jobs.snapshot(job).blocked_by == ["nothing_to_apply"]
    # As many harmed cars as fixed ones: not from the panel, whatever is ticked.
    harmed, harmed_jobs = _checked(
        [car(1), car(2, power_kw=90)],
        matcher=ScriptedMatcher(
            lambda values: (("review_required", None) if values.get("drive_type") else ("resolved", "K1"))
            if values.get("power_kw") == 90 else by_drive(values)
        ),
    )
    result = harmed_jobs.snapshot(harmed)
    assert (result.counts.gained, result.counts.lost) == (1, 1)
    assert (result.can_apply, result.blocked_by) == (False, ["harms_more_than_it_fixes"])
    # While it runs, the only thing to say is that it has not covered every car.
    jobs = PreviewJobs(clock=_Clock())
    assert jobs.snapshot(jobs.create(plan(), affected=3, cap=500)).blocked_by == [
        "not_all_cars_checked"]
