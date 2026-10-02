"""The correctable set: every field a person may correct on one car, defined once.

One `CorrectableSpec` per field says what the field is called, what it accepts,
which of the matcher's evidence keys belong to it, how a correction changes what
the matcher is handed and which vehicle columns carry the copy. Everything else
is driven from `SPECS`: validation (`canonical_value`), the read seam
(`matcher_values` and the spec's keys), the vehicle's copy (`vehicle_copy`) and
what the lookup shows (`describe`, `states`).

One more thing can be corrected that is not a value: the stop that keeps a car
whose record needs normalization review away from the matcher
(`NORMALIZATION_STOP`). A person can release one car from it, and take the
release back.

Pure functions: nothing here reads or writes a database.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from api.app.features.match_review.field_resolution import RESOLVABLE_TARGETS, _canonical_values
from api.app.features.vehicle_corrections.schemas import (
    CorrectableField,
    CorrectionDecision,
    CorrectionState,
    CorrectionStatus,
    FieldType,
)
from api.app.features.vehicle_matching.schemas import KTypeCandidate, MatcherInputs
from ingestion.normalization_rules import fuel_carriers, fuel_match_tokens
from ingestion.translation_dictionaries import REVIEWED_RULE_SET_VERSION, load_translation_rule_set
from ingestion.vehicle_core_fields import (
    FIELDS_BY_NAME,
    INTEGER_TYPES,
    SOURCE_TS,
    clean_int,
    clean_text,
)
from ingestion.vehicle_correction_decisions import DecisionRef
from ingestion.vehicle_fact_corrections import StoredCorrection

#: Free text is a name or a code, never a sentence.
TEXT_MAX_LENGTH = 80
#: A car's fuel is one to three energy carriers.
MAX_CARRIERS = 3
#: A value the lookup shows as the car's own: it lies under every other source.
SOURCE_REGISTRY = "registry"

_STATUS: dict[str, CorrectionStatus] = {"set": "set", "ignore": "ignored", "withdraw": "withdrawn"}
# The normalization's own date. The matcher falls back to it for the build month
# when the car has no year or no month, and it carries both: once either is
# corrected, only the year and the month the matcher is handed may speak.
_DATE_FALLBACK = ("production_date", "production_date_precision")


#: The pseudo-field a car is released on. It has no value: an `ignore` hands the
#: matcher the car although its record's normalization asks for review, and a
#: `withdraw` lets the stop apply again. Not one of the lookup's correctable fields.
NORMALIZATION_STOP = "normalization_stop"
#: The normalization status that stops a record before matching, the matcher's
#: outcome for such a car, and the status a released car is handed with.
STOPPED_STATUS = "review_required"
STOPPED_TERMINAL = "normalization_review"
RELEASED_STATUS = "resolved"
#: The matcher's own word for a stopped record that carries no reasons.
STOP_WITHOUT_REASONS = "normalization_review_required"


class FieldNotCorrectableError(ValueError):
    """The field is not one a person may correct."""


class InvalidValueError(ValueError):
    """The value is not one the field accepts."""


Current = Callable[[Mapping[str, Any], MatcherInputs | None], str | None]


@dataclass(frozen=True)
class CorrectableSpec:
    """Everything about one correctable field."""

    field: str
    label: str
    type: FieldType
    #: The matcher's evidence keys that belong to the field.
    evidence_keys: tuple[str, ...]
    #: The normalized keys the matcher reads the field from: a `set` writes
    #: them, an `ignore` removes them.
    matcher_keys: tuple[str, ...]
    #: What a stored value hands the matcher, by normalized key.
    to_matcher: Callable[[Any], dict[str, Any]]
    #: The vehicle columns that carry the copy of a `set`, and what it puts in
    #: each; None leaves a column empty.
    vehicle_columns: tuple[str, ...]
    to_vehicle: Callable[[Any], dict[str, Any]]
    #: What the matcher uses for the field today, as text.
    current: Current
    #: The closed vocabulary; empty when the field takes any text, or any
    #: number within its bounds. A whole number with few values (the month)
    #: lists them, so a screen can show and enforce the range.
    values: tuple[str, ...] = ()
    #: An integer's inclusive range; an open upper end moves with the calendar.
    bounds: tuple[int, int | None] | None = None
    #: Normalized keys the matcher would fall back to for the same evidence;
    #: any correction of the field removes them.
    matcher_fallbacks: tuple[str, ...] = ()
    #: What a candidate KType carries for the field, where that is worth suggesting.
    candidate_values: Callable[[KTypeCandidate], Iterable[object]] | None = None


def _as_text(field_type: FieldType, value: object) -> str | None:
    """A normalized value as the matcher reads it; None when it has none."""

    if field_type == "integer":
        number = clean_int(value)
        return str(number) if number is not None and number > 0 else None
    text = str(value).strip() if value else ""
    return text or None


def _plain(
    field: str,
    *,
    label: str | None = None,
    values: tuple[str, ...] = (),
    bounds: tuple[int, int | None] | None = None,
    evidence_keys: tuple[str, ...] | None = None,
    fallbacks: tuple[str, ...] = (),
    current: Current | None = None,
    candidate_values: Callable[[KTypeCandidate], Iterable[object]] | None = None,
) -> CorrectableSpec:
    """A field the matcher reads under its own name and the vehicle carries in one column."""

    core = FIELDS_BY_NAME[field]
    field_type: FieldType = "integer" if core.sql_type in INTEGER_TYPES else "text"

    def as_handed(normalized: Mapping[str, Any], inputs: MatcherInputs | None) -> str | None:
        return _as_text(field_type, normalized.get(field))

    return CorrectableSpec(
        field=field,
        label=label or core.label,
        type=field_type,
        evidence_keys=evidence_keys or (field,),
        matcher_keys=(field,),
        to_matcher=lambda value: {field: value},
        vehicle_columns=(field,),
        to_vehicle=lambda value: {field: value},
        current=current or as_handed,
        values=values,
        bounds=bounds,
        matcher_fallbacks=fallbacks,
        candidate_values=candidate_values,
    )


def _energy_carriers() -> tuple[str, ...]:
    """The reviewed energy carriers in the registry's own code order: petrol, diesel, ..."""

    rule_set = load_translation_rule_set(REVIEWED_RULE_SET_VERSION)
    return tuple(
        dict.fromkeys(
            rule.canonical_value
            for rule in rule_set.rules
            if rule.canonical_field == "energy_sources" and rule.canonical_value
        )
    )


