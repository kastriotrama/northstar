"""Field-level resolution semantics and predicate scoring.

Transportstyrelsen frequently supplies a value that NorthStar cannot interpret
rather than no value at all: `is_4wd = 0` states only "not four-wheel drive",
leaving front- and rear-wheel drive indistinguishable. Such a field is
`unresolved`, not `missing`, and the difference decides what a reviewer can do
about it.

A resolution rule closes that gap by asserting that a *conjunction of source
conditions* implies a canonical value, so one authored rule resolves every row
matching the predicate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache


class FieldStatus(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    MISSING = "missing"


# Source field -> the signature key its meaning should end up in. The signature
# holds the normalized values, so a null signature key with a present source
# value is exactly the unresolved case.
SOURCE_TO_SIGNATURE: dict[str, str] = {
    "is_4wd": "drive_type",
    "body_code": "bodywork_form",
    "fuel1": "energy_sources",
    "kw": "power_kw",
    "ccm": "displacement_cc",
    "vehicle_year": "production_year",
    "brand": "manufacturer",
    "model": "model_family",
    "model_no": "model_family",
    "variant": "model_family",
    "type_text": "model_family",
    "fab_code": "manufacturer",
}

# Canonical keys held in a chunk signature — the fields a `normalized`
# condition may reference.
SIGNATURE_FIELDS: frozenset[str] = frozenset(
    {
        "manufacturer",
        "model_family",
        "production_year",
        "energy_sources",
        "engine_code",
        "displacement_cc",
        "power_kw",
        "drive_type",
        "bodywork_form",
    }
)

# Fields offered as predicate terms when narrowing an unresolved population.
CANDIDATE_DISCRIMINATORS: tuple[str, ...] = (
    "brand",
    "model",
    "model_no",
    "variant",
    "version",
    "type_text",
    "fab_code",
    "body_code",
    "fuel1",
    "kw",
    "ccm",
    "vehicle_year",
    "eu_category",
    "vehicle_class",
    "eeg_type_approval",
)

# Which source fields are *semantically* relevant to each target, in
# preference order. Statistics alone rank `vehicle_year` highest for
# `drive_type` because it splits the population evenly — but a model year says
# nothing about which wheels are driven. Drive type is a property of the model,
# so identity fields come first. This is the one place domain knowledge is
# stated explicitly rather than inferred.
TARGET_FIELD_PRIORS: dict[str, tuple[str, ...]] = {
    "drive_type": ("fab_code", "brand", "model", "model_no", "type_text", "variant"),
    "bodywork_form": ("body_code", "type_text", "brand", "model"),
    "model_family": ("brand", "model_no", "type_text", "variant", "version"),
    "manufacturer": ("brand", "fab_code"),
}

def _canonical_values(field: str) -> tuple[str, ...]:
    """Canonical vocabulary for a field, taken from the reviewed rule set.

    Derived rather than restated so the picker cannot drift out of step with
    the rules that normalization actually applies.
    """

    from ingestion.translation_dictionaries import (
        REVIEWED_RULE_SET_VERSION,
        load_translation_rule_set,
    )

    rule_set = load_translation_rule_set(REVIEWED_RULE_SET_VERSION)
    return tuple(
        sorted(
            {
                rule.canonical_value
                for rule in rule_set.by_id.values()
                if rule.canonical_field == field and rule.canonical_value
            }
        )
    )


# An empty tuple means an OPEN vocabulary: any value is accepted, and the
# screen offers observed values as suggestions rather than a fixed list.
# `model_family` is genuinely unbounded — there is no closed set of model names
# — while drive type and bodywork form are closed sets.
RESOLVABLE_TARGETS: dict[str, tuple[str, ...]] = {
    # The reviewed rules only ever produce `awd` (from is_4wd = 1); the other
    # two are exactly what a resolution rule exists to assert.
    "drive_type": ("fwd", "rwd", "awd"),
    "bodywork_form": _canonical_values("bodywork_form"),
    "model_family": (),
    "manufacturer": (),
    "energy_sources": _canonical_values("energy_sources"),
    "transmission_type": _canonical_values("transmission_type"),
}


@lru_cache(maxsize=1)
def _value_decoder() -> dict[tuple[str, str], str]:
    """(source field, raw value) -> what the register means by it.

    Registry values are terse codes — `AC`, `01`, `VO`, `1` — and a reviewer
    should not have to memorise them. Meanings come from the same reviewed
    rules normalization applies, plus the derived fabrikatkod catalogue, so the
    screen explains a code exactly as the pipeline interprets it.
    """

    from ingestion.normalization_rules import _FAB_CODE_MANUFACTURERS
    from ingestion.translation_dictionaries import (
        REVIEWED_RULE_SET_VERSION,
        load_translation_rule_set,
    )

    decoder: dict[tuple[str, str], str] = {
        ("fab_code", code): manufacturer
        for code, manufacturer in _FAB_CODE_MANUFACTURERS.items()
    }
    for rule in load_translation_rule_set(REVIEWED_RULE_SET_VERSION).by_id.values():
        meaning = rule.display_value or rule.canonical_value
        if not meaning:
            continue
        for source_field in rule.source_fields:
            for term in rule.source_terms:
                decoder.setdefault((source_field, str(term).upper()), str(meaning))
    return decoder


def describe_value(field: str, value: str) -> str | None:
    """Human meaning for one raw value, or None when the register defines none."""

    return _value_decoder().get((field, value.strip().upper()))


def field_status(source_value: str | None, normalized_value: str | None) -> FieldStatus:
    """Classify one field for one vehicle.

    `missing` means the registry told us nothing. `unresolved` means it told us
    something we cannot yet interpret — the actionable case.
    """

    if normalized_value is not None and normalized_value.strip():
        return FieldStatus.RESOLVED
    if source_value is not None and source_value.strip():
        return FieldStatus.UNRESOLVED
    return FieldStatus.MISSING


class ConditionLayer(StrEnum):
    """Which layer a rule condition reads.

    `source` reads the registry string verbatim; `normalized` reads the
    canonical value normalization derived from it. Normalized conditions carry
    far more leverage (82 manufacturers vs 17,995 brand spellings) but can hide
    the very evidence a rule needs — the chassis code in
    `MERCEDES-BENZ 204 K` distinguishes a rear-wheel-drive C-Class from a
    front-wheel-drive B-Class, and `Mercedes-Benz` does not.
    """

    SOURCE = "source"
    NORMALIZED = "normalized"


class ConditionOperator(StrEnum):
    """Operators a condition may use.

    Numeric comparisons cast the registry's text values, guarded by a regex so
    non-numeric junk yields NULL rather than aborting the whole query.
    """

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    STARTS_WITH = "starts_with"
    CONTAINS = "contains"
    GTE = "gte"
    LTE = "lte"


NUMERIC_OPERATORS = frozenset({ConditionOperator.GTE, ConditionOperator.LTE})
CONDITION_OPERATOR_VALUES: frozenset[str] = frozenset(
    operator.value for operator in ConditionOperator
)


@dataclass(frozen=True)
class PredicateTerm:
    """One clause of a rule: a field tested against one or more values.

    Values within a term are OR-ed; terms are AND-ed together. This is
    conjunctive normal form restricted to a single field per clause — enough
    to express `brand starts_with 204 OR 212`, while staying readable enough
    that a reviewer can audit an immutable rule at a glance. Arbitrary boolean
    nesting is deliberately not supported.
    """

    layer: str
    field: str
    operator: str
    values: tuple[str, ...]


SEPARATION_FLOOR = 0.02


@dataclass(frozen=True)
class DiscriminatorScore:
    """How useful a field is for carving an unresolved population into rules.

    Three explainable factors, deliberately not information gain so a reviewer
    can see why a field was suggested:

    - `coverage`: share of the population that has any value at all.
    - `separation`: how far the field is from constant. A field like
      `eu_category`, where one value covers ~100% of rows, separates nothing
      even though its coverage is perfect.
    - `concision`: penalty for cardinality. 400 distinct power ratings would
      split the population cleanly but demand 400 rules to do it.
    """

    field: str
    present_count: int
    distinct_count: int
    coverage: float
    separation: float
    concision: float

    @property
    def usable(self) -> bool:
        return (
            self.distinct_count >= 2
            and self.present_count > 0
            and self.separation >= SEPARATION_FLOOR
        )

    @property
    def score(self) -> float:
        if not self.usable:
            return 0.0
        return round(self.coverage * self.separation * self.concision, 4)


@dataclass(frozen=True)
class ValuePattern:
    """A shared prefix that groups several raw values into one condition."""

    prefix: str
    row_count: int
    distinct_values: int
    coverage: float
    score: float


def suggest_value_patterns(
    values: list[tuple[str, int]],
    *,
    population: int,
    max_suggestions: int = 6,
) -> list[ValuePattern]:
    """Find token prefixes that carve a population into meaningful blocks.

    Registry strings are often structured — `MERCEDES-BENZ 204 K` is make plus
    chassis code plus body — so a prefix can isolate a real sub-population that
    no single exact value covers. Prefixes are ranked by ``coverage × (1 −
    coverage)``, which peaks on balanced splits and scores a prefix covering
    everything (or almost nothing) at zero: covering the whole population
    divides it no better than no condition at all.
    """

    if population <= 0:
        return []
    prefix_rows: dict[str, int] = {}
    prefix_values: dict[str, set[str]] = {}
    for value, count in values:
        tokens = value.split()
        # Include the full token count: a short value like `MERCEDES-BENZ 204`
        # is itself a proper prefix of the longer `MERCEDES-BENZ 204 K`.
        for index in range(1, len(tokens) + 1):
            prefix = " ".join(tokens[:index])
            prefix_rows[prefix] = prefix_rows.get(prefix, 0) + count
            prefix_values.setdefault(prefix, set()).add(value)
    patterns: list[ValuePattern] = []
    for prefix, rows in prefix_rows.items():
        distinct = len(prefix_values[prefix])
        if distinct < 2:
            continue
        coverage = min(rows / population, 1.0)
        patterns.append(
            ValuePattern(
                prefix=prefix,
                row_count=rows,
                distinct_values=distinct,
                coverage=round(coverage, 4),
                score=round(coverage * (1.0 - coverage), 4),
            )
        )
    patterns.sort(key=lambda item: (item.score, item.row_count), reverse=True)
    return [pattern for pattern in patterns if pattern.score > 0][:max_suggestions]


def score_discriminator(
    *,
    field: str,
    population: int,
    present_count: int,
    distinct_count: int,
    top_counts: list[int],
) -> DiscriminatorScore:
    from math import log10

    coverage = present_count / population if population else 0.0
    dominant = max(top_counts) if top_counts else 0
    separation = 1.0 - (dominant / present_count) if present_count else 0.0
    concision = 1.0 / (1.0 + log10(distinct_count)) if distinct_count >= 1 else 0.0
    return DiscriminatorScore(
        field=field,
        present_count=present_count,
        distinct_count=distinct_count,
        coverage=round(min(coverage, 1.0), 4),
        separation=round(max(separation, 0.0), 4),
        concision=round(concision, 4),
    )
