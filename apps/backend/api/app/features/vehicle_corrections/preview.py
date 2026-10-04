"""The check of a correction over many cars: what would change, car by car.

Nothing is written. Each car of the scope is read through the matcher's own
seam and sorted into exactly one outcome: left alone because a person already
corrected the field, not like the anchor car after all, unaffected, or
evaluated twice -- as it is, and with the correction laid on top -- by the real
matcher with every guard on.

The check runs on a worker thread of the API process, on a small server: one
check at a time, a bounded number of cars and seconds, stoppable between two
cars. The evaluator is asked not to remember anything (`remember=False`), so a
check cannot grow its memo; what a check keeps itself is one small entry per
car, and one result per distinct matcher input while it runs.

Jobs live in this process's memory, like the summary jobs: a restart loses
them and the person checks again.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import Counter, OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from api.app.features.vehicle_corrections import fields
from api.app.features.vehicle_corrections.schemas import (
    CorrectionPreview,
    EngineCheck,
    MatchState,
    PreviewCar,
    PreviewCarPage,
    PreviewCounts,
    PreviewScopeView,
)
from api.app.features.vehicle_corrections.scope import Scope
from api.app.features.vehicle_matching.impact import engine_agreement, resolution_change
from api.app.features.vehicle_matching.repository import (
    CarRecord,
    Hypothetical,
    lay_hypothetical,
    matcher_input_hash,
)
from api.app.features.vehicle_matching.service import (
    MatchOutcome,
    matcher_inputs,
    outcome_of,
)
from ingestion.match_run_service import MatchSourceRecord
from ingestion.tecdoc.match_run_adapters import MatchEvaluation

#: Every outcome, in the order a result lists them.
OUTCOMES: tuple[str, ...] = (
    "gained", "lost", "moved", "worse", "same", "still_unresolved",
    "no_effect", "already_corrected", "not_like_this",
)
#: The cars a correction would harm: written only when the person includes them.
HARMED: frozenset[str] = frozenset({"lost", "moved", "worse"})
#: The cars an application writes without being asked twice.
WRITABLE: frozenset[str] = frozenset({"gained", "same", "still_unresolved"})

BLOCK_INCOMPLETE = "not_all_cars_checked"
BLOCK_NOTHING = "nothing_to_apply"
BLOCK_HARM = "harms_more_than_it_fixes"
BLOCK_EXPIRED = "preview_expired"

#: A check older than this is not applied: the cars may have moved on.
EXPIRES_AFTER_SECONDS = 30 * 60
#: Checks kept in memory, the running one included.
KEEP = 10
#: Cars read per query. Small, so a lookup made meanwhile waits for one
#: evaluation and one short read at most.
PAGE = 25

# How hard an unresolved car is to bring to a KType: a tie needs a choice, a
# car without a candidate needs data, a hard conflict contradicts the catalog.
# A terminal that is not listed (stopped before matching) ranks with "no candidate".
_HARDNESS: dict[str, int] = {"review_required": 1, "unmatched": 2, "hard_conflict": 3}
_UNLISTED_HARDNESS = 2


class PreviewBusyError(RuntimeError):
    """Another check is running; this server runs one at a time."""


class PreviewNotFoundError(LookupError):
    """No check with that id on this process (a restart forgets them)."""


class CheckMatcher(Protocol):
    """What a check needs of the process's matcher (`vehicle_matching.service.Matcher`)."""

    @property
    def batch_id(self) -> str: ...

    @property
    def catalog(self) -> Mapping[str, Any]: ...

    def evaluate(
        self, record: MatchSourceRecord, *, remember: bool = True
    ) -> tuple[MatchEvaluation, Any]: ...

    def query(self, record: MatchSourceRecord) -> Any: ...

    def key(self, record: MatchSourceRecord) -> tuple[object, ...] | None: ...


class CheckPageLike(Protocol):
    @property
    def cars(self) -> Sequence[CarRecord]: ...

    @property
    def choices(self) -> Mapping[str, tuple[str, str | None]]: ...


def harder(before: str, after: str) -> bool:
    """True when an unresolved car would end on a terminal that is harder to resolve."""

    return _HARDNESS.get(after, _UNLISTED_HARDNESS) > _HARDNESS.get(before, _UNLISTED_HARDNESS)


def classify(before: MatchOutcome, after: MatchOutcome) -> str:
    """Where an evaluated car is sorted, by where the matcher ends before and after.

    `gained`, `lost` and `moved` are the impact report's own definitions
    (`impact.resolution_change`); a car that resolves to the same KType both
    times is `same`, and one that resolves neither time is `worse` when its
    terminal got harder and `still_unresolved` otherwise.
    """

    change = resolution_change(before.terminal, before.ktype, after.terminal, after.ktype)
    if change is not None:
        return change
    if before.terminal == "resolved":
        return "same"
    return "worse" if harder(before.terminal, after.terminal) else "still_unresolved"


