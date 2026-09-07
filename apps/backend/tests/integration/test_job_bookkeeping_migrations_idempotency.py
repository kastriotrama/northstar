from collections.abc import Iterator

import psycopg
import pytest
from psycopg import Connection

from ingestion.config import get_ingestion_settings
from ingestion.job_bookkeeping import (
    JobAlreadyRunningError,
    claim_job_run,
    complete_job_run,
    fail_job_run,
    fetch_job_run,
)
from ingestion.job_bookkeeping_migrations import (
    JOB_BOOKKEEPING_MIGRATION_STATEMENTS,
    JOB_RUNS_TABLE,
    JobBookkeepingSchemaContractError,
    run_job_bookkeeping_migrations,
    verify_job_bookkeeping_schema_contract,
)

TEST_JOB = "scrum19-integration-test"


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

    run_job_bookkeeping_migrations(connection)
    yield connection

    with connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {JOB_RUNS_TABLE} WHERE job_name = %s", (TEST_JOB,))
    connection.commit()
    connection.close()


def test_migrations_run_twice_and_apply_all_statements(
    pg_connection: Connection,
) -> None:
    first = run_job_bookkeeping_migrations(pg_connection)
    second = run_job_bookkeeping_migrations(pg_connection)

    assert first == second
    assert len(first) == len(JOB_BOOKKEEPING_MIGRATION_STATEMENTS)


def test_completed_batch_retry_is_a_no_op(pg_connection: Connection) -> None:
    claim = claim_job_run(
        pg_connection,
        job_name=TEST_JOB,
        batch_id="completed-batch",
    )
    pg_connection.commit()
    assert claim.should_execute is True

    completed = complete_job_run(
        pg_connection,
        claim.job_run.id,
        records_processed=10,
        records_succeeded=9,
        records_failed=1,
    )
    pg_connection.commit()

    retried = claim_job_run(
        pg_connection,
        job_name=TEST_JOB,
        batch_id="completed-batch",
    )
    pg_connection.commit()

    assert completed.finished_at is not None
    assert retried.should_execute is False
    assert retried.job_run.id == completed.id
    assert retried.job_run.records_processed == 10


def test_failed_batch_can_retry_but_running_batch_cannot(
    pg_connection: Connection,
) -> None:
    claim = claim_job_run(
        pg_connection,
        job_name=TEST_JOB,
        batch_id="retry-batch",
    )
    pg_connection.commit()

    with pytest.raises(JobAlreadyRunningError):
        claim_job_run(
            pg_connection,
            job_name=TEST_JOB,
            batch_id="retry-batch",
        )
    pg_connection.rollback()

    failed = fail_job_run(
        pg_connection,
        claim.job_run.id,
        records_processed=3,
        records_succeeded=2,
        records_failed=1,
        error_code="source_timeout",
        error_summary="Sanitized source timeout during integration test",
    )
    pg_connection.commit()
    retried = claim_job_run(
        pg_connection,
        job_name=TEST_JOB,
        batch_id="retry-batch",
    )
    pg_connection.commit()

    assert failed.status == "failed"
    assert failed.error_code == "source_timeout"
    assert retried.should_execute is True
    assert retried.job_run.status == "running"
    assert retried.job_run.records_processed == 0
    assert (
        fetch_job_run(
            pg_connection,
            job_name=TEST_JOB,
            batch_id="retry-batch",
        )
        == retried.job_run
    )


def test_schema_verifier_rejects_contract_drift(
    pg_connection: Connection,
) -> None:
    run_job_bookkeeping_migrations(pg_connection)
    with pg_connection.cursor() as cursor:
        cursor.execute("DROP INDEX core.ingest_job_runs_status_started_at_idx")

    with pytest.raises(JobBookkeepingSchemaContractError):
        verify_job_bookkeeping_schema_contract(pg_connection)
    pg_connection.rollback()
