"""A car whose registry model text names a family no normalization rule maps yet."""

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
    VehicleCandidate("xc40", "Volvo", "XC40 (536)", model_aliases=("XC40",), year_from=2017),
    VehicleCandidate("ex40", "Volvo", "EX40 (536)", model_aliases=("EX40",), year_from=2024),
)
GUARD = ModelGuard(TecDocDryRunEvaluator(CATALOG))


@pytest.fixture(scope="module")
def db() -> Iterator[Connection]:
    with throwaway_database("vehicle_core_model_text") as connection:
        prepare_schema(connection)
        for index in range(3):
            insert_ts_record(connection, volvo(vin=f"YV1XK0000R{index:07d}", plate=f"EXF{index:03d}",
                                               model="EX40", vehicle_year=2025, **NO_CODES))
        # A model text that is no family: nothing is filled.
        insert_ts_record(connection, volvo(vin="YV1XK0000R9999999", plate="EXX001",
                                           model="EX41", vehicle_year=2025, **NO_CODES))
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
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


def test_the_cars_carry_the_model_text_but_no_family(db: Connection) -> None:
    assert _model(db, "EXF000") == (None, None)


def test_a_reviewed_name_in_the_model_text_fills_the_family(db: Connection) -> None:
    family = FAMILIES_BY_ID["MOD-MT"]
    learned = learn_rules(db, family)

    assert [(rule.key_values, rule.value, rule.support) for rule in learned] == [
        (("Volvo", "EX40"), "EX40", 3)
    ]
    store_rules(db, family, learned, learned_from=family.learned_from)
    assert apply_rules(db, family, guard=GUARD).filled == 3
    db.commit()

    model, source = _model(db, "EXF001")
    assert model == "EX40" and str(source).startswith("rule:MOD-MT-")
    assert _model(db, "EXX001") == (None, None)
    # Learned again once every car is filled, the rule stays.
    assert store_rules(db, family, learn_rules(db, family), learned_from=family.learned_from).retired == 0


def test_a_make_code_no_rule_knows_gets_its_make_and_model_from_the_brand_text() -> None:
    with throwaway_database("vehicle_core_brand_text") as connection:
        prepare_schema(connection)
        # Ten V70s under TS's own make code: they teach that "VOLVO" is Volvo.
        for index in range(10):
            insert_ts_record(connection, volvo(vin=f"YV1BW84S1F30000{index:02d}", plate=f"SIB{index:03d}"))
        # A code no rule knows (AIS writes "PO" for Polestar, TS for Pontiac), and a
        # brand text that repeats the make.
        insert_ts_record(connection, volvo(vin="YV1XK0000R8888888", plate="AIS001", brand="VOLVO VOLVO EX40",
                                           fab_code="ZZ", model=None, vehicle_year=2025, **NO_CODES))
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        connection.commit()
        with connection.cursor() as cursor:
            cursor.execute("SELECT manufacturer FROM core.vehicles WHERE plate = 'AIS001'")
            assert cursor.fetchone() == (None,)

        make = FAMILIES_BY_ID["MFR-BW"]
        assert [(r.key_values, r.value) for r in learn_rules(connection, make)] == [(("VOLVO",), "Volvo")]
        store_rules(connection, make, learn_rules(connection, make), learned_from=make.learned_from)
        assert apply_rules(connection, make).filled == 1
        model = FAMILIES_BY_ID["MOD-BRT"]
        store_rules(connection, model, learn_rules(connection, model), learned_from=model.learned_from)
        assert apply_rules(connection, model, guard=GUARD).filled == 1
        connection.commit()

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT manufacturer, model_family, field_sources ->> 'manufacturer', "
                "field_sources ->> 'model_family' FROM core.vehicles WHERE plate = 'AIS001'"
            )
            row = cursor.fetchone()
        assert row is not None
        assert row[:2] == ("Volvo", "EX40")
        assert str(row[2]).startswith("rule:MFR-BW-") and str(row[3]).startswith("rule:MOD-BRT-")
