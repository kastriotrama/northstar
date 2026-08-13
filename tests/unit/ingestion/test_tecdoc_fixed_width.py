from pathlib import Path

import pytest

from ingestion.tecdoc.dat_extraction import extract_dat_hierarchy
from ingestion.tecdoc.fixed_width import TABLE_FORMATS, TecDocFormatError, parse_row
from ingestion.tecdoc.reference_data import (
    canonical_bodywork_by_kt086,
    canonical_drive_by_kt082,
    canonical_engine_fuels,
    load_key_table_labels,
    official_bodywork_labels,
    official_transmission_type_labels,
)


def make_row(table: str, **values: str) -> str:
    table_format = TABLE_FORMATS[table]
    characters = [" "] * table_format.row_length
    marker_position = 26 if table_format.reserved_prefix else 4
    characters[marker_position : marker_position + 3] = table
    for field in table_format.fields:
        value = values.get(field.name, "")
        assert len(value) <= field.length
        characters[field.position : field.position + field.length] = value.ljust(field.length)
    return "".join(characters)


def write_table(directory: Path, table: str, rows: list[str]) -> None:
    text = "\r\n".join(rows) + ("\r\n" if rows else "")
    (directory / f"{table}.dat").write_text(text, encoding="utf-8")


def test_parser_uses_documented_positions_and_preserves_unicode() -> None:
    row = make_row(
        "100",
        manufacturer_id="000005",
        short_code="AUDI",
        description_id="100000005",
        is_pc="1",
        is_engine="1",
        deleted="0",
    )
    parsed = parse_row(row, row_number=7, table_format=TABLE_FORMATS["100"])

    assert parsed.values["manufacturer_id"] == "000005"
    assert parsed.values["is_pc"] == "1"
    assert parsed.source_ref == "100:7"


def test_parser_rejects_wrong_length_and_table_marker() -> None:
    with pytest.raises(TecDocFormatError, match="expected 107"):
        parse_row("too short", row_number=1, table_format=TABLE_FORMATS["120"])
    row = make_row("120", ktype_id="1")
    with pytest.raises(TecDocFormatError, match="table marker"):
        parse_row(row[:4] + "999" + row[7:], row_number=1, table_format=TABLE_FORMATS["120"])


def test_extracts_passenger_hierarchy_and_preserves_multiple_engines(tmp_path: Path) -> None:
    write_table(tmp_path, "100", [make_row(
        "100", manufacturer_id="000005", description_id="100000005",
        is_pc="1", is_engine="1", deleted="0",
    )])
    write_table(tmp_path, "110", [make_row(
        "110", model_id="00050", description_id="110000050",
        manufacturer_id="000005", year_from="201701", is_pc="1", deleted="0",
    )])
    write_table(tmp_path, "120", [make_row(
        "120", ktype_id="000012345", description_id="120012345", model_id="00050",
        year_from="201801", power_kw="0140", displacement_cc="01969",
        fuel_type_code="001", drive_type_code="004", body_type_code="006", deleted="0",
    )])
    write_table(tmp_path, "155", [
        make_row(
            "155", manufacturer_id="000005", engine_id="00001", engine_code="D4204T14",
            displacement_cc_from="01969", displacement_cc_to="01969",
            fuel_type_code="002", deleted="0",
        ),
        make_row(
            "155", manufacturer_id="000005", engine_id="00002", engine_code="B4204T35",
            displacement_cc_from="01969", displacement_cc_to="01969",
            fuel_type_code="001", deleted="0",
        ),
    ])
    write_table(tmp_path, "125", [
        make_row("125", ktype_id="000012345", sequence="001", engine_id="00001", exclude="0"),
        make_row(
            "125", ktype_id="000012345", sequence="002", engine_id="00001",
            country_code="S", exclude="0",
        ),
        make_row("125", ktype_id="000012345", sequence="003", engine_id="00002", exclude="0"),
    ])
    write_table(tmp_path, "544", [make_row(
        "544", transmission_id="00042", manufacturer_id="000005",
        transmission_code="TG-81SC", transmission_type_code="002",
        transmission_identity="AWF8F45", speeds="08",
    )])
    write_table(tmp_path, "547", [make_row(
        "547", ktype_id="000012345", sequence="01", transmission_id="00042",
        year_from="201801", exclude="0",
    )])
    write_table(tmp_path, "012", [
        make_row("012", description_id="100000005", language_id="004", text="VOLVO"),
        make_row("012", description_id="110000050", language_id="004", text="XC60"),
        make_row("012", description_id="120012345", language_id="004", text="D4 AWD"),
    ])

    records = tuple(extract_dat_hierarchy(tmp_path))

    assert len(records) == 1
    assert records[0].manufacturer_name == "VOLVO"
    assert records[0].model_name == "XC60"
    assert records[0].ktype_name == "D4 AWD"
    assert records[0].manufacturer_groups == ("PC", "Engine")
    assert [engine.engine_code for engine in records[0].engines] == ["D4204T14", "B4204T35"]
    assert records[0].engines[0].engine_source_row_ref == "155:1"
    assert records[0].engines[0].deleted is False
    assert records[0].engines[0].applicability[0].source_row_ref == "125:1"
    assert len(records[0].engines[0].applicability) == 2
    assert len(records[0].transmissions) == 1
    assert records[0].transmissions[0].transmission_code == "TG-81SC"
    assert records[0].transmissions[0].speeds == 8
    assert records[0].transmissions[0].applicability[0].source_row_ref == "547:1"


