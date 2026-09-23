"""Run the real TS-to-TecDoc matcher over cars from the Vehicles tab, and explain it.

Nothing here reimplements matching. Every outcome comes from
`TecDocDryRunEvaluator` -- the evaluator the audit CLI runs -- built from the
same catalog, rules and vocabulary alignments. This module only reads the
evaluation back in terms a reviewer can act on: how many KTypes a car could be,
and which field stands between it and exactly one.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import Counter, OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from api.app.features.vehicle_matching.repository import (
    CarRecord,
    VehicleMatchingRepository,
)
from api.app.features.vehicle_matching.schemas import (
    FieldCount,
    KTypeCandidate,
    MatchBucket,
    MatcherInputs,
    MatchExample,
    MatchSummary,
    MatchSummaryJob,
    ReasonCount,
    VehicleMatchLookup,
)
from ingestion.fuzzy_matching import FuzzyMatchConfig, VehicleCandidate
from ingestion.match_run_service import MatchSourceRecord
from ingestion.tecdoc.match_run_adapters import (
    MatchEvaluation,
    ResolvedMatchQuery,
    TecDocDryRunEvaluator,
)
from ingestion.tecdoc.model_aliases import ReviewedModelAliasIndex

#: The matcher's own cap on returned candidates. A car showing this many
#: compatible KTypes may have more.
CANDIDATE_LIMIT = FuzzyMatchConfig().max_candidates

_EXAMPLES_PER_BUCKET = 5
_BUCKETS: tuple[MatchBucket, ...] = ("one", "several", "none", "not_matchable")


class VehicleNotFoundError(LookupError):
    """No car in the projection carries that plate or VIN."""


class NoCatalogError(RuntimeError):
    """No TecDoc candidate catalog has been promoted into this database."""


@dataclass(frozen=True)
class Matcher:
    batch_id: str
    evaluator: TecDocDryRunEvaluator
    catalog: dict[str, VehicleCandidate]
    # The evaluator memoizes into a plain dict and was written for one caller. A
    # lookup and a running summary job share it, so each evaluation holds the lock
    # -- per car, not per job, so a lookup waits at most one evaluation.
    lock: threading.Lock = field(default_factory=threading.Lock, compare=False, repr=False)

    def evaluate(
        self, record: MatchSourceRecord
    ) -> tuple[MatchEvaluation, ResolvedMatchQuery | None]:
        with self.lock:
            return self.evaluator.evaluate(record), self.evaluator.resolved_query(record)


def build_matcher(repository: VehicleMatchingRepository, batch_id: str | None) -> Matcher:
    """The audit's evaluator, built the way `match-ts-tecdoc` builds it."""

    resolved_batch = batch_id or repository.latest_catalog_batch()
    if not resolved_batch:
        raise NoCatalogError("No TecDoc candidate catalog batch exists in this database.")
    sources = repository.matcher_sources(resolved_batch)
    evaluator = TecDocDryRunEvaluator(
        sources.catalog,
        sources.manufacturer_rules,
        ReviewedModelAliasIndex(sources.rule_set),
        fuel_alignment=sources.fuel_alignment,
        drive_alignment=sources.drive_alignment,
    )
    return Matcher(
        batch_id=resolved_batch,
        evaluator=evaluator,
        catalog={candidate.candidate_reference: candidate for candidate in sources.catalog},
    )


class MatcherCache:
    """One matcher per process: loading 60k+ KTypes costs seconds, evaluating does not.

    Built lazily under a lock so two first requests do not both pay for it. The
    evaluator memoizes per matcher key, so repeat queries get faster, and that
    memo grows with distinct cars evaluated -- restart the API to release it.
    """

    def __init__(self, factory: Callable[[], Matcher]) -> None:
        self._factory = factory
        self._matcher: Matcher | None = None
        self._lock = threading.Lock()

    def get(self) -> Matcher:
        if self._matcher is None:
            with self._lock:
                if self._matcher is None:
                    self._matcher = self._factory()
        return self._matcher


# ------------------------------------------------------------------ reading an evaluation


def _evidence(candidate: dict[str, Any]) -> dict[str, Any]:
    evidence = candidate.get("evidence")
    return dict(evidence) if isinstance(evidence, dict) else {}


def _is_compatible(candidate: dict[str, Any]) -> bool:
    return not _evidence(candidate).get("conflicting_fields")


def _matcher_ran(evaluation: MatchEvaluation) -> bool:
    """True when the car was scored, as opposed to stopped before matching."""

    return any(reason.startswith("match:") for reason in evaluation.reason_codes)


