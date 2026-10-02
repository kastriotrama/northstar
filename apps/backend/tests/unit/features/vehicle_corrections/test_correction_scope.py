"""Which cars a correction applies to: the scopes, their conditions and their words."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from api.app.features.vehicle_corrections import fields, scope
from api.app.features.vehicle_corrections.schemas import PreviewScope, ScopeCondition
from api.app.features.vehicle_corrections.scope import (
    InvalidScopeError,
    ScopeNotOfferedError,
    ScopeTooBroadError,
)
from ingestion.vehicle_core_query import compile_vehicle_filter

#: A made-up car's vehicle values: an estate with no drive type and one fuel.
ANCHOR: dict[str, Any] = {
    "manufacturer": "VOLVO", "vehicle_scope": "passenger", "model_family": "V70",
    "production_year": 2012, "production_month": 3, "power_kw": 120, "displacement_cc": 1984,
    "engine_code": "B4204T", "drive_type": None, "bodywork_form": "estate", "fuel": "petrol",
    "fuel_secondary": None, "electrification_type": None, "variant_code": "BW",
    "version_code": None, "type_approval": "e4*2001/116*0076", "registry_type_code": "B",
    "registry_make_code": "VOLVO", "registry_brand_text": "VOLVO V70",
    "registry_model_text": "V70 II",
}


def _pins(option: scope.Scope) -> list[tuple[str, str, list[str]]]:
    return [(item.field, item.operator, item.values) for item in option.conditions]


def _rungs(field: str, action: str = "set", current: str | None = "x",
           **anchor: Any) -> list[list[str]]:
    """Each rung's columns besides the make and the vehicle type, in order."""

    return [
        [item.field for item in rung.conditions[2:]]
        for rung in scope.ladder({**ANCHOR, **anchor}, field, action, current)
    ]


def test_every_scope_starts_with_the_make_and_carries_the_vehicle_type() -> None:
    for field in fields.SPECS:
        for option in scope.options(ANCHOR, field, "set", None):
            assert _pins(option)[:2] == [
                ("manufacturer", "equals", ["VOLVO"]), ("vehicle_scope", "equals", ["passenger"]),
            ], field
            assert option.manufacturer == "VOLVO"
            # Only plain comparisons of real vehicle columns: the filter compiles them.
            compiled = compile_vehicle_filter(option.terms)
            assert compiled.sql.startswith("v.manufacturer = ANY(%s) AND v.vehicle_scope = ANY(%s)")


def test_the_same_data_scope_pins_every_value_the_matcher_reads() -> None:
    option = scope.same_data(ANCHOR, "drive_type")

    assert (option.kind, option.rung) == ("same_data", None)
    assert [item.field for item in option.conditions] == [
        "manufacturer", "vehicle_scope", "model_family", "production_year", "production_month",
        "power_kw", "displacement_cc", "engine_code", "drive_type", "bodywork_form", "fuel",
        "fuel_secondary", "electrification_type",
    ]
    by_field = {item.field: item for item in option.conditions}
    # A value the car has is pinned to it; one it lacks, to its absence.
    assert (by_field["power_kw"].operator, by_field["power_kw"].values) == ("equals", ["120"])
    for empty in ("drive_type", "fuel_secondary", "electrification_type"):
        assert (by_field[empty].operator, by_field[empty].values) == ("is_empty", [])
    assert scope.label(option) == "Cars with exactly the same data"
    assert scope.label(option, 1) == "The one car with exactly this data"
    assert scope.label(option, 4275) == "The 4,275 cars with exactly the same data"


def test_each_field_has_its_own_ladder_narrowest_first() -> None:
    # The engine code: with the power first, then without; ignoring it, or a
    # car without one, has one rung.
    assert _rungs("engine_code", current="B4204T") == [
        ["model_family", "power_kw", "engine_code"], ["model_family", "engine_code"]]
    assert _rungs("engine_code", "ignore", "B4204T") == [["model_family", "engine_code"]]
    assert _rungs("engine_code", current=None, engine_code=None) == [
        ["model_family", "power_kw", "displacement_cc", "fuel", "engine_code"]]
    # The drive type: within the registry's type code first, then the whole model.
    assert _rungs("drive_type", current=None) == [
        ["model_family", "registry_type_code", "drive_type"], ["model_family", "drive_type"]]
    assert _rungs("bodywork_form") == [["model_family", "registry_type_code", "bodywork_form"]]
    assert _rungs("power_kw") == [["model_family", "engine_code", "power_kw"]]
    assert _rungs("model_family") == [
        ["registry_brand_text", "registry_model_text", "model_family"]]
    assert _rungs("manufacturer") == [["registry_make_code", "registry_brand_text"]]
    assert _rungs("fuel") == [["model_family", "engine_code", "fuel", "fuel_secondary"]]
    assert _rungs("electrification_type") == [
        ["model_family", "engine_code", "electrification_type"]]
    # The dates and the displacement are mostly one car's own: same data only.
    for alone in ("displacement_cc", "production_year", "production_month"):
        assert _rungs(alone) == []
        assert [option.kind for option in scope.options(ANCHOR, alone, "set", "1")] == [
            "same_data"]


