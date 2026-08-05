from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import Connection

from ingestion.config import get_ingestion_settings
from ingestion.job_bookkeeping_migrations import JOB_RUNS_TABLE, run_job_bookkeeping_migrations
from ingestion.normalization_migrations import (
    NORMALIZATION_RESULTS_TABLE,
    run_normalization_migrations,
)
from ingestion.normalization_repository import fetch_batch_results
from ingestion.normalization_service import normalize_batch
from ingestion.review_queue_migrations import REVIEW_QUEUE_TABLE, run_review_queue_migrations
from ingestion.staging_loaders import copy_raw_records
from ingestion.staging_migrations import run_staging_migrations


@pytest.fixture
def pg_connection() -> Iterator[Connection]:
    settings = get_ingestion_settings()
    try:
        connection = psycopg.connect(settings.database_url)
    except psycopg.OperationalError:
        if settings.environment == "test":
            raise
        pytest.skip("PostgreSQL is unavailable; start it with docker compose up -d postgres")
        return
    run_staging_migrations(connection)
    run_review_queue_migrations(connection)
    run_job_bookkeeping_migrations(connection)
    run_normalization_migrations(connection)
    yield connection
    connection.close()


def test_pipeline_persists_results_routes_review_and_retries_as_noop(
    pg_connection: Connection,
) -> None:
    batch_id = f"scrum82-test-{uuid4()}"
    try:
        copy_raw_records(
            pg_connection,
            table="staging.transportstyrelsen_raw",
            source_batch_id=batch_id,
            expected_source_count=2,
            records=[
                {
                    "manufacturer": "Volvo Car Corporation",
                    "model": "V60",
                    "eu_category": "M1",
                    "body_code": "AC",
                    "gearbox": "Z",
                },
                {
                    "manufacturer": "Unknown Builder",
                    "base_manufacturer": "Volvo",
                    "eu_category": "M1",
                    "body_code": "AC",
                },
            ],
        )
        summary = normalize_batch(pg_connection, batch_id=batch_id, page_size=1)
        retry = normalize_batch(pg_connection, batch_id=batch_id, page_size=1)
        results = fetch_batch_results(pg_connection, batch_id)

        assert summary.processed == 2
        assert summary.provisional == 1
        assert summary.review_required == 1
        assert retry.already_completed is True
        assert len(results) == 2
        assert "plate" not in str(results)
        with pg_connection.cursor() as cursor:
            cursor.execute(
                f"SELECT count(*) FROM {REVIEW_QUEUE_TABLE} WHERE source_batch_id = %s",
                (batch_id,),
            )
            assert cursor.fetchone() == (1,)
            cursor.execute(
                "SELECT count(*) FROM staging.transportstyrelsen_raw "
                "WHERE source_batch_id = %s",
                (batch_id,),
            )
            assert cursor.fetchone() == (2,)
            cursor.execute(
                f"SELECT DISTINCT pipeline_version FROM {NORMALIZATION_RESULTS_TABLE} "
                "WHERE source_batch_id = %s",
                (batch_id,),
            )
            assert cursor.fetchall() == [("normalization-pipeline-v2",)]
    finally:
        pg_connection.rollback()
        with pg_connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {REVIEW_QUEUE_TABLE} WHERE source_batch_id = %s",
                (batch_id,),
            )
            cursor.execute(
                f"DELETE FROM {NORMALIZATION_RESULTS_TABLE} WHERE source_batch_id = %s",
                (batch_id,),
            )
            cursor.execute(
                f"DELETE FROM {JOB_RUNS_TABLE} WHERE batch_id = %s",
                (batch_id,),
            )
            cursor.execute(
                "DELETE FROM staging.transportstyrelsen_raw WHERE source_batch_id = %s",
                (batch_id,),
            )
        pg_connection.commit()