def bucket_for(evaluation: MatchEvaluation) -> MatchBucket:
    if not _matcher_ran(evaluation):
        return "not_matchable"
    compatible = sum(1 for candidate in evaluation.candidate_matches if _is_compatible(candidate))
    if compatible == 0:
        return "none"
    return "one" if compatible == 1 else "several"


# Comparable value of each field the matcher scores, as each candidate carries it.
_CANDIDATE_FIELDS: dict[str, Callable[[VehicleCandidate], object]] = {
    "model": lambda candidate: candidate.model,
    "year": lambda candidate: (candidate.year_from, candidate.year_to),
    "fuels": lambda candidate: candidate.fuels,
    "engine_code": lambda candidate: candidate.engine_codes,
    "displacement_cc": lambda candidate: candidate.displacement_cc,
    "power_kw": lambda candidate: candidate.power_kw,
    "drive_type": lambda candidate: candidate.drive_type,
    "bodywork": lambda candidate: candidate.bodyworks,
}


def separating_fields(
    evaluation: MatchEvaluation, catalog: dict[str, VehicleCandidate]
) -> list[str]:
    """Fields whose values differ among the compatible candidates.

    Only meaningful with two or more; each is a field that, known for this car,
    could leave a single KType standing.
    """

    compatible = [
        catalog[str(candidate["candidate_reference"])]
        for candidate in evaluation.candidate_matches
        if _is_compatible(candidate) and str(candidate["candidate_reference"]) in catalog
    ]
    if len(compatible) < 2:
        return []
    return [
        field
        for field, value in _CANDIDATE_FIELDS.items()
        if len({_hashable(value(candidate)) for candidate in compatible}) > 1
    ]


def _hashable(value: object) -> object:
    if isinstance(value, frozenset | set):
        return tuple(sorted(str(item) for item in value))
    return value


def missing_on_car(fields: Sequence[str], query: ResolvedMatchQuery | None) -> list[str]:
    """Of these fields, the ones the matcher had no value for on this car."""

    if query is None:
        return []
    present = {
        "model": bool(query.model_values),
        "year": query.year is not None,
        "fuels": bool(query.fuels),
        "engine_code": query.engine_code is not None,
        "displacement_cc": query.displacement_cc is not None,
        "power_kw": query.power_kw is not None,
        "drive_type": query.drive_type is not None,
        "bodywork": query.bodywork is not None,
    }
    return [field for field in fields if not present.get(field, True)]


def _inputs(query: ResolvedMatchQuery | None) -> MatcherInputs | None:
    if query is None:
        return None
    return MatcherInputs(
        manufacturer=query.scope_manufacturer,
        model_values=list(query.model_values),
        production_year=query.year,
        fuels=sorted(query.fuels),
        engine_code=query.engine_code,
        displacement_cc=query.displacement_cc,
        power_kw=query.power_kw,
        drive_type=query.drive_type,
        bodywork_form=query.bodywork,
        model_recovered_from=query.recovery_reason,
    )


def _candidate(candidate: dict[str, Any], catalog: dict[str, VehicleCandidate]) -> KTypeCandidate:
    reference = str(candidate["candidate_reference"])
    evidence = _evidence(candidate)
    known = catalog.get(reference)
    return KTypeCandidate(
        ktype=reference,
        candidate_only=str(candidate.get("candidate_type")) == "TecDocKTypeCandidateOnly",
        confidence=float(candidate.get("confidence") or 0.0),
        manufacturer=known.manufacturer if known else str(evidence.get("manufacturer") or ""),
        model=known.model if known else str(evidence.get("model") or ""),
        year_from=known.year_from if known else None,
        year_to=known.year_to if known else None,
        fuels=sorted(known.fuels) if known else [],
        engine_codes=sorted(known.engine_codes) if known else [],
        displacement_cc=known.displacement_cc if known else None,
        power_kw=known.power_kw if known else None,
        drive_type=known.drive_type if known else None,
        bodyworks=sorted(known.bodyworks) if known else [],
        matched_fields=[str(item) for item in evidence.get("matched_fields") or []],
        missing_fields=[str(item) for item in evidence.get("missing_fields") or []],
        conflicting_fields=[str(item) for item in evidence.get("conflicting_fields") or []],
        compatible=_is_compatible(candidate),
    )


# ------------------------------------------------------------------------------- service


class JobCapacityError(RuntimeError):
    """Too many summaries are already running on this process."""


class SummaryJobNotFoundError(LookupError):
    """No summary job with that id on this process."""


