from collections.abc import Iterator
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import Connection

from ingestion.config import get_ingestion_settings
from ingestion.match_run_migrations import (
    MATCH_RUN_CHECKPOINTS_TABLE,
    MATCH_RUN_REASON_COUNTS_TABLE,
    MATCH_RUNS_TABLE,
    run_match_run_migrations,
)
from ingestion.match_run_repository import increment_match_run_reason_counts


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
    run_match_run_migrations(connection)
    yield connection
    connection.close()


def _insert_run(connection: Connection, operation_id: UUID) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {MATCH_RUNS_TABLE} ("
            "operation_id, source_system, source_version, source_batch_prefix, "
            "expected_source_rows, normalization_rule_version, "
            "candidate_catalog_version, policy_version, code_revision, mode) "
            "VALUES (%s, 'Transportstyrelsen', 'ts-2026-08', 'passenger-part-', "
            "6515471, 'ts-review-20260817T073842135705Z', 'tecdoc-0326', "
            "'confidence-routing-v1', 'abc123', 'dry_run')",
            (operation_id,),
        )
    connection.commit()


def test_match_run_migration_is_idempotent_and_enforces_accounting(
    pg_connection: Connection,
) -> None:
    operation_id = uuid4()
    try:
        assert run_match_run_migrations(pg_connection) == run_match_run_migrations(pg_connection)
        _insert_run(pg_connection, operation_id)

        with pytest.raises(psycopg.errors.CheckViolation), pg_connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {MATCH_RUNS_TABLE} SET processed = 1 WHERE operation_id = %s",
                (operation_id,),
            )
        pg_connection.rollback()

        with pg_connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {MATCH_RUN_CHECKPOINTS_TABLE} "
                "(operation_id, batch_number, last_source_record_id, last_source_cursor, "
                "processed, counters) VALUES "
                "(%s, 1, 25000, 'ABC123', 25000, '{\"resolved\": 25000}'::jsonb)",
                (operation_id,),
            )
        pg_connection.commit()

        with pytest.raises(psycopg.errors.UniqueViolation), pg_connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {MATCH_RUN_CHECKPOINTS_TABLE} "
                "(operation_id, batch_number, last_source_record_id, last_source_cursor, "
                "processed, counters) VALUES (%s, 2, 25000, 'ABC124', 25000, '{}'::jsonb)",
                (operation_id,),
            )
        pg_connection.rollback()

        increment_match_run_reason_counts(
            pg_connection,
            operation_id=operation_id,
            reason_counts={"model_evidence_missing": 2},
        )
        increment_match_run_reason_counts(
            pg_connection,
            operation_id=operation_id,
            reason_counts={"model_evidence_missing": 3},
        )
        pg_connection.commit()
        with pg_connection.cursor() as cursor:
            cursor.execute(
                f"SELECT occurrence_count FROM {MATCH_RUN_REASON_COUNTS_TABLE} "
                "WHERE operation_id=%s AND reason_code='model_evidence_missing'",
                (operation_id,),
            )
            assert cursor.fetchone() == (5,)
    finally:
        pg_connection.rollback()
        with pg_connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {MATCH_RUN_REASON_COUNTS_TABLE} WHERE operation_id = %s",
                (operation_id,),
            )
            cursor.execute(
                f"DELETE FROM {MATCH_RUN_CHECKPOINTS_TABLE} WHERE operation_id = %s",
                (operation_id,),
            )
            cursor.execute(
                f"DELETE FROM {MATCH_RUNS_TABLE} WHERE operation_id = %s",
                (operation_id,),
            )
        pg_connection.commit()
