"""Merge source observations into one vehicle, field by field.

Pure: no database, no I/O. Every writer -- the TS backfill, the AIS import, a
reviewer's rule, an enrichment rule -- turns what it knows into `Observation`s and
calls `merge`, so the precedence in `vehicle_core_fields` is applied the same way
everywhere and can be tested without a database.

Nothing a source said is lost. When an observation loses, or displaces the value
that was there, the other value is kept in `field_alternatives`, keyed by its
source. That is what lets a retired review fall back to the provider value it had
overridden, instead of leaving the field empty.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ingestion.vehicle_core_fields import (
    FIELDS_BY_NAME,
    PROVIDER_SOURCES,
    SOURCE_AIS,
    SOURCE_DERIVED,
    SOURCE_REVIEW,
    SOURCE_RULE,
    SOURCE_TS,
    SourceRef,
    parse_source_ref,
)


@dataclass(frozen=True)
class Observation:
    """What one source says about one field.

    `clears` marks an explicit "this is no longer known" -- a newer registry
    vehicle type makes the older EU category stale. A plain None only means the
    source is silent, and a silent source never erases what another one said.
    """

    value: Any
    ref: SourceRef
    clears: bool = False


@dataclass
class VehicleState:
    """The mutable picture of one vehicle while observations are merged into it."""

    vehicle_id: str
    origin_source: str
    origin_observed_on: date | None
    values: dict[str, Any] = field(default_factory=dict)
    field_sources: dict[str, str] = field(default_factory=dict)
    field_alternatives: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    ts_record_id: int | None = None


@dataclass(frozen=True)
class FieldChange:
    field: str
    before: Any
    after: Any
    source: str


@dataclass
class MergeResult:
    """What a merge did, for the ledger and the import summary."""

    filled: list[FieldChange] = field(default_factory=list)
    changed: list[FieldChange] = field(default_factory=list)
    cleared: list[FieldChange] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)

    @property
    def touched(self) -> bool:
        return bool(self.filled or self.changed or self.cleared)

    def attributes(self) -> list[str]:
        return sorted({change.field for change in (*self.filled, *self.changed, *self.cleared)})

    def evidence(self) -> dict[str, Any]:
        """Changed and cleared values in full; fills only by name, they replace nothing."""

        return {
            change.field: {"from": _jsonable(change.before), "to": _jsonable(change.after)}
            for change in (*self.changed, *self.cleared)
        }


def _rank(source: str, policy: str) -> int:
    if source == SOURCE_REVIEW:
        return 100
    if source in PROVIDER_SOURCES:
        if policy == "ts_first":
            return 60 if source == SOURCE_TS else 50
        return 50
    if source == SOURCE_DERIVED:
        return 30
    if source == SOURCE_RULE:
        return 20
    return 0


def _beats(new: SourceRef, current: SourceRef, policy: str) -> bool:
    new_rank, current_rank = _rank(new.source, policy), _rank(current.source, policy)
    if new_rank != current_rank:
        return new_rank > current_rank
    if new.source == current.source:
        # The same source speaking again is a refresh of what it said before.
        return True
    # Two providers of equal standing: the more recent observation wins, and a
    # tie keeps what is there so repeated runs cannot flip a value back and forth.
    return (new.observed_on or date.min) > (current.observed_on or date.min)


def current_source(state: VehicleState, name: str) -> SourceRef:
    """Where the field's present value came from."""

    encoded = state.field_sources.get(name)
    if encoded:
        return parse_source_ref(encoded)
    return SourceRef(source=state.origin_source, observed_on=state.origin_observed_on)


def _is_origin(state: VehicleState, ref: SourceRef) -> bool:
    return ref.source == state.origin_source and ref.observed_on == state.origin_observed_on


