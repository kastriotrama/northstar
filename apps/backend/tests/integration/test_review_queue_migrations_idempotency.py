import json
from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import Connection
from psycopg.errors import CheckViolation

from ingestion.config import get_ingestion_settings
from ingestion.review_queue import (
    CandidateMatch,
    enqueue_review_item,
    fetch_review_items_by_status,
    transition_review_item,
)
from ingestion.review_queue_migrations import (
    REVIEW_QUEUE_MIGRATION_STATEMENTS,
    REVIEW_QUEUE_TABLE,
    ReviewQueueSchemaContractError,
    run_review_queue_migrations,
    verify_review_queue_schema_contract,
)
from scripts.fit_margin_threshold import load_verdicts

TEST_SOURCE = "scrum18-integration-test"


@pytest.fixture(scope="module")
def pg_connection() -> Iterator[Connection]:
    settings = get_ingestion_settings()
    try:
        connection = psycopg.connect(settings.database_url)
    except psycopg.OperationalError:
        if settings.environment == "test":
            raise
        pytest.skip("PostgreSQL is unavailable; start it with docker compose up -d postgres")
        return

    run_review_queue_migrations(connection)
    yield connection

    with connection.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM {REVIEW_QUEUE_TABLE} WHERE source_system = %s",
            (TEST_SOURCE,),
        )
    connection.commit()
    connection.close()


def test_migrations_run_twice_and_apply_all_statements(
    pg_connection: Connection,
) -> None:
    first = run_review_queue_migrations(pg_connection)
    second = run_review_queue_migrations(pg_connection)

    assert first == second
    assert len(first) == len(REVIEW_QUEUE_MIGRATION_STATEMENTS)


def test_calibration_fitter_reads_only_version_matched_review_evidence(
    pg_connection: Connection,
) -> None:
    batch = f"margin-calibration-integration-{uuid4()}"
    pins = {"source_version": "source-v1", "normalization_rule_version": "rules-v1",
            "candidate_catalog_version": "catalog-v1", "seed": 7}
    item_id = enqueue_review_item(
        pg_connection,
        review_id=uuid4(),
        source_system=TEST_SOURCE,
        source_batch_id=batch,
        source_table="staging.transportstyrelsen_raw",
        source_record_id=42,
        reason_code="match_margin_calibration",
        reason_detail=json.dumps({"pins": pins, "separation_margin": 0.4,
                                  "band": "0.40-1.00"}),
        target_entity_type="vehicle",
        confidence=0.95,
    )
    transition_review_item(
        pg_connection, item_id, "resolved", resolved_by="synthetic-test-reviewer",
        resolution={"verdict": "accept", "reason": "Synthetic integration fixture"},
    )
    pg_connection.commit()
    assert load_verdicts(pg_connection, batch_label=batch, expected_pins=pins) == (
        [(0.4, "0.40-1.00", "accept")], 0
    )
    with pytest.raises(ValueError, match="pins"):
        load_verdicts(
            pg_connection, batch_label=batch,
            expected_pins={**pins, "candidate_catalog_version": "catalog-v2"},
        )


