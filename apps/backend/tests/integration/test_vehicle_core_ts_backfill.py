"""The TS backfill turns TS records into NorthStar vehicles, idempotently.

Runs against a throwaway database: see `throwaway_database`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from psycopg import Connection
from psycopg.errors import RestrictViolation, UniqueViolation

from ingestion.vehicle_core_store import load_vehicle
from ingestion.vehicle_core_ts import backfill_vehicle_core
from tests.integration.throwaway_database import throwaway_database
from tests.integration.vehicle_core_fixtures import (
    insert_ts_record,
    prepare_schema,
    project,
    volvo,
)


@pytest.fixture(scope="module")
def db() -> Iterator[Connection]:
    with throwaway_database("vehicle_core_ts") as connection:
        prepare_schema(connection)
        # One car seen twice: the import's temporary plate, then its permanent one.
        insert_ts_record(connection, volvo(plate="TPD118"), batch="old", ingested_at="2026-08-01")
        insert_ts_record(connection, volvo(plate="ABC123"), batch="new", ingested_at="2026-08-07")
        # Two old cars that share a short chassis number: different vehicles.
        insert_ts_record(
            connection,
            volvo(vin="000003", plate="OLD001", vehicle_year=1967, registration_date="19661026",
                  variant=None, version=None, build_month=None),
        )
        insert_ts_record(
            connection,
            volvo(vin="000003", plate="OLD002", vehicle_year=1975, registration_date="19741029",
                  variant=None, version=None, build_month=None),
        )
        # A repeated batch copy of ABC123 that the per-plate dedupe drops.
        insert_ts_record(connection, volvo(plate="ABC123"), batch="repeat", ingested_at="2026-08-02")
        connection.commit()
        project(connection)
        yield connection


def _vehicle_ids(connection: Connection) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT vehicle_id FROM core.vehicles ORDER BY vehicle_id")
        return [str(row[0]) for row in cursor.fetchall()]


def test_every_ts_car_becomes_one_nor_vehicle(db: Connection) -> None:
    summary = backfill_vehicle_core(db, min_free_bytes=None)

    ids = _vehicle_ids(db)
    assert len(ids) == 3  # the Volvo (two plates) and the two old cars
    assert all(vehicle_id.startswith("NOR-") for vehicle_id in ids)
    assert summary.records_read == 4  # the dedupe left one row per plate
    assert summary.duplicates_linked == 1  # the repeated copy of ABC123


def test_a_full_vin_seen_under_two_plates_is_one_vehicle_with_plate_history(db: Connection) -> None:
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT vehicle_id FROM core.vehicle_identifiers "
            "WHERE kind = 'vin' AND value = 'YV1BW84S1F1234567' AND valid_to IS NULL"
        )
        (vehicle_id,) = cursor.fetchone()
        cursor.execute(
            "SELECT value, valid_to IS NULL FROM core.vehicle_identifiers "
            "WHERE vehicle_id = %s AND kind = 'plate' ORDER BY value",
            (vehicle_id,),
        )
        plates = cursor.fetchall()
    state = load_vehicle(db, vehicle_id)

    assert state is not None
    assert state.values["plate"] == "ABC123"
    assert plates == [("ABC123", True), ("TPD118", False)]
    assert state.values["manufacturer"] == "Volvo"
    assert state.values["first_registration_date"].isoformat() == "2015-03-12"
    assert state.values["production_month"] == 2


def test_a_shared_short_chassis_number_is_not_identity(db: Connection) -> None:
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT count(DISTINCT vehicle_id) FROM core.vehicle_identifiers "
            "WHERE kind = 'chassis' AND value = '000003'"
        )
        assert cursor.fetchone()[0] == 2


def test_every_ts_record_is_linked_to_its_vehicle(db: Connection) -> None:
    with db.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM staging.transportstyrelsen_raw")
        raw = cursor.fetchone()[0]
        cursor.execute(
            "SELECT count(*) FROM core.vehicle_source_links WHERE source_system = 'transportstyrelsen'"
        )
        assert cursor.fetchone()[0] == raw


def test_running_again_mints_nothing_and_changes_nothing(db: Connection) -> None:
    before = _vehicle_ids(db)
    with db.cursor() as cursor:
        cursor.execute("SELECT max(updated_at) FROM core.vehicles")
        last_change = cursor.fetchone()[0]

    summary = backfill_vehicle_core(db, min_free_bytes=None)

    assert _vehicle_ids(db) == before
    assert summary.vehicles_created == 0
    assert summary.vehicles_updated == 0
    with db.cursor() as cursor:
        cursor.execute("SELECT max(updated_at) FROM core.vehicles")
        assert cursor.fetchone()[0] == last_change


def test_a_plate_cannot_be_current_on_two_vehicles(db: Connection) -> None:
    ids = _vehicle_ids(db)
    with db.cursor() as cursor, pytest.raises(UniqueViolation):
        cursor.execute(
            "INSERT INTO core.vehicle_identifiers (vehicle_id, kind, value, source) "
            "VALUES (%s, 'plate', 'ABC123', 'manual')",
            (ids[0] if ids[0] != _owner(db, "ABC123") else ids[1],),
        )
    db.rollback()


def test_vehicles_identifiers_and_links_are_never_deleted(db: Connection) -> None:
    backfill_vehicle_core(db, min_free_bytes=None)
    for table in ("core.vehicle_source_links", "core.vehicle_identifiers", "core.vehicles"):
        with db.cursor() as cursor, pytest.raises(RestrictViolation, match="never deleted"):
            cursor.execute(f"DELETE FROM {table}")
        db.rollback()


def _owner(connection: Connection, plate: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT vehicle_id FROM core.vehicle_identifiers "
            "WHERE kind = 'plate' AND value = %s AND valid_to IS NULL",
            (plate,),
        )
        return str(cursor.fetchone()[0])
