"""Idempotent PostgreSQL enrichment-ledger migrations (SCRUM-17).

The ledger is the append-only provenance record for every graph write and
enrichment event. Per the accepted SCRUM-13 relationship contract, the graph
holds resolved singular facts; conflicting source evidence and enrichment
rationale live here, never as parallel graph edges.

Tables live in the `core` schema: durable operational tables (this ledger
now; the review queue and job bookkeeping of Stories 3.3/3.4 later), as
opposed to `staging`, which holds disposable raw landings.

Statement names are a stable public contract asserted by the doc contract
tests in docs/ledger-schema-design.md. Every statement is idempotent
(IF NOT EXISTS / CREATE OR REPLACE) so the migration can run repeatedly.
Append-only is enforced in the database, not just by convention: a trigger
rejects UPDATE and DELETE on the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from psycopg import Connection

CORE_SCHEMA_NAME = "core"
LEDGER_TABLE = f"{CORE_SCHEMA_NAME}.enrichment_ledger"

_LEDGER_COLUMN_CONTRACT = (
    ("id", "bigint", False, None, "ALWAYS"),
    ("event_id", "uuid", False, None, None),
    ("source", "text", False, None, None),
    ("target_node_id", "text", False, None, None),
    ("attributes_added", "ARRAY", False, "empty_text_array", None),
    ("nodes_benefited", "integer", False, "one", None),
    ("cost_eur", "numeric", False, "zero", None),
    ("confidence", "double precision", False, None, None),
    ("evidence", "jsonb", False, "empty_json", None),
    ("source_batch_id", "text", True, None, None),
    ("corrects_ledger_id", "bigint", True, None, None),
    ("created_at", "timestamp with time zone", False, "now", None),
)
_LEDGER_PRIMARY_KEY = ("id",)
_REQUIRED_CONSTRAINTS = {
    "enrichment_ledger_pkey": ("p", ("PRIMARY KEY", "(id)")),
    "enrichment_ledger_event_id_key": ("u", ("UNIQUE", "(event_id)")),
    "enrichment_ledger_id_target_key": (
        "u",
        ("UNIQUE", "(id, target_node_id)"),
    ),
    "enrichment_ledger_source_nonempty": (
        "c",
        ("CHECK", "btrim(source)", "<>"),
    ),
    "enrichment_ledger_target_node_id_format": (
        "c",
        ("CHECK", "char_length(target_node_id)", "= 30"),
    ),
    "enrichment_ledger_nodes_benefited_positive": (
        "c",
        ("CHECK", "nodes_benefited", ">= 1"),
    ),
    "enrichment_ledger_cost_nonnegative": (
        "c",
        ("CHECK", "cost_eur", ">="),
    ),
    "enrichment_ledger_confidence_range": (
        "c",
        ("CHECK", "confidence", ">=", "<="),
    ),
    "enrichment_ledger_correction_target_fk": (
        "f",
        (
            "FOREIGN KEY (corrects_ledger_id, target_node_id)",
            "REFERENCES core.enrichment_ledger(id, target_node_id)",
        ),
    ),
}
_REQUIRED_INDEX_FRAGMENTS = {
    "enrichment_ledger_target_node_id_idx": ("(target_node_id)",),
    "enrichment_ledger_created_at_idx": ("(created_at)",),
    "enrichment_ledger_corrects_once_idx": (
        "CREATE UNIQUE INDEX",
        "(corrects_ledger_id)",
        "WHERE (corrects_ledger_id IS NOT NULL)",
    ),
}
_APPEND_ONLY_TRIGGER_NAME = "enrichment_ledger_append_only"
_APPEND_ONLY_TRUNCATE_TRIGGER_NAME = "enrichment_ledger_append_only_truncate"
_REQUIRED_TRIGGERS = {
    _APPEND_ONLY_TRIGGER_NAME: (27, "core", "enrichment_ledger_block_mutation"),
    _APPEND_ONLY_TRUNCATE_TRIGGER_NAME: (
        34,
        "core",
        "enrichment_ledger_block_mutation",
    ),
}


class LedgerSchemaContractError(RuntimeError):
    """Raised when the existing ledger table does not match the contract."""


@dataclass(frozen=True)
class LedgerMigrationStatement:
    """One named, idempotent ledger schema statement."""

    name: str
    kind: Literal["schema", "table", "index", "function", "trigger"]
    sql: str


LEDGER_MIGRATION_STATEMENTS: tuple[LedgerMigrationStatement, ...] = (
    LedgerMigrationStatement(
        name="create_core_schema",
        kind="schema",
        sql=f"CREATE SCHEMA IF NOT EXISTS {CORE_SCHEMA_NAME}",
    ),
    LedgerMigrationStatement(
        name="create_enrichment_ledger_table",
        kind="table",
        sql=(
            f"CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} ("
            "id BIGINT GENERATED ALWAYS AS IDENTITY, "
            "event_id UUID NOT NULL, "
            "source TEXT NOT NULL, "
            "target_node_id TEXT NOT NULL, "
            "attributes_added TEXT[] NOT NULL DEFAULT '{}', "
            "nodes_benefited INTEGER NOT NULL DEFAULT 1, "
            "cost_eur NUMERIC(12,4) NOT NULL DEFAULT 0, "
            "confidence DOUBLE PRECISION NOT NULL, "
            "evidence JSONB NOT NULL DEFAULT '{}'::jsonb, "
            "source_batch_id TEXT, "
            "corrects_ledger_id BIGINT, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
            "CONSTRAINT enrichment_ledger_pkey PRIMARY KEY (id), "
            "CONSTRAINT enrichment_ledger_event_id_key UNIQUE (event_id), "
            "CONSTRAINT enrichment_ledger_id_target_key UNIQUE (id, target_node_id), "
            "CONSTRAINT enrichment_ledger_source_nonempty "
            "CHECK (btrim(source) <> ''), "
            "CONSTRAINT enrichment_ledger_target_node_id_format "
            "CHECK (char_length(target_node_id) = 30), "
            "CONSTRAINT enrichment_ledger_nodes_benefited_positive "
            "CHECK (nodes_benefited >= 1), "
            "CONSTRAINT enrichment_ledger_cost_nonnegative CHECK (cost_eur >= 0), "
            "CONSTRAINT enrichment_ledger_confidence_range "
            "CHECK (confidence >= 0 AND confidence <= 1), "
            "CONSTRAINT enrichment_ledger_correction_target_fk "
            "FOREIGN KEY (corrects_ledger_id, target_node_id) "
            f"REFERENCES {LEDGER_TABLE}(id, target_node_id)"
            ")"
        ),
    ),
    LedgerMigrationStatement(
        name="enrichment_ledger_corrects_once_index",
        kind="index",
        sql=(
            "CREATE UNIQUE INDEX IF NOT EXISTS enrichment_ledger_corrects_once_idx "
            f"ON {LEDGER_TABLE} (corrects_ledger_id) "
            "WHERE corrects_ledger_id IS NOT NULL"
        ),
    ),
    LedgerMigrationStatement(
        name="enrichment_ledger_target_node_id_index",
        kind="index",
        sql=(
            "CREATE INDEX IF NOT EXISTS enrichment_ledger_target_node_id_idx "
            f"ON {LEDGER_TABLE} (target_node_id)"
        ),
    ),
    LedgerMigrationStatement(
        name="enrichment_ledger_created_at_index",
        kind="index",
        sql=(
            "CREATE INDEX IF NOT EXISTS enrichment_ledger_created_at_idx "
            f"ON {LEDGER_TABLE} (created_at)"
        ),
    ),
    LedgerMigrationStatement(
        name="enrichment_ledger_append_only_function",
        kind="function",
        sql=(
            f"CREATE OR REPLACE FUNCTION {CORE_SCHEMA_NAME}"
            ".enrichment_ledger_block_mutation() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            f"RAISE EXCEPTION '{LEDGER_TABLE} is append-only: % is not allowed'"
            ", TG_OP; "
            "END $$"
        ),
    ),
    LedgerMigrationStatement(
        name="enrichment_ledger_append_only_trigger",
        kind="trigger",
        sql=(
            f"CREATE OR REPLACE TRIGGER {_APPEND_ONLY_TRIGGER_NAME} "
            f"BEFORE UPDATE OR DELETE ON {LEDGER_TABLE} "
            f"FOR EACH ROW EXECUTE FUNCTION {CORE_SCHEMA_NAME}"
            ".enrichment_ledger_block_mutation()"
        ),
    ),
    # Row-level triggers do not fire on TRUNCATE; without this statement-level
    # trigger a single TRUNCATE would silently erase the entire history.
    LedgerMigrationStatement(
        name="enrichment_ledger_append_only_truncate_trigger",
        kind="trigger",
        sql=(
            f"CREATE OR REPLACE TRIGGER {_APPEND_ONLY_TRUNCATE_TRIGGER_NAME} "
            f"BEFORE TRUNCATE ON {LEDGER_TABLE} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {CORE_SCHEMA_NAME}"
            ".enrichment_ledger_block_mutation()"
        ),
    ),
)


def run_ledger_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply every ledger migration statement; return applied names in order."""

    try:
        with connection.cursor() as cursor:
            for statement in LEDGER_MIGRATION_STATEMENTS:
                cursor.execute(statement.sql)
        verify_ledger_schema_contract(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return tuple(statement.name for statement in LEDGER_MIGRATION_STATEMENTS)


def verify_ledger_schema_contract(connection: Connection) -> None:
    """Ensure the existing ledger table matches the complete durable contract."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, data_type, is_nullable, column_default, "
            "is_identity, identity_generation "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position",
            (CORE_SCHEMA_NAME, "enrichment_ledger"),
        )
        columns = tuple(
            (
                str(row[0]),
                str(row[1]),
                str(row[2]) == "YES",
                _classify_column_default(None if row[3] is None else str(row[3])),
                None if str(row[4]) == "NO" else str(row[5]),
            )
            for row in cursor.fetchall()
        )
        cursor.execute(
            "SELECT key_usage.column_name "
            "FROM information_schema.table_constraints AS constraints "
            "JOIN information_schema.key_column_usage AS key_usage "
            "ON constraints.constraint_name = key_usage.constraint_name "
            "AND constraints.constraint_schema = key_usage.constraint_schema "
            "AND constraints.table_schema = key_usage.table_schema "
            "AND constraints.table_name = key_usage.table_name "
            "WHERE constraints.table_schema = %s "
            "AND constraints.table_name = %s "
            "AND constraints.constraint_type = 'PRIMARY KEY' "
            "ORDER BY key_usage.ordinal_position",
            (CORE_SCHEMA_NAME, "enrichment_ledger"),
        )
        primary_key = tuple(str(row[0]) for row in cursor.fetchall())
        cursor.execute(
            "SELECT constraint_record.conname, constraint_record.contype, "
            "pg_get_constraintdef(constraint_record.oid) "
            "FROM pg_constraint AS constraint_record "
            "JOIN pg_class AS table_class "
            "ON constraint_record.conrelid = table_class.oid "
            "JOIN pg_namespace AS schema_ns "
            "ON table_class.relnamespace = schema_ns.oid "
            "WHERE schema_ns.nspname = %s AND table_class.relname = %s",
            (CORE_SCHEMA_NAME, "enrichment_ledger"),
        )
        constraints = {str(row[0]): (str(row[1]), str(row[2])) for row in cursor.fetchall()}
        cursor.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
            (CORE_SCHEMA_NAME, "enrichment_ledger"),
        )
        indexes = {str(row[0]): str(row[1]) for row in cursor.fetchall()}
        # pg_trigger exposes TRUNCATE plus enabled state and exact event bits.
        cursor.execute(
            "SELECT trigger.tgname, trigger.tgenabled, trigger.tgtype, "
            "function_ns.nspname, function_record.proname "
            "FROM pg_trigger AS trigger "
            "JOIN pg_class AS table_class ON trigger.tgrelid = table_class.oid "
            "JOIN pg_namespace AS schema_ns ON table_class.relnamespace = schema_ns.oid "
            "JOIN pg_proc AS function_record ON trigger.tgfoid = function_record.oid "
            "JOIN pg_namespace AS function_ns "
            "ON function_record.pronamespace = function_ns.oid "
            "WHERE schema_ns.nspname = %s AND table_class.relname = %s "
            "AND NOT trigger.tgisinternal",
            (CORE_SCHEMA_NAME, "enrichment_ledger"),
        )
        triggers = {
            str(row[0]): (str(row[1]), int(row[2]), str(row[3]), str(row[4]))
            for row in cursor.fetchall()
        }

    if columns != _LEDGER_COLUMN_CONTRACT:
        message = (
            f"{LEDGER_TABLE} column contract mismatch: "
            f"expected {_LEDGER_COLUMN_CONTRACT!r}, got {columns!r}"
        )
        raise LedgerSchemaContractError(message)
    if primary_key != _LEDGER_PRIMARY_KEY:
        message = (
            f"{LEDGER_TABLE} primary key mismatch: "
            f"expected {_LEDGER_PRIMARY_KEY!r}, got {primary_key!r}"
        )
        raise LedgerSchemaContractError(message)
    for name, (expected_type, fragments) in _REQUIRED_CONSTRAINTS.items():
        actual_constraint = constraints.get(name)
        if (
            actual_constraint is None
            or actual_constraint[0] != expected_type
            or any(fragment not in actual_constraint[1] for fragment in fragments)
        ):
            raise LedgerSchemaContractError(
                f"{LEDGER_TABLE} constraint {name!r} mismatch: got {actual_constraint!r}"
            )
    for name, fragments in _REQUIRED_INDEX_FRAGMENTS.items():
        definition = indexes.get(name)
        if definition is None or any(fragment not in definition for fragment in fragments):
            raise LedgerSchemaContractError(
                f"{LEDGER_TABLE} index {name!r} mismatch: got {definition!r}"
            )
    for name, (event_bits, function_schema, function_name) in _REQUIRED_TRIGGERS.items():
        actual_trigger = triggers.get(name)
        expected = ("O", event_bits, function_schema, function_name)
        if actual_trigger != expected:
            raise LedgerSchemaContractError(
                f"{LEDGER_TABLE} trigger {name!r} mismatch: "
                f"expected {expected!r}, got {actual_trigger!r}"
            )


def _classify_column_default(default: str | None) -> str | None:
    if default is None:
        return None
    if default == "'{}'::text[]":
        return "empty_text_array"
    if default == "'{}'::jsonb":
        return "empty_json"
    if default in {"1", "1.0"}:
        return "one"
    if default in {"0", "0.0"}:
        return "zero"
    if default == "now()":
        return "now"
    return default
