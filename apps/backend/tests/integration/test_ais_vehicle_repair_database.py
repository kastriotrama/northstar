"""Vehicles AIS created from a record the import misread are repaired in place, once."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from psycopg import Connection

from ingestion.ais_vehicle_repair import repair_ais_vehicles
from ingestion.ledger_migrations import LEDGER_TABLE
from ingestion.vehicle_core_merge import VehicleState
from ingestion.vehicle_core_rules import FAMILIES_BY_ID, learn_rules, store_rules
from ingestion.vehicle_core_store import load_vehicle, mint_vehicle_id, save_vehicles
from ingestion.vehicle_core_ts import backfill_vehicle_core
from tests.integration.throwaway_database import throwaway_database
from tests.integration.vehicle_core_fixtures import (
    insert_ts_record,
    prepare_schema,
    project,
    volvo,
)


def _ais_vehicle(**values: object) -> VehicleState:
    state = VehicleState(mint_vehicle_id(), "ais", date(2026, 9, 19))
    state.values.update(
        {
            "registry_status": "registered",
            "registry_vehicle_type": "PB",
            "vehicle_scope": "passenger",
            "registry_brand_text": "VOLVO V70",
            "manufacturer": "Volvo",
            "fuel": "diesel",
            "power_kw": 133,
            "production_year": 2015,
            "production_month": 2,
            "first_registration_date": date(2015, 3, 12),
            "normalization_status": "resolved",
            "normalization_confidence": 0.95,
        }
    )
    state.values.update(values)
    return state


@pytest.fixture(scope="module")
def db() -> Iterator[tuple[Connection, str, str, str]]:
    with throwaway_database("ais_vehicle_repair") as connection:
        prepare_schema(connection)
        # The registry writes this make's code in three characters.
        insert_ts_record(connection, volvo(fab_code="VOX", plate="TSX001"))
        insert_ts_record(connection, volvo(vin="YV1BW84S1F2222222", plate="TSV002"))
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        for family in ("TSC-EU", "TSC-BRAND", "TSC-MODEL", "TSC-CCM", "TSC-4WD", "TSC-BT"):
            spec = FAMILIES_BY_ID[family]
            store_rules(connection, spec, learn_rules(connection, spec, min_support=1),
                        learned_from="transportstyrelsen")
        # "VOX101100" as the import used to cut it, and a car it cut correctly.
        cut_wrongly = _ais_vehicle(
            vin="YV1BW84S1F5555555", plate="NEW555", registry_make_code="VO", group_code="X101100"
        )
        # A car of a group the registry snapshot does not know: its whole AIS name
        # is its brand text.
        undivided = _ais_vehicle(
            vin="YV1BW84S1F6666666", plate="NEW666", registry_make_code="VO", group_code="909090",
            registry_brand_text="VOLVO EX30",
        )
        # Nothing to repair: the name is only the brand text.
        bare = _ais_vehicle(
            vin="YV1BW84S1F7777777", plate="NEW777", registry_make_code="VO", group_code="909090",
            registry_brand_text="VOLVO",
        )
        save_vehicles(connection, [cut_wrongly, undivided, bare])
        connection.commit()
        yield connection, cut_wrongly.vehicle_id, undivided.vehicle_id, bare.vehicle_id


def _ledger_rows(connection: Connection, vehicle_id: str) -> int:
    row = connection.execute(
        f"SELECT count(*) FROM {LEDGER_TABLE} WHERE target_node_id = %s", (vehicle_id,)
    ).fetchone()
    assert row is not None
    return int(row[0])


def test_a_dry_run_counts_and_writes_nothing(db: tuple[Connection, str, str, str]) -> None:
    connection, wrong, _, _ = db

    summary = repair_ais_vehicles(connection)

    assert (summary.selected, summary.repaired, summary.unchanged, summary.written) == (3, 2, 1, 0)
    assert summary.make_codes == {"VO -> VOX": 1}
    vehicle = load_vehicle(connection, wrong)
    assert vehicle is not None
    assert vehicle.values["registry_make_code"] == "VO"
    assert _ledger_rows(connection, wrong) == 0


def test_the_vehicles_are_repaired_and_the_changes_are_in_the_ledger(
    db: tuple[Connection, str, str, str],
) -> None:
    connection, wrong, undivided, bare = db
    untouched = load_vehicle(connection, bare)

    summary = repair_ais_vehicles(connection, dry_run=False)

    assert (summary.selected, summary.repaired, summary.written) == (3, 2, 2)
    vehicle = load_vehicle(connection, wrong)
    assert vehicle is not None
    assert vehicle.values["registry_make_code"] == "VOX"
    assert vehicle.values["group_code"] == "101100"
    # What the registry's cars of the make and group say.
    assert vehicle.values["registry_brand_text"] == "VOLVO"
    assert vehicle.values["registry_model_text"] == "V70"
    assert vehicle.values["displacement_cc"] == 1969
    assert vehicle.values["registry_all_wheel_drive"] is False
    assert vehicle.field_sources["registry_model_text"].startswith("rule:TSC-MODEL-")
    assert vehicle.values["manufacturer"] == "Volvo"
    assert vehicle.values["normalization_status"] == "resolved"
    # Not read again: the vehicle keeps what it had.
    assert vehicle.values["fuel"] == "diesel"
    assert vehicle.values["power_kw"] == 133
    assert _ledger_rows(connection, wrong) == 1

    # The name of the car of an unknown group is divided at the make's brand text.
    divided = load_vehicle(connection, undivided)
    assert divided is not None
    assert divided.values["registry_brand_text"] == "VOLVO"
    assert divided.field_sources["registry_brand_text"].startswith("rule:TSC-BT-")
    assert divided.values["registry_model_text"] == "EX30"
    assert "registry_model_text" not in divided.field_sources
    assert _ledger_rows(connection, undivided) == 1

    after = load_vehicle(connection, bare)
    assert untouched is not None and after is not None
    assert after.values == untouched.values
    assert _ledger_rows(connection, bare) == 0


def test_a_second_run_changes_nothing(db: tuple[Connection, str, str, str]) -> None:
    connection, wrong, undivided, _ = db

    summary = repair_ais_vehicles(connection, dry_run=False)

    # The car whose name is only the brand text is read again and left as it is.
    assert (summary.selected, summary.repaired, summary.unchanged, summary.written) == (1, 0, 1, 0)
    assert _ledger_rows(connection, wrong) == 1
    assert _ledger_rows(connection, undivided) == 1
