from collections.abc import Iterator

import psycopg
import pytest
from psycopg import Connection

from ingestion.config import get_ingestion_settings
from ingestion.staging_loaders import copy_raw_records, count_batch_rows
from ingestion.staging_migrations import (
    STAGING_MIGRATION_STATEMENTS,
    fetch_staging_schema_names,
    fetch_staging_table_names,
    run_staging_migrations,
)


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

    yield connection
    connection.close()


def test_staging_migrations_run_twice_and_create_all_named_objects(
    pg_connection: Connection,
) -> None:
    first_applied = run_staging_migrations(pg_connection)
    second_applied = run_staging_migrations(pg_connection)

    assert first_applied == second_applied
    assert len(first_applied) == len(STAGING_MIGRATION_STATEMENTS)

    assert fetch_staging_schema_names(pg_connection) == {"staging"}

    table_names = fetch_staging_table_names(pg_connection)
    for statement in STAGING_MIGRATION_STATEMENTS:
        if statement.qualified_table is not None:
            _, table_name = statement.qualified_table.split(".", 1)
            assert table_name in table_names


def test_copy_raw_records_loads_transportstyrelsen_raw_via_copy(
    pg_connection: Connection,
) -> None:
    run_staging_migrations(pg_connection)
    batch_id = "scrum16-test-transportstyrelsen"
    records = [
        {"plate": "ABC123", "model": "E350"},
        {"plate": "XYZ789", "model": "XC90"},
    ]
    cleanup_sql = "DELETE FROM staging.transportstyrelsen_raw WHERE source_batch_id = %s"

    with pg_connection.cursor() as cursor:
        cursor.execute(cleanup_sql, (batch_id,))
    pg_connection.commit()

    try:
        written = copy_raw_records(
            pg_connection,
            table="staging.transportstyrelsen_raw",
            source_batch_id=batch_id,
            records=records,
        )
        landed = count_batch_rows(
            pg_connection,
            table="staging.transportstyrelsen_raw",
            source_batch_id=batch_id,
        )
        # The documented three-way row-count validation: source == written == landed.
        assert len(records) == written == landed == 2

        with pg_connection.cursor() as cursor:
            cursor.execute(
                "SELECT raw_record, source_batch_id, ingested_at "
                "FROM staging.transportstyrelsen_raw "
                "WHERE source_batch_id = %s ORDER BY id",
                (batch_id,),
            )
            rows = cursor.fetchall()

        assert [row[0] for row in rows] == records
        assert all(row[1] == batch_id for row in rows)
        assert all(row[2] is not None for row in rows)
    finally:
        with pg_connection.cursor() as cursor:
            cursor.execute(cleanup_sql, (batch_id,))
        pg_connection.commit()


def test_copy_raw_records_loads_tecdoc_manufacturer_via_copy(
    pg_connection: Connection,
) -> None:
    run_staging_migrations(pg_connection)
    batch_id = "scrum16-test-tecdoc-manufacturer"
    records = [{"code": "MB", "name": "Mercedes-Benz"}]
    cleanup_sql = "DELETE FROM staging.tecdoc_manufacturer WHERE source_batch_id = %s"

    with pg_connection.cursor() as cursor:
        cursor.execute(cleanup_sql, (batch_id,))
    pg_connection.commit()

    try:
        written = copy_raw_records(
            pg_connection,
            table="staging.tecdoc_manufacturer",
            source_batch_id=batch_id,
            records=records,
        )
        assert written == 1

        with pg_connection.cursor() as cursor:
            cursor.execute(
                "SELECT raw_record FROM staging.tecdoc_manufacturer "
                "WHERE source_batch_id = %s",
                (batch_id,),
            )
            rows = cursor.fetchall()

        assert [row[0] for row in rows] == records
    finally:
        with pg_connection.cursor() as cursor:
            cursor.execute(cleanup_sql, (batch_id,))
        pg_connection.commit()
