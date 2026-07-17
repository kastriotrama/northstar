import pytest

from ingestion.staging_migrations import (
    ALLOWED_STAGING_TABLES,
    STAGING_MIGRATION_STATEMENTS,
    TECDOC_MANUFACTURER_TABLE,
    TRANSPORTSTYRELSEN_RAW_TABLE,
    tecdoc_staging_table_statement,
)


def test_every_statement_is_idempotent() -> None:
    for statement in STAGING_MIGRATION_STATEMENTS:
        assert "IF NOT EXISTS" in statement.sql


def test_statement_names_are_unique() -> None:
    names = [statement.name for statement in STAGING_MIGRATION_STATEMENTS]
    assert len(names) == len(set(names))


def test_transportstyrelsen_raw_has_batch_and_ingested_columns() -> None:
    assert "source_batch_id TEXT NOT NULL" in TRANSPORTSTYRELSEN_RAW_TABLE.sql
    assert "ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()" in TRANSPORTSTYRELSEN_RAW_TABLE.sql
    assert TRANSPORTSTYRELSEN_RAW_TABLE.qualified_table == "staging.transportstyrelsen_raw"


def test_tecdoc_pattern_generates_expected_table_for_new_entity() -> None:
    statement = tecdoc_staging_table_statement("model")

    assert statement.name == "create_staging_tecdoc_model_table"
    assert statement.qualified_table == "staging.tecdoc_model"
    assert "CREATE TABLE IF NOT EXISTS staging.tecdoc_model" in statement.sql
    assert "raw_record JSONB NOT NULL" in statement.sql


def test_tecdoc_manufacturer_is_a_worked_example_of_the_pattern() -> None:
    assert TECDOC_MANUFACTURER_TABLE.qualified_table == "staging.tecdoc_manufacturer"
    assert TECDOC_MANUFACTURER_TABLE in STAGING_MIGRATION_STATEMENTS


@pytest.mark.parametrize(
    "entity_name",
    ["Manufacturer", "model-name", "1model", "model;drop table x", "", " model"],
)
def test_tecdoc_pattern_rejects_unsafe_entity_names(entity_name: str) -> None:
    with pytest.raises(ValueError, match="entity_name"):
        tecdoc_staging_table_statement(entity_name)


def test_allowed_staging_tables_matches_table_statements() -> None:
    expected = {
        statement.qualified_table
        for statement in STAGING_MIGRATION_STATEMENTS
        if statement.qualified_table is not None
    }
    assert ALLOWED_STAGING_TABLES == expected
    assert "staging.transportstyrelsen_raw" in ALLOWED_STAGING_TABLES
