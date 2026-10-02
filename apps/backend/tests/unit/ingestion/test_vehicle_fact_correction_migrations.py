from ingestion.vehicle_fact_correction_migrations import (
    COLUMNS,
    VEHICLE_FACT_CORRECTION_MIGRATION_STATEMENTS,
    VEHICLE_FACT_CORRECTIONS_TABLE,
)
from ingestion.vehicle_fact_corrections import CorrectionHead, NewCorrection, StoredCorrection


def test_statement_names_are_a_stable_contract() -> None:
    assert [statement.name for statement in VEHICLE_FACT_CORRECTION_MIGRATION_STATEMENTS] == [
        "create_vehicle_fact_corrections_table",
        "vehicle_fact_corrections_group_index",
        "vehicle_fact_corrections_append_only_function",
        "vehicle_fact_corrections_append_only_trigger",
        "vehicle_fact_corrections_append_only_truncate_trigger",
    ]


def test_every_statement_is_idempotent_and_of_its_kind() -> None:
    prefixes = {
        "table": ("CREATE TABLE IF NOT EXISTS",),
        "index": ("CREATE INDEX IF NOT EXISTS", "CREATE UNIQUE INDEX IF NOT EXISTS"),
        "function": ("CREATE OR REPLACE FUNCTION",),
        "trigger": ("CREATE OR REPLACE TRIGGER",),
    }
    for statement in VEHICLE_FACT_CORRECTION_MIGRATION_STATEMENTS:
        assert statement.sql.startswith(prefixes[statement.kind]), statement.name
        assert VEHICLE_FACT_CORRECTIONS_TABLE in statement.sql or statement.kind == "function"


def test_chain_rules_and_immutability_are_declared_in_the_database() -> None:
    by_name = {s.name: s.sql for s in VEHICLE_FACT_CORRECTION_MIGRATION_STATEMENTS}
    table = " ".join(by_name["create_vehicle_fact_corrections_table"].split())

    assert "CONSTRAINT vehicle_fact_corrections_pkey PRIMARY KEY (correction_id)" in table
    # The link carries the position, so a row can only supersede the row of the
    # same vehicle and field one place below it: no cycle, no second root, no
    # fork, no crossing into another field.
    assert "chain_position INTEGER NOT NULL" in table
    assert "supersedes_position INTEGER GENERATED ALWAYS AS" in table
    assert "UNIQUE (vehicle_id, field, chain_position)" in table
    assert "FOREIGN KEY (supersedes_correction_id, vehicle_id, field, supersedes_position)" in table
    assert "(correction_id, vehicle_id, field, chain_position)" in table
    assert "CHECK ((supersedes_correction_id IS NULL) = (chain_position = 0))" in table
    assert "DEFERRABLE" not in table
    assert "REFERENCES core.vehicles (vehicle_id) ON DELETE RESTRICT" in table
    assert "{" not in table.replace("{64}", "")  # no unformatted placeholder left behind
    assert "WHERE group_id IS NOT NULL" in by_name["vehicle_fact_corrections_group_index"]
    assert "RAISE EXCEPTION" in by_name["vehicle_fact_corrections_append_only_function"]
    assert "BEFORE UPDATE OR DELETE" in by_name["vehicle_fact_corrections_append_only_trigger"]
    assert "BEFORE TRUNCATE" in by_name["vehicle_fact_corrections_append_only_truncate_trigger"]


def test_the_columns_come_in_the_contracts_order() -> None:
    assert COLUMNS == (
        "correction_id", "vehicle_id", "field", "chain_position", "action", "value",
        "supersedes_correction_id", "group_id", "reviewer", "reason", "previous_value",
        "previous_source", "catalog_batch", "automatic_terminal", "automatic_ktype",
        "code_version", "evidence_fingerprint", "evidence", "created_at",
    )


def test_the_row_types_carry_exactly_the_tables_columns() -> None:
    assert tuple(StoredCorrection.__dataclass_fields__) == COLUMNS
    # The writer assigns the position and the database the time; the generated
    # column is not a column a writer or a copy handles.
    assert tuple(NewCorrection.__dataclass_fields__) == tuple(
        column for column in COLUMNS if column not in ("created_at", "chain_position")
    )
    assert "supersedes_position" not in COLUMNS
    assert CorrectionHead._fields == ("action", "value", "correction_id")
