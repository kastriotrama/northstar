"""Re-reading stopped records' tyre sizes: only they change, and the vehicle follows.

Runs against a throwaway database: see `throwaway_database`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.normalization_migrations import NORMALIZATION_RESULTS_TABLE
from ingestion.tyre_reparse import reparse_tyre_sizes
from ingestion.vehicle_core_ts import backfill_vehicle_core
from tests.integration.throwaway_database import throwaway_database
from tests.integration.vehicle_core_fixtures import insert_ts_record, prepare_schema, project, volvo

_READABLE = "225/45ZR17 91W"
_TYPO = "22545R17X"


def _as_an_older_parser_left_it(connection: Connection, record_id: int) -> None:
    """Rewrite the fixture's result the way pipeline v10 stored it: tyres rejected."""

    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {NORMALIZATION_RESULTS_TABLE} SET status = 'review_required', "
            "confidence = 0.55, pipeline_version = 'normalization-pipeline-v10', "
            "review_reasons = ARRAY['tyre_size_unrecognized'], "
            "normalized_payload = jsonb_set(normalized_payload #- '{normalized,tyre_front}' "
            "#- '{normalized,tyre_rear}' #- '{normalized,tyre_staggered}' "
            "#- '{normalized,rim_diameter_in}', '{candidates}', %s) "
            "WHERE source_record_id = %s",
            (Jsonb({"tyre_front": _READABLE, "tyre_rear": _READABLE}), record_id),
        )


@pytest.fixture()
def db() -> Iterator[tuple[Connection, dict[str, int]]]:
    with throwaway_database("tyre_reparse") as connection:
        prepare_schema(connection)
        ids = {
            "readable": insert_ts_record(connection, volvo(
                vin="YV1BW84S1F1000001", plate="AAA111", tyre_front=_READABLE, tyre_rear=_READABLE)),
            "typo": insert_ts_record(connection, volvo(
                vin="YV1BW84S1F1000002", plate="BBB222", tyre_front=_READABLE, tyre_rear=_TYPO)),
            "fine": insert_ts_record(connection, volvo(vin="YV1BW84S1F1000003", plate="CCC333")),
        }
        _as_an_older_parser_left_it(connection, ids["readable"])
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection)
        connection.commit()
        yield connection, ids


def _latest(connection: Connection, record_id: int) -> tuple[Any, ...]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT status, review_reasons, pipeline_version, normalized_payload, "
            f"(SELECT count(*) FROM {NORMALIZATION_RESULTS_TABLE} WHERE source_record_id = %s) "
            f"FROM {NORMALIZATION_RESULTS_TABLE} WHERE source_record_id = %s "
            "ORDER BY updated_at DESC, id DESC LIMIT 1",
            (record_id, record_id),
        )
        row = cursor.fetchone()
    assert row is not None
    return tuple(row)


