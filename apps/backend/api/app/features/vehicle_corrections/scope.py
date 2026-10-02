"""Which cars a correction applies to: the scopes a person can choose from.

A correction is entered on one car (the anchor). Besides that car alone it can
apply to the cars with exactly the same data, or to "all cars like this": the
same make and the same present value for the corrected field, pinned further by
a few columns per field. Those columns form a ladder, narrowest first; each rung
is its own option. The rungs come from a what-if run on the pilot sample, which
measured how many cars each scope fixes and how many it harms
(docs/vehicle-fact-corrections.md).

A scope is a list of plain conditions on `core.vehicles` columns -- equality,
emptiness, a bound; never a computed key -- compiled by
`ingestion.vehicle_core_query`. Every scope starts with the anchor's
manufacturer, the column every scope query is driven from, and a car without
one offers no scope beyond itself. The SQL only selects candidates: each one is
then read through the matcher's own seam and checked exactly
(`fields.same_present`).

Pure functions: nothing here reads or writes a database.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

from api.app.features.vehicle_corrections import fields
from api.app.features.vehicle_corrections.schemas import (
    NarrowableField,
    PreviewScope,
    ScopeCondition,
)
from ingestion.vehicle_core_fields import FIELDS_BY_NAME, INTEGER_TYPES
from ingestion.vehicle_core_query import VehicleTerm

MultiKind = Literal["same_data", "like_this"]

#: The vehicle columns a scope reads off the anchor car.
BASE_COLUMNS: tuple[str, ...] = ("manufacturer", "vehicle_scope")
#: "Exactly the same data": every value the matcher reads off a vehicle.
SAME_DATA_COLUMNS: tuple[str, ...] = (
    "model_family", "production_year", "production_month", "power_kw", "displacement_cc",
    "engine_code", "drive_type", "bodywork_form", "fuel", "fuel_secondary",
    "electrification_type",
)
#: Columns a person may narrow a scope on, each prefilled with the anchor's own value.
NARROWABLE: tuple[str, ...] = (
    "engine_code", "power_kw", "displacement_cc", "production_year", "variant_code",
    "version_code", "type_approval", "drive_type", "bodywork_form", "fuel",
)
#: The columns that carry a field's present value; the field's own column unless listed.
_PRESENT_COLUMNS: dict[str, tuple[str, ...]] = {"fuel": ("fuel", "fuel_secondary")}
_MODEL = ("model_family",)
#: "All cars like this", per corrected field: the columns each rung pins besides
#: the make, the vehicle type and the field's present value, narrowest first.
#: A field that is not listed offers only this car and the cars with the same data.
_LADDERS: dict[str, tuple[tuple[str, ...], ...]] = {
    "drive_type": ((*_MODEL, "registry_type_code"), _MODEL),
    "bodywork_form": ((*_MODEL, "registry_type_code"),),
    "power_kw": ((*_MODEL, "engine_code"),),
    "model_family": (("registry_brand_text", "registry_model_text"),),
    "manufacturer": (("registry_make_code", "registry_brand_text"),),
    "fuel": ((*_MODEL, "engine_code"),),
    "electrification_type": ((*_MODEL, "engine_code"),),
}
ANCHOR_COLUMNS: tuple[str, ...] = tuple(
    dict.fromkeys(
        (
            *BASE_COLUMNS,
            *SAME_DATA_COLUMNS,
            *NARROWABLE,
            *(column for rungs in _LADDERS.values() for rung in rungs for column in rung),
        )
    )
)

# How a column reads in a label: its words, and the unit a value carries.
_WORDS: dict[str, tuple[str, str]] = {
    "vehicle_scope": ("vehicle type", ""),
    "model_family": ("model family", ""),
    "engine_code": ("engine code", ""),
    "power_kw": ("power", " kW"),
    "displacement_cc": ("displacement", " cc"),
    "drive_type": ("drive type", ""),
    "bodywork_form": ("bodywork", ""),
    "fuel": ("fuel", ""),
    "fuel_secondary": ("second fuel", ""),
    "electrification_type": ("electrification", ""),
    "production_year": ("production year", ""),
    "production_month": ("build month", ""),
    "registry_type_code": ("registry type code", ""),
    "registry_make_code": ("registry make code", ""),
    "registry_brand_text": ("brand text", ""),
    "registry_model_text": ("model text", ""),
    "variant_code": ("variant", ""),
    "version_code": ("version", ""),
    "type_approval": ("type approval", ""),
}
#: Registry text is quoted in a label: it may hold several words.
_QUOTED = frozenset({"registry_brand_text", "registry_model_text"})
_PASSENGER = "passenger"


class ScopeTooBroadError(ValueError):
    """The scope has no manufacturer, or could not be counted in time."""


class ScopeNotOfferedError(ValueError):
    """The field does not offer this kind of scope."""


class InvalidScopeError(ValueError):
    """The scope names a rung, a column or a comparison that cannot be used."""


@dataclass(frozen=True)
class Scope:
    """One set of cars a correction can apply to, as conditions on vehicle columns."""

    kind: MultiKind
    field: str
    #: `like_this`: the rung of the field's ladder, 0 the narrowest.
    rung: int | None
    #: The manufacturer first, then the vehicle type, the rung's columns, the
    #: field's present value and whatever a person narrowed on.
    conditions: tuple[ScopeCondition, ...]

    @property
    def terms(self) -> list[VehicleTerm]:
        return [(item.field, item.operator, tuple(item.values)) for item in self.conditions]

    @property
    def manufacturer(self) -> str:
        return self.conditions[0].values[0]

    @property
    def model_family(self) -> str | None:
        """The model the scope is pinned to; None when it pins none or the absence of one."""

        pinned = next((item for item in self.conditions if item.field == "model_family"), None)
        return pinned.values[0] if pinned is not None and pinned.operator == "equals" else None

    def stored(self, anchor_value: str | None) -> dict[str, Any]:
        """The scope as a decision stores it, with the present value every car was checked for."""

        return {
            "kind": self.kind,
            "rung": self.rung,
            "conditions": [item.model_dump() for item in self.conditions],
            "anchor_value": anchor_value,
        }


def _text(value: Any) -> str | None:
    """A vehicle column's value as a condition carries it; None when the car has none."""

    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _pin(anchor: Mapping[str, Any], column: str) -> ScopeCondition:
    """The condition that holds a column to the anchor's value, or to the absence of one."""

    value = _text(anchor.get(column))
    if value is None:
        return ScopeCondition(field=column, operator="is_empty")
    return ScopeCondition(field=column, operator="equals", values=[value])