@dataclass(frozen=True)
class CheckedCar:
    """One car of a check: its outcome, and what an application needs to write it."""

    vehicle_id: str
    plate: str | None
    outcome: str
    #: Where the matcher ends for the car as it is and with the correction;
    #: None for a car that was not evaluated.
    before: MatchOutcome | None
    after: MatchOutcome | None
    #: sha256 of what the matcher was handed for the car as it is. An
    #: application writes the car only while this still holds.
    input_hash: str
    #: What the matcher uses for the field on this car today, and its source.
    previous_value: str | None
    previous_source: str


@dataclass(frozen=True)
class PreviewPlan:
    """What a check checks: one correction, for the cars of one scope."""

    anchor_vehicle_id: str
    field: str
    action: str
    value: str | None
    scope: Scope
    scope_label: str
    #: What the matcher uses for the field on the anchor car today: every car
    #: of the scope must have the same (both none counts).
    anchor_value: str | None


@dataclass
class PreviewJob:
    preview_id: str
    plan: PreviewPlan
    #: Cars the scope held when the check started, and the most it may check.
    affected: int
    cap: int
    started_at: float
    status: str = "running"
    finished_at: float | None = None
    error: str | None = None
    catalog_batch: str = ""
    stopped_by: str | None = None
    cars: list[CheckedCar] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)
    by_terminal: Counter[str] = field(default_factory=Counter)
    engine: Counter[str] = field(default_factory=Counter)
    with_choice: int = 0
    choice_would_disagree: int = 0
    cancel: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def finish(self, status: str, now: float, *, error: str | None = None) -> None:
        with self.lock:
            self.status = status
            self.error = error
            self.finished_at = now

    @property
    def complete(self) -> bool:
        """Every affected car was checked: no cap, no time-out, not stopped, no failure."""

        return (
            self.status == "done"
            and self.stopped_by is None
            and len(self.cars) == self.affected
        )


