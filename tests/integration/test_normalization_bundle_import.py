from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import Connection

from ingestion.config import get_ingestion_settings
from ingestion.normalization_bundle import import_normalization_bundle

FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "normalization_bundle_minimal.xlsx"
)


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
    yield connection
    connection.close()


def test_bundle_import_populates_normalizes_verifies_and_retries(
    pg_connection: Connection,
    current_normalization_bundle: Path,
) -> None:
    first = import_normalization_bundle(pg_connection, current_normalization_bundle)
    retry = import_normalization_bundle(pg_connection, current_normalization_bundle)

    assert first.source_batch_id == "normalization-bundle-fixture-v1"
    assert first.rule_version == "ts-review-fixture-v1"
    assert first.raw_records == 1
    assert first.normalized_results == 1
    assert first.verified is True
    assert retry == first