def _base(anchor: Mapping[str, Any]) -> list[ScopeCondition]:
    if _text(anchor.get("manufacturer")) is None:
        raise ScopeTooBroadError(
            "This car has no manufacturer, so a correction can only apply to this car."
        )
    return [_pin(anchor, column) for column in BASE_COLUMNS]


def _with(conditions: list[ScopeCondition], anchor: Mapping[str, Any],
          columns: Sequence[str]) -> list[ScopeCondition]:
    pinned = {item.field for item in conditions}
    return [*conditions, *(_pin(anchor, column) for column in columns if column not in pinned)]


def _rungs(field: str, action: str, current: str | None) -> tuple[tuple[str, ...], ...]:
    """The field's ladder for this correction: the columns of each rung, narrowest first."""

    if field == "engine_code":
        if current is None:
            return ((*_MODEL, "power_kw", "displacement_cc", "fuel"),)
        if action == "ignore":
            return (_MODEL,)
        return ((*_MODEL, "power_kw"), _MODEL)
    return _LADDERS.get(field, ())


def same_data(anchor: Mapping[str, Any], field: str) -> Scope:
    """The cars whose every matcher-read vehicle value is the anchor's."""

    fields.spec_for(field)
    conditions = _with(_base(anchor), anchor, SAME_DATA_COLUMNS)
    return Scope("same_data", field, None, tuple(conditions))


def ladder(anchor: Mapping[str, Any], field: str, action: str, current: str | None) -> list[Scope]:
    """The "all cars like this" options for a correction, narrowest first.

    `current` is what the matcher uses for the field on the anchor today: it
    says which ladder applies (a car with an engine code is not grouped like
    one without). Empty for a field that offers none.
    """

    fields.spec_for(field)
    present = _PRESENT_COLUMNS.get(field, (field,))
    return [
        Scope("like_this", field, index, tuple(_with(_with(_base(anchor), anchor, rung), anchor, present)))
        for index, rung in enumerate(_rungs(field, action, current))
    ]


def options(anchor: Mapping[str, Any], field: str, action: str, current: str | None) -> list[Scope]:
    """Every scope beyond the anchor itself; empty when the car has no manufacturer."""

    try:
        return [same_data(anchor, field), *ladder(anchor, field, action, current)]
    except ScopeTooBroadError:
        return []


def narrowable(scope: Scope, anchor: Mapping[str, Any]) -> list[NarrowableField]:
    """The columns the scope does not pin yet, each with the anchor's own value."""

    pinned = {item.field for item in scope.conditions}
    return [
        NarrowableField(
            field=column, label=FIELDS_BY_NAME[column].label, value=_text(anchor.get(column))
        )
        for column in NARROWABLE
        if column not in pinned
    ]


