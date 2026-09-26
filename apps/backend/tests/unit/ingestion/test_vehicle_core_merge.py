"""Which source wins a vehicle's field, and what happens to the one that loses."""

from __future__ import annotations

from datetime import date

import pytest

from ingestion.vehicle_core_fields import (
    SourceRef,
    clean_engine_code,
    clean_int,
    clean_registry_date,
    clean_text,
    clean_year_month,
    is_strong_vin,
    normalize_plate,
    parse_source_ref,
)
from ingestion.vehicle_core_merge import (
    Observation,
    VehicleState,
    derive,
    merge,
    retract,
)

TS_DAY = date(2023, 12, 4)
AIS_DAY = date(2026, 9, 19)


def ts(value: object) -> Observation:
    return Observation(value, SourceRef("transportstyrelsen", "1001", TS_DAY))


def ais(value: object, *, clears: bool = False) -> Observation:
    return Observation(value, SourceRef("ais", None, AIS_DAY), clears=clears)


def review(value: object, rule: str = "r1") -> Observation:
    return Observation(value, SourceRef("review", rule))


def rule(value: object, rule_id: str = "ENG-VV-1") -> Observation:
    return Observation(value, SourceRef("rule", rule_id))


def ts_vehicle(**values: object) -> VehicleState:
    state = VehicleState("NOR-01ARZ3NDEKTSV4RRFFQ69G5FAV", "transportstyrelsen", TS_DAY)
    merge(state, {name: ts(value) for name, value in values.items()})
    return state


def test_origin_values_carry_no_source_reference() -> None:
    state = ts_vehicle(colour="SVART")

    assert state.values["colour"] == "SVART"
    assert state.field_sources == {}


def test_newest_policy_lets_the_later_register_snapshot_win_and_keeps_the_older_value() -> None:
    state = ts_vehicle(colour="SVART")

    result = merge(state, {"colour": ais("VIT")})

    assert state.values["colour"] == "VIT"
    assert state.field_sources["colour"] == "ais@2026-09-19"
    assert state.field_alternatives["colour"] == [
        {"source": "transportstyrelsen@2023-12-04", "value": "SVART"}
    ]
    assert [change.field for change in result.changed] == ["colour"]


def test_ts_first_policy_keeps_ts_and_records_the_other_value_beside_it() -> None:
    state = ts_vehicle(model_year=2015)

    merge(state, {"model_year": ais(2016)})

    assert state.values["model_year"] == 2015
    assert state.field_alternatives["model_year"][0]["value"] == 2016


def test_a_value_ts_never_had_is_filled_from_ais() -> None:
    state = ts_vehicle()

    result = merge(state, {"engine_code": ais("D5244T21")})

    assert state.values["engine_code"] == "D5244T21"
    assert [change.field for change in result.filled] == ["engine_code"]
    assert result.evidence() == {}


def test_a_confirmation_changes_nothing_not_even_the_source() -> None:
    state = ts_vehicle(colour="SVART")

    result = merge(state, {"colour": ais("SVART")})

    assert not result.touched
    assert state.field_sources == {}


def test_a_silent_source_never_erases_another_sources_value() -> None:
    state = ts_vehicle()
    merge(state, {"engine_code": ais("D5244T21")})

    merge(state, {"engine_code": ts(None)})

    assert state.values["engine_code"] == "D5244T21"


def test_an_explicit_clear_from_a_newer_source_removes_a_stale_value() -> None:
    state = ts_vehicle(eu_category="M1")

    merge(state, {"eu_category": ais(None, clears=True)})

    assert state.values["eu_category"] is None
    assert state.field_alternatives["eu_category"][0]["value"] == "M1"


def test_a_review_beats_every_source_and_retiring_it_restores_what_it_displaced() -> None:
    state = ts_vehicle(bodywork_form="estate")

    merge(state, {"bodywork_form": review("suv")})
    assert state.values["bodywork_form"] == "suv"

    merge(state, {"bodywork_form": ais("hatchback")})
    assert state.values["bodywork_form"] == "suv"

    retract(state, "bodywork_form", "review", "r1")
    # The best remaining source: AIS is newer than TS for a `newest` field.
    assert state.values["bodywork_form"] == "hatchback"


