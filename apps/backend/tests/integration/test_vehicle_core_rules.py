"""Enrichment rules are learned from sourced values, fill gaps only, and retire cleanly."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from psycopg import Connection

from ingestion.vehicle_core_ais import AisExtract, AisRecord, import_ais_extract
from ingestion.vehicle_core_rules import (
    FAMILIES_BY_ID,
    apply_rules,
    learn_rules,
    retire_rule,
    store_rules,
)
from ingestion.vehicle_core_store import load_vehicle
from ingestion.vehicle_core_ts import backfill_vehicle_core
from tests.integration.throwaway_database import throwaway_database
from tests.integration.vehicle_core_fixtures import (
    insert_ts_record,
    prepare_schema,
    project,
    volvo,
)

VINS = [f"YV1BW84S1F12345{index}{index}" for index in range(5)]


@pytest.fixture(scope="module")
def db() -> Iterator[Connection]:
    with throwaway_database("vehicle_core_rules") as connection:
        prepare_schema(connection)
        for index, vin in enumerate(VINS):
            insert_ts_record(connection, volvo(vin=vin, plate=f"RUL{index:03d}"),
                             ingested_at="2026-08-07")
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        extract = AisExtract(Path("export.xml"), datetime(2026, 9, 19, tzinfo=UTC), "cd" * 32)
        records = [
            AisRecord(vin, f"RUL{index:03d}", {
                "vehicle_type": "PB",
                # The last car AIS left without an engine code.
                **({"engine_code": "D5244T21"} if index < 4 else {}),
            })
            for index, vin in enumerate(VINS)
        ]
        import_ais_extract(connection, Path("export.xml"), extract=extract, records=records,
                           min_free_bytes=None)
        yield connection


def _engine(connection: Connection, plate: str) -> tuple[object, object]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT engine_code, field_sources ->> 'engine_code' FROM core.vehicles WHERE plate = %s",
            (plate,),
        )
        return cursor.fetchone()


def test_a_rule_is_learned_from_sourced_values_and_fills_the_gap(db: Connection) -> None:
    family = FAMILIES_BY_ID["ENG-VV"]
    learned = learn_rules(db, family, min_support=4)

    assert len(learned) == 1
    assert learned[0].value == "D5244T21"
    assert learned[0].support == 4
    assert learned[0].agreement == 1.0

    stored = store_rules(db, family, learned, learned_from="ais")
    assert stored.added == 1
    applied = apply_rules(db, family)
    db.commit()

    assert applied.filled == 1
    code, source = _engine(db, "RUL004")
    assert code == "D5244T21"
    assert source == f"rule:{learned[0].rule_id}"
    # Cars AIS spoke for are untouched.
    assert _engine(db, "RUL000") == ("D5244T21", "ais@2026-09-19")


def test_storing_the_same_rules_again_changes_nothing(db: Connection) -> None:
    family = FAMILIES_BY_ID["ENG-VV"]
    learned = learn_rules(db, family, min_support=4)

    stored = store_rules(db, family, learned, learned_from="ais")

    assert (stored.added, stored.kept, stored.retired) == (0, 1, 0)


def test_a_rule_below_the_threshold_is_not_learned(db: Connection) -> None:
    assert learn_rules(db, FAMILIES_BY_ID["ENG-VV"], min_support=6) == []


def test_retiring_a_rule_takes_back_exactly_what_it_filled(db: Connection) -> None:
    family = FAMILIES_BY_ID["ENG-VV"]
    (rule,) = learn_rules(db, family, min_support=4)

    assert retire_rule(db, rule.rule_id) == 1
    db.commit()

    assert _engine(db, "RUL004") == (None, None)
    assert _engine(db, "RUL000") == ("D5244T21", "ais@2026-09-19")
    state = load_vehicle(db, _vehicle_id(db, "RUL004"))
    assert state is not None and "engine_code" not in state.field_alternatives


def _vehicle_id(connection: Connection, plate: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT vehicle_id FROM core.vehicles WHERE plate = %s", (plate,))
        return str(cursor.fetchone()[0])