def _narrow(condition: ScopeCondition) -> ScopeCondition:
    if condition.field not in NARROWABLE:
        raise InvalidScopeError(f"A scope cannot be narrowed on {condition.field!r}.")
    whole_number = FIELDS_BY_NAME[condition.field].sql_type in INTEGER_TYPES
    if condition.operator in {"gte", "lte"} and not whole_number:
        raise InvalidScopeError(f"{condition.field} takes no lower or upper bound.")
    if whole_number and any(
        not (value.strip().isascii() and value.strip().isdigit()) for value in condition.values
    ):
        raise InvalidScopeError(f"{condition.field} is narrowed on whole numbers.")
    return condition.model_copy(update={"values": [value.strip() for value in condition.values]})


def _key(condition: ScopeCondition) -> tuple[str, str, tuple[str, ...]]:
    return condition.field, condition.operator, tuple(sorted(condition.values))


def resolve(
    anchor: Mapping[str, Any],
    field: str,
    action: str,
    current: str | None,
    requested: PreviewScope,
) -> Scope:
    """The scope a check runs on: one of this car's options, narrowed as asked.

    The conditions are always built here from the car itself; what the request
    carries only says which option is meant. Raises `ScopeTooBroadError` for a
    car without a manufacturer, `ScopeNotOfferedError` when the field has no
    such scope, and `InvalidScopeError` for a rung that does not exist, echoed
    conditions that are no option of this car, or a narrowing that is not allowed.
    """

    if requested.kind == "same_data":
        chosen = same_data(anchor, field)
    else:
        rungs = ladder(anchor, field, action, current)
        if not rungs:
            raise ScopeNotOfferedError(
                f"A correction of {fields.spec_for(field).label} applies to this car or to "
                "the cars with exactly the same data."
            )
        if requested.rung is not None:
            if requested.rung >= len(rungs):
                raise InvalidScopeError("This car offers no such scope.")
            chosen = rungs[requested.rung]
        elif requested.conditions is not None:
            echoed = {_key(item) for item in requested.conditions}
            matching = [
                rung for rung in rungs if {_key(item) for item in rung.conditions} == echoed
            ]
            if not matching:
                raise InvalidScopeError("These conditions are not a scope this car offers.")
            chosen = matching[0]
        else:
            chosen = rungs[0]
    narrowed = [_narrow(condition) for condition in requested.narrow]
    return replace(chosen, conditions=(*chosen.conditions, *narrowed))


def _joined(parts: Sequence[str]) -> str:
    if len(parts) < 2:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _phrase(condition: ScopeCondition) -> str:
    words, unit = _WORDS.get(condition.field, (condition.field.replace("_", " "), ""))
    if condition.operator == "is_empty":
        return f"no {words}"
    shown = [
        f"“{value}”" if condition.field in _QUOTED else f"{value}{unit}"
        for value in condition.values
    ]
    if condition.operator == "gte":
        return f"{words} {shown[0]} or more"
    if condition.operator == "lte":
        return f"{words} {shown[0]} or less"
    return f"{words} {' or '.join(shown)}"


def label(scope: Scope, count: int | None = None) -> str:
    """The scope in plain words, as the person sees it and a decision stores it.

    "All Volvo V70 cars with no drive type"; for the cars with the same data,
    "The 4 cars with exactly the same data" once they are counted.
    """

    if scope.kind == "same_data":
        narrowed = [_phrase(item) for item in scope.conditions if item.field not in
                    (*BASE_COLUMNS, *SAME_DATA_COLUMNS)]
        tail = f", {_joined(narrowed)}" if narrowed else ""
        if count is None:
            return f"Cars with exactly the same data{tail}"
        if count == 1:
            return f"The one car with exactly this data{tail}"
        return f"The {count:,} cars with exactly the same data{tail}"

    by_field = {item.field: item for item in scope.conditions[: len(BASE_COLUMNS)]}
    vehicle_type = by_field["vehicle_scope"]
    passenger = vehicle_type.operator == "equals" and vehicle_type.values == [_PASSENGER]
    head = ["All", scope.manufacturer]
    if scope.model_family is not None:
        head.append(scope.model_family)
    head.append("cars" if passenger else "vehicles")
    present = _PRESENT_COLUMNS.get(scope.field, (scope.field,))
    rest = [item for item in scope.conditions[1:] if not (
        (item.field == "vehicle_scope" and passenger)
        or (item.field == "model_family" and item.operator == "equals"
            and item.values == [scope.model_family])
    )]
    # The corrected field's own present value leads: it is why these cars are alike.
    ordered = [
        *(item for item in rest if item.field in present),
        *(item for item in rest if item.field not in present),
    ]
    phrases = list(dict.fromkeys(_phrase(item) for item in ordered))
    return " ".join(head) + (f" with {_joined(phrases)}" if phrases else "")