class PreviewJobs:
    """The checks this API process holds: at most one running, the last few kept."""

    def __init__(
        self,
        *,
        keep: int = KEEP,
        expires_after: float = EXPIRES_AFTER_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._jobs: OrderedDict[str, PreviewJob] = OrderedDict()
        self._keep = keep
        self._expires_after = expires_after
        self._clock = clock
        self._lock = threading.Lock()

    def now(self) -> float:
        return self._clock()

    def busy(self) -> bool:
        with self._lock:
            return any(job.status == "running" for job in self._jobs.values())

    def create(self, plan: PreviewPlan, *, affected: int, cap: int) -> PreviewJob:
        """Register a check; raises `PreviewBusyError` while another one runs."""

        with self._lock:
            if any(job.status == "running" for job in self._jobs.values()):
                raise PreviewBusyError("Another check is running. Try again in a moment.")
            job = PreviewJob(
                preview_id=uuid.uuid4().hex,
                plan=plan,
                affected=affected,
                cap=cap,
                started_at=self._clock(),
            )
            self._jobs[job.preview_id] = job
            # Forget the oldest finished checks beyond `keep`; never the running one.
            finished = [key for key, item in self._jobs.items() if item.status != "running"]
            for key in finished[: max(0, len(self._jobs) - self._keep)]:
                del self._jobs[key]
            return job

    def get(self, preview_id: str) -> PreviewJob:
        with self._lock:
            job = self._jobs.get(preview_id)
        if job is None:
            raise PreviewNotFoundError("No such check on this server. Check again.")
        return job

    def expired(self, job: PreviewJob) -> bool:
        """True once a finished check is too old to be applied."""

        with job.lock:
            finished_at = job.finished_at
        return finished_at is not None and self._clock() - finished_at > self._expires_after

    # ------------------------------------------------------------------ what a check says

    def blocked_by(self, job: PreviewJob) -> list[str]:
        """Why the check cannot be applied as it stands; empty when it can."""

        with job.lock:
            running = job.status == "running"
            complete = job.complete
            counts = Counter(job.counts)
        reasons: list[str] = []
        if running:
            return [BLOCK_INCOMPLETE]
        if not complete:
            reasons.append(BLOCK_INCOMPLETE)
        if harms_more_than_it_fixes(counts):
            reasons.append(BLOCK_HARM)
        if not any(counts[outcome] for outcome in (*WRITABLE, *HARMED)):
            reasons.append(BLOCK_NOTHING)
        if self.expired(job):
            reasons.append(BLOCK_EXPIRED)
        return reasons

    def snapshot(self, job: PreviewJob) -> CorrectionPreview:
        blocked = self.blocked_by(job)
        with job.lock:
            counts = _counts(job)
            return CorrectionPreview(
                preview_id=job.preview_id,
                status=job.status,  # type: ignore[arg-type]
                field=job.plan.field,
                action=job.plan.action,  # type: ignore[arg-type]
                value=job.plan.value,
                scope=PreviewScopeView(
                    kind=job.plan.scope.kind,
                    label=job.plan.scope_label,
                    rung=job.plan.scope.rung,
                    conditions=list(job.plan.scope.conditions),
                ),
                affected=job.affected,
                cap=job.cap,
                checked=len(job.cars),
                complete=job.complete,
                stopped_by=job.stopped_by,  # type: ignore[arg-type]
                counts=counts,
                engine_check=EngineCheck(
                    agree=job.engine["agree"],
                    differ=job.engine["differ"],
                    unchecked=job.engine["unchecked"],
                ),
                would_write=sum(job.counts[outcome] for outcome in WRITABLE),
                can_apply=not blocked,
                blocked_by=blocked,
                catalog_batch=job.catalog_batch,
                seconds_elapsed=round(
                    (job.finished_at or self._clock()) - job.started_at, 1
                ),
                error=job.error,
            )

    @staticmethod
    def cars(
        job: PreviewJob, outcome: str | None, *, offset: int, limit: int
    ) -> PreviewCarPage:
        """The checked cars of one outcome (or all of them), in the order they were checked."""

        with job.lock:
            rows = [car for car in job.cars if outcome is None or car.outcome == outcome]
        return PreviewCarPage(
            preview_id=job.preview_id,
            outcome=outcome,  # type: ignore[arg-type]
            total=len(rows),
            offset=offset,
            cars=[
                PreviewCar(
                    vehicle_id=car.vehicle_id,
                    plate=car.plate,
                    outcome=car.outcome,  # type: ignore[arg-type]
                    before=_state(car.before),
                    after=_state(car.after),
                )
                for car in rows[offset : offset + limit]
            ],
        )


def _state(outcome: MatchOutcome | None) -> MatchState | None:
    return None if outcome is None else MatchState(terminal=outcome.terminal, ktype=outcome.ktype)


def _counts(job: PreviewJob) -> PreviewCounts:
    return PreviewCounts(
        **{outcome: job.counts[outcome] for outcome in OUTCOMES},
        with_choice=job.with_choice,
        choice_would_disagree=job.choice_would_disagree,
        still_unresolved_by_terminal=dict(sorted(job.by_terminal.items())),
    )


def harms_more_than_it_fixes(counts: Mapping[str, int]) -> bool:
    """True when the check found harmed cars, and at least as many as it fixes."""

    harmed = sum(counts.get(outcome, 0) for outcome in HARMED)
    return harmed > 0 and harmed >= counts.get("gained", 0)


def measurement(job: PreviewJob) -> dict[str, Any]:
    """The check as a decision stores it: every number an event's CHECKs ask for, and the rest."""

    with job.lock:
        counts = _counts(job).model_dump()
        return {
            "preview_id": job.preview_id,
            "affected": job.affected,
            "checked": len(job.cars),
            "cap": job.cap,
            "complete": job.complete,
            "stopped_by": job.stopped_by,
            **counts,
            "engine_check": {
                "agree": job.engine["agree"],
                "differ": job.engine["differ"],
                "unchecked": job.engine["unchecked"],
            },
        }


# ------------------------------------------------------------------------- running a check


def run_check(
    job: PreviewJob,
    vehicle_ids: Sequence[str],
    *,
    read_page: Callable[[Sequence[str]], CheckPageLike],
    matcher: Callable[[], CheckMatcher],
    max_seconds: float,
    clock: Callable[[], float] = time.time,
    page_size: int = PAGE,
) -> None:
    """Check `vehicle_ids` (at most the job's cap of the scope's cars) and settle the job.

    Stops between two cars when asked to (`job.cancel`) or when `max_seconds`
    have passed; what was checked until then is kept. Each distinct matcher
    input is evaluated once and the result reused for every car that shares
    it; that memo is the check's own and is dropped when it ends.
    """

    try:
        engine = matcher()
        with job.lock:
            job.catalog_batch = engine.batch_id
            if job.affected > len(vehicle_ids):
                job.stopped_by = "cap"
        memo: dict[object, MatchOutcome] = {}
        deadline = job.started_at + max_seconds
        for start in range(0, len(vehicle_ids), page_size):
            page = read_page(vehicle_ids[start : start + page_size])
            for car in page.cars:
                if job.cancel.is_set():
                    with job.lock:
                        job.stopped_by = "stopped"
                    job.finish("cancelled", clock())
                    return
                if clock() > deadline:
                    with job.lock:
                        job.stopped_by = "time_limit"
                    job.finish("done", clock())
                    return
                checked = check_car(job.plan, car, engine, memo)
                choice = page.choices.get(str(car.vehicle_id))
                with job.lock:
                    _tally(job, checked, car, choice, engine)
        job.finish("done", clock())
    except Exception as error:  # noqa: BLE001 -- a worker thread has no caller to raise to
        job.finish("failed", clock(), error=type(error).__name__)


def check_car(
    plan: PreviewPlan,
    car: CarRecord,
    matcher: CheckMatcher,
    memo: dict[object, MatchOutcome],
) -> CheckedCar:
    """Sort one car into its outcome; the matcher runs only for a car the correction changes."""

    spec = fields.SPECS[plan.field]
    payload = car.record.payload
    normalized = payload.get("normalized")
    normalized = normalized if isinstance(normalized, dict) else {}
    input_hash = matcher_input_hash(car.record)
    current = spec.current(normalized, matcher_inputs(matcher.query(car.record)))

    def sorted_as(
        outcome: str, before: MatchOutcome | None = None, after: MatchOutcome | None = None
    ) -> CheckedCar:
        return CheckedCar(
            vehicle_id=str(car.vehicle_id),
            plate=car.plate,
            outcome=outcome,
            before=before,
            after=after,
            input_hash=input_hash,
            previous_value=current,
            previous_source=fields.current_source(spec, car.overlaid),
        )

    # A person's own word on the field stands: the car is left as it is.
    if plan.field in car.corrections:
        return sorted_as("already_corrected")
    # The SQL only picked candidates. What the matcher uses today must be the anchor's.
    if not fields.same_present(plan.field, current, plan.anchor_value):
        return sorted_as("not_like_this")
    what_if = lay_hypothetical(
        car,
        Hypothetical(plan.field, "set" if plan.action == "set" else "ignore", plan.value),
    )
    if matcher_input_hash(what_if.record) == input_hash:
        return sorted_as("no_effect")
    before = _evaluate(matcher, car.record, input_hash, memo)
    after = _evaluate(matcher, what_if.record, matcher_input_hash(what_if.record), memo)
    return sorted_as(classify(before, after), before, after)


def _evaluate(
    matcher: CheckMatcher,
    record: MatchSourceRecord,
    input_hash: str,
    memo: dict[object, MatchOutcome],
) -> MatchOutcome:
    """Where the matcher ends for this input: evaluated once per distinct input.

    Two cars the matcher keys alike are guaranteed the same evaluation
    (`evaluation_key`); a car it stops before keying is told apart by the hash
    of everything it was handed.
    """

    key: object = matcher.key(record) or ("input", input_hash)
    found = memo.get(key)
    if found is None:
        evaluation, _ = matcher.evaluate(record, remember=False)
        found = memo[key] = outcome_of(evaluation)
    return found


def _tally(
    job: PreviewJob,
    checked: CheckedCar,
    car: CarRecord,
    choice: tuple[str, str | None] | None,
    matcher: CheckMatcher,
) -> None:
    job.cars.append(checked)
    job.counts[checked.outcome] += 1
    after = checked.after
    if after is None:
        return
    if checked.outcome == "still_unresolved":
        job.by_terminal[after.terminal] += 1
    if choice is not None and choice[0] != "withdraw":
        job.with_choice += 1
        if after.terminal == "resolved" and choice[1] is not None and after.ktype != choice[1]:
            job.choice_would_disagree += 1
    if checked.outcome == "gained":
        job.engine[_engine_check(job.plan, car, after, matcher)] += 1


def _engine_check(
    plan: PreviewPlan, car: CarRecord, after: MatchOutcome, matcher: CheckMatcher
) -> str:
    """The engine proxy for a car that would resolve: `agree`, `differ` or `unchecked`.

    A corrected engine code is not checked against itself: such a car is
    `unchecked`, whatever the KType's engine list says.
    """

    if plan.field == "engine_code":
        return "unchecked"
    normalized = car.record.payload.get("normalized")
    code = normalized.get("engine_code") if isinstance(normalized, dict) else None
    agreement = engine_agreement(
        str(code) if code else None, matcher.catalog.get(str(after.ktype))
    )
    if agreement is None:
        return "unchecked"
    return "agree" if agreement else "differ"