def test_a_rung_pins_the_absence_of_a_value_the_car_does_not_have() -> None:
    narrowest, wider = scope.ladder(ANCHOR, "drive_type", "set", None)

    assert _pins(narrowest)[2:] == [
        ("model_family", "equals", ["V70"]), ("registry_type_code", "equals", ["B"]),
        ("drive_type", "is_empty", []),
    ]
    assert (narrowest.rung, wider.rung, wider.model_family) == (0, 1, "V70")
    # A car without a type code, or with a wrong drive type, is grouped by that.
    odd = scope.ladder({**ANCHOR, "registry_type_code": " ", "drive_type": "rwd"},
                       "drive_type", "set", "rwd")[0]
    assert _pins(odd)[3:] == [
        ("registry_type_code", "is_empty", []), ("drive_type", "equals", ["rwd"])]
    no_engine = scope.ladder({**ANCHOR, "engine_code": None}, "power_kw", "set", "120")[0]
    assert ("engine_code", "is_empty", []) in _pins(no_engine)
    # A scope on a model that is missing pins its absence, and says so in its copy.
    no_model = scope.ladder({**ANCHOR, "model_family": None}, "drive_type", "set", None)[1]
    assert (no_model.model_family, _pins(no_model)[2]) == (None, ("model_family", "is_empty", []))


def test_labels_say_in_plain_words_which_cars() -> None:
    def labels(field: str, action: str = "set", current: str | None = "x",
               **anchor: Any) -> list[str]:
        return [scope.label(rung)
                for rung in scope.ladder({**ANCHOR, **anchor}, field, action, current)]

    assert labels("drive_type", current=None) == [
        "All VOLVO V70 cars with no drive type and registry type code B",
        "All VOLVO V70 cars with no drive type",
    ]
    assert labels("engine_code", manufacturer="TESLA", model_family=None, engine_code="3DU",
                  power_kw=None)[1] == "All TESLA cars with engine code 3DU and no model family"
    assert labels("power_kw") == ["All VOLVO V70 cars with power 120 kW and engine code B4204T"]
    assert labels("fuel", fuel_secondary="electricity") == [
        "All VOLVO V70 cars with fuel petrol, second fuel electricity and engine code B4204T"]
    assert labels("model_family") == [
        "All VOLVO V70 cars with brand text “VOLVO V70” and model text “V70 II”"]
    # Anything but a passenger car is a vehicle, and its type is named.
    assert labels("bodywork_form", vehicle_scope="light_commercial") == [
        ("All VOLVO V70 vehicles with bodywork estate, vehicle type light_commercial and "
         "registry type code B")]


def test_a_car_without_a_manufacturer_offers_no_scope_beyond_itself() -> None:
    for blank in (None, "", "  "):
        anchor = {**ANCHOR, "manufacturer": blank}
        assert scope.options(anchor, "drive_type", "set", None) == []
        with pytest.raises(ScopeTooBroadError):
            scope.same_data(anchor, "drive_type")
        with pytest.raises(ScopeTooBroadError):
            scope.resolve(anchor, "drive_type", "set", None, PreviewScope(kind="like_this"))
    with pytest.raises(fields.FieldNotCorrectableError):
        scope.same_data(ANCHOR, "colour")


