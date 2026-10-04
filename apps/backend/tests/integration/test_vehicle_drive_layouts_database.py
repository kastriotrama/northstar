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


# --- what the table leaves open: the cars alike, then the variant -----------------------


@pytest.fixture()
def alike() -> Iterator[Connection]:
    """Eight cars of one variant (same VIN descriptor, same power); none has a drive type."""

    with throwaway_database("vehicle_drive_evidence") as connection:
        prepare_schema(connection)
        for number in range(1, 9):
            insert_ts_record(
                connection, volvo(vin=f"YV1BW84S1F100000{number}", plate=f"CAR00{number}")
            )
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        with connection.cursor() as cursor:
            cursor.execute("UPDATE core.vehicles SET drive_type = NULL")
        connection.commit()
        yield connection


def _set(connection: Connection, plates: list[str], assignments: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE core.vehicles SET {assignments} WHERE plate = ANY(%s)", (plates,))
    connection.commit()


def _learn_and_apply(connection: Connection, name: str) -> int:
    family = FAMILIES_BY_ID[name]
    store_rules(connection, family, learn_rules(connection, family), learned_from=family.learned_from)
    filled = apply_rules(connection, family).filled
    connection.commit()
    return filled


def test_a_car_without_a_statement_takes_the_drive_type_of_the_cars_alike(alike: Connection) -> None:
    known = [f"CAR00{number}" for number in range(1, 6)]
    _set(alike, known, "drive_type = 'fwd', field_sources = field_sources || "
                       "jsonb_build_object('drive_type', 'review:r1')")
    _set(alike, ["CAR006"], "registry_all_wheel_drive = NULL")  # the registry says nothing

    assert _learn_and_apply(alike, "DRV-EVP") == 3
    assert _drive(alike, "CAR006") == ("fwd", "rule:DRV")
    assert _drive(alike, "CAR007") == ("fwd", "rule:DRV")
    assert _drive(alike, "CAR001") == ("fwd", "review:r1")

    # Its own fills are no evidence: with the reviewed cars gone, nothing is learned.
    _set(alike, known, "drive_type = NULL, field_sources = field_sources - 'drive_type'")
    assert learn_rules(alike, FAMILIES_BY_ID["DRV-EVP"]) == []


def test_cars_alike_never_contradict_the_registrys_statement(alike: Connection) -> None:
    known = [f"CAR00{number}" for number in range(1, 6)]
    _set(alike, known, "drive_type = 'awd', registry_all_wheel_drive = TRUE")
    _set(alike, ["CAR006"], "registry_all_wheel_drive = NULL")
    # CAR007 and CAR008 are marked as not four-wheel drive.

    assert _learn_and_apply(alike, "DRV-EVP") == 1
    assert _drive(alike, "CAR006")[0] == "awd"
    assert _drive(alike, "CAR007")[0] is None
    assert _drive(alike, "CAR008")[0] is None


def test_cars_alike_that_disagree_state_nothing(alike: Connection) -> None:
    _set(alike, ["CAR001", "CAR002", "CAR003"], "drive_type = 'fwd'")
    _set(alike, ["CAR004", "CAR005"], "drive_type = 'awd', registry_all_wheel_drive = TRUE")
    _set(alike, ["CAR006"], "registry_all_wheel_drive = NULL")

    assert _learn_and_apply(alike, "DRV-EVP") == 0
    assert _drive(alike, "CAR006")[0] is None


def test_the_variant_is_named_by_what_the_car_carries(alike: Connection) -> None:
    tesla = "manufacturer = 'Tesla', model_family = 'Model Y', fuel = 'electricity'"
    _set(alike, ["CAR001"], f"{tesla}, power_kw = 220, registry_all_wheel_drive = NULL")
    _set(alike, ["CAR002"], f"{tesla}, power_kw = 378, registry_all_wheel_drive = NULL")
    _set(alike, ["CAR003"], f"{tesla}, power_kw = 378")  # marked as not four-wheel drive
    _set(alike, ["CAR004"], f"{tesla}, power_kw = 280, registry_all_wheel_drive = NULL")
    _set(alike, ["CAR005"], "manufacturer = 'Volkswagen', model_family = NULL, "
                            "production_year = 1973, registry_model_text = NULL, "
                            "registry_brand_text = 'VOLKSWAGEN 1303 S'")

    assert _learn_and_apply(alike, "DRV-CAR") == 3
    assert _drive(alike, "CAR001") == ("rwd", "rule:DRV")
    assert _drive(alike, "CAR002") == ("awd", "rule:DRV")
    assert _drive(alike, "CAR003")[0] is None  # a second motor contradicts the registry
    assert _drive(alike, "CAR004")[0] is None  # between two variants
    assert _drive(alike, "CAR005") == ("rwd", "rule:DRV")  # a Beetle, by the registry's text
    # A second run keeps the rules and fills nothing.
    again = learn_rules(alike, FAMILIES_BY_ID["DRV-CAR"])
    stored = store_rules(alike, FAMILIES_BY_ID["DRV-CAR"], again, learned_from="x")
    assert (stored.added, stored.retired, apply_rules(alike, FAMILIES_BY_ID["DRV-CAR"]).filled) == (
        0, 0, 0)
