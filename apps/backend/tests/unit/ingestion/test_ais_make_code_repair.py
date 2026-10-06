"""A vehicle AIS created under a make code cut to two characters is described again."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from ingestion.ais_make_code_repair import REPAIRED_FIELDS, repair, stored_record
from ingestion.vehicle_core_ais import Normalizer
from ingestion.vehicle_core_merge import VehicleState
from ingestion.vehicle_core_rules import CompletionRules

RULES = CompletionRules(
    {
        "TSC-EU": {("POL", "021900"): ("M1", "TSC-EU-p")},
        "TSC-BRAND": {("POL", "021900"): ("POLESTAR", "TSC-BRAND-p")},
        "TSC-MODEL": {("POL", "021900"): ("POLESTAR 2", "TSC-MODEL-p")},
        "TSC-VAR": {("POL", "021900"): ("EKS", "TSC-VAR-p")},
        "TSC-4WD": {("POL", "021900"): ("false", "TSC-4WD-p")},
    }
)


class _Normalizer(Normalizer):
    """Names the manufacturer only under the registry's make code, as the reviewed rules do."""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    def normalize(self, raw: Mapping[str, Any]) -> tuple[dict[str, Any], str, float]:
        self.seen.append(dict(raw))
        known = raw.get("fab_code") in {"POL", "GEE"}
        normalized = {
            "manufacturer": "Polestar" if known else None,
            "drive_type": "2wd" if raw.get("is_4wd") == "0" else None,
            "vehicle_scope": "passenger",
        }
        if known:
            return normalized, "resolved", 0.95
        return normalized, "review_required", 0.55


def _cut_wrongly(**values: Any) -> VehicleState:
    state = VehicleState("NOR-01ARZ3NDEKTSV4RRFFQ69G5FAV", "ais", date(2026, 9, 19))
    state.values.update(
        {
            "vin": "LPSVSEDEEML000001",
            "plate": "POL002",
            "registry_status": "registered",
            "registry_vehicle_type": "PB",
            "vehicle_scope": "passenger",
            "registry_make_code": "PO",
            "group_code": "L021900",
            "registry_brand_text": "POLESTAR POLESTAR 2",
            "manufacturer": "Polestar",
            "model_family": "2",
            "drive_type": "rwd",
            "fuel": "electricity",
            "transmission": "automatic",
            "power_kw": 170,
            "production_year": 2021,
            "production_month": 3,
            "first_registration_date": date(2021, 5, 4),
            "normalization_status": "review_required",
            "normalization_confidence": 0.55,
        }
    )
    state.field_sources.update(
        {
            "manufacturer": "rule:MFR-BW-a",
            "model_family": "rule:MOD-VIN-b",
            "drive_type": "rule:DRV-CAR-c",
        }
    )
    state.values.update(values)
    return state


def test_the_export_string_is_put_back_together() -> None:
    record = stored_record(_cut_wrongly())

    assert record is not None
    assert record.get("group_code") == "POL021900"
    assert (record.make_code, record.group_number) == ("POL", "021900")
    assert record.get("car_name") == "POLESTAR POLESTAR 2"
    assert record.get("build_month") == "202103"
    assert record.get("registration_date") == "2021-05-04"
    # The raw fuel and gearbox codes are gone; nothing is invented for them.
    assert record.get("fuel") is None
    assert record.get("gearbox") is None


def test_the_codes_and_what_follows_from_them_are_repaired() -> None:
    state = _cut_wrongly()
    normalizer = _Normalizer()

    changes = repair(state, RULES, normalizer)

    assert changes is not None
    assert normalizer.seen[0]["fab_code"] == "POL"
    assert state.values["registry_make_code"] == "POL"
    assert state.values["group_code"] == "021900"
    assert state.values["registry_brand_text"] == "POLESTAR"
    assert state.values["registry_model_text"] == "POLESTAR 2"
    assert state.values["variant_code"] == "EKS"
    assert state.values["eu_category"] == "M1"
    assert state.values["registry_all_wheel_drive"] is False
    assert state.values["normalization_status"] == "resolved"
    assert changes["normalization_status"] == ("review_required", "resolved")
    assert changes["registry_make_code"] == ("PO", "POL")
    # The codes are still AIS's own; the completed values name their rule.
    assert "registry_make_code" not in state.field_sources
    assert "group_code" not in state.field_sources
    assert state.field_sources["registry_brand_text"] == "rule:TSC-BRAND-p"
    assert state.field_sources["variant_code"] == "rule:TSC-VAR-p"
    # As in the import, the rule's brand text takes the AIS name's place.
    assert "registry_brand_text" not in state.field_alternatives


def test_nothing_outside_the_repaired_fields_is_touched() -> None:
    state = _cut_wrongly()
    untouched = {
        name: value for name, value in state.values.items() if name not in REPAIRED_FIELDS
    }

    repair(state, RULES, _Normalizer())

    assert {name: state.values.get(name) for name in untouched} == untouched
    # The record read again has no fuel code; the fuel the vehicle holds stays.
    assert state.values["fuel"] == "electricity"


def test_a_named_driven_axle_is_not_replaced_by_the_generic_value() -> None:
    state = _cut_wrongly()

    repair(state, RULES, _Normalizer())

    assert state.values["drive_type"] == "rwd"
    assert state.field_sources["drive_type"] == "rule:DRV-CAR-c"


def test_a_car_without_a_group_loses_the_leftover_group_code() -> None:
    state = _cut_wrongly(
        registry_make_code="GE", group_code="E000000", registry_brand_text="GEELY GEELY EX5"
    )

    changes = repair(state, RULES, _Normalizer())

    assert changes is not None
    assert state.values["registry_make_code"] == "GEE"
    assert state.values.get("group_code") is None
    assert changes["group_code"] == ("E000000", None)
    # No rule knows the car: its AIS name stays what it was.
    assert state.values["registry_brand_text"] == "GEELY GEELY EX5"


def test_a_vehicle_whose_brand_text_is_no_longer_the_ais_name_is_left_alone() -> None:
    state = _cut_wrongly()
    state.field_sources["registry_brand_text"] = "review:4f7c"
    before = dict(state.values)

    assert stored_record(state) is None
    assert repair(state, RULES, _Normalizer()) is None
    assert state.values == before
