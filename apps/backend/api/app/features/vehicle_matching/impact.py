"""Match impact report: one repeatable measurement of how well cars reach one KType.

Each run evaluates a seeded sample of NorthStar vehicles with the real matcher
(`Matcher`, the Vehicles tab's own evaluator) against a pinned catalog batch.
The same seed over the same population picks the same cars, so two runs --
before and after a matcher or data change -- are directly comparable car by car.

Three measures per run:

- the matcher's own terminals (`resolved` is the automatic outcome);
- engine agreement: whether a resolved KType's TecDoc engines include the car's
  engine code. A proxy, and independent only for changes that do not themselves
  use the engine code;
- a reference set of cars whose correct KType is known, when one is given.
"""

from __future__ import annotations

import multiprocessing
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from api.app.features.vehicle_matching.repository import CarRecord
from api.app.features.vehicle_matching.service import Matcher, bucket_for
from ingestion.fuzzy_matching import VehicleCandidate

REPORT_VERSION = 1
_TOP_REASONS = 20
_TOP_MANUFACTURERS = 15
_BRACKET = re.compile(r"\s*\([^()]*\)")
#: Reason codes that record how a value was found, not why a car stopped.
_TRACE_ONLY = ("match:automatic", "model_evidence:", "model_recovered_from", "route:resolved")
_SEPARATORS = re.compile(r"[\s.\-/_]+")


@dataclass(frozen=True)
class CarOutcome:
    """What the matcher decided for one car, reduced to what the report counts."""

    vehicle_id: str
    manufacturer: str | None
    terminal: str
    bucket: str
    top_reference: str | None
    reason_codes: tuple[str, ...]
    engine_code: str | None


def engine_forms(code: str) -> set[str]:
    """An engine code and its TecDoc bracket parts, compared without separators.

    `BHZ (DV6FC)` -> BHZ, DV6FC and the whole; TecDoc's `D 4204 T14` and AIS's
    `D4204T14`, like `K4M 856` and `K4M-856`, are the same engine.
    """

    text = code.strip().upper()
    if not text:
        return set()
    forms = {text, _BRACKET.sub("", text)}
    forms.update(re.findall(r"\(([^()]*)\)", text))
    compact = {_SEPARATORS.sub("", form) for form in forms}
    return {form for form in compact if form}


def engine_agreement(car_engine: str | None, candidate: VehicleCandidate | None) -> bool | None:
    """True/False when both sides carry an engine code; None when it cannot be checked."""

    if not car_engine or candidate is None or not candidate.engine_codes:
        return None
    car = engine_forms(car_engine)
    known: set[str] = set()
    for code in candidate.engine_codes:
        known |= engine_forms(code)
    return bool(car & known)


def evaluate_car(matcher: Matcher, car: CarRecord) -> CarOutcome:
    evaluation, _ = matcher.evaluate(car.record)
    normalized = car.record.payload.get("normalized")
    engine = normalized.get("engine_code") if isinstance(normalized, dict) else None
    return CarOutcome(
        vehicle_id=car.vehicle_id or str(car.source_record_id),
        manufacturer=car.manufacturer,
        terminal=evaluation.terminal,
        bucket=bucket_for(evaluation),
        top_reference=evaluation.top_candidate_reference,
        reason_codes=evaluation.reason_codes,
        engine_code=str(engine) if engine else None,
    )


# The matcher is built once in the parent and inherited by forked workers, so
# the catalog (60k+ KTypes) is loaded once, not per worker.
_WORKER_MATCHER: Matcher | None = None


def _evaluate_in_worker(car: CarRecord) -> CarOutcome:
    assert _WORKER_MATCHER is not None
    return evaluate_car(_WORKER_MATCHER, car)


def evaluate_cars(matcher: Matcher, cars: Sequence[CarRecord], *, workers: int = 1) -> list[CarOutcome]:
    """Evaluate every car, in order. Workers fork, so they are available on POSIX only."""

    if workers <= 1 or len(cars) < 2:
        return [evaluate_car(matcher, car) for car in cars]
    global _WORKER_MATCHER
    _WORKER_MATCHER = matcher
    try:
        with multiprocessing.get_context("fork").Pool(workers) as pool:
            return pool.map(_evaluate_in_worker, cars, chunksize=64)
    finally:
        _WORKER_MATCHER = None


@dataclass
class ImpactReport:
    label: str
    catalog_batch: str
    seed: str
    population: str
    evaluated: int = 0
    terminals: dict[str, int] = field(default_factory=dict)
    buckets: dict[str, int] = field(default_factory=dict)
    top_reasons: dict[str, int] = field(default_factory=dict)
    engine: dict[str, int] = field(default_factory=dict)
    reference: dict[str, int] = field(default_factory=dict)
    manufacturers: dict[str, dict[str, int]] = field(default_factory=dict)
    #: vehicle_id -> [terminal, top KType]: what a later run is compared against.
    cars: dict[str, list[str | None]] = field(default_factory=dict)
    version: int = REPORT_VERSION

    def share(self, terminal: str) -> float:
        return self.terminals.get(terminal, 0) / self.evaluated if self.evaluated else 0.0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> ImpactReport:
        if data.get("version") != REPORT_VERSION:
            raise ValueError(f"unsupported report version {data.get('version')!r}")
        return cls(**{key: value for key, value in data.items()})


