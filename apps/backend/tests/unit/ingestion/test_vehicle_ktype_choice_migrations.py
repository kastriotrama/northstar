from ingestion.vehicle_core_migrations import REQUIRED_INDEXES, VEHICLE_CORE_MIGRATIONS
from ingestion.vehicle_ktype_choice_migrations import (
    COLUMNS,
    VEHICLE_KTYPE_CHOICE_MIGRATION_STATEMENTS,
    VEHICLE_KTYPE_CHOICES_TABLE,
)
from ingestion.vehicle_ktype_choices import NewChoice, StoredChoice


def test_statement_names_are_a_stable_contract() -> None:
    assert [statement.name for statement in VEHICLE_KTYPE_CHOICE_MIGRATION_STATEMENTS] == [
        "create_vehicle_ktype_choices_table",
        "vehicle_ktype_choices_append_only_function",
        "vehicle_ktype_choices_append_only_trigger",
        "vehicle_ktype_choices_append_only_truncate_trigger",
    ]


def test_every_statement_is_idempotent_and_of_its_kind() -> None:
    prefixes = {
        "table": ("CREATE TABLE IF NOT EXISTS",),
        "index": ("CREATE INDEX IF NOT EXISTS", "CREATE UNIQUE INDEX IF NOT EXISTS"),
        "function": ("CREATE OR REPLACE FUNCTION",),
        "trigger": ("CREATE OR REPLACE TRIGGER",),
    }
    for statement in VEHICLE_KTYPE_CHOICE_MIGRATION_STATEMENTS:
        assert statement.sql.startswith(prefixes[statement.kind]), statement.name
        assert VEHICLE_KTYPE_CHOICES_TABLE in statement.sql or statement.kind == "function"


def test_chain_rules_and_immutability_are_declared_in_the_database() -> None:
    by_name = {s.name: s.sql for s in VEHICLE_KTYPE_CHOICE_MIGRATION_STATEMENTS}
    table = by_name["create_vehicle_ktype_choices_table"]

    assert "CONSTRAINT vehicle_ktype_choices_pkey PRIMARY KEY (choice_id)" in table
    # The link carries the position, so a row can only supersede the same
    # vehicle's row one place below it: no cycle, no second root, no fork.
    assert "chain_position INTEGER NOT NULL" in table
    assert "supersedes_position INTEGER GENERATED ALWAYS AS" in table
    assert "UNIQUE (vehicle_id, chain_position)" in table
    assert "FOREIGN KEY (supersedes_choice_id, vehicle_id, supersedes_position)" in table
    assert "(choice_id, vehicle_id, chain_position)" in table
    assert "CHECK ((supersedes_choice_id IS NULL) = (chain_position = 0))" in table
    assert "DEFERRABLE" not in table
    assert "REFERENCES core.vehicles (vehicle_id) ON DELETE RESTRICT" in table
    assert "{" not in table.replace("{64}", "")  # no unformatted placeholder left behind
    assert "RAISE EXCEPTION" in by_name["vehicle_ktype_choices_append_only_function"]
    assert "BEFORE UPDATE OR DELETE" in by_name["vehicle_ktype_choices_append_only_trigger"]
    assert "BEFORE TRUNCATE" in by_name["vehicle_ktype_choices_append_only_truncate_trigger"]


def test_the_row_types_carry_exactly_the_tables_columns() -> None:
    assert tuple(StoredChoice.__dataclass_fields__) == COLUMNS
    # The writer assigns the position and the database the time; the generated
    # column is not a column a writer or a copy handles.
    assert tuple(NewChoice.__dataclass_fields__) == tuple(
        c for c in COLUMNS if c not in ("created_at", "chain_position")
    )
    assert "supersedes_position" not in COLUMNS


def test_the_match_state_index_is_partial_and_not_an_invariant() -> None:
    statements = dict(VEHICLE_CORE_MIGRATIONS)

    sql = statements["create_vehicles_match_state_index"]
    assert sql.startswith("CREATE INDEX IF NOT EXISTS vehicles_match_state_idx")
    assert "(match_state, vehicle_id) WHERE match_state IS NOT NULL" in sql
    assert "vehicles_match_state_idx" not in REQUIRED_INDEXES
