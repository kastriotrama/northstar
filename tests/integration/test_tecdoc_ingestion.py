from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import Connection

from ingestion.config import get_ingestion_settings
from ingestion.ledger_migrations import run_ledger_migrations
from ingestion.tecdoc.migrations import run_tecdoc_migrations
from ingestion.tecdoc.models import TecDocVehicleRow
from ingestion.tecdoc.service import ingest_tecdoc_vehicle_tree


@pytest.fixture(scope="module")
def pg_connection() -> Iterator[Connection]:
    try:
        connection = psycopg.connect(get_ingestion_settings().database_url)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is unavailable; start it with docker compose up -d postgres")
    yield connection
    connection.close()


def test_tecdoc_batch_is_traceable_and_repeatable(pg_connection: Connection) -> None:
    run_ledger_migrations(pg_connection)
    run_tecdoc_migrations(pg_connection)
    batch_id = f"tecdoc-integration-{uuid4()}"
    row = TecDocVehicleRow(
        ktype_id="12345",
        manufacturer_id="5",
        manufacturer_name="Volvo",
        model_id="50",
        model_name="XC60",
        variant_id=f"variant-{uuid4()}",
        variant_name="D4 AWD",
        year_from=2018,
        source_row_refs=("120:12345", "100:5", "110:50"),
    )
    arguments = {
        "rows": (row,),
        "batch_id": batch_id,
        "source_version": "0326",
        "format_version": "2.70",
        "license_reference": None,
        "source_path": "/licensed/REFERENCE_DATA_0326",
        "source_checksum": "a" * 64,
    }

    first = ingest_tecdoc_vehicle_tree(pg_connection, **arguments)  # type: ignore[arg-type]
    second = ingest_tecdoc_vehicle_tree(pg_connection, **arguments)  # type: ignore[arg-type]

    assert first.source_rows == first.unique_ktypes == 1
    assert first.candidates_written == 4
    assert first.ledger_entries_written == 4
    assert second.candidates_written == 0
    assert second.ledger_entries_written == 4
    with pg_connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, source_version, source_row_count, license_reference "
            "FROM core.tecdoc_source_batches "
            "WHERE batch_id=%s",
            (batch_id,),
        )
        assert cursor.fetchone() == ("completed", "0326", 1, "not_provided")
        cursor.execute(
            "SELECT source_row_refs FROM core.tecdoc_canonical_candidates "
            "WHERE batch_id=%s AND entity_type='alias'",
            (batch_id,),
        )
        assert cursor.fetchone() == (["100:5", "110:50", "120:12345"],)