def _build_month(normalized: Mapping[str, Any], inputs: MatcherInputs | None) -> str | None:
    """The month of the build month the matcher composed (YYYYMM), if it has one."""

    if inputs is None or inputs.build_month is None:
        return None
    return str(inputs.build_month % 100)


def _fuel(normalized: Mapping[str, Any], inputs: MatcherInputs | None) -> str | None:
    """The car's energy carriers as the matcher reads them: its tokens first."""

    tokens = normalized.get("fuel_match_tokens")
    carriers = (
        fuel_carriers([str(token) for token in tokens])
        if isinstance(tokens, list | tuple) and tokens
        else normalized.get("energy_sources")
    )
    if not isinstance(carriers, list | tuple) or not carriers:
        return None
    return ",".join(str(carrier) for carrier in carriers)


def _fuel_on_vehicle(carriers: Sequence[str]) -> dict[str, Any]:
    return {
        "fuel": carriers[0],
        "fuel_secondary": carriers[1] if len(carriers) > 1 else None,
        "fuel_match_tokens": fuel_match_tokens(carriers),
    }


#: Every correctable field, in the order the lookup lists them: the fields a
#: reviewer's rule may target, then the build month, the fuel and the
#: electrification.
SPECS: dict[str, CorrectableSpec] = {
    spec.field: spec
    for spec in (
        _plain("manufacturer"),
        _plain(
            "model_family",
            evidence_keys=("model", "model_series"),
            candidate_values=lambda candidate: (candidate.model,),
        ),
        _plain("engine_code", candidate_values=lambda candidate: candidate.engine_codes),
        _plain(
            "power_kw", bounds=(1, 2000), candidate_values=lambda candidate: (candidate.power_kw,)
        ),
        _plain(
            "displacement_cc",
            bounds=(1, 20000),
            candidate_values=lambda candidate: (candidate.displacement_cc,),
        ),
        _plain(
            "drive_type",
            values=RESOLVABLE_TARGETS["drive_type"],
            candidate_values=lambda candidate: (candidate.drive_type,),
        ),
        _plain(
            "bodywork_form",
            values=RESOLVABLE_TARGETS["bodywork_form"],
            evidence_keys=("bodywork",),
            candidate_values=lambda candidate: candidate.bodyworks,
        ),
        # A candidate has a span of years, and every candidate the same
        # manufacturer: neither is suggested.
        _plain(
            "production_year",
            bounds=(1880, None),
            evidence_keys=("year",),
            fallbacks=_DATE_FALLBACK,
        ),
        _plain(
            "production_month",
            label="Build month",
            bounds=(1, 12),
            values=tuple(str(month) for month in range(1, 13)),
            evidence_keys=("year_month",),
            fallbacks=_DATE_FALLBACK,
            current=_build_month,
        ),
        CorrectableSpec(
            field="fuel",
            label="Fuel",
            type="list",
            evidence_keys=("fuels",),
            matcher_keys=("energy_sources", "fuel_match_tokens"),
            to_matcher=lambda carriers: {
                "energy_sources": list(carriers),
                "fuel_match_tokens": fuel_match_tokens(carriers),
            },
            vehicle_columns=("fuel", "fuel_secondary", "fuel_match_tokens"),
            to_vehicle=_fuel_on_vehicle,
            current=_fuel,
            values=_energy_carriers(),
        ),
        # The vehicle's column is not among the values laid over a car's
        # derivation, so without a correction the matcher reads the origin
        # record's own electrification.
        _plain(
            "electrification_type",
            values=_canonical_values("electrification_type"),
            evidence_keys=("electrification", "match_guard:plug_in"),
        ),
    )
}


