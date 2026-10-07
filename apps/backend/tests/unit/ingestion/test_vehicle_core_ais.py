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


def test_a_known_second_fuel_does_not_send_the_car_through_the_normalizer() -> None:
    from ingestion.vehicle_core_ais import comparable_ts_codes

    record = AisRecord("V", None, {"fuel": "1", "fuel2": "3", "gearbox": "A", "body_code": "AC"})
    projected = {"fuel1": "1", "gearbox": "A", "body_code": "AC"}

    hybrid = _state(fuel_secondary="electricity", registry_vehicle_type="PB")
    assert changed_codes(record, comparable_ts_codes(projected, hybrid, record)) == {}

    # A car TS knew no second fuel for: AIS adds one, and the normalizer must see it.
    petrol = _state(fuel_secondary=None, registry_vehicle_type="PB")
    assert changed_codes(record, comparable_ts_codes(projected, petrol, record)) == {"fuel2": "3"}


def test_the_group_code_is_split_from_its_end() -> None:
    # The registry's make code is two characters or three; its group number is six digits.
    two = AisRecord("V", None, {"group_code": "VW890007"})
    three = AisRecord("V", None, {"group_code": "POL021900"})
    no_group = AisRecord("V", None, {"group_code": "GEE000000"})

    assert (two.make_code, two.group_number) == ("VW", "890007")
    assert (three.make_code, three.group_number) == ("POL", "021900")
    assert (no_group.make_code, no_group.group_number) == ("GEE", None)


def test_a_three_character_make_finds_its_completion_rules() -> None:
    record = AisRecord("V", "P", {"car_name": "POLESTAR POLESTAR 2", "group_code": "POL021900"})
    rules = CompletionRules(
        {
            "TSC-BRAND": {("POL", "021900"): ("POLESTAR", "TSC-BRAND-p")},
            "TSC-MODEL": {("POL", "021900"): ("POLESTAR 2", "TSC-MODEL-p")},
            # Pontiac's group of the same digits is another make's.
            "TSC-VAR": {("PO", "L021900"): ("WRONG", "TSC-VAR-x")},
        }
    )

    raw, by_rule = ts_shaped_record(record, rules)

    assert raw["fab_code"] == "POL"
    assert raw["group_no"] == "021900"
    assert (raw["brand"], raw["model"]) == ("POLESTAR", "POLESTAR 2")
    assert "variant" not in raw
    assert by_rule["registry_model_text"] == "TSC-MODEL-p"


def test_the_model_is_read_from_the_name_when_only_the_brand_text_is_known() -> None:
    rules = CompletionRules({"TSC-BRAND": {("CUA", "030100"): ("CUPRA", "TSC-BRAND-c")}})
    record = AisRecord(
        "V", "P", {"car_name": "Cupra  BORN 150 KW 58/62 KWH", "group_code": "CUA030100"}
    )

    raw, by_rule = ts_shaped_record(record, rules)

    assert raw["brand"] == "CUPRA"
    assert raw["model"] == "BORN 150 KW 58/62 KWH"
    # The model text is AIS's own word, not the rule's.
    assert "registry_model_text" not in by_rule


def test_a_name_that_does_not_start_with_the_brand_text_gives_no_model() -> None:
    rules = CompletionRules({"TSC-BRAND": {("CUA", "030100"): ("CUPRA", "TSC-BRAND-c")}})
    for name in ("SEAT CUPRA BORN", "CUPRA", "CUPRAX BORN"):
        record = AisRecord("V", "P", {"car_name": name, "group_code": "CUA030100"})

        raw, _ = ts_shaped_record(record, rules)

        assert raw["brand"] == "CUPRA"
        assert "model" not in raw


def test_a_name_of_an_unknown_group_is_divided_at_the_makes_shortest_brand_text() -> None:
    rules = CompletionRules(
        {
            "TSC-BT": {
                ("TO", "TOYOTA RAV4"): ("TOYOTA RAV4", "TSC-BT-long"),
                ("TO", "TOYOTA"): ("TOYOTA", "TSC-BT-short"),
                ("VW", "VOLKSWAGEN"): ("VOLKSWAGEN", "TSC-BT-vw1"),
                ("VW", "VOLKSWAGEN, VW"): ("VOLKSWAGEN, VW", "TSC-BT-vw2"),
            }
        }
    )
    toyota = AisRecord("V", "P", {"car_name": "TOYOTA TOYOTA RAV4", "group_code": "TO555555"})
    volkswagen = AisRecord(
        "V", "P", {"car_name": "VOLKSWAGEN, VW TAYRON", "group_code": "VW555555"}
    )

    raw, by_rule = ts_shaped_record(toyota, rules)
    assert (raw["brand"], raw["model"]) == ("TOYOTA", "TOYOTA RAV4")
    assert by_rule["registry_brand_text"] == "TSC-BT-short"
    assert by_rule["manufacturer"] == "TSC-BT-short"
    assert "registry_model_text" not in by_rule

    # "VOLKSWAGEN" is followed by a comma, not by the model: the longer text divides.
    raw, by_rule = ts_shaped_record(volkswagen, rules)
    assert (raw["brand"], raw["model"]) == ("VOLKSWAGEN, VW", "TAYRON")


def test_a_name_no_brand_text_of_the_make_starts_stays_whole() -> None:
    rules = CompletionRules({"TSC-BT": {("VO", "VOLVO"): ("VOLVO", "TSC-BT-v")}})
    for name, group in (("VOLVO", "VO555555"), ("VOLVOX EX30", "VO555555"), ("VOLVO EX30", "PG555555")):
        record = AisRecord("V", "P", {"car_name": name, "group_code": group})

        raw, by_rule = ts_shaped_record(record, rules)

        assert raw["brand"] == name
        assert "model" not in raw
        assert by_rule == {}


def test_the_groups_own_brand_text_comes_before_the_makes() -> None:
    rules = CompletionRules(
        {
            "TSC-BRAND": {("VW", "890007"): ("VOLKSWAGEN, VW 1KM", "TSC-BRAND-b")},
            "TSC-BT": {("VW", "VOLKSWAGEN, VW"): ("VOLKSWAGEN, VW", "TSC-BT-vw")},
        }
    )
    record = AisRecord("V", "P", {"car_name": "VOLKSWAGEN, VW 1KM GOLF", "group_code": "VW890007"})

    raw, by_rule = ts_shaped_record(record, rules)

    assert (raw["brand"], raw["model"]) == ("VOLKSWAGEN, VW 1KM", "GOLF")
    assert by_rule["registry_brand_text"] == "TSC-BRAND-b"