class VehicleMatchingService:
    def __init__(
        self,
        repository: VehicleMatchingRepository,
        matcher: Callable[[], Matcher],
        jobs: SummaryJobs,
    ) -> None:
        self._repository = repository
        self._matcher = matcher
        self._jobs = jobs

    def lookup(self, identifier: str) -> VehicleMatchLookup:
        key = "".join(identifier.split()).upper()
        if not key:
            raise VehicleNotFoundError("Enter a plate or a VIN.")
        ids = self._repository.ids_for_identifier(key)
        records = self._repository.car_records(ids[:1])
        if not records:
            raise VehicleNotFoundError(f"No vehicle with plate or VIN {key!r}.")
        return self._explain(records[0], other_ids=ids[1:])

    def lookup_record(self, source_record_id: int) -> VehicleMatchLookup:
        """One exact record -- what a screen that already has the row asks for.

        A plate can carry several records; the record panel must explain the
        one it is showing, not whichever is newest.
        """

        records = self._repository.car_records([source_record_id])
        if not records:
            raise VehicleNotFoundError(f"No vehicle with record id {source_record_id}.")
        return self._explain(records[0], other_ids=[])

    def _explain(self, car: CarRecord, *, other_ids: Sequence[int]) -> VehicleMatchLookup:
        matcher = self._matcher()
        evaluation, query = matcher.evaluate(car.record)
        separating = separating_fields(evaluation, matcher.catalog)
        return VehicleMatchLookup(
            source_record_id=car.source_record_id,
            plate=car.plate,
            vin=car.vin,
            catalog_batch=matcher.batch_id,
            terminal=evaluation.terminal,
            bucket=bucket_for(evaluation),
            confidence=evaluation.confidence,
            top_ktype=evaluation.top_candidate_reference,
            reason_codes=list(evaluation.reason_codes),
            rule_filled=list(car.rule_filled),
            inputs=_inputs(query),
            candidates=[
                _candidate(candidate, matcher.catalog)
                for candidate in evaluation.candidate_matches
            ],
            candidate_limit=CANDIDATE_LIMIT,
            separating_fields=separating,
            missing_separating_fields=missing_on_car(separating, query),
            decision_trace=[dict(entry) for entry in evaluation.decision_trace],
            other_source_record_ids=list(other_ids),
        )

    def start_summary(
        self,
        conditions: Sequence[Any],
        text: str,
        limit: int,
        *,
        run_in_background: bool = True,
    ) -> MatchSummaryJob:
        """Pick the cars now, evaluate them on a worker thread.

        The population query runs here so that a malformed filter fails the
        request itself rather than a job nobody is watching. Evaluation is the
        slow part -- the production matcher spends ~0.1s a car, over a second
        for a manufacturer with thousands of KTypes -- so it never blocks a request.
        """

        population, ids = self._repository.population(conditions, text, limit=limit)
        job = self._jobs.create(population=population, target=len(ids))
        if run_in_background:
            threading.Thread(target=self._run, args=(job, ids), daemon=True).start()
        else:
            self._run(job, ids)
        return self._jobs.snapshot(job)

    def summary_job(self, job_id: str) -> MatchSummaryJob:
        return self._jobs.snapshot(self._jobs.get(job_id))

    def cancel_summary(self, job_id: str) -> MatchSummaryJob:
        job = self._jobs.get(job_id)
        job.cancel.set()
        return self._jobs.snapshot(job)

    def _run(self, job: SummaryJob, ids: Sequence[int]) -> None:
        try:
            matcher = self._matcher()
            job.catalog_batch = matcher.batch_id
            # Pages keep each read to a bounded IN-list, whatever the target is.
            for start in range(0, len(ids), 200):
                for car in self._repository.car_records(ids[start : start + 200]):
                    if job.cancel.is_set():
                        job.finish("cancelled")
                        return
                    evaluation, query = matcher.evaluate(car.record)
                    with job.lock:
                        job.tally.add(car, evaluation, query, matcher.catalog)
            job.finish("done")
        except Exception as error:  # noqa: BLE001 -- a worker thread has no caller to raise to
            job.finish("failed", error=f"{type(error).__name__}: {error}")


@dataclass
class SummaryJob:
    job_id: str
    population: int
    target: int
    started_at: float = field(default_factory=time.time)
    status: str = "running"
    finished_at: float | None = None
    error: str | None = None
    catalog_batch: str | None = None
    tally: _Tally = field(default_factory=lambda: _Tally())
    cancel: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def finish(self, status: str, *, error: str | None = None) -> None:
        with self.lock:
            self.status = status
            self.error = error
            self.finished_at = time.time()


