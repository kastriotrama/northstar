from ingestion.ledger_migrations import (
    LEDGER_MIGRATION_STATEMENTS,
    LEDGER_TABLE,
)


def test_every_statement_is_idempotent() -> None:
    for statement in LEDGER_MIGRATION_STATEMENTS:
        assert "IF NOT EXISTS" in statement.sql or "CREATE OR REPLACE" in statement.sql


def test_statement_names_are_unique() -> None:
    names = [statement.name for statement in LEDGER_MIGRATION_STATEMENTS]
    assert len(names) == len(set(names))


def test_table_statement_covers_required_ledger_columns() -> None:
    table_sql = next(
        statement.sql
        for statement in LEDGER_MIGRATION_STATEMENTS
        if statement.name == "create_enrichment_ledger_table"
    )
    for fragment in (
        "source TEXT NOT NULL",
        "target_node_id TEXT NOT NULL",
        "attributes_added TEXT[] NOT NULL",
        "nodes_benefited INTEGER NOT NULL",
        "cost_eur NUMERIC(12,4) NOT NULL",
        "confidence DOUBLE PRECISION NOT NULL",
        "evidence JSONB NOT NULL",
        "corrects_ledger_id BIGINT REFERENCES",
        "created_at TIMESTAMPTZ NOT NULL",
    ):
        assert fragment in table_sql, fragment


def test_append_only_triggers_cover_update_delete_and_truncate() -> None:
    trigger_sqls = [
        statement.sql
        for statement in LEDGER_MIGRATION_STATEMENTS
        if statement.kind == "trigger"
    ]
    assert len(trigger_sqls) == 2
    assert any("BEFORE UPDATE OR DELETE" in sql for sql in trigger_sqls)
    assert any("BEFORE TRUNCATE" in sql for sql in trigger_sqls)
    assert all(LEDGER_TABLE in sql for sql in trigger_sqls)


def test_identity_column_blocks_explicit_id_inserts() -> None:
    table_sql = next(
        statement.sql
        for statement in LEDGER_MIGRATION_STATEMENTS
        if statement.name == "create_enrichment_ledger_table"
    )
    assert "GENERATED ALWAYS AS IDENTITY" in table_sql
    assert "BIGSERIAL" not in table_sql


def test_statement_kinds_match_sql() -> None:
    expected_prefixes = {
        "schema": "CREATE SCHEMA",
        "table": "CREATE TABLE",
        "index": "CREATE INDEX",
        "function": "CREATE OR REPLACE FUNCTION",
        "trigger": "CREATE OR REPLACE TRIGGER",
    }
    for statement in LEDGER_MIGRATION_STATEMENTS:
        assert statement.sql.startswith(expected_prefixes[statement.kind])
