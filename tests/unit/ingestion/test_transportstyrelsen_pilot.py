from ingestion.transportstyrelsen_pilot import parse_vehicle_line


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
        (549, 550): "Z",
        (572, 574): "01",
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
    assert all("owner" not in field for field in record)


def test_parser_rejects_a_non_vehicle_record_layout() -> None:
    assert parse_vehicle_line("short unrelated record") == {}
