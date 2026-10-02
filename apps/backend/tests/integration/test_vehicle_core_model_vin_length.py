"""Sister models share a VIN descriptor; the registered length tells them apart."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from psycopg import Connection

from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator
from ingestion.vehicle_core_rules import FAMILIES_BY_ID, apply_rules, learn_rules, store_rules
from ingestion.vehicle_core_ts import backfill_vehicle_core
from ingestion.vehicle_model_guard import ModelGuard
from tests.integration.throwaway_database import throwaway_database
from tests.integration.vehicle_core_fixtures import (
    insert_ts_record,
    prepare_schema,
    project,
    volvo,
)

NO_CODES = {"variant": None, "version": None, "type_text": None}
CATALOG = (
    VehicleCandidate("v70", "Volvo", "V70 III (135)", model_aliases=("V70",), year_from=2007, year_to=2016),
    VehicleCandidate("xc70", "Volvo", "XC70 II (136)", model_aliases=("XC70",), year_from=2007, year_to=2016),
)
GUARD = ModelGuard(TecDocDryRunEvaluator(CATALOG))
# Length is an AIS observation; TS rows carry none, so the test sets it directly.
LENGTHS = {"V70": 4823, "XC70": 4838}


@pytest.fixture(scope="module")
def db() -> Iterator[Connection]:
    with throwaway_database("vehicle_core_model_vin_length") as connection:
        prepare_schema(connection)
        lengths: dict[str, int] = {}
        for model, prefix in (("V70", "A"), ("XC70", "B")):
            for index in range(10):
                plate = f"{prefix}SIB{index:02d}"
                insert_ts_record(connection, volvo(vin=f"YV1SZ59H1F{prefix}0000{index:02d}", plate=plate,
                                                   model=model, **NO_CODES))
                lengths[plate] = LENGTHS[model]
        # Only the shared descriptor and the length name these cars.
        for plate, length in (("LEN001", 4838), ("LEN002", 4900)):
            insert_ts_record(connection, volvo(vin=f"YV1SZ59H1F{plate}0", plate=plate,
                                               model=None, **NO_CODES))
            lengths[plate] = length
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        with connection.cursor() as cursor:
            cursor.executemany(
                "UPDATE core.vehicles SET length_mm = %s WHERE plate = %s",
                [(length, plate) for plate, length in lengths.items()],
            )
        connection.commit()
        yield connection


def _model(connection: Connection, plate: str) -> tuple[object, object]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT model_family, field_sources ->> 'model_family' FROM core.vehicles WHERE plate = %s",
            (plate,),
        )
        row = cursor.fetchone()
    assert row is not None
    return row[0], row[1]


def test_the_vin_descriptor_alone_names_no_model(db: Connection) -> None:
    assert learn_rules(db, FAMILIES_BY_ID["MOD-VIN"]) == []


def test_descriptor_and_length_name_the_sister_model(db: Connection) -> None:
    family = FAMILIES_BY_ID["MOD-VINL"]
    learned = learn_rules(db, family)

    assert sorted((rule.key_values, rule.value) for rule in learned) == [
        (("Volvo", "YV1SZ59H", "4823"), "V70"),
        (("Volvo", "YV1SZ59H", "4838"), "XC70"),
    ]
    store_rules(db, family, learned, learned_from=family.learned_from)
    assert apply_rules(db, family, guard=GUARD).filled == 1
    db.commit()

    model, source = _model(db, "LEN001")
    assert model == "XC70" and str(source).startswith("rule:MOD-VINL-")
    # A length no known sister has names nothing.
    assert _model(db, "LEN002") == (None, None)


def test_descriptor_and_model_year_name_a_renamed_model() -> None:
    with throwaway_database("vehicle_core_model_vin_year") as connection:
        prepare_schema(connection)
        # One descriptor, renamed between model years: P (2023) is an XC70, R (2024) a V70.
        for model, year in (("XC70", "P"), ("V70", "R")):
            for index in range(10):
                insert_ts_record(connection, volvo(vin=f"YV1SZ59H1{year}{model[:2]}{index:05d}",
                                                   plate=f"{model[:2]}{year}{index:03d}", model=model, **NO_CODES))
        insert_ts_record(connection, volvo(vin="YV1SZ59H1RZZ00001", plate="YR0001", model=None, **NO_CODES))
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        connection.commit()

        assert learn_rules(connection, FAMILIES_BY_ID["MOD-VIN"]) == []
        family = FAMILIES_BY_ID["MOD-VINY"]
        learned = learn_rules(connection, family)
        assert sorted((rule.key_values, rule.value) for rule in learned) == [
            (("Volvo", "YV1SZ59H", "P"), "XC70"),
            (("Volvo", "YV1SZ59H", "R"), "V70"),
        ]
        store_rules(connection, family, learned, learned_from=family.learned_from)
        assert apply_rules(connection, family, guard=GUARD).filled == 1
        connection.commit()
        assert _model(connection, "YR0001")[0] == "V70"