def test_narrowing_adds_conditions_prefilled_from_the_car() -> None:
    wide = scope.ladder(ANCHOR, "drive_type", "set", None)[1]

    offered = {item.field: item.value for item in scope.narrowable(wide, ANCHOR)}
    # What the scope pins already is not offered again; a value the car lacks is null.
    assert offered == {
        "engine_code": "B4204T", "power_kw": "120", "displacement_cc": "1984",
        "production_year": "2012", "variant_code": "BW", "version_code": None,
        "type_approval": "e4*2001/116*0076", "bodywork_form": "estate", "fuel": "petrol",
    }
    assert [item.label for item in scope.narrowable(wide, ANCHOR)][:2] == [
        "Engine code", "Power (kW)"]

    narrowed = scope.resolve(ANCHOR, "drive_type", "set", None, PreviewScope(
        kind="like_this", rung=1,
        narrow=[
            ScopeCondition(field="production_year", operator="gte", values=[" 2010 "]),
            ScopeCondition(field="production_year", operator="lte", values=["2014"]),
            ScopeCondition(field="engine_code", values=["B4204T", "B4204T2"]),
            ScopeCondition(field="version_code", operator="is_empty"),
        ],
    ))
    assert narrowed.conditions[:4] == wide.conditions
    assert _pins(narrowed)[4:] == [
        ("production_year", "gte", ["2010"]), ("production_year", "lte", ["2014"]),
        ("engine_code", "equals", ["B4204T", "B4204T2"]), ("version_code", "is_empty", []),
    ]
    assert scope.label(narrowed) == (
        "All VOLVO V70 cars with no drive type, production year 2010 or more, production "
        "year 2014 or less, engine code B4204T or B4204T2 and no version")
    compiled = compile_vehicle_filter(narrowed.terms)
    assert "v.version_code IS NULL OR v.version_code = ''" in compiled.sql
    assert compiled.sql.startswith("v.manufacturer = ANY(%s)")
    assert compiled.parameters[0] == ["VOLVO"]
    same = scope.resolve(ANCHOR, "drive_type", "set", None, PreviewScope(
        kind="same_data", narrow=[ScopeCondition(field="variant_code", values=["BW"])]))
    assert scope.label(same, 3) == "The 3 cars with exactly the same data, variant BW"


@pytest.mark.parametrize(
    "condition",
    [
        ScopeCondition(field="manufacturer", values=["SAAB"]),  # never widened or moved
        ScopeCondition(field="plate", values=["ABC123"]),
        ScopeCondition(field="engine_code", operator="gte", values=["B"]),
        ScopeCondition(field="power_kw", values=["many"]),
        ScopeCondition(field="production_year", operator="gte", values=["20x0"]),
    ],
)
def test_a_narrowing_that_is_not_allowed_is_refused(condition: ScopeCondition) -> None:
    with pytest.raises(InvalidScopeError):
        scope.resolve(ANCHOR, "drive_type", "set", None,
                      PreviewScope(kind="like_this", narrow=[condition]))


def test_the_chosen_option_is_rebuilt_from_the_car_never_taken_from_the_request() -> None:
    narrowest, wider = scope.ladder(ANCHOR, "drive_type", "set", None)

    def chosen(**requested: Any) -> scope.Scope:
        return scope.resolve(ANCHOR, "drive_type", "set", None,
                             PreviewScope(kind="like_this", **requested))

    # By rung, by the option's conditions as the scopes call returned them, or the narrowest.
    assert chosen(rung=1) == wider
    assert chosen(conditions=list(wider.conditions)) == wider
    assert chosen(conditions=list(reversed(narrowest.conditions))) == narrowest
    assert chosen() == narrowest
    with pytest.raises(InvalidScopeError):
        chosen(rung=2)
    # Conditions that are no option of this car (a wider make, a dropped pin) are refused.
    with pytest.raises(InvalidScopeError):
        chosen(conditions=list(wider.conditions[:1]))
    with pytest.raises(InvalidScopeError):
        chosen(conditions=[ScopeCondition(field="manufacturer", values=["VOLVO", "SAAB"]),
                           *wider.conditions[1:]])
    with pytest.raises(ScopeNotOfferedError):
        scope.resolve(ANCHOR, "production_year", "set", "2012", PreviewScope(kind="like_this"))
    same = scope.resolve(ANCHOR, "production_year", "set", "2012", PreviewScope(kind="same_data"))
    assert same == scope.same_data(ANCHOR, "production_year")


def test_a_scope_is_stored_with_the_value_every_car_was_checked_for() -> None:
    wider = scope.ladder(ANCHOR, "drive_type", "set", None)[1]

    stored = wider.stored(None)

    assert set(stored) == {"kind", "rung", "conditions", "anchor_value"}
    assert (stored["kind"], stored["rung"], stored["anchor_value"]) == ("like_this", 1, None)
    assert stored["conditions"][0] == {
        "field": "manufacturer", "operator": "equals", "values": ["VOLVO"]}


@pytest.mark.parametrize(
    "condition",
    [
        {"field": "drive_type", "operator": "is_empty", "values": ["x"]},
        {"field": "drive_type", "operator": "equals", "values": []},
        {"field": "power_kw", "operator": "gte", "values": ["1", "2"]},
        {"field": "drive_type", "operator": "contains", "values": ["x"]},
        {"field": "drive_type", "values": [" "]},
        {"field": "drive_type", "values": ["a\u0000b"]},
    ],
)
def test_a_malformed_condition_is_refused_by_the_contract(condition: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ScopeCondition(**condition)
