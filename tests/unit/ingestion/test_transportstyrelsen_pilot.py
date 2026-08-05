from pathlib import Path

from ingestion.transportstyrelsen_pilot import (
    is_passenger_car,
    parse_vehicle_line,
    select_deterministic_sample,
)


def test_parser_selects_vehicle_fields_and_not_owner_data() -> None:
    chars = [" "] * 1900
    values = {
        (0, 6): "ABC123",
        (39, 47): "M1",
        (47, 71): "VOLVO",
        (71, 95): "V60",
        (95, 99): "2024",
        (111, 128): "VIN12345678901234",
        (175, 235): "VOLVO CAR CORPORATION",
        (235, 295): "VOLVO",
        (504, 506): "AC",
        (544, 549): "01969",
        (549, 550): "Z",
        (572, 574): "01",
        (578, 582): "0145",
        (681, 682): "1",
    }
    for (start, end), value in values.items():
        chars[start:end] = value.ljust(end - start)

    record = parse_vehicle_line("".join(chars))

    assert record["plate"] == "ABC123"
    assert record["manufacturer"] == "VOLVO CAR CORPORATION"
    assert record["base_manufacturer"] == "VOLVO"
    assert record["body_code"] == "AC"
    assert record["model_year"] == 2024
    assert record["ccm"] == 1969
    assert record["kw"] == 145
    assert all("owner" not in field for field in record)


def test_parser_rejects_a_non_vehicle_record_layout() -> None:
    assert parse_vehicle_line("short unrelated record") == {}


def test_passenger_car_gate_prefers_m1_and_falls_back_to_personbil_type() -> None:
    assert is_passenger_car({"eu_category": "M1", "vehicle_type": "PB"}) is True
    assert is_passenger_car({"vehicle_type": "PERSONBIL"}) is True
    assert is_passenger_car({"eu_category": "N1", "vehicle_type": "LB"}) is False
    assert is_passenger_car({"eu_category": "O2", "vehicle_type": "SLÄP"}) is False
    assert is_passenger_car({"vehicle_type": "MC"}) is False


def _vehicle_line(*, plate: str, vehicle_type: str, eu_category: str = "") -> str:
    chars = [" "] * 1900
    for (start, end), value in {
        (0, 6): plate,
        (31, 39): vehicle_type,
        (39, 47): eu_category,
    }.items():
        chars[start:end] = value.ljust(end - start)
    return "".join(chars)


def test_sample_excludes_non_passenger_vehicle_categories(tmp_path: Path) -> None:
    source = tmp_path / "transportstyrelsen.txt"
    source.write_text(
        "\n".join(
            (
                _vehicle_line(plate="CAR001", vehicle_type="PB", eu_category="M1"),
                _vehicle_line(plate="TRK001", vehicle_type="LB", eu_category="N1"),
                _vehicle_line(plate="TRL001", vehicle_type="SLÄP", eu_category="O2"),
                _vehicle_line(plate="CAR002", vehicle_type="PB"),
                _vehicle_line(plate="BIK001", vehicle_type="MC", eu_category="L3E"),
            )
        ),
        encoding="iso-8859-1",
    )

    sample = select_deterministic_sample(source, limit=10)

    assert {record["plate"] for record in sample} == {"CAR001", "CAR002"}