def test_ignores_deleted_and_non_pc_models(tmp_path: Path) -> None:
    write_table(tmp_path, "100", [make_row(
        "100", manufacturer_id="000005", description_id="100000005", is_pc="1", deleted="0"
    )])
    write_table(tmp_path, "110", [make_row(
        "110", model_id="00050", description_id="110000050",
        manufacturer_id="000005", is_cv="1", deleted="0",
    )])
    write_table(tmp_path, "120", [make_row(
        "120", ktype_id="000012345", description_id="120012345",
        model_id="00050", deleted="0",
    )])
    write_table(tmp_path, "125", [])
    write_table(tmp_path, "155", [])
    write_table(tmp_path, "012", [])

    assert tuple(extract_dat_hierarchy(tmp_path)) == ()


def test_loads_official_key_labels_and_maps_only_supported_fuels(tmp_path: Path) -> None:
    write_table(tmp_path, "020", [make_row(
        "020", language_id="004", description_id="000000001", iso_code="en"
    )])
    write_table(tmp_path, "052", [
        make_row(
            "052", key_table_id="088", key="001", description_id="000010788", deleted="0"
        ),
        make_row(
            "052", key_table_id="088", key="039", description_id="000012777", deleted="0"
        ),
    ])
    write_table(tmp_path, "030", [
        make_row(
            "030", description_id="000010788", language_id="004", text="Petrol", deleted="0"
        ),
        make_row(
            "030", description_id="000012777", language_id="004", text="Bi-Fuel", deleted="0"
        ),
    ])

    assert load_key_table_labels(tmp_path, key_table_id="088") == {
        "001": "Petrol", "039": "Bi-Fuel"
    }
    assert canonical_engine_fuels(tmp_path) == {"001": "petrol"}


def test_exposes_official_bodywork_and_transmission_labels(tmp_path: Path) -> None:
    write_table(tmp_path, "020", [make_row(
        "020", language_id="004", description_id="000000001", iso_code="en"
    )])
    write_table(tmp_path, "052", [
        make_row("052", key_table_id="085", key="002", description_id="000000085", deleted="0"),
        make_row("052", key_table_id="086", key="053", description_id="000000086", deleted="0"),
    ])
    write_table(tmp_path, "030", [
        make_row("030", description_id="000000085", language_id="004", text="Fully Automatic", deleted="0"),
        make_row("030", description_id="000000086", language_id="004", text="SUV", deleted="0"),
    ])

    assert official_transmission_type_labels(tmp_path) == {"002": "Fully Automatic"}
    assert official_bodywork_labels(tmp_path) == {"053": "SUV"}
    assert canonical_bodywork_by_kt086()["053"] == "suv"
    assert canonical_bodywork_by_kt086()["040"] == "multi_purpose_vehicle"
    assert canonical_drive_by_kt082() == {
        "001": "fwd", "002": "rwd", "003": "awd", "004": "awd",
        "005": "awd", "011": "awd",
    }
