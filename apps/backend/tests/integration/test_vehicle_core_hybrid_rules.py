"""The hybrid type of a car AIS added is learned from the registry's cars alike."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from psycopg import Connection

from ingestion.vehicle_core_merge import VehicleState
from ingestion.vehicle_core_rules import (
    FAMILIES_BY_ID,
    apply_rules,
    learn_rules,
    retire_rule,
    store_rules,
)
from ingestion.vehicle_core_store import load_vehicle, mint_vehicle_id, save_vehicles
from ingestion.vehicle_core_ts import backfill_vehicle_core
from tests.integration.throwaway_database import throwaway_database
from tests.integration.vehicle_core_fixtures import (
    insert_ts_record,
    prepare_schema,
    project,
    volvo,
)

FAMILY = FAMILIES_BY_ID["ELT-GC"]
HYBRIDS, PLUG_INS, PLAIN = "202020", "404040", "303030"


def _ais_car(group: str, *, second_fuel: str | None = None) -> VehicleState:
    """A car AIS added: its combustion fuel, and a second fuel only when AIS gave one."""

    state = VehicleState(mint_vehicle_id(), "ais", date(2026, 9, 19))
    state.values.update(
        {
            "registry_status": "registered",
            "registry_vehicle_type": "PB",
            "vehicle_scope": "passenger",
            "registry_make_code": "VO",
            "group_code": group,
            "fuel": "diesel",
            "fuel_secondary": second_fuel,
            "fuel_match_tokens": (
                ["diesel", "electricity", "hybrid_diesel"] if second_fuel else ["diesel"]
            ),
        }
    )
    return state


@pytest.fixture(scope="module")
def db() -> Iterator[tuple[Connection, dict[str, str]]]:
    with throwaway_database("vehicle_core_hybrid_rules") as connection:
        prepare_schema(connection)
        number = 0
        for group, ev_config in ((HYBRIDS, "ELHYBRID"), (PLUG_INS, "LADDHYBRID"), (PLAIN, None)):
            for _ in range(5):
                number += 1
                insert_ts_record(connection, volvo(
                    vin=f"YV1BW84S1F{number:07d}", plate=f"HYB{number:03d}", group_no=group,
                    ev_config=ev_config,
                ))
        # One registry car of the hybrids' group that the registry says is no hybrid.
        insert_ts_record(connection, volvo(vin="YV1BW84S1F9000001", plate="HYB900", group_no=HYBRIDS))
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        cars = {
            "hybrid": _ais_car(HYBRIDS),
            "plain": _ais_car(PLAIN),
            "plug_in_without_electricity": _ais_car(PLUG_INS),
            "plug_in": _ais_car(PLUG_INS, second_fuel="electricity"),
        }
        save_vehicles(connection, cars.values())
        connection.commit()
        yield connection, {name: state.vehicle_id for name, state in cars.items()}


def _car(connection: Connection, vehicle_id: str) -> VehicleState:
    state = load_vehicle(connection, vehicle_id)
    assert state is not None
    return state


def test_a_group_of_hybrids_is_a_rule_and_a_group_of_plain_cars_is_none(
    db: tuple[Connection, dict[str, str]],
) -> None:
    connection, _ = db

    rules = learn_rules(connection, FAMILY, min_support=5, min_agreement=0.8)
    store_rules(connection, FAMILY, rules, learned_from="transportstyrelsen")
    connection.commit()

    assert {(rule.key_values, rule.value) for rule in rules} == {
        (("VO", HYBRIDS), "hybrid"),
        (("VO", PLUG_INS), "plug_in_hybrid"),
    }
    # The registry car that is no hybrid counted against its group's rule.
    hybrid_rule = next(rule for rule in rules if rule.value == "hybrid")
    assert (hybrid_rule.support, hybrid_rule.agreement) == (6, 0.8333)


def test_a_hybrid_ais_added_gets_its_type_its_second_fuel_and_its_tokens(
    db: tuple[Connection, dict[str, str]],
) -> None:
    connection, cars = db

    summary = apply_rules(connection, FAMILY)
    connection.commit()

    assert summary.filled == 2
    hybrid = _car(connection, cars["hybrid"])
    assert hybrid.values["electrification_type"] == "hybrid"
    assert hybrid.values["fuel_secondary"] == "electricity"
    assert hybrid.values["fuel_match_tokens"] == ["diesel", "electricity", "hybrid_diesel"]
    for name in ("electrification_type", "fuel_secondary", "fuel_match_tokens"):
        assert hybrid.field_sources[name].startswith("rule:ELT-GC-")
    # The tokens AIS gave stay behind the rule's.
    assert hybrid.field_alternatives["fuel_match_tokens"] == [
        {"source": "ais@2026-09-19", "value": ["diesel"]}
    ]

    plug_in = _car(connection, cars["plug_in"])
    assert plug_in.values["electrification_type"] == "plug_in_hybrid"
    # Its second fuel and tokens were AIS's own and stay so.
    assert "fuel_secondary" not in plug_in.field_sources
    assert "fuel_match_tokens" not in plug_in.field_sources


def test_the_rule_fills_only_where_the_registry_is_silent(
    db: tuple[Connection, dict[str, str]],
) -> None:
    connection, cars = db

    # No rule for a group of plain cars.
    assert _car(connection, cars["plain"]).values.get("electrification_type") is None
    # A plug-in charges from the grid: a car whose fuels have no electricity is not one.
    unplugged = _car(connection, cars["plug_in_without_electricity"])
    assert unplugged.values.get("electrification_type") is None
    assert unplugged.values.get("fuel_secondary") is None
    # A registry car without a hybrid type is one the registry says is no hybrid.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT electrification_type, fuel_secondary FROM core.vehicles WHERE plate = 'HYB900'"
        )
        assert cursor.fetchone() == (None, None)


def test_applying_again_fills_nothing(db: tuple[Connection, dict[str, str]]) -> None:
    connection, _ = db

    assert apply_rules(connection, FAMILY).filled == 0


def test_retiring_the_rule_takes_back_the_type_and_what_it_implied(
    db: tuple[Connection, dict[str, str]],
) -> None:
    connection, cars = db
    rule_id = _car(connection, cars["hybrid"]).field_sources["electrification_type"].split(":", 1)[1]

    assert retire_rule(connection, rule_id) == 1
    connection.commit()

    hybrid = _car(connection, cars["hybrid"])
    assert hybrid.values.get("electrification_type") is None
    assert hybrid.values.get("fuel_secondary") is None
    assert hybrid.values["fuel_match_tokens"] == ["diesel"]
    assert not {"electrification_type", "fuel_secondary", "fuel_match_tokens"} & set(hybrid.field_sources)
    assert "fuel_match_tokens" not in hybrid.field_alternatives
