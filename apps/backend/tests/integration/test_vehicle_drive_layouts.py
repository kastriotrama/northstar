"""The reviewed drive layouts fill a two-wheel-drive car's driven axle, and only that.

Runs against a throwaway database: see `throwaway_database`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from psycopg import Connection

from ingestion.vehicle_core_rules import (
    DRIVE_LAYOUT_FAMILY,
    FAMILIES_BY_ID,
    apply_rules,
    learn_rules,
    store_rules,
)
from ingestion.vehicle_core_ts import backfill_vehicle_core
from tests.integration.throwaway_database import throwaway_database
from tests.integration.vehicle_core_fixtures import insert_ts_record, prepare_schema, project, volvo

FAMILY = FAMILIES_BY_ID[DRIVE_LAYOUT_FAMILY]


@pytest.fixture()
def db() -> Iterator[Connection]:
    with throwaway_database("vehicle_drive_layouts") as connection:
        prepare_schema(connection)
        # The fixture Volvo is a V70 the registry marks as not four-wheel drive.
        insert_ts_record(connection, volvo(vin="YV1BW84S1F1000001", plate="FWD001"))
        insert_ts_record(connection, volvo(vin="YV1BW84S1F1000002", plate="AWD001", is_4wd="1"))
        insert_ts_record(connection, volvo(vin="YV1BW84S1F1000003", plate="GEN001"))
        insert_ts_record(connection, volvo(vin="YV1BW84S1F1000004", plate="SET001"))
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        with connection.cursor() as cursor:
            # One car carries the generic value, one a value a reviewer set.
            cursor.execute("UPDATE core.vehicles SET drive_type = '2wd', field_sources = "
                           "field_sources || '{\"drive_type\": \"rule:TSC-4WD-x\"}' WHERE plate = 'GEN001'")
            cursor.execute("UPDATE core.vehicles SET drive_type = 'rwd', field_sources = "
                           "field_sources || '{\"drive_type\": \"review:r1\"}' WHERE plate = 'SET001'")
        connection.commit()
        yield connection


def _drive(connection: Connection, plate: str) -> tuple[object, object]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT drive_type, split_part(field_sources ->> 'drive_type', '-', 1) "
            "FROM core.vehicles WHERE plate = %s",
            (plate,),
        )
        row = cursor.fetchone()
    assert row is not None
    return row[0], row[1]


def test_the_layout_is_filled_for_two_wheel_drive_cars_only(db: Connection) -> None:
    before_awd = _drive(db, "AWD001")
    learned = learn_rules(db, FAMILY)
    assert {(rule.key_values[0], rule.key_values[1], rule.value) for rule in learned} == {
        ("Volvo", "V70", "fwd")}
    assert learned[0].support == 3  # the three cars the registry marks as not four-wheel drive

    store_rules(db, FAMILY, learned, learned_from=FAMILY.learned_from)
    filled = apply_rules(db, FAMILY)
    db.commit()

    assert filled.filled == 2
    assert _drive(db, "FWD001") == ("fwd", "rule:DRV")           # a gap, filled
    assert _drive(db, "GEN001") == ("fwd", "rule:DRV")           # the generic value, replaced
    assert _drive(db, "SET001") == ("rwd", "review:r1")          # a reviewer's value stands
    assert _drive(db, "AWD001") == before_awd                    # four-wheel drive: not ours


def test_learning_again_keeps_the_rules_and_fills_nothing_twice(db: Connection) -> None:
    first = learn_rules(db, FAMILY)
    store_rules(db, FAMILY, first, learned_from=FAMILY.learned_from)
    apply_rules(db, FAMILY)
    db.commit()

    again = learn_rules(db, FAMILY)
    stored = store_rules(db, FAMILY, again, learned_from=FAMILY.learned_from)
    refilled = apply_rules(db, FAMILY)

    assert [rule.rule_id for rule in again] == [rule.rule_id for rule in first]
    assert (stored.added, stored.retired, refilled.filled) == (0, 0, 0)


def test_a_corrected_statement_takes_its_fills_back(
    db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_rules(db, FAMILY, learn_rules(db, FAMILY), learned_from=FAMILY.learned_from)
    apply_rules(db, FAMILY)
    db.commit()

    # The table no longer states the model: its rule is retired and the value goes with it.
    monkeypatch.setattr("ingestion.vehicle_core_rules.drive_layout", lambda *_: None)
    withdrawn = store_rules(db, FAMILY, learn_rules(db, FAMILY), learned_from=FAMILY.learned_from)
    taken_back = apply_rules(db, FAMILY)
    db.commit()

    assert (withdrawn.retired, taken_back.retracted, taken_back.filled) == (1, 2, 0)
    assert _drive(db, "FWD001")[0] is None
    assert _drive(db, "GEN001")[0] is None
    assert _drive(db, "SET001") == ("rwd", "review:r1")  # never ours, so never taken back

    # The table states the other axle: the cars are filled again, with the new value.
    monkeypatch.setattr("ingestion.vehicle_core_rules.drive_layout", lambda *_: "rwd")
    store_rules(db, FAMILY, learn_rules(db, FAMILY), learned_from=FAMILY.learned_from)
    again = apply_rules(db, FAMILY)
    db.commit()

    assert (again.retracted, again.filled) == (0, 2)
    assert _drive(db, "FWD001") == ("rwd", "rule:DRV")
