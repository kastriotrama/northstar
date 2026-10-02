"""The correctable set: what each field accepts, hands the matcher, copies and shows."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from correction_test_support import CANDIDATES, CAR, SOURCES, candidate, inputs_of, row

from api.app.features.match_review.field_resolution import RESOLVABLE_TARGETS, _canonical_values
from api.app.features.vehicle_corrections import fields
from api.app.features.vehicle_corrections.fields import (
    FieldNotCorrectableError,
    InvalidValueError,
    canonical_value,
    describe,
    matcher_values,
    same_value,
    states,
    vehicle_copy,
)
from ingestion.normalization_rules import fuel_carriers, fuel_match_tokens, normalize_ts_record
from ingestion.vehicle_core_fields import FIELDS_BY_NAME, REVIEWABLE_FIELDS

TODAY = date(2026, 10, 2)
ADDED = ("production_month", "fuel", "electrification_type")


# ----------------------------------------------------------------------- the definition


def test_the_set_is_the_reviewable_fields_then_month_fuel_and_electrification() -> None:
    assert tuple(fields.SPECS) == (*REVIEWABLE_FIELDS, *ADDED)
    assert [(spec.label, spec.type) for spec in fields.SPECS.values()] == [
        ("Manufacturer", "text"), ("Model family", "text"), ("Engine code", "text"),
        ("Power (kW)", "integer"), ("Displacement (cc)", "integer"), ("Drive type", "text"),
        ("Bodywork", "text"), ("Production year", "integer"), ("Build month", "integer"),
        ("Fuel", "list"), ("Electrification", "text"),
    ]
    assert {name: spec.evidence_keys for name, spec in fields.SPECS.items()} == {
        "manufacturer": ("manufacturer",), "model_family": ("model", "model_series"),
        "engine_code": ("engine_code",), "power_kw": ("power_kw",),
        "displacement_cc": ("displacement_cc",), "drive_type": ("drive_type",),
        "bodywork_form": ("bodywork",), "production_year": ("year",),
        "production_month": ("year_month",), "fuel": ("fuels",),
        "electrification_type": ("electrification", "match_guard:plug_in"),
    }


def test_every_field_has_the_rules_its_type_needs() -> None:
    for name, spec in fields.SPECS.items():
        assert (spec.bounds is not None) == (spec.type == "integer"), name
        assert spec.matcher_keys and spec.vehicle_columns, name
        # The copy lives in real vehicle columns, and the table takes the name.
        assert all(column in FIELDS_BY_NAME for column in spec.vehicle_columns), name
        assert name.replace("_", "").isalpha() and name == name.lower(), name
    bounds = {name: spec.bounds for name, spec in fields.SPECS.items() if spec.bounds}
    assert bounds == {
        "power_kw": (1, 2000), "displacement_cc": (1, 20000), "production_year": (1880, None),
        "production_month": (1, 12),
    }


def test_closed_vocabularies_are_the_reviewed_ones() -> None:
    closed = {name: spec.values for name, spec in fields.SPECS.items() if spec.values}

    assert set(closed) == {"drive_type", "bodywork_form", "fuel", "electrification_type"}
    assert closed["drive_type"] == RESOLVABLE_TARGETS["drive_type"] == ("fwd", "rwd", "awd")
    assert closed["bodywork_form"] == RESOLVABLE_TARGETS["bodywork_form"]
    assert closed["electrification_type"] == (
        "battery_electric", "fuel_cell_hybrid", "hybrid", "plug_in_hybrid")
    # The carriers the reviewed rules know, in the registry's own code order,
    # so that a hybrid reads "petrol,electricity" as its registration does.
    assert closed["fuel"][:3] == ("petrol", "diesel", "electricity")
    assert set(closed["fuel"]) == set(_canonical_values("energy_sources"))
    assert len(set(closed["fuel"])) == len(closed["fuel"])


def test_what_the_matcher_reads_and_the_vehicle_carries_per_field() -> None:
    reads = {name: (spec.matcher_keys, spec.matcher_fallbacks) for name, spec in fields.SPECS.items()}
    date_fallback = ("production_date", "production_date_precision")

    for name in ("manufacturer", "model_family", "engine_code", "power_kw", "displacement_cc",
                 "drive_type", "bodywork_form", "electrification_type"):
        assert reads[name] == ((name,), ())
        assert fields.SPECS[name].vehicle_columns == (name,)
    # The year and the month are one build month to the matcher; the
    # normalization's own date would speak for either once it is corrected.
    assert reads["production_year"] == (("production_year",), date_fallback)
    assert reads["production_month"] == (("production_month",), date_fallback)
    assert reads["fuel"] == (("energy_sources", "fuel_match_tokens"), ())
    assert fields.SPECS["fuel"].vehicle_columns == ("fuel", "fuel_secondary", "fuel_match_tokens")
    suggested = {name for name, spec in fields.SPECS.items() if spec.candidate_values}
    assert suggested == {"model_family", "engine_code", "power_kw", "displacement_cc",
                         "drive_type", "bodywork_form"}


# --------------------------------------------------------------------- what a field accepts


@pytest.mark.parametrize(
    ("field", "raw", "stored"),
    [
        ("power_kw", "150", "150"),
        ("power_kw", " 0150 ", "150"),
        ("power_kw", "1", "1"),
        ("power_kw", "2000", "2000"),
        ("displacement_cc", "1969", "1969"),
        ("displacement_cc", "20000", "20000"),
        ("production_year", "1880", "1880"),
        ("production_year", "2027", "2027"),  # next year's cars are registered this year
        ("production_month", "1", "1"),
        ("production_month", "09", "9"),
        ("production_month", "12", "12"),
        ("drive_type", "awd", "awd"),
        ("drive_type", " fwd ", "fwd"),
        ("bodywork_form", "estate", "estate"),
        ("electrification_type", "plug_in_hybrid", "plug_in_hybrid"),
        ("fuel", "diesel", "diesel"),
        ("fuel", "petrol,electricity", "petrol,electricity"),
        ("fuel", " electricity , petrol ", "petrol,electricity"),  # vocabulary order
        ("fuel", "cng,\npetrol,e85", "petrol,cng,e85"),
        ("manufacturer", "  Volvo ", "Volvo"),
        ("model_family", "XC60  II", "XC60 II"),
        ("engine_code", "D 4204\tT14\n", "D 4204 T14"),
        ("engine_code", "x" * 80, "x" * 80),
    ],
)
def test_an_accepted_value_is_stored_in_one_spelling(field: str, raw: str, stored: str) -> None:
    assert canonical_value(field, raw, today=TODAY) == stored
    # Stored once more it is itself, and it hands the matcher and the vehicle something.
    assert canonical_value(field, stored, today=TODAY) == stored
    assert matcher_values(field, stored) and vehicle_copy(field, stored)


@pytest.mark.parametrize(
    ("field", "raw"),
    [
        # blank
        ("power_kw", None), ("power_kw", ""), ("engine_code", "   "), ("drive_type", ""),
        ("fuel", ""), ("fuel", " , "), ("production_month", " "),
        # the registry's own words for "not known" are no value
        ("engine_code", "-"), ("model_family", "unknown"), ("manufacturer", "Okänd"),
        # wrong type
        ("power_kw", "abc"), ("power_kw", "150.5"), ("power_kw", "-5"), ("power_kw", "1e3"),
        ("power_kw", "１５０"), ("displacement_cc", "2,0"), ("production_year", "2015-03"),
        ("production_month", "March"),
        # out of range
        ("power_kw", "0"), ("power_kw", "2001"), ("displacement_cc", "0"),
        ("displacement_cc", "20001"), ("production_year", "1879"), ("production_year", "2028"),
        ("production_month", "0"), ("production_month", "13"), ("power_kw", "9" * 40),
        # not in the closed vocabulary
        ("drive_type", "4wd"), ("drive_type", "AWD"), ("drive_type", "2wd"),
        ("bodywork_form", "wagon"), ("electrification_type", "mild_hybrid"),
        ("electrification_type", "hybrid,plug_in_hybrid"), ("fuel", "gasoline"),
        ("fuel", "petrol,hybrid_petrol"), ("fuel", "petrol;electricity"), ("fuel", "Petrol"),
        # a list names one to three different carriers
        ("fuel", "petrol,petrol"), ("fuel", "petrol,diesel,electricity,cng"), ("fuel", "petrol,"),
        # too long
        ("engine_code", "x" * 81), ("manufacturer", "y" * 200), ("model_family", "z" * 5000),
        # control characters (PostgreSQL text cannot hold NUL)
        ("engine_code", "DF\x00GA"), ("manufacturer", "Vol\x07vo"), ("fuel", "petrol\x00"),
    ],
)
def test_a_value_the_field_does_not_take_is_refused(field: str, raw: str | None) -> None:
    with pytest.raises(InvalidValueError) as caught:
        canonical_value(field, raw, today=TODAY)

    assert fields.SPECS[field].label in str(caught.value)


def test_the_production_year_ceiling_moves_with_the_calendar() -> None:
    assert canonical_value("production_year", "2031", today=date(2030, 1, 1)) == "2031"
    with pytest.raises(InvalidValueError, match="between 1880 and 2031"):
        canonical_value("production_year", "2032", today=date(2030, 12, 31))
    # Without a date it is today's.
    this_year = str(datetime.now(UTC).year)
    assert canonical_value("production_year", this_year) == this_year


@pytest.mark.parametrize(
    "field", ["energy_sources", "fuel_secondary", "ktype", "plate", "vin", "colour", "", "x"]
)
def test_only_the_defined_fields_can_be_corrected(field: str) -> None:
    with pytest.raises(FieldNotCorrectableError):
        canonical_value(field, "1")
    with pytest.raises(FieldNotCorrectableError):
        fields.spec_for(field)
    # A row for a field this version does not know changes nothing.
    assert matcher_values(field, "1") == {} == vehicle_copy(field, "1")


# ------------------------------------------------- what a set hands the matcher and the vehicle


@pytest.mark.parametrize(
    ("field", "value", "handed", "copied"),
    [
        ("engine_code", "DFGA", {"engine_code": "DFGA"}, {"engine_code": "DFGA"}),
        ("power_kw", "150", {"power_kw": 150}, {"power_kw": 150}),
        ("production_year", "2014", {"production_year": 2014}, {"production_year": 2014}),
        ("production_month", "9", {"production_month": 9}, {"production_month": 9}),
        ("drive_type", "awd", {"drive_type": "awd"}, {"drive_type": "awd"}),
        ("electrification_type", "hybrid", {"electrification_type": "hybrid"},
         {"electrification_type": "hybrid"}),
        ("fuel", "diesel",
         {"energy_sources": ["diesel"], "fuel_match_tokens": ["diesel"]},
         {"fuel": "diesel", "fuel_secondary": None, "fuel_match_tokens": ["diesel"]}),
        ("fuel", "petrol,electricity",
         {"energy_sources": ["petrol", "electricity"],
          "fuel_match_tokens": ["petrol", "electricity", "hybrid_petrol"]},
         {"fuel": "petrol", "fuel_secondary": "electricity",
          "fuel_match_tokens": ["petrol", "electricity", "hybrid_petrol"]}),
        ("fuel", "petrol,cng,e85",
         {"energy_sources": ["petrol", "cng", "e85"],
          "fuel_match_tokens": ["petrol", "cng", "e85"]},
         {"fuel": "petrol", "fuel_secondary": "cng",
          "fuel_match_tokens": ["petrol", "cng", "e85"]}),
    ],
)
def test_a_stored_value_reaches_the_matcher_and_the_vehicle_typed(
    field: str, value: str, handed: dict[str, Any], copied: dict[str, Any]
) -> None:
    assert matcher_values(field, value) == handed
    assert vehicle_copy(field, value) == copied
    spec = fields.SPECS[field]
    assert set(handed) <= set(spec.matcher_keys)
    assert tuple(copied) == spec.vehicle_columns


@pytest.mark.parametrize(
    ("field", "value"),
    [("power_kw", "many"), ("power_kw", ""), ("production_month", "x"), ("model_family", " - "),
     ("engine_code", ""), ("fuel", "steam"), ("fuel", ""), ("fuel", "petrol,steam")],
)
def test_a_stored_value_this_version_cannot_read_changes_nothing(field: str, value: str) -> None:
    assert matcher_values(field, value) == {} == vehicle_copy(field, value)


def test_the_fuel_tokens_are_normalizations_own() -> None:
    """One rule, stated once: a corrected hybrid is compared exactly as a registered one."""

    registered = normalize_ts_record({"fuel1": "1", "fuel2": "3"}).normalized
    corrected = matcher_values("fuel", canonical_value("fuel", "electricity,petrol"))

    assert corrected["energy_sources"] == registered["energy_sources"] == ["petrol", "electricity"]
    assert corrected["fuel_match_tokens"] == registered["fuel_match_tokens"]
    assert fuel_match_tokens(["diesel", "electricity"]) == ["diesel", "electricity", "hybrid_diesel"]
    assert fuel_match_tokens(["electricity"]) == ["electricity"]
    assert fuel_match_tokens([]) == []
    # And read back: the carriers among the tokens.
    assert fuel_carriers(registered["fuel_match_tokens"]) == ["petrol", "electricity"]


def test_a_set_is_unchanged_when_the_matcher_would_be_handed_the_same() -> None:
    assert same_value("engine_code", "DPCA", "DPCA")
    assert not same_value("engine_code", "DPCA", "dpca")
    assert not same_value("engine_code", "DPCA", None)
    assert same_value("production_month", "3", "3")
    # A fuel is its carriers, in any order.
    assert same_value("fuel", "petrol,electricity", "electricity,petrol")
    assert not same_value("fuel", "petrol", "petrol,electricity")
    assert not same_value("fuel", "petrol", None)


# ------------------------------------------------------------------ what the lookup shows


def _by_field(described: list[Any]) -> dict[str, Any]:
    return {item.field: item for item in described}


def test_every_correctable_field_is_described_in_order() -> None:
    described = describe(CAR, SOURCES, inputs_of(CAR), CANDIDATES)

    assert [item.field for item in described] == list(fields.SPECS)
    by_field = _by_field(described)
    for name, spec in fields.SPECS.items():
        item = by_field[name]
        assert (item.label, item.type) == (spec.label, spec.type)
        assert item.values == list(spec.values)
        assert item.evidence_keys == list(spec.evidence_keys)
    assert by_field["drive_type"].values == ["fwd", "rwd", "awd"]
    assert by_field["fuel"].values[:3] == ["petrol", "diesel", "electricity"]
    assert by_field["engine_code"].values == by_field["power_kw"].values == []


def test_the_current_value_is_what_the_matcher_uses_as_text() -> None:
    by_field = _by_field(describe(CAR, SOURCES, inputs_of(CAR), CANDIDATES))

    assert {name: item.current_value for name, item in by_field.items()} == {
        "manufacturer": "Volvo", "model_family": "XC60", "engine_code": "DPCA",
        "power_kw": "140", "displacement_cc": "1969", "drive_type": None,
        "bodywork_form": "suv", "production_year": "2018", "production_month": "3",
        "fuel": "petrol,electricity", "electrification_type": "plug_in_hybrid",
    }
    # The car's own record unless something supplied the value instead.
    assert {name: item.current_source for name, item in by_field.items()} == {
        "manufacturer": "registry", "model_family": "registry", "engine_code": "review",
        "power_kw": "registry", "displacement_cc": "registry", "drive_type": "registry",
        "bodywork_form": "registry", "production_year": "registry",
        "production_month": "registry", "fuel": "registry", "electrification_type": "registry",
    }


@pytest.mark.parametrize(
    ("value", "shown"),
    [(133, "133"), ("133", "133"), (133.0, "133"), ("133.0", "133"), (0, None), (-4, None),
     ("", None), (None, None), ("abc", None), (True, None)],
)
def test_a_number_is_shown_as_the_matcher_reads_it(value: object, shown: str | None) -> None:
    (power,) = [item for item in describe({"power_kw": value}, {}, None, [])
                if item.field == "power_kw"]

    assert power.current_value == shown


def test_the_build_month_shown_is_the_one_the_matcher_composed() -> None:
    def month(car: dict[str, Any], inputs: Any) -> str | None:
        return _by_field(describe(car, {}, inputs, []))["production_month"].current_value

    assert month(CAR, inputs_of(CAR)) == "3"
    assert month(CAR, inputs_of(CAR).model_copy(update={"build_month": 201812})) == "12"
    # A month the matcher could not use (no year), or a car it never keyed on, shows none.
    assert month(CAR, inputs_of(CAR).model_copy(update={"build_month": None})) is None
    assert month(CAR, None) is None


def test_the_fuel_shown_is_what_the_matcher_reads_tokens_first() -> None:
    def fuel(car: dict[str, Any]) -> str | None:
        return _by_field(describe(car, {}, None, []))["fuel"].current_value

    assert fuel({"energy_sources": ["diesel"]}) == "diesel"
    # The vehicle's tokens are what the matcher reads when both are there (a fuel
    # AIS changed); the combined hybrid token is no carrier.
    assert fuel({"energy_sources": ["petrol"], "fuel_match_tokens": ["diesel"]}) == "diesel"
    assert fuel({"fuel_match_tokens": ("diesel", "electricity", "hybrid_diesel")}) == (
        "diesel,electricity")
    assert fuel({"energy_sources": []}) is None and fuel({}) is None


def test_a_source_is_named_in_the_lookups_words() -> None:
    sources = {
        "manufacturer": "transportstyrelsen", "model_family": "rule", "engine_code": "ais",
        "power_kw": "derived", "drive_type": "correction", "bodywork_form": "review",
        "fuel_match_tokens": "ais", "production_month": "transportstyrelsen",
    }

    by_field = _by_field(describe(CAR, sources, None, []))

    assert {name: item.current_source for name, item in by_field.items()} == {
        "manufacturer": "registry", "model_family": "rule", "engine_code": "ais",
        "power_kw": "derived", "displacement_cc": "registry", "drive_type": "correction",
        "bodywork_form": "review", "production_year": "registry",
        "production_month": "registry", "fuel": "ais", "electrification_type": "registry",
    }
    corrected = _by_field(describe(CAR, {**sources, "fuel": "correction"}, None, []))
    assert corrected["fuel"].current_source == "correction"


def test_suggestions_are_the_candidates_values_without_the_current_one() -> None:
    by_field = _by_field(describe(CAR, SOURCES, inputs_of(CAR), CANDIDATES))

    assert by_field["engine_code"].suggestions == ["DFGA", "DTSA"]  # DPCA is the car's
    assert by_field["power_kw"].suggestions == ["110"]
    assert by_field["displacement_cc"].suggestions == []
    assert by_field["drive_type"].suggestions == ["awd"]
    assert by_field["bodywork_form"].suggestions == []
    assert by_field["model_family"].suggestions == ["XC60 II (246)"]
    # A candidate has a span of years and every candidate the same manufacturer;
    # the month, the fuel and the electrification are the car's to state.
    for name in ("production_year", "manufacturer", *ADDED):
        assert by_field[name].suggestions == []


def test_a_suggestion_is_always_a_value_the_field_accepts() -> None:
    odd = [
        candidate("A", engines=("DFGA", " ", "x" * 90), power=0),
        candidate("B", engines=("DFGA", "DTSA"), power=None),
    ]
    odd[0].drive_type, odd[1].drive_type = "all_wheel_drive", None
    odd[0].bodyworks, odd[1].bodyworks = ["suv", "off-road vehicle"], ["estate", "suv"]

    by_field = _by_field(describe({}, {}, None, odd))

    assert by_field["engine_code"].suggestions == ["DFGA", "DTSA"]
    assert by_field["power_kw"].suggestions == []
    assert by_field["drive_type"].suggestions == []
    assert by_field["bodywork_form"].suggestions == ["suv", "estate"]
    assert all(item.current_value is None for item in by_field.values())
    for item in by_field.values():
        for suggestion in item.suggestions:
            assert canonical_value(item.field, suggestion) == suggestion


def test_the_heads_are_listed_by_field_with_their_status() -> None:
    withdrawn = row("power_kw", "withdraw", previous_value="150", previous_source="correction",
                    chain_position=2)
    ignored = row("engine_code", "ignore", reason="a typo in the register")
    corrected = row("bodywork_form", "set", "estate", previous_value="suv",
                    previous_source="registry", chain_position=1)

    listed = states({
        "power_kw": (withdrawn, 3), "engine_code": (ignored, 1), "bodywork_form": (corrected, 2),
    })

    assert [(item.field, item.status, item.value, item.history_count) for item in listed] == [
        ("bodywork_form", "set", "estate", 2),
        ("engine_code", "ignored", None, 1),
        ("power_kw", "withdrawn", None, 3),
    ]
    first = listed[0]
    assert first.correction_id == corrected.correction_id
    assert (first.reviewer, first.reason, first.created_at) == ("Ada", None, corrected.created_at)
    assert (first.previous_value, first.previous_source, first.group_id) == ("suv", "registry", None)
    assert listed[1].reason == "a typo in the register"
    assert states({}) == []