def test_a_rule_only_fills_and_never_overrides_a_source() -> None:
    state = ts_vehicle()

    merge(state, {"engine_code": rule("D4204T14")})
    assert state.values["engine_code"] == "D4204T14"
    assert state.field_sources["engine_code"] == "rule:ENG-VV-1"

    merge(state, {"engine_code": ais("D4204T23")})
    assert state.values["engine_code"] == "D4204T23"

    retract(state, "engine_code", "rule", "ENG-VV-1")
    assert state.values["engine_code"] == "D4204T23"
    assert "engine_code" not in state.field_alternatives


def test_retracting_one_rule_leaves_another_rules_value_alone() -> None:
    state = ts_vehicle()
    merge(state, {"engine_code": rule("B4204T", "ENG-GC-2")})

    retract(state, "engine_code", "rule", "ENG-VV-1")

    assert state.values["engine_code"] == "B4204T"


def test_an_electric_cars_power_is_derived_from_its_ev_power_only_when_missing() -> None:
    state = ts_vehicle(fuel="electricity", ev_power_kw=150)

    derive(state, TS_DAY)
    assert state.values["power_kw"] == 150
    assert state.field_sources["power_kw"].startswith("derived:ev_power")

    merge(state, {"power_kw": ais(160)})
    assert state.values["power_kw"] == 160


def test_equal_standing_providers_break_ties_by_keeping_what_is_there() -> None:
    state = VehicleState("NOR-01ARZ3NDEKTSV4RRFFQ69G5FAV", "ais", AIS_DAY)
    merge(state, {"colour": ais("VIT")})

    merge(state, {"colour": Observation("RÖD", SourceRef("transportstyrelsen", "9", AIS_DAY))})

    assert state.values["colour"] == "VIT"


def test_unknown_fields_are_a_programming_error() -> None:
    with pytest.raises(KeyError):
        merge(ts_vehicle(), {"not_a_field": ts(1)})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ais@2026-09-19", SourceRef("ais", None, AIS_DAY)),
        ("transportstyrelsen:1001@2023-12-04", SourceRef("transportstyrelsen", "1001", TS_DAY)),
        ("rule:ENG-VV-1a2b", SourceRef("rule", "ENG-VV-1a2b", None)),
        ("review:4f7c", SourceRef("review", "4f7c", None)),
    ],
)
def test_source_references_round_trip(text: str, expected: SourceRef) -> None:
    assert parse_source_ref(text) == expected
    assert expected.encode() == text


def test_value_cleaning() -> None:
    assert clean_text("  OKÄND ") is None
    assert clean_text(" Volvo   V70 ") == "Volvo V70"
    assert clean_int("0") is None
    assert clean_int("0", zero_is_absent=False) == 0
    assert clean_int("300.0") == 300
    assert clean_int("x") is None
    assert clean_registry_date("20150312") == date(2015, 3, 12)
    assert clean_registry_date("2015-02-30") is None
    assert clean_year_month("201402") == (2014, 2)
    assert clean_year_month("201413") == (None, None)
    assert normalize_plate(" abc 123 ") == "ABC123"


def test_only_a_full_vin_is_strong_identity() -> None:
    assert is_strong_vin("YV1BW84S1F1234567")
    assert not is_strong_vin("000003")
    assert not is_strong_vin("ZZZ8DZVA027095")
    assert not is_strong_vin("YV1BW84S1F123456O")  # O is not a VIN character


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("N47-D20C", "N47-D20C"), ("1", None), ("D", None), ("12", None), ("b4204t", "B4204T")],
)
def test_junk_engine_codes_are_absence(raw: str, expected: str | None) -> None:
    assert clean_engine_code(raw) == expected