def build_report(
    outcomes: Iterable[CarOutcome],
    catalog: Mapping[str, VehicleCandidate],
    *,
    label: str,
    catalog_batch: str,
    seed: str,
    population: str,
    reference: Mapping[str, str] | None = None,
    reference_outcomes: Iterable[CarOutcome] = (),
) -> ImpactReport:
    """Count one run of the sample.

    `reference` maps vehicle_id to the known correct KType. It is scored over the
    sample's reference cars plus `reference_outcomes` (reference cars outside
    the sample), which never enter the sample's own counts.
    """

    terminals: Counter[str] = Counter()
    buckets: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    engine: Counter[str] = Counter()
    ref: Counter[str] = Counter()
    makers: dict[str, Counter[str]] = {}
    cars: dict[str, list[str | None]] = {}
    scored: list[CarOutcome] = []
    for outcome in outcomes:
        scored.append(outcome)
        terminals[outcome.terminal] += 1
        buckets[outcome.bucket] += 1
        if outcome.terminal != "resolved":
            reasons.update(r for r in outcome.reason_codes if not r.startswith(_TRACE_ONLY))
        maker = makers.setdefault(outcome.manufacturer or "(none)", Counter())
        maker["cars"] += 1
        maker[outcome.terminal] += 1
        cars[outcome.vehicle_id] = [outcome.terminal, outcome.top_reference]
        if outcome.terminal == "resolved":
            agreed = engine_agreement(
                outcome.engine_code, catalog.get(outcome.top_reference or "")
            )
            engine["resolved"] += 1
            engine["unchecked" if agreed is None else "agrees" if agreed else "differs"] += 1
    if reference is not None:
        for outcome in (*scored, *reference_outcomes):
            if outcome.vehicle_id not in reference:
                continue
            ref["cars"] += 1
            if outcome.terminal != "resolved":
                ref["not_resolved"] += 1
            elif outcome.top_reference == reference[outcome.vehicle_id]:
                ref["correct"] += 1
            else:
                ref["wrong"] += 1
    top_makers = sorted(makers.items(), key=lambda item: -item[1]["cars"])[:_TOP_MANUFACTURERS]
    return ImpactReport(
        label=label,
        catalog_batch=catalog_batch,
        seed=seed,
        population=population,
        evaluated=sum(terminals.values()),
        terminals=dict(terminals.most_common()),
        buckets=dict(buckets.most_common()),
        top_reasons=dict(reasons.most_common(_TOP_REASONS)),
        engine=dict(engine),
        reference=dict(ref),
        manufacturers={name: dict(counts) for name, counts in top_makers},
        cars=cars,
    )


@dataclass(frozen=True)
class Comparison:
    gained: int
    lost: int
    moved: int
    shared: int


def compare(before: ImpactReport, after: ImpactReport) -> Comparison:
    """Car-by-car change in automatic resolution between two runs of the same sample."""

    if before.catalog_batch != after.catalog_batch:
        raise ValueError("reports use different catalog batches; they are not comparable")
    gained = lost = moved = shared = 0
    for vehicle_id, (terminal, ktype) in after.cars.items():
        previous = before.cars.get(vehicle_id)
        if previous is None:
            continue
        shared += 1
        was, was_ktype = previous
        if was != "resolved" and terminal == "resolved":
            gained += 1
        elif was == "resolved" and terminal != "resolved":
            lost += 1
        elif was == terminal == "resolved" and was_ktype != ktype:
            moved += 1
    return Comparison(gained=gained, lost=lost, moved=moved, shared=shared)


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def render(report: ImpactReport, baseline: ImpactReport | None = None) -> str:
    """Plain-text summary for a terminal or a Jira comment."""

    n = report.evaluated
    lines = [
        f"Match impact: {report.label}",
        f"catalog {report.catalog_batch} | seed {report.seed} | {report.population} | {n} cars",
        "",
        "Terminals:",
    ]
    for terminal, count in report.terminals.items():
        delta = ""
        if baseline is not None:
            diff = 100 * (report.share(terminal) - baseline.share(terminal))
            delta = f"  ({diff:+.1f} pts)"
        lines.append(f"  {terminal:<22}{count:>8}  {_pct(count, n):>6}{delta}")
    resolved = report.engine.get("resolved", 0)
    checked = report.engine.get("agrees", 0) + report.engine.get("differs", 0)
    lines += [
        "",
        (
            f"Engine agreement of resolved: {report.engine.get('agrees', 0)} agree, "
            f"{report.engine.get('differs', 0)} differ "
            f"({_pct(report.engine.get('differs', 0), checked)} of checkable), "
            f"{report.engine.get('unchecked', 0)} of {resolved} uncheckable"
        ),
    ]
    if report.reference:
        cars = report.reference.get("cars", 0)
        lines.append(
            f"Reference set: {cars} cars, correct {report.reference.get('correct', 0)} "
            f"({_pct(report.reference.get('correct', 0), cars)}), wrong "
            f"{report.reference.get('wrong', 0)}, not resolved {report.reference.get('not_resolved', 0)}"
        )
    if baseline is not None:
        change = compare(baseline, report)
        lines.append(
            f"Against '{baseline.label}' ({change.shared} shared cars): +{change.gained} resolved, "
            f"-{change.lost} lost, {change.moved} moved to another KType"
        )
    lines += ["", "Why not resolved (top reasons):"]
    lines += [f"  {count:>8}  {reason}" for reason, count in report.top_reasons.items()]
    lines += ["", "Resolved by manufacturer:"]
    for name, counts in report.manufacturers.items():
        lines.append(
            f"  {name:<20}{counts['cars']:>7} cars  {_pct(counts.get('resolved', 0), counts['cars']):>6}"
        )
    return "\n".join(lines)
