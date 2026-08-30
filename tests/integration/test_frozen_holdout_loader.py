"""Exercise the frozen-row loader against a real, read-only PostgreSQL connection."""

from collections.abc import Iterator

import psycopg
import pytest
from psycopg import Connection

from ingestion.config import get_ingestion_settings
from scripts.validate_frozen_matcher_holdout import _load_frozen_rows
from scripts.validate_local_matcher_cohort import digest


@pytest.fixture()
def connection() -> Iterator[Connection]:
    settings = get_ingestion_settings()
    try:
        connection = psycopg.connect(
            settings.database_url, options="-c default_transaction_read_only=on"
        )
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is unavailable; start it with docker compose up -d postgres")
    yield connection
    connection.close()


def test_loads_only_the_checksum_pinned_source_row(connection: Connection) -> None:
    row = connection.execute(
        "SELECT id, source_batch_id, raw_record "
        "FROM staging.transportstyrelsen_raw ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("local staging database has no TS source rows")
    source_id, source_batch_id, raw_record = int(row[0]), str(row[1]), dict(row[2])
    holdout = {
        "source_prefix": source_batch_id,
        "rows": [{
            "source_record_id": source_id,
            "row_key": digest([source_id, raw_record]),
        }],
    }

    assert _load_frozen_rows(connection, holdout) == [(source_id, raw_record)]


def test_rejects_a_divergent_source_checksum(connection: Connection) -> None:
    row = connection.execute(
        "SELECT id, source_batch_id FROM staging.transportstyrelsen_raw ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("local staging database has no TS source rows")
    holdout = {
        "source_prefix": str(row[1]),
        "rows": [{"source_record_id": int(row[0]), "row_key": "not-the-row-checksum"}],
    }

    with pytest.raises(ValueError, match="checksum differs"):
        _load_frozen_rows(connection, holdout)
