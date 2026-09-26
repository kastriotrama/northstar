"""Source data for vehicle-core integration tests, built the way production builds it.

A TS record is inserted into staging, normalized by the real pipeline, stored as a
normalization result, and projected into `vehicle_facts` by the real refresh --
so a test exercises the same rows the backfill reads on live.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.job_bookkeeping_migrations import run_job_bookkeeping_migrations
from ingestion.ledger_migrations import run_ledger_migrations
from ingestion.match_chunk_migrations import run_match_chunk_migrations
from ingestion.normalization_migrations import (
    NORMALIZATION_RESULTS_TABLE,
    run_normalization_migrations,
)
from ingestion.normalization_rules import PIPELINE_VERSION, normalize_ts_record
from ingestion.staging_migrations import run_staging_migrations
from ingestion.vehicle_core_migrations import run_vehicle_core_migrations
from ingestion.vehicle_facts import STAGING_TABLE, refresh_vehicle_facts
from ingestion.vehicle_facts_dedupe import dedupe_vehicle_facts
from ingestion.vehicle_facts_migrations import run_vehicle_facts_migrations


def prepare_schema(connection: Connection) -> None:
    run_staging_migrations(connection)
    run_normalization_migrations(connection)
    run_match_chunk_migrations(connection)
    run_vehicle_facts_migrations(connection)
    run_ledger_migrations(connection)
    run_job_bookkeeping_migrations(connection)
    run_vehicle_core_migrations(connection)
    connection.commit()


def volvo(**overrides: Any) -> dict[str, Any]:
    """A plausible TS row for a 2015 Volvo V70 diesel; override what a test needs."""

    record: dict[str, Any] = {
        "vin": "YV1BW84S1F1234567",
        "plate": "ABC123",
        "brand": "VOLVO",
        "model": "V70",
        "fab_code": "VO",
        "group_no": "101100",
        "type_text": "BW",
        "variant": "BW84",
        "version": "BW84S1F",
        "fuel1": "2",
        "fuel2": "0",
        "fuel3": "0",
        "gearbox": "A",
        "body_code": "AC",
        "is_4wd": "0",
        "kw": "133",
        "ccm": "1969",
        "vehicle_year": 2015,
        "registration_date": "20150312",
        "build_month": "201502",
        "eu_category": "M1",
        "vehicle_type": "PB",
        "color": "SVART",
        "passengers": "5",
        "tyre_front": "225/50 R17 98V",
        "tyre_rear": "225/50 R17 98V",
    }
    record.update(overrides)
    return {key: value for key, value in record.items() if value is not None}


def insert_ts_record(
    connection: Connection,
    raw: dict[str, Any],
    *,
    batch: str = "test-ts-batch",
    ingested_at: str | None = None,
) -> int:
    with connection.cursor() as cursor:
        if ingested_at:
            cursor.execute(
                f"INSERT INTO {STAGING_TABLE} (source_batch_id, raw_record, ingested_at) "
                "VALUES (%s, %s, %s) RETURNING id",
                (batch, Jsonb(raw), ingested_at),
            )
        else:
            cursor.execute(
                f"INSERT INTO {STAGING_TABLE} (source_batch_id, raw_record) VALUES (%s, %s) "
                "RETURNING id",
                (batch, Jsonb(raw)),
            )
        record_id = int(cursor.fetchone()[0])
        outcome = normalize_ts_record(raw)
        cursor.execute(
            f"INSERT INTO {NORMALIZATION_RESULTS_TABLE} (normalization_id, source_system, "
            "source_batch_id, source_table, source_record_id, mapping_version, rule_version, "
            "pipeline_version, status, normalized_payload, applied_rule_ids, review_reasons, "
            "confidence) VALUES (%s, 'transportstyrelsen', %s, %s, %s, 'test-mapping', "
            "'test-rules', %s, %s, %s, %s, %s, %s)",
            (
                uuid4(),
                batch,
                STAGING_TABLE,
                record_id,
                PIPELINE_VERSION,
                outcome.status,
                Jsonb({"normalized": outcome.normalized, "candidates": outcome.candidates}),
                list(outcome.applied_rule_ids),
                list(outcome.review_reasons),
                outcome.confidence,
            ),
        )
    return record_id


def project(connection: Connection, *, dedupe: bool = True) -> None:
    """Build `vehicle_facts` the way production does: refresh, then one row per plate."""

    refresh_vehicle_facts(connection, free_bytes=None)
    if dedupe:
        dedupe_vehicle_facts(connection)
    connection.commit()
