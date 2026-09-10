"""Verify vocabulary rulings in isolated Compose PostgreSQL, not the source DB."""

from collections.abc import Iterator

import psycopg
import pytest
from psycopg import Connection

from ingestion.config import get_ingestion_settings
from ingestion.tecdoc.resolution_migrations import run_tecdoc_resolution_migrations
from ingestion.vocabulary_alignment import load_fuel_alignment
from scripts.port_vocabulary_alignment_seed import port_seed


@pytest.fixture()
def connection() -> Iterator[Connection]:
    settings = get_ingestion_settings()
    if settings.environment != "test":
        pytest.skip("requires explicitly isolated test environment")
    with psycopg.connect(settings.database_url) as connection:
        run_tecdoc_resolution_migrations(connection)
        yield connection
        connection.rollback()


def test_seed_port_is_idempotent_and_live_rules_are_loaded(connection: Connection) -> None:
    port_seed(connection, reviewed_by="integration-test")
    repeated = port_seed(connection, reviewed_by="integration-test")
    # ON CONFLICT DO UPDATE always reports a row touched, so re-running still
    # reports the same counts rather than zero -- idempotent means "same end
    # state", not "no-op the second time".
    assert repeated == {"equivalent": 2, "compatible": 3}

    alignment = load_fuel_alignment(connection)
    assert alignment.ts_equivalences == {"electricity": "electric", "methane": "cng"}
    assert alignment.tecdoc_equivalences == {}
    assert alignment.compatible_pairs == frozenset({("ethanol", "petrol")})


def test_a_reviewer_correction_takes_effect_immediately(connection: Connection) -> None:
    """Unlike the retired vocabulary_alignments, this table is live and mutable."""

    port_seed(connection, reviewed_by="integration-test")
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE core.tecdoc_resolution_rules SET canonical_value = 'ev' "
            "WHERE canonical_field = 'fuel' AND source_system = 'transportstyrelsen' "
            "AND comparison_key = 'ELECTRICITY'"
        )
    connection.commit()

    alignment = load_fuel_alignment(connection)
    assert alignment.ts_equivalences["electricity"] == "ev"


def test_no_rows_for_a_vocabulary_is_a_harmless_empty_alignment(connection: Connection) -> None:
    alignment = load_fuel_alignment(connection)
    assert alignment.ts_equivalences == {}
    assert alignment.tecdoc_equivalences == {}
    assert alignment.compatible_pairs == frozenset()