def test_queue_round_trip_status_worklist_and_resolution(
    pg_connection: Connection,
) -> None:
    review_id = uuid4()
    item_id = enqueue_review_item(
        pg_connection,
        review_id=review_id,
        source_system=f"  {TEST_SOURCE}  ",
        source_batch_id="ts-sanitized-batch",
        source_table="staging.transportstyrelsen_raw",
        source_record_id=42,
        reason_code="manufacturer_role_unknown",
        reason_detail="Finished-vehicle and base-vehicle roles need classification.",
        target_entity_type="Manufacturer",
        confidence=0.62,
        candidate_matches=(
            CandidateMatch(
                candidate_reference="manufacturer:marketed-brand",
                candidate_type="Manufacturer",
                confidence=0.62,
                evidence={"matched_fields": ["brand"]},
            ),
            CandidateMatch(
                candidate_reference="manufacturer:base-vehicle",
                candidate_type="Manufacturer",
                confidence=0.58,
                evidence={"matched_fields": ["base_vehicle_manufacturer"]},
            ),
        ),
    )
    pg_connection.commit()

    pending = fetch_review_items_by_status(
        pg_connection,
        "pending",
        limit=1000,
        source_system=TEST_SOURCE,
    )
    item = next(row for row in pending if row.id == item_id)
    assert item.review_id == review_id
    assert item.source_system == TEST_SOURCE
    assert item.status == "pending"
    assert len(item.candidate_matches) == 2

    in_review = transition_review_item(pg_connection, item_id, "in_review")
    resolved = transition_review_item(
        pg_connection,
        item_id,
        "resolved",
        resolved_by="stakeholder-review",
        resolution={
            "decision": "classify organization before selecting manufacturer",
            "reprocess": True,
        },
    )
    pg_connection.commit()

    assert in_review.status == "in_review"
    assert resolved.status == "resolved"
    assert resolved.resolved_at is not None
    assert resolved.resolved_by == "stakeholder-review"
    assert any(
        row.id == item_id
        for row in fetch_review_items_by_status(
            pg_connection,
            "resolved",
            source_system=TEST_SOURCE,
        )
    )


def test_enqueue_retry_is_idempotent_and_payload_safe(
    pg_connection: Connection,
) -> None:
    review_id = uuid4()
    kwargs = {
        "review_id": review_id,
        "source_system": TEST_SOURCE,
        "source_table": "staging.tecdoc_vehicle_type",
        "source_record_id": 84,
        "reason_code": "ambiguous_vehicle_variant",
        "confidence": 0.55,
    }
    first_id = enqueue_review_item(pg_connection, **kwargs)
    pg_connection.commit()
    retried_id = enqueue_review_item(pg_connection, **kwargs)
    pg_connection.commit()

    assert retried_id == first_id

    with pytest.raises(ValueError, match="different item"):
        enqueue_review_item(
            pg_connection,
            **{**kwargs, "confidence": 0.75},
        )
    pg_connection.rollback()


def test_database_rejects_invalid_status_and_candidate_shape(
    pg_connection: Connection,
) -> None:
    with pytest.raises(CheckViolation), pg_connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {REVIEW_QUEUE_TABLE} "
            "(review_id, source_system, source_table, source_record_id, "
            "reason_code, status) VALUES (%s, %s, %s, %s, %s, %s)",
            (
                uuid4(),
                TEST_SOURCE,
                "staging.transportstyrelsen_raw",
                101,
                "test_invalid_status",
                "closed",
            ),
        )
    pg_connection.rollback()

    with pytest.raises(CheckViolation), pg_connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {REVIEW_QUEUE_TABLE} "
            "(review_id, source_system, source_table, source_record_id, "
            "reason_code, candidate_matches) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
            (
                uuid4(),
                TEST_SOURCE,
                "staging.transportstyrelsen_raw",
                102,
                "test_invalid_candidate_shape",
                "{}",
            ),
        )
    pg_connection.rollback()

    with pytest.raises(CheckViolation), pg_connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {REVIEW_QUEUE_TABLE} "
            "(review_id, source_system, source_table, source_record_id, "
            "reason_code, resolution) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
            (
                uuid4(),
                TEST_SOURCE,
                "staging.transportstyrelsen_raw",
                103,
                "test_invalid_resolution_shape",
                "[]",
            ),
        )
    pg_connection.rollback()


@pytest.mark.parametrize(
    "drift_sql",
    [
        "ALTER TABLE core.review_queue ALTER COLUMN status DROP DEFAULT",
        "ALTER TABLE core.review_queue DROP CONSTRAINT review_queue_status_values",
        "DROP INDEX core.review_queue_status_created_at_idx",
        "DROP INDEX core.review_queue_source_batch_status_idx",
    ],
)
def test_schema_verifier_rejects_contract_drift(
    pg_connection: Connection,
    drift_sql: str,
) -> None:
    run_review_queue_migrations(pg_connection)
    with pg_connection.cursor() as cursor:
        cursor.execute(drift_sql)

    with pytest.raises(ReviewQueueSchemaContractError):
        verify_review_queue_schema_contract(pg_connection)
    pg_connection.rollback()