def _jsonable(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return list(value)
    return value


def _same(left: Any, right: Any) -> bool:
    return _jsonable(left) == _jsonable(right)


def _set_alternative(state: VehicleState, name: str, ref: SourceRef, value: Any) -> None:
    """Keep one alternative per source system, replacing what it said before."""

    entries = [
        entry
        for entry in state.field_alternatives.get(name, [])
        if parse_source_ref(entry["source"]).source != ref.source
    ]
    if value is not None:
        entries.append({"source": ref.encode(), "value": _jsonable(value)})
    if entries:
        state.field_alternatives[name] = entries
    else:
        state.field_alternatives.pop(name, None)


def _drop_alternative(state: VehicleState, name: str, source: str) -> None:
    entries = [
        entry
        for entry in state.field_alternatives.get(name, [])
        if parse_source_ref(entry["source"]).source != source
    ]
    if entries:
        state.field_alternatives[name] = entries
    else:
        state.field_alternatives.pop(name, None)


def _set_value(state: VehicleState, name: str, value: Any, ref: SourceRef) -> None:
    state.values[name] = value
    if value is None or _is_origin(state, ref):
        state.field_sources.pop(name, None)
    else:
        state.field_sources[name] = ref.encode()


def _best_alternative(state: VehicleState, name: str) -> tuple[SourceRef, Any] | None:
    policy = FIELDS_BY_NAME[name].policy
    best: tuple[SourceRef, Any] | None = None
    for entry in state.field_alternatives.get(name, []):
        ref = parse_source_ref(entry["source"])
        if best is None or _beats(ref, best[0], policy):
            best = (ref, entry["value"])
    return best


def merge_one(state: VehicleState, name: str, observation: Observation, result: MergeResult) -> None:
    """Merge one observation of one field."""

    spec = FIELDS_BY_NAME[name]
    new_ref = observation.ref
    current_value = state.values.get(name)
    current_ref = current_source(state, name)
    new_value = observation.value

    if new_value is None and not observation.clears:
        if current_value is not None and current_ref.source == new_ref.source:
            # The source that supplied the value no longer states it: fall back to
            # the best other source, or to nothing.
            fallback = _best_alternative(state, name)
            if fallback is None:
                _set_value(state, name, None, new_ref)
                result.cleared.append(FieldChange(name, current_value, None, new_ref.encode()))
            else:
                ref, value = fallback
                _drop_alternative(state, name, ref.source)
                _set_value(state, name, value, ref)
                result.changed.append(FieldChange(name, current_value, value, ref.encode()))
        else:
            _drop_alternative(state, name, new_ref.source)
        return

    if current_value is None:
        if new_value is None:
            return
        _drop_alternative(state, name, new_ref.source)
        _set_value(state, name, new_value, new_ref)
        result.filled.append(FieldChange(name, None, new_value, new_ref.encode()))
        return

    if _same(current_value, new_value):
        # A source confirming what is already there changes nothing -- not even the
        # recorded source. Re-stamping every confirmed value with the newest source
        # would put a reference on most fields of every vehicle after each import,
        # gigabytes that say nothing a reader could act on.
        _drop_alternative(state, name, new_ref.source)
        return

    if _beats(new_ref, current_ref, spec.policy):
        if current_ref.source != new_ref.source:
            _set_alternative(state, name, current_ref, current_value)
        _drop_alternative(state, name, new_ref.source)
        _set_value(state, name, new_value, new_ref)
        change = FieldChange(name, current_value, new_value, new_ref.encode())
        (result.cleared if new_value is None else result.changed).append(change)
        return

    # The present value stands. What the new source said is kept beside it.
    if new_value is not None and not _same(current_value, new_value):
        _set_alternative(state, name, new_ref, new_value)
        result.kept.append(name)
    else:
        _drop_alternative(state, name, new_ref.source)


def merge(state: VehicleState, observations: Mapping[str, Observation | None]) -> MergeResult:
    """Merge a batch of observations; unknown fields are a programming error."""

    result = MergeResult()
    for name, observation in observations.items():
        if name not in FIELDS_BY_NAME:
            raise KeyError(f"{name!r} is not a vehicle field")
        if observation is None:
            continue
        merge_one(state, name, observation, result)
    return result


def retract(state: VehicleState, name: str, source: str, ref: str | None = None) -> MergeResult:
    """A source withdraws what it said (a retired review or rule), wherever it sits.

    When `ref` is given only that exact assertion is withdrawn -- retiring one rule
    must not undo another rule's value on the same field.
    """

    result = MergeResult()
    current_ref = current_source(state, name)
    matches_current = current_ref.source == source and (ref is None or current_ref.ref == ref)
    if matches_current and state.values.get(name) is not None:
        before = state.values.get(name)
        fallback = _best_alternative(state, name)
        if fallback is None:
            _set_value(state, name, None, current_ref)
            result.cleared.append(FieldChange(name, before, None, current_ref.encode()))
        else:
            fallback_ref, value = fallback
            _drop_alternative(state, name, fallback_ref.source)
            _set_value(state, name, value, fallback_ref)
            result.changed.append(FieldChange(name, before, value, fallback_ref.encode()))
        return result
    entries = [
        entry
        for entry in state.field_alternatives.get(name, [])
        if not (
            parse_source_ref(entry["source"]).source == source
            and (ref is None or parse_source_ref(entry["source"]).ref == ref)
        )
    ]
    if entries:
        state.field_alternatives[name] = entries
    else:
        state.field_alternatives.pop(name, None)
    return result


def derive(state: VehicleState, observed_on: date | None) -> MergeResult:
    """Values a vehicle's own data implies, below any source and above any rule.

    An electric car's power: TS records it only in its EV fields, so without this
    310k EVs reach matching with no power at all. The AIS export states the same
    figure; when it does, the provider value outranks this one anyway.
    """

    observations: dict[str, Observation | None] = {}
    if (
        state.values.get("power_kw") is None
        and state.values.get("fuel") == "electricity"
        and state.values.get("ev_power_kw") is not None
    ):
        observations["power_kw"] = Observation(
            state.values["ev_power_kw"],
            SourceRef(SOURCE_DERIVED, "ev_power", observed_on),
        )
    return merge(state, observations)


def ais_ref(extract_id: str, exported_on: date) -> SourceRef:
    return SourceRef(SOURCE_AIS, extract_id, exported_on)


def ts_ref(record_id: int, observed_on: date | None) -> SourceRef:
    return SourceRef(SOURCE_TS, str(record_id), observed_on)