def _vehicle_status(connection: Connection, record_id: int) -> tuple[Any, ...]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT v.normalization_status, v.manufacturer, v.model_family, v.power_kw, f.norm_status "
            "FROM core.vehicles v JOIN core.vehicle_facts f ON f.source_record_id = v.ts_record_id "
            "WHERE v.ts_record_id = %s",
            (record_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return tuple(row)


def test_a_dry_run_counts_and_writes_nothing(db: tuple[Connection, dict[str, int]]) -> None:
    connection, ids = db
    before = _latest(connection, ids["readable"])

    summary = reparse_tyre_sizes(connection)

    assert summary.to_json() == {
        "stopped": 2, "readable": 1, "written": 0, "still_unread": 1,
        "vehicles_refreshed": 0, "statuses": {"resolved": 1},
    }
    assert _latest(connection, ids["readable"]) == before


def test_only_the_tyre_part_of_a_readable_record_changes(db: tuple[Connection, dict[str, int]]) -> None:
    connection, ids = db
    stopped_vehicle = _vehicle_status(connection, ids["readable"])
    old_payload = _latest(connection, ids["readable"])[3]
    typo_before = _latest(connection, ids["typo"])
    fine_before = _latest(connection, ids["fine"])
    assert (stopped_vehicle[0], stopped_vehicle[4]) == ("review_required", "review_required")

    summary = reparse_tyre_sizes(connection, dry_run=False)

    assert (summary.readable, summary.written, summary.still_unread, summary.vehicles_refreshed) == (
        1, 1, 1, 1)
    status, reasons, pipeline, payload, rows = _latest(connection, ids["readable"])
    assert (status, list(reasons), rows) == ("resolved", [], 2)  # appended, the old row is kept
    assert pipeline.startswith("normalization-pipeline-v10+tyres-")
    assert payload["normalized"]["tyre_front"]["section_width_mm"] == 225
    # Every value the tyre step does not own is exactly what it was.
    tyre_keys = {"tyre_front", "tyre_rear", "tyre_staggered", "rim_diameter_in"}
    assert {k: v for k, v in payload["normalized"].items() if k not in tyre_keys} == {
        k: v for k, v in old_payload["normalized"].items() if k not in tyre_keys}
    assert payload["candidates"] == {}
    # The vehicle and the registry screen follow; the car's own values did not move.
    released = _vehicle_status(connection, ids["readable"])
    assert (released[0], released[4]) == ("resolved", "resolved")
    assert released[1:4] == stopped_vehicle[1:4]
    # A typo stays stopped, and a record that was never stopped is not touched.
    assert _latest(connection, ids["typo"]) == typo_before
    assert _latest(connection, ids["fine"]) == fine_before


def test_a_second_run_writes_nothing(db: tuple[Connection, dict[str, int]]) -> None:
    connection, ids = db
    reparse_tyre_sizes(connection, dry_run=False)
    again = reparse_tyre_sizes(connection, dry_run=False)

    # The readable record is no longer stopped; only the typo is still found.
    assert (again.stopped, again.readable, again.written) == (1, 0, 0)
    assert _latest(connection, ids["readable"])[4] == 2


def test_a_vehicle_whose_plate_moved_to_another_is_left_as_it_is(
    db: tuple[Connection, dict[str, int]],
) -> None:
    """The full register has cars whose plate a newer vehicle took over. Their record
    still names the plate; re-merging it would open the plate a second time."""

    connection, ids = db
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT vehicle_id FROM core.vehicles WHERE ts_record_id = %s", (ids["readable"],)
        )
        old = cursor.fetchone()[0]  # type: ignore[index]
        cursor.execute("SELECT vehicle_id FROM core.vehicles WHERE ts_record_id = %s", (ids["fine"],))
        new = cursor.fetchone()[0]  # type: ignore[index]
        # The plate leaves the old vehicle and the newer one carries it now.
        cursor.execute(
            "UPDATE core.vehicle_identifiers SET valid_to = DATE '2026-09-19' "
            "WHERE kind = 'plate' AND vehicle_id = ANY(%s)", ([old, new],),
        )
        cursor.execute(
            "UPDATE core.vehicles SET plate = NULL, registry_status = 'deregistered' "
            "WHERE vehicle_id = %s", (old,),
        )
        cursor.execute(
            "INSERT INTO core.vehicle_identifiers (vehicle_id, kind, value, source) "
            "VALUES (%s, 'plate', 'AAA111', 'ais')", (new,),
        )
    connection.commit()

    summary = reparse_tyre_sizes(connection, dry_run=False)

    # The record's result is rewritten; its vehicle is not touched.
    assert (summary.readable, summary.written, summary.vehicles_refreshed) == (1, 1, 0)
    assert _latest(connection, ids["readable"])[0] == "resolved"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT vehicle_id FROM core.vehicle_identifiers "
            "WHERE kind = 'plate' AND value = 'AAA111' AND valid_to IS NULL"
        )
        assert [row[0] for row in cursor.fetchall()] == [new]
        cursor.execute("SELECT plate, registry_status FROM core.vehicles WHERE vehicle_id = %s", (old,))
        assert cursor.fetchone() == (None, "deregistered")
    connection.commit()