class SummaryJobs:
    """Summary jobs held in this API process's memory.

    Enough for local diagnosis: a restart loses them, and they are not shared
    between API workers. Before this runs on a multi-worker or restarted server
    the jobs need a table.
    """

    def __init__(self, *, max_running: int = 2, keep: int = 20) -> None:
        self._jobs: OrderedDict[str, SummaryJob] = OrderedDict()
        self._max_running = max_running
        self._keep = keep
        self._lock = threading.Lock()

    def create(self, *, population: int, target: int) -> SummaryJob:
        with self._lock:
            running = sum(1 for job in self._jobs.values() if job.status == "running")
            if running >= self._max_running:
                raise JobCapacityError(
                    f"{running} summaries are already running; wait for one or cancel it."
                )
            job = SummaryJob(job_id=uuid.uuid4().hex, population=population, target=target)
            self._jobs[job.job_id] = job
            # Forget the oldest finished jobs beyond `keep`; never a running one.
            finished = [key for key, item in self._jobs.items() if item.status != "running"]
            for key in finished[: max(0, len(self._jobs) - self._keep)]:
                del self._jobs[key]
            return job

    def get(self, job_id: str) -> SummaryJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise SummaryJobNotFoundError(f"No summary job {job_id!r} on this server.")
        return job

    @staticmethod
    def snapshot(job: SummaryJob) -> MatchSummaryJob:
        with job.lock:
            elapsed = (job.finished_at or time.time()) - job.started_at
            return MatchSummaryJob(
                job_id=job.job_id,
                status=job.status,  # type: ignore[arg-type]
                target=job.target,
                evaluated=job.tally.evaluated,
                seconds_elapsed=round(elapsed, 1),
                error=job.error,
                summary=job.tally.summary(
                    catalog_batch=job.catalog_batch or "", population=job.population
                ),
            )


class _Tally:
    def __init__(self) -> None:
        self.evaluated = 0
        self.buckets: Counter[str] = Counter()
        self.terminals: Counter[str] = Counter()
        self.several_counts: Counter[str] = Counter()
        self.none_conflicts: Counter[str] = Counter()
        self.none_without_candidates = 0
        self.separating: Counter[str] = Counter()
        self.missing_separating: Counter[str] = Counter()
        self.not_matchable: Counter[str] = Counter()
        self.examples: dict[str, list[MatchExample]] = {bucket: [] for bucket in _BUCKETS}

    def add(
        self,
        car: CarRecord,
        evaluation: MatchEvaluation,
        query: ResolvedMatchQuery | None,
        catalog: dict[str, VehicleCandidate],
    ) -> None:
        bucket = bucket_for(evaluation)
        compatible = sum(1 for c in evaluation.candidate_matches if _is_compatible(c))
        self.evaluated += 1
        self.buckets[bucket] += 1
        self.terminals[evaluation.terminal] += 1
        if bucket == "not_matchable":
            self.not_matchable.update(evaluation.reason_codes)
        elif bucket == "none":
            if evaluation.candidate_matches:
                top = _evidence(evaluation.candidate_matches[0])
                self.none_conflicts.update(str(f) for f in top.get("conflicting_fields") or [])
            else:
                self.none_without_candidates += 1
        elif bucket == "several":
            label = f"{compatible}+" if compatible >= CANDIDATE_LIMIT else str(compatible)
            self.several_counts[label] += 1
            fields = separating_fields(evaluation, catalog)
            self.separating.update(fields)
            self.missing_separating.update(missing_on_car(fields, query))
        if len(self.examples[bucket]) < _EXAMPLES_PER_BUCKET:
            self.examples[bucket].append(
                MatchExample(
                    source_record_id=car.source_record_id,
                    plate=car.plate,
                    manufacturer=car.manufacturer,
                    model_family=car.model_family,
                    candidates=compatible,
                )
            )

    def summary(self, *, catalog_batch: str, population: int) -> MatchSummary:
        return MatchSummary(
            catalog_batch=catalog_batch,
            population=population,
            evaluated=self.evaluated,
            sampled=population > self.evaluated,
            buckets={bucket: self.buckets.get(bucket, 0) for bucket in _BUCKETS},
            terminals=dict(self.terminals.most_common()),
            several_candidate_counts=dict(sorted(self.several_counts.items())),
            candidate_limit=CANDIDATE_LIMIT,
            none_conflicting_fields=_fields(self.none_conflicts),
            none_without_candidates=self.none_without_candidates,
            several_separating_fields=_fields(self.separating),
            several_missing_separating_fields=_fields(self.missing_separating),
            not_matchable_reasons=[
                ReasonCount(reason=reason, cars=count)
                for reason, count in self.not_matchable.most_common(15)
            ],
            examples={bucket: list(self.examples[bucket]) for bucket in _BUCKETS},
        )


def _fields(counter: Counter[str]) -> list[FieldCount]:
    return [FieldCount(field=field, cars=count) for field, count in counter.most_common()]
