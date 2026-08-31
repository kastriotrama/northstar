"""Fit the candidate-margin gate to human-adjudicated match decisions.

The adjudication set is stratified by separation margin, so pooled rates
describe the sample rather than the population. Each labelled pair therefore
carries an importance weight of (population pairs in band) / (labelled pairs
in band), and every rate reported here is weighted.

Thresholds are chosen on the *lower* confidence bound of weighted precision.
A point estimate would let a few lucky items near the boundary set policy for
millions of rows, so a threshold whose support is too thin is reported as
undecidable instead of being silently returned.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

ACCEPT = "accept"
REJECT = "reject"
UNSURE = "unsure"
VERDICTS = frozenset({ACCEPT, REJECT, UNSURE})

Z_SCORES: dict[float, float] = {0.90: 1.6449, 0.95: 1.9600, 0.99: 2.5758}


@dataclass(frozen=True)
class LabelledPair:
    """One adjudicated top-vs-runner-up pair with its population weight."""

    margin: float
    band: str
    verdict: str
    weight: float

    def __post_init__(self) -> None:
        if self.verdict not in {ACCEPT, REJECT}:
            raise ValueError("a labelled pair must be accept or reject")
        if self.weight <= 0.0:
            raise ValueError("weight must be positive")


@dataclass(frozen=True)
class SweepEntry:
    """Weighted precision and recall for one candidate threshold."""

    threshold: float
    admitted_items: int
    weighted_precision: float
    precision_lower_bound: float
    weighted_recall: float
    effective_sample_size: float
    decidable: bool


def wilson_lower_bound(successes: float, total: float, z: float) -> float:
    """Wilson score lower bound, tolerant of fractional (weighted) counts."""

    if total <= 0:
        return 0.0
    proportion = min(1.0, max(0.0, successes / total))
    denominator = 1.0 + (z * z) / total
    centre = proportion + (z * z) / (2 * total)
    spread = z * math.sqrt(
        (proportion * (1.0 - proportion) + (z * z) / (4 * total)) / total
    )
    return max(0.0, (centre - spread) / denominator)


def effective_sample_size(weights: tuple[float, ...]) -> float:
    """Kish effective sample size; uneven weighting costs information."""

    total = sum(weights)
    squared = sum(weight * weight for weight in weights)
    return (total * total / squared) if squared > 0 else 0.0


def band_weights(
    *, band_population: dict[str, int], labelled_per_band: dict[str, int]
) -> dict[str, float]:
    """Importance weight per band, recovering population rates from strata."""

    weights: dict[str, float] = {}
    for band, sampled in labelled_per_band.items():
        population = band_population.get(band, 0)
        if sampled > 0 and population > 0:
            weights[band] = population / sampled
    return weights


def sweep_thresholds(
    pairs: tuple[LabelledPair, ...],
    *,
    confidence: float = 0.95,
    minimum_effective_sample: float = 30.0,
) -> tuple[SweepEntry, ...]:
    """Weighted precision/recall at every observed margin, ascending."""

    z = Z_SCORES.get(round(confidence, 2))
    if z is None:
        raise ValueError(f"confidence must be one of {sorted(Z_SCORES)}")
    if not pairs:
        return ()

    total_accept_weight = sum(pair.weight for pair in pairs if pair.verdict == ACCEPT)
    entries: list[SweepEntry] = []
    for threshold in sorted({pair.margin for pair in pairs}):
        admitted = tuple(pair for pair in pairs if pair.margin >= threshold)
        if not admitted:
            continue
        weights = tuple(pair.weight for pair in admitted)
        total_weight = sum(weights)
        accept_weight = sum(
            pair.weight for pair in admitted if pair.verdict == ACCEPT
        )
        effective = effective_sample_size(weights)
        precision = accept_weight / total_weight
        # Scale to the effective sample size so the interval reflects the
        # information actually present, not the inflated weighted totals.
        entries.append(
            SweepEntry(
                threshold=round(threshold, 6),
                admitted_items=len(admitted),
                weighted_precision=round(precision, 6),
                precision_lower_bound=round(
                    wilson_lower_bound(precision * effective, effective, z), 6
                ),
                weighted_recall=(
                    round(accept_weight / total_accept_weight, 6)
                    if total_accept_weight > 0
                    else 0.0
                ),
                effective_sample_size=round(effective, 3),
                decidable=effective >= minimum_effective_sample,
            )
        )
    return tuple(entries)


def choose_threshold(
    entries: tuple[SweepEntry, ...], *, target_precision: float
) -> SweepEntry | None:
    """Smallest decidable threshold whose precision lower bound clears target."""

    if not 0.0 < target_precision < 1.0:
        raise ValueError("target_precision must be between 0 and 1")
    qualifying = [
        entry
        for entry in entries
        if entry.decidable and entry.precision_lower_bound >= target_precision
    ]
    return min(qualifying, key=lambda entry: entry.threshold) if qualifying else None