def stop_reasons(status: str, review_reasons: Sequence[str]) -> tuple[str, ...]:
    """Why a record is stopped before matching; empty when it is not stopped.

    A policy route (a motorhome, a test record) and a failed normalization are
    other outcomes: neither is this stop, and neither can be released.
    """

    if status != STOPPED_STATUS:
        return ()
    return tuple(str(reason) for reason in review_reasons) or (STOP_WITHOUT_REASONS,)


def spec_for(field: str) -> CorrectableSpec:
    spec = SPECS.get(field)
    if spec is None:
        raise FieldNotCorrectableError(f"{field!r} is not a field that can be corrected.")
    return spec


def _today() -> date:
    return datetime.now(UTC).date()


def _has_control_character(text: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in text)


def canonical_value(field: str, raw: str | None, *, today: date | None = None) -> str:
    """The value as it is stored and compared.

    An integer as its digits, text trimmed, a list as its members in vocabulary
    order joined by commas. Raises `InvalidValueError` for a blank value, a
    number that is not digits or lies outside the field's range, a word outside
    a closed vocabulary, a list that is too long or names a member twice, and
    text longer than a name or a code is.
    """

    spec = spec_for(field)
    # Line breaks and tabs are spacing a pasted value may carry; nothing else
    # of that kind belongs in a value (PostgreSQL text cannot even hold NUL).
    text = " ".join((raw or "").split())
    if not text:
        raise InvalidValueError(f"Enter a value for {spec.label}.")
    if _has_control_character(text):
        raise InvalidValueError(f"{spec.label} must not contain control characters.")
    if spec.type == "integer":
        if not (text.isascii() and text.isdigit()) or len(text) > 9:
            raise InvalidValueError(f"{spec.label} must be a whole number, digits only.")
        number = int(text)
        lowest, highest = spec.bounds or (1, 999_999_999)
        if highest is None:
            highest = (today or _today()).year + 1
        if not lowest <= number <= highest:
            raise InvalidValueError(f"{spec.label} must be between {lowest} and {highest}.")
        return str(number)
    if spec.type == "list":
        members = [member.strip() for member in text.split(",")]
        if any(member not in spec.values for member in members):
            raise InvalidValueError(
                f"{spec.label} must name values from: {', '.join(spec.values)}."
            )
        if len(set(members)) != len(members) or len(members) > MAX_CARRIERS:
            raise InvalidValueError(
                f"{spec.label} must be one to {MAX_CARRIERS} different values."
            )
        return ",".join(sorted(members, key=spec.values.index))
    if spec.values:
        if text not in spec.values:
            raise InvalidValueError(f"{spec.label} must be one of: {', '.join(spec.values)}.")
        return text
    cleaned = clean_text(text)
    if cleaned is None:
        raise InvalidValueError(f"Enter a value for {spec.label}.")
    if len(cleaned) > TEXT_MAX_LENGTH:
        raise InvalidValueError(f"{spec.label} must be at most {TEXT_MAX_LENGTH} characters.")
    return cleaned


def _typed(spec: CorrectableSpec, value: str) -> Any:
    """A stored value in the type the matcher and the vehicle carry; None if it is not one."""

    if spec.type == "integer":
        return int(value) if value.isascii() and value.isdigit() else None
    if spec.type == "list":
        members = [member for member in value.split(",") if member]
        return members if members and all(member in spec.values for member in members) else None
    return clean_text(value)


