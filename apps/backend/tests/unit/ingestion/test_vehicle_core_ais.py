"""Reading the AIS export and turning its records into observations."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from ingestion.vehicle_core_ais import (
    AisExtract,
    AisRecord,
    ais_observations,
    changed_codes,
    iter_ais_records,
    read_export_time,
    ts_shaped_record,
)
from ingestion.vehicle_core_merge import VehicleState
from ingestion.vehicle_core_rules import CompletionRules

EXPORT = """<?xml version="1.0" encoding="UTF-8"?>
<!-- Configuration: exported by domain exporter -->
<STEP-ProductInformation ExportTime="2026-09-19 10:28:27" ContextID="Swedish CTX">
  <Entities>
    <Entity ID="WBA3D31090J307644" UserTypeID="Vehicle Identification Number" ParentID="VIN Numbers Se">
      <Name>AAA004</Name>
      <Values>
        <Value AttributeID="ATTR_Name of the car">BMW 320D OMBYGGD BIL</Value>
        <Value AttributeID="ATTR_AIS Engine code">N47-D20C</Value>
        <Value AttributeID="ATTR_Type of vehicle">TR</Value>
        <Value AttributeID="ATTR_Group code">BW230107</Value>
        <Value AttributeID="Import_Deleted">true</Value>
        <Value AttributeID="ATTR_MarkedForDeletion" Derived="true">true</Value>
      </Values>
      <EntityCrossReference EntityID="AIS_TYPE_326650" Type="VIN to AIS Car"/>
    </Entity>
    <Entity ID="ZYN17H-SOMETHING" UserTypeID="Something Else" ParentID="X"><Name>X</Name></Entity>
    <Entity ID="vsszzz7mz7v504106" UserTypeID="Vehicle Identification Number" ParentID="VIN Numbers Se">
      <Name></Name>
      <Values>
        <Value AttributeID="ATTR_License plate number">aaa 006</Value>
        <Value AttributeID="ATTR_Output kW">110</Value>
      </Values>
    </Entity>
  </Entities>
</STEP-ProductInformation>
"""


def _export(tmp_path: Path) -> Path:
    path = tmp_path / "export.xml"
    path.write_text(EXPORT, encoding="utf-8")
    return path


def test_records_stream_with_plate_from_name_or_the_plate_attribute(tmp_path: Path) -> None:
    records = list(iter_ais_records(_export(tmp_path)))

    assert [record.vin for record in records] == ["WBA3D31090J307644", "VSSZZZ7MZ7V504106"]
    assert records[0].plate == "AAA004"
    assert records[0].deleted
    assert records[0].vehicle_type == "TR"
    assert records[0].make_code == "BW"
    assert records[0].group_number == "230107"
    assert records[1].plate == "AAA006"
    assert records[1].get("kw") == "110"


def test_the_export_time_comes_from_the_root_element(tmp_path: Path) -> None:
    assert read_export_time(_export(tmp_path)) == datetime(2026, 9, 19, 10, 28, 27, tzinfo=UTC)


def _extract() -> AisExtract:
    return AisExtract(Path("x.xml"), datetime(2026, 9, 19, 10, 28, 27, tzinfo=UTC), "ab" * 32)


def _state(**values: object) -> VehicleState:
    state = VehicleState("NOR-01ARZ3NDEKTSV4RRFFQ69G5FAV", "transportstyrelsen", date(2023, 12, 4))
    state.values.update(values)
    return state


def test_a_deregistration_is_observed_with_its_date_and_no_plate() -> None:
    record = AisRecord("WBA3D31090J307644", "AAA004", {"import_deleted": "true"})

    observations = ais_observations(record, _state(registry_status="registered"), _extract())

    assert observations["registry_status"].value == "deregistered"
    assert observations["registry_status_observed_on"].value == date(2026, 9, 19)
    assert "plate" not in observations


def test_a_changed_vehicle_type_makes_the_eu_category_stale() -> None:
    record = AisRecord("WBA3D31090J307644", "AAA004", {"vehicle_type": "TR"})

    observations = ais_observations(
        record, _state(registry_vehicle_type="PB", eu_category="M1"), _extract()
    )

    assert observations["eu_category"].clears
    assert observations["vehicle_scope"].value == "other"


def test_codes_are_compared_with_the_ts_record_ignoring_zero() -> None:
    record = AisRecord("V", None, {"fuel": "7", "fuel2": "0", "gearbox": "A", "body_code": "AC"})

    assert changed_codes(record, {"fuel1": "1", "gearbox": "A", "body_code": "AC"}) == {"fuel1": "7"}


def test_a_new_car_is_completed_by_group_code_rules() -> None:
    record = AisRecord(
        "WVWZZZ1KZAW000001",
        "NEW111",
        {"car_name": "VOLKSWAGEN, VW 1KM GOLF", "group_code": "VW890007", "fuel": "1"},
    )
    rules = CompletionRules(
        {
            "TSC-EU": {("VW", "890007"): ("M1", "TSC-EU-a")},
            "TSC-BRAND": {("VW", "890007"): ("VOLKSWAGEN, VW 1KM", "TSC-BRAND-b")},
            "TSC-MODEL": {("VW", "890007"): ("GOLF", "TSC-MODEL-c")},
            "TSC-4WD": {("VW", "890007"): ("false", "TSC-4WD-d")},
        }
    )

    raw, by_rule = ts_shaped_record(record, rules)

    assert raw["eu_category"] == "M1"
    assert raw["brand"] == "VOLKSWAGEN, VW 1KM"
    assert raw["model"] == "GOLF"
    assert raw["is_4wd"] == "0"
    assert by_rule["manufacturer"] == "TSC-BRAND-b"
    assert by_rule["eu_category"] == "TSC-EU-a"


def test_a_model_rule_without_its_brand_rule_is_not_used() -> None:
    record = AisRecord("W", "P", {"car_name": "VOLVO V70", "group_code": "VO101100"})
    rules = CompletionRules({"TSC-MODEL": {("VO", "101100"): ("V70", "TSC-MODEL-x")}})

    raw, by_rule = ts_shaped_record(record, rules)

    assert raw["brand"] == "VOLVO V70"
    assert "model" not in raw
    assert by_rule == {}
