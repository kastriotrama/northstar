"""A reviewer's rule reaches the NorthStar vehicle in the same transaction, and its retirement restores it."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.vehicle_core_review import sync_applied_review, sync_retired_review
from ingestion.vehicle_core_store import load_vehicle
from ingestion.vehicle_core_ts import backfill_vehicle_core
from ingestion.vehicle_facts_query import compile_predicate
from ingestion.vehicle_facts_rules import apply_rule, retire_rule
from tests.integration.throwaway_database import throwaway_database
from tests.integration.vehicle_core_fixtures import (
    insert_ts_record,
    prepare_schema,
    project,
    volvo,
)


@pytest.fixture(scope="module")
def db() -> Iterator[Connection]:
    with throwaway_database("vehicle_core_review") as connection:
        prepare_schema(connection)
        insert_ts_record(connection, volvo(plate="XC4001", model="XC40", variant="XZ", version="XZ1"))
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        yield connection


def _rule(connection: Connection, target_field: str, target_value: str) -> tuple:
    build_id, rule_id = uuid4(), uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO core.match_chunk_builds (build_id, source_batch_id, signature_version, "
            "status_filter, status, finished_at) VALUES (%s, 'test', 'v1', ARRAY['provisional'], "
            "'completed', now())",
            (build_id,),
        )
        cursor.execute(
            "INSERT INTO core.match_resolution_rules (rule_id, build_id, source_field, source_value, "
            "target_field, target_value, conditions, author, matched_rows, would_resolve, "
            "already_resolved, override) VALUES (%s, %s, 'model', 'XC40', %s, %s, %s, 'test', 1, 1, 0, "
            "true)",
            (rule_id, build_id, target_field, target_value,
             Jsonb([{"field": "model", "values": ["XC40"]}])),
        )
    connection.commit()
    return rule_id, build_id


def _vehicle(connection: Connection) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT vehicle_id FROM core.vehicles WHERE plate = 'XC4001'")
        return str(cursor.fetchone()[0])


def test_an_override_rule_reaches_the_vehicle_and_retiring_it_restores_the_value(
    db: Connection,
) -> None:
    vehicle_id = _vehicle(db)
    before = load_vehicle(db, vehicle_id)
    assert before is not None
    derived = before.values["bodywork_form"]
    assert derived != "suv"

    rule_id, build_id = _rule(db, "bodywork_form", "suv")
    predicate = compile_predicate([("source", "model", "equals", ("XC40",))])
    apply_rule(
        db, rule_id=rule_id, build_id=build_id, predicate=predicate,
        target_field="bodywork_form", target_value="suv", override=True,
        on_batch=sync_applied_review,
    )

    reviewed = load_vehicle(db, vehicle_id)
    assert reviewed is not None
    assert reviewed.values["bodywork_form"] == "suv"
    assert reviewed.field_sources["bodywork_form"] == f"review:{rule_id}"
    assert reviewed.field_alternatives["bodywork_form"][0]["value"] == derived

    retire_rule(db, rule_id=rule_id, target_field="bodywork_form", on_retire=sync_retired_review)

    restored = load_vehicle(db, vehicle_id)
    assert restored is not None
    assert restored.values["bodywork_form"] == derived
    assert "bodywork_form" not in restored.field_sources
    assert "bodywork_form" not in restored.field_alternatives


def test_a_ts_refresh_keeps_the_review(db: Connection) -> None:
    vehicle_id = _vehicle(db)
    rule_id, build_id = _rule(db, "drive_type", "awd")
    predicate = compile_predicate([("source", "model", "equals", ("XC40",))])
    apply_rule(
        db, rule_id=rule_id, build_id=build_id, predicate=predicate,
        target_field="drive_type", target_value="awd", override=True,
        on_batch=sync_applied_review,
    )

    backfill_vehicle_core(db, min_free_bytes=None)

    state = load_vehicle(db, vehicle_id)
    assert state is not None
    assert state.values["drive_type"] == "awd"
