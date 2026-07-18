from collections.abc import Iterator
from decimal import Decimal

import psycopg
import pytest
from psycopg import Connection
from psycopg.errors import RaiseException

from ingestion.config import get_ingestion_settings
from ingestion.ledger import fetch_entries_for_node, record_ledger_entry
from ingestion.ledger_migrations import (
    LEDGER_MIGRATION_STATEMENTS,
    LEDGER_TABLE,
    run_ledger_migrations,
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
        source=TEST_SOURCE,
        target_node_id=node_id,
        confidence=1.0,
        attributes_added=["engine_code", "fuel_type"],
        source_batch_id="scrum17-batch-1",
    )
    correction_id = record_ledger_entry(
        pg_connection,
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
    assert entries[0].cost_eur == Decimal("0")
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
        source=TEST_SOURCE,
        target_node_id=node_id,
        confidence=1.0,
    )
    pg_connection.commit()

    with pytest.raises(RaiseException, match="append-only"):
        with pg_connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {LEDGER_TABLE} SET confidence = 0.5 WHERE id = %s",
                (entry_id,),
            )
    pg_connection.rollback()

    with pytest.raises(RaiseException, match="append-only"):
        with pg_connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {LEDGER_TABLE} WHERE id = %s", (entry_id,))
    pg_connection.rollback()

    with pytest.raises(RaiseException, match="append-only"):
        with pg_connection.cursor() as cursor:
            cursor.execute(f"TRUNCATE {LEDGER_TABLE}")
    pg_connection.rollback()

    entries = fetch_entries_for_node(pg_connection, node_id)
    assert [entry.id for entry in entries] == [entry_id]
    assert entries[0].confidence == 1.0