def matcher_values(field: str, value: str) -> dict[str, Any]:
    """What a `set` hands the matcher, by normalized key.

    Empty for a field or a value this version does not know (a row a later
    version wrote): such a correction is left alone rather than guessed at.
    """

    spec = SPECS.get(field)
    typed = None if spec is None else _typed(spec, value)
    return {} if spec is None or typed is None else spec.to_matcher(typed)


def vehicle_copy(field: str, value: str) -> dict[str, Any]:
    """What a `set` puts on the vehicle, by column; None leaves a column empty.

    Empty for a field or a value this version does not know, as `matcher_values`.
    """

    spec = SPECS.get(field)
    typed = None if spec is None else _typed(spec, value)
    return {} if spec is None or typed is None else spec.to_vehicle(typed)


def same_value(field: str, value: str, current: str | None) -> bool:
    """True when a `set` to `value` would change nothing the matcher is handed."""

    if current is None:
        return False
    if SPECS[field].type == "list":
        return set(value.split(",")) == set(current.split(","))
    return value == current


def same_present(field: str, value: str | None, other: str | None) -> bool:
    """True when two cars have the same present value for a field; both having none counts."""

    if value is None or other is None:
        return value is None and other is None
    return same_value(field, value, other)


def changes_nothing(field: str, action: str, value: str | None, current: str | None) -> bool:
    """True when a `set` or an `ignore` would change nothing the matcher is handed.

    A `set` to the value the matcher already uses, or an `ignore` where it has
    none: what one car's correction is refused for, and what a check over many
    cars sorts a car out for.
    """

    if action == "set":
        return value is not None and same_value(field, value, current)
    return current is None


def current_source(spec: CorrectableSpec, overlaid: Mapping[str, str]) -> str:
    """Where the field's value comes from: the car's own record unless something supplied it."""

    for key in (spec.field, *spec.matcher_keys):
        source = overlaid.get(key)
        if source:
            return SOURCE_REGISTRY if source == SOURCE_TS else source
    return SOURCE_REGISTRY


def _suggestions(
    spec: CorrectableSpec, candidates: Sequence[KTypeCandidate], current: str | None
) -> list[str]:
    if spec.candidate_values is None:
        return []
    found: dict[str, None] = {}
    for candidate in candidates:
        for value in spec.candidate_values(candidate):
            if value is None:
                continue
            try:
                found[canonical_value(spec.field, str(value))] = None
            except InvalidValueError:
                # A candidate's word the field does not take cannot be saved.
                continue
    return [value for value in found if value != current]


def describe(
    normalized: Mapping[str, Any],
    overlaid: Mapping[str, str],
    inputs: MatcherInputs | None,
    candidates: Sequence[KTypeCandidate],
) -> list[CorrectableField]:
    """Every correctable field of one car, as the lookup shows it.

    `normalized` and `overlaid` are the record the matcher was handed and the
    sources of its values, corrections included; `inputs` is what the matcher
    keyed on, and `candidates` are the KTypes the lookup lists.
    """

    fields = []
    for spec in SPECS.values():
        current = spec.current(normalized, inputs)
        fields.append(
            CorrectableField(
                field=spec.field,
                label=spec.label,
                type=spec.type,
                values=list(spec.values),
                evidence_keys=list(spec.evidence_keys),
                current_value=current,
                current_source=current_source(spec, overlaid),
                suggestions=_suggestions(spec, candidates, current),
            )
        )
    return fields


def states(
    heads: Mapping[str, tuple[StoredCorrection, int]],
    decisions: Mapping[UUID, DecisionRef] | None = None,
) -> list[CorrectionState]:
    """The head of every chain the car has, by field; a withdrawn one too.

    `decisions` names, by `group_id`, the decision behind a row a decision
    about many cars wrote.
    """

    def decision(head: StoredCorrection) -> CorrectionDecision | None:
        found = (decisions or {}).get(head.group_id) if head.group_id is not None else None
        if found is None:
            return None
        return CorrectionDecision(
            decision_id=found.decision_id,
            scope_label=found.scope_label,
            member_count=found.member_count,
            reviewer=found.reviewer,
        )

    return [
        CorrectionState(
            field=head.field,
            status=_STATUS[head.action],
            correction_id=head.correction_id,
            value=head.value,
            reviewer=head.reviewer,
            reason=head.reason,
            created_at=head.created_at,
            previous_value=head.previous_value,
            previous_source=head.previous_source,
            group_id=head.group_id,
            history_count=history_count,
            decision=decision(head),
        )
        for head, history_count in sorted(heads.values(), key=lambda item: item[0].field)
    ]
