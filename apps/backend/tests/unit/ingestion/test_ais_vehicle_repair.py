"""A vehicle AIS created from a record the import misread is described again."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from ingestion.ais_vehicle_repair import REPAIRED_FIELDS, repair, stored_record
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
        "TSC-BT": {
            ("VO", "VOLVO"): ("VOLVO", "TSC-BT-v"),
            ("VO", "VOLVO M + V50"): ("VOLVO M + V50", "TSC-BT-w"),
        },
    }
)


class _Normalizer(Normalizer):
    """Names the manufacturer only under the registry's make code, as the reviewed rules do."""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    def normalize(self, raw: Mapping[str, Any]) -> tuple[dict[str, Any], str, float]:
        self.seen.append(dict(raw))
        # A model is read from a model text, and a missing fuel code is a reason
        # to stop only for the make code this stub uses to say so.
        known = raw.get("fab_code") in {"POL", "GEE"} or bool(raw.get("model"))
        if raw.get("fab_code") == "FU" and not raw.get("fuel1"):
            known = True
        normalized = {
            "manufacturer": "Polestar" if raw.get("fab_code") in {"POL", "GEE"} else None,
            "model_family": str(raw["model"]).title() if raw.get("model") else None,
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

    repaired = repair(state, RULES, normalizer)

    assert repaired is not None
    changes = repaired.changes
    assert not repaired.status_kept
    assert normalizer.seen[0]["fab_code"] == "POL"
    # The old reading, made to see whether the stored values explain the status.
    assert normalizer.seen[1]["fab_code"] == "PO"
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


def test_what_a_rule_supplied_since_the_import_is_not_overruled() -> None:
    state = _cut_wrongly()
    rules = CompletionRules(
        {**RULES.rules, "TSC-MODEL": {("POL", "021900"): ("POLESTAR 4", "TSC-MODEL-q")}}
    )

    repaired = repair(state, rules, _Normalizer())

    assert repaired is not None
    # The stub reads another model and a generic drive type off the record; the
    # vehicle keeps the model and the driven axle its rules gave it.
    assert state.values["registry_model_text"] == "POLESTAR 4"
    assert state.values["model_family"] == "2"
    assert state.field_sources["model_family"] == "rule:MOD-VIN-b"
    assert state.values["drive_type"] == "rwd"
    assert state.field_sources["drive_type"] == "rule:DRV-CAR-c"
    assert "model_family" not in repaired.changes


def test_a_car_without_a_group_loses_the_leftover_group_code() -> None:
    state = _cut_wrongly(
        registry_make_code="GE", group_code="E000000", registry_brand_text="GEELY GEELY EX5"
    )

    repaired = repair(state, RULES, _Normalizer())

    assert repaired is not None
    assert state.values["registry_make_code"] == "GEE"
    assert state.values.get("group_code") is None
    assert repaired.changes["group_code"] == ("E000000", None)
    # No rule knows the car: its AIS name stays what it was.
    assert state.values["registry_brand_text"] == "GEELY GEELY EX5"


def test_a_vehicle_whose_brand_text_is_no_longer_the_ais_name_is_left_alone() -> None:
    state = _cut_wrongly()
    state.field_sources["registry_brand_text"] = "review:4f7c"
    before = dict(state.values)

    assert stored_record(state) is None
    assert repair(state, RULES, _Normalizer()) is None
    assert state.values == before


def _undivided(**values: Any) -> VehicleState:
    """A car of a group new to us: the whole AIS name is its brand text, and it has no model text."""

    state = _cut_wrongly(
        registry_make_code="VO",
        group_code="777777",
        registry_brand_text="VOLVO EX30",
        manufacturer="Volvo",
        model_family=None,
        normalization_status="review_required",
    )
    state.field_sources.pop("model_family")
    state.values.update(values)
    return state


def test_an_undivided_name_is_divided_at_the_makes_brand_text() -> None:
    state = _undivided()

    repaired = repair(state, RULES, _Normalizer())

    assert repaired is not None
    assert state.values["registry_brand_text"] == "VOLVO"
    assert state.field_sources["registry_brand_text"] == "rule:TSC-BT-v"
    # The model text is AIS's own word: it carries no rule.
    assert state.values["registry_model_text"] == "EX30"
    assert "registry_model_text" not in state.field_sources
    assert state.values["model_family"] == "Ex30"
    assert (state.values["registry_make_code"], state.values["group_code"]) == ("VO", "777777")
    assert repaired.changes["normalization_status"] == ("review_required", "resolved")


def test_a_name_that_is_only_the_brand_text_changes_nothing() -> None:
    state = _undivided(registry_brand_text="VOLVO")
    before = dict(state.values)

    repaired = repair(state, RULES, _Normalizer())

    assert repaired is not None
    assert repaired.changes == {}
    assert state.values == before


def test_a_status_the_stored_values_do_not_explain_is_kept() -> None:
    # Read the old way from what the vehicle holds, this car normalizes fine: it was
    # stopped for something the vehicle no longer has (here, its fuel code).
    state = _undivided(registry_make_code="FU", registry_brand_text="FUTURA ONE")
    rules = CompletionRules({"TSC-BT": {("FU", "FUTURA"): ("FUTURA", "TSC-BT-f")}})

    repaired = repair(state, rules, _Normalizer())

    assert repaired is not None
    assert repaired.status_kept
    assert state.values["registry_model_text"] == "ONE"
    assert state.values["normalization_status"] == "review_required"
    assert state.values["normalization_confidence"] == 0.55
