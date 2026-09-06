"""Verify vocabulary invariants in isolated Compose PostgreSQL, not the source DB."""

from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import Connection

from ingestion.config import get_ingestion_settings
from ingestion.vocabulary_alignment import load_fuel_alignment
from ingestion.vocabulary_migrations import run_vocabulary_migrations
from ingestion.vocabulary_seed import INITIAL_FUEL_ALIGNMENT_VERSION, apply_vocabulary_seed


@pytest.fixture()
def connection() -> Iterator[Connection]:
    settings = get_ingestion_settings()
    if settings.environment != "test":
        pytest.skip("requires explicitly isolated test environment")
    with psycopg.connect(settings.database_url) as connection:
        run_vocabulary_migrations(connection)
        yield connection
        connection.rollback()


def test_seed_is_idempotent_and_activated_rules_are_loaded(connection: Connection) -> None:
    apply_vocabulary_seed(connection, activated_by="integration-test")
    repeated = apply_vocabulary_seed(connection, activated_by="integration-test")
    assert repeated["rows_inserted"] == 0
    alignment = load_fuel_alignment(connection, alignment_version=INITIAL_FUEL_ALIGNMENT_VERSION)
    assert alignment is not None
    assert alignment.ts_equivalences == {"electricity": "electric", "methane": "cng"}
    assert alignment.tecdoc_equivalences == {}
    assert alignment.compatible_pairs == frozenset({("ethanol", "petrol")})
    run_vocabulary_migrations(connection)
    assert load_fuel_alignment(connection, alignment_version=INITIAL_FUEL_ALIGNMENT_VERSION) == alignment


@pytest.mark.parametrize("statement", [
    "UPDATE core.vocabulary_alignments SET evidence_note = 'changed'",
    "DELETE FROM core.vocabulary_alignments",
    "TRUNCATE core.vocabulary_alignments",
    "TRUNCATE core.vocabulary_alignment_versions CASCADE",
    "UPDATE core.vocabulary_alignment_versions SET sealed = FALSE",
    "DELETE FROM core.vocabulary_alignment_versions",
    ("INSERT INTO core.vocabulary_alignments "
    "(alignment_version,vocabulary,source_system,source_term,canonical_term,relation) "
    "VALUES ('align-2026-08-28-v1','fuel','transportstyrelsen','extra','petrol','equivalent')"),
])
def test_activated_version_rejects_mutation(connection: Connection, statement: str) -> None:
    apply_vocabulary_seed(connection, activated_by="integration-test")
    with pytest.raises(psycopg.errors.RaiseException), connection.transaction():
        connection.execute(statement)


def test_unknown_and_unsealed_versions_are_rejected(connection: Connection) -> None:
    with pytest.raises(ValueError, match="unknown or not activated"):
        load_fuel_alignment(connection, alignment_version="missing-version")
    version = f"test-unsealed-{uuid4()}"
    connection.execute(
        "INSERT INTO core.vocabulary_alignment_versions "
        "(alignment_version, activation_note, activated_by, sealed) VALUES (%s,'test','test',FALSE)",
        (version,),
    )
    with pytest.raises(ValueError, match="unknown or not activated"):
        load_fuel_alignment(connection, alignment_version=version)


def test_legacy_pin_performs_no_queries() -> None:
    from unittest.mock import MagicMock
    connection = MagicMock()
    assert load_fuel_alignment(connection, alignment_version="unpinned-legacy") is None
    connection.cursor.assert_not_called()
