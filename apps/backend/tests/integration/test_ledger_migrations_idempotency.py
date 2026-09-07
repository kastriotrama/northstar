from collections.abc import Iterator
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from psycopg import Connection
from psycopg.errors import ForeignKeyViolation, RaiseException, UniqueViolation

from ingestion.config import get_ingestion_settings
from ingestion.ledger import fetch_entries_for_node, record_ledger_entry
from ingestion.ledger_migrations import (
    LEDGER_MIGRATION_STATEMENTS,
    LEDGER_TABLE,
    LedgerSchemaContractError,
    run_ledger_migrations,
    verify_ledger_schema_contract,
)
from northstar.node_ids import mint_node_id

TEST_SOURCE = "scrum17-integration-test"


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

    # No teardown deletes: the ledger is append-only by design, so test rows
    # (marked by TEST_SOURCE) remain. Node ids are freshly minted per run,
    # so accumulated rows never interfere with later assertions.
    connection.close()


def test_ledger_migrations_run_twice_and_apply_all_statements(
    pg_connection: Connection,
) -> None:
    first_applied = run_ledger_migrations(pg_connection)
    second_applied = run_ledger_migrations(pg_connection)

    assert first_applied == second_applied
    assert len(first_applied) == len(LEDGER_MIGRATION_STATEMENTS)


def test_ledger_round_trip_and_provenance_query(pg_connection: Connection) -> None:
    run_ledger_migrations(pg_connection)
    node_id = mint_node_id("VEH")

    # The writer does not commit; the caller owns the transaction. Both
    # entries commit together, as one logical operation would.
    first_id = record_ledger_entry(
        pg_connection,
        event_id=uuid4(),
        source=f"  {TEST_SOURCE}  ",
        target_node_id=node_id,
        confidence=1.0,
        attributes_added=["engine_code", "fuel_type"],
        source_batch_id="scrum17-batch-1",
    )
    correction_id = record_ledger_entry(
        pg_connection,
        event_id=uuid4(),
        source=TEST_SOURCE,
        target_node_id=node_id,
        confidence=0.9,
        attributes_added=["engine_code"],
        evidence={"note": "corrected engine_code after review"},
        corrects_ledger_id=first_id,
    )
    pg_connection.commit()

    entries = fetch_entries_for_node(pg_connection, node_id)

    assert [entry.id for entry in entries] == [first_id, correction_id]
    assert entries[0].attributes_added == ("engine_code", "fuel_type")
    assert entries[0].source == TEST_SOURCE
    assert entries[0].cost_eur == Decimal(0)
    assert entries[1].corrects_ledger_id == first_id
    assert entries[1].evidence == {"note": "corrected engine_code after review"}
    assert all(entry.created_at is not None for entry in entries)


def test_ledger_rejects_update_and_delete_at_database_level(
    pg_connection: Connection,
) -> None:
    run_ledger_migrations(pg_connection)
    node_id = mint_node_id("ENG")
    entry_id = record_ledger_entry(
        pg_connection,
        event_id=uuid4(),
        source=TEST_SOURCE,
        target_node_id=node_id,
        confidence=1.0,
    )
    pg_connection.commit()

    with pytest.raises(RaiseException, match="append-only"), pg_connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {LEDGER_TABLE} SET confidence = 0.5 WHERE id = %s",
            (entry_id,),
        )
    pg_connection.rollback()

    with pytest.raises(RaiseException, match="append-only"), pg_connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {LEDGER_TABLE} WHERE id = %s", (entry_id,))
    pg_connection.rollback()

    with pytest.raises(RaiseException, match="append-only"), pg_connection.cursor() as cursor:
        cursor.execute(f"TRUNCATE {LEDGER_TABLE}")
    pg_connection.rollback()

    entries = fetch_entries_for_node(pg_connection, node_id)
    assert [entry.id for entry in entries] == [entry_id]
    assert entries[0].confidence == 1.0


def test_ledger_retry_with_same_event_id_is_idempotent(
    pg_connection: Connection,
) -> None:
    run_ledger_migrations(pg_connection)
    node_id = mint_node_id("VEH")
    event_id = uuid4()

    first_id = record_ledger_entry(
        pg_connection,
        event_id=event_id,
        source=TEST_SOURCE,
        target_node_id=node_id,
        confidence=1.0,
        attributes_added=["engine_code"],
    )
    pg_connection.commit()
    retried_id = record_ledger_entry(
        pg_connection,
        event_id=event_id,
        source=TEST_SOURCE,
        target_node_id=node_id,
        confidence=1.0,
        attributes_added=["engine_code"],
    )
    pg_connection.commit()

    assert retried_id == first_id
    assert [entry.id for entry in fetch_entries_for_node(pg_connection, node_id)] == [first_id]

    with pytest.raises(ValueError, match="different ledger event"):
        record_ledger_entry(
            pg_connection,
            event_id=event_id,
            source=TEST_SOURCE,
            target_node_id=node_id,
            confidence=0.5,
            attributes_added=["engine_code"],
        )
    pg_connection.rollback()


def test_correction_must_target_the_same_node(pg_connection: Connection) -> None:
    run_ledger_migrations(pg_connection)
    original_node_id = mint_node_id("ENG")
    other_node_id = mint_node_id("ENG")
    original_id = record_ledger_entry(
        pg_connection,
        event_id=uuid4(),
        source=TEST_SOURCE,
        target_node_id=original_node_id,
        confidence=1.0,
    )
    pg_connection.commit()

    with pytest.raises(ForeignKeyViolation):
        record_ledger_entry(
            pg_connection,
            event_id=uuid4(),
            source=TEST_SOURCE,
            target_node_id=other_node_id,
            confidence=0.9,
            corrects_ledger_id=original_id,
        )
    pg_connection.rollback()


def test_correction_chain_does_not_branch(pg_connection: Connection) -> None:
    run_ledger_migrations(pg_connection)
    node_id = mint_node_id("ENG")
    original_id = record_ledger_entry(
        pg_connection,
        event_id=uuid4(),
        source=TEST_SOURCE,
        target_node_id=node_id,
        confidence=1.0,
    )
    record_ledger_entry(
        pg_connection,
        event_id=uuid4(),
        source=TEST_SOURCE,
        target_node_id=node_id,
        confidence=0.9,
        corrects_ledger_id=original_id,
    )
    pg_connection.commit()

    with pytest.raises(UniqueViolation):
        record_ledger_entry(
            pg_connection,
            event_id=uuid4(),
            source=TEST_SOURCE,
            target_node_id=node_id,
            confidence=0.8,
            corrects_ledger_id=original_id,
        )
    pg_connection.rollback()


@pytest.mark.parametrize(
    "drift_sql",
    [
        "ALTER TABLE core.enrichment_ledger ALTER COLUMN cost_eur DROP DEFAULT",
        "ALTER TABLE core.enrichment_ledger DROP CONSTRAINT enrichment_ledger_correction_target_fk",
        "DROP INDEX core.enrichment_ledger_created_at_idx",
        "ALTER TABLE core.enrichment_ledger DISABLE TRIGGER enrichment_ledger_append_only",
    ],
)
def test_schema_verifier_rejects_contract_drift(
    pg_connection: Connection,
    drift_sql: str,
) -> None:
    run_ledger_migrations(pg_connection)
    with pg_connection.cursor() as cursor:
        cursor.execute(drift_sql)

    with pytest.raises(LedgerSchemaContractError):
        verify_ledger_schema_contract(pg_connection)
    pg_connection.rollback()
