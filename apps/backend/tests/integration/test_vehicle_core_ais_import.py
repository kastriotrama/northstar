"""The AIS export updates NorthStar vehicles by the field policy, exactly once per extract."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from psycopg import Connection

from ingestion.vehicle_core_ais import import_ais_extract, open_extract
from ingestion.vehicle_core_rules import FAMILIES_BY_ID, learn_rules, store_rules
from ingestion.vehicle_core_store import load_vehicle
from ingestion.vehicle_core_ts import backfill_vehicle_core
from tests.integration.throwaway_database import throwaway_database
from tests.integration.vehicle_core_fixtures import (
    insert_ts_record,
    prepare_schema,
    project,
    volvo,
)

EXPORT_TIME = "2026-09-19 10:28:27"


def _entity(vin: str, name: str, values: dict[str, str]) -> str:
    rendered = "".join(
        f'<Value AttributeID="{key}">{value}</Value>' for key, value in values.items()
    )
    return (
        f'<Entity ID="{vin}" UserTypeID="Vehicle Identification Number" ParentID="VIN Numbers Se">'
        f"<Name>{name}</Name><Values>{rendered}</Values></Entity>"
    )


def _export(path: Path, entities: list[str]) -> Path:
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<STEP-ProductInformation ExportTime="{EXPORT_TIME}" ExportContext="Swedish CTX">'
        f"<Entities>{''.join(entities)}</Entities></STEP-ProductInformation>",
        encoding="utf-8",
    )
    return path


def _car(**values: str) -> dict[str, str]:
    base = {
        "ATTR_Name of the car": "VOLVO V70",
        "ATTR_Group code": "VO101100",
        "ATTR_Type of vehicle": "PB",
        "ATTR_Fuel": "2",
        "ATTR_Gearbox": "A",
        "ATTR_Chassie/Body": "AC",
        "ATTR_Output kW": "133",
        "ATTR_Car colour": "SVART",
        "ATTR_Registration date": "20150312",
        "ATTR_Month of manufacturing": "201502",
    }
    base.update(values)
    return base


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Connection, Path]]:
    with throwaway_database("vehicle_core_ais") as connection:
        prepare_schema(connection)
        insert_ts_record(connection, volvo(plate="ABC123"), ingested_at="2026-08-07")
        insert_ts_record(
            connection,
            volvo(vin="000003", plate="OLD001", vehicle_year=1967, registration_date="19661026",
                  variant=None, version=None, build_month=None),
        )
        insert_ts_record(
            connection,
            volvo(vin="000004", plate="OLD002", vehicle_year=1975, registration_date="19741029",
                  variant=None, version=None, build_month=None),
        )
        insert_ts_record(connection, volvo(vin="YV1BW84S1F7777777", plate="MOV111"))
        insert_ts_record(connection, volvo(vin="YV1BW84S1F9999999", plate="COR111"))
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        # Completion rules learned from the TS cars, so a new car can be completed.
        for family in ("TSC-EU", "TSC-BRAND", "TSC-MODEL", "TSC-CCM", "TSC-4WD"):
            spec = FAMILIES_BY_ID[family]
            store_rules(connection, spec, learn_rules(connection, spec, min_support=1),
                        learned_from="transportstyrelsen")
        connection.commit()

        export = _export(
            tmp_path_factory.mktemp("ais") / "export.xml",
            [
                # Same car: new engine code, weights, model year; repainted.
                _entity("YV1BW84S1F1234567", "ABC123", _car(**{
                    "ATTR_AIS Engine code": "D5244T21", "ATTR_Model year": "2015",
                    "ATTR_Weight": "1640", "ATTR_Max weight": "2180", "ATTR_Car length": "4814",
                    "ATTR_Car colour": "VIT",
                })),
                # An old car scrapped since the TS snapshot.
                _entity("000003", "OLD001", _car(**{"Import_Deleted": "true",
                                                     "ATTR_Registration date": "19661026"})),
                # An old car converted to an A-traktor.
                _entity("000004", "OLD002", _car(**{"ATTR_Type of vehicle": "TR",
                                                     "ATTR_Chassie/Body": "07",
                                                     "ATTR_Registration date": "19741029"})),
                # MOV111 moved: its car has a new plate, a new car carries MOV111.
                _entity("YV1BW84S1F7777777", "MOV222", _car()),
                _entity("WVWZZZ1KZAW000001", "MOV111", _car(**{"ATTR_Name of the car": "VOLVO V70",
                                                                "ATTR_AIS Engine code": "B4204T"})),
                # COR111's VIN was corrected in the register.
                _entity("YV1BW84S1F9999990", "COR111", _car()),
                # Out of scope: a truck and a car deregistered before we saw it.
                _entity("YV2T0X1A3TZ169397", "ZYN17H", _car(**{"ATTR_Type of vehicle": "LB"})),
                _entity("WBA3D31090J307644", "AAA004", _car(**{"Import_Deleted": "true"})),
            ],
        )
        yield connection, export


def _vehicle_by_plate(connection: Connection, plate: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT vehicle_id FROM core.vehicle_identifiers "
            "WHERE kind = 'plate' AND value = %s AND valid_to IS NULL",
            (plate,),
        )
        row = cursor.fetchone()
    assert row is not None, plate
    return str(row[0])


def test_import_merges_by_policy(db: tuple[Connection, Path]) -> None:
    connection, export = db
    summary = import_ais_extract(connection, export, min_free_bytes=None)

    assert summary.records_read == 8
    assert summary.matched == 4
    assert summary.vehicles_created == 1
    assert summary.vin_corrections == 1
    assert summary.not_in_scope == 2
    assert summary.deregistered == 1
    assert summary.vehicle_type_changes == 1

    state = load_vehicle(connection, _vehicle_by_plate(connection, "ABC123"))
    assert state is not None
    assert state.values["engine_code"] == "D5244T21"
    assert state.values["kerb_weight_kg"] == 1640
    assert state.values["colour"] == "VIT"
    assert state.field_sources["engine_code"] == "ais@2026-09-19"
    # The TS colour is kept beside the newer AIS one, not thrown away.
    assert state.field_alternatives["colour"][0]["value"] == "SVART"


def test_deregistered_and_converted_cars(db: tuple[Connection, Path]) -> None:
    connection, _ = db
    scrapped = load_vehicle(connection, _vehicle_by_plate(connection, "OLD001"))
    tractor = load_vehicle(connection, _vehicle_by_plate(connection, "OLD002"))

    assert scrapped is not None and scrapped.values["registry_status"] == "deregistered"
    assert tractor is not None
    assert tractor.values["registry_vehicle_type"] == "TR"
    assert tractor.values["vehicle_scope"] != "passenger"
    assert tractor.values["eu_category"] is None


def test_a_moved_plate_is_closed_on_the_car_it_left(db: tuple[Connection, Path]) -> None:
    connection, _ = db
    old_car = _vehicle_by_plate(connection, "MOV222")
    new_car = _vehicle_by_plate(connection, "MOV111")

    assert old_car != new_car
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT valid_to FROM core.vehicle_identifiers "
            "WHERE vehicle_id = %s AND kind = 'plate' AND value = 'MOV111'",
            (old_car,),
        )
        assert cursor.fetchone()[0].isoformat() == "2026-09-19"
    created = load_vehicle(connection, new_car)
    assert created is not None
    assert created.origin_source == "ais"
    assert created.values["engine_code"] == "B4204T"
    assert created.values["manufacturer"] == "Volvo"


def test_a_corrected_vin_updates_the_car_instead_of_minting(db: tuple[Connection, Path]) -> None:
    connection, _ = db
    state = load_vehicle(connection, _vehicle_by_plate(connection, "COR111"))

    assert state is not None
    assert state.values["vin"] == "YV1BW84S1F9999990"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT value, valid_to IS NULL FROM core.vehicle_identifiers "
            "WHERE vehicle_id = %s AND kind = 'vin' ORDER BY value",
            (state.vehicle_id,),
        )
        assert cursor.fetchall() == [("YV1BW84S1F9999990", True), ("YV1BW84S1F9999999", False)]


def test_the_same_extract_is_imported_once(db: tuple[Connection, Path]) -> None:
    connection, export = db
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*), max(updated_at) FROM core.vehicles")
        before = cursor.fetchone()

    summary = import_ais_extract(connection, export, min_free_bytes=None)

    assert summary.skipped_already_imported
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*), max(updated_at) FROM core.vehicles")
        assert cursor.fetchone() == before


def test_extract_identity_comes_from_the_file(db: tuple[Connection, Path]) -> None:
    _, export = db
    extract = open_extract(export)

    assert extract.exported_on.isoformat() == "2026-09-19"
    assert extract.extract_id.startswith("ais-20260919T102827-")
