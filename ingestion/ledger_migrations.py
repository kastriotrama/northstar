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
    ("id", "bigint", False),
    ("source", "text", False),
    ("target_node_id", "text", False),
    ("attributes_added", "ARRAY", False),
    ("nodes_benefited", "integer", False),
    ("cost_eur", "numeric", False),
    ("confidence", "double precision", False),
    ("evidence", "jsonb", False),
    ("source_batch_id", "text", True),
    ("corrects_ledger_id", "bigint", True),
    ("created_at", "timestamp with time zone", False),
)
_LEDGER_PRIMARY_KEY = ("id",)
_APPEND_ONLY_TRIGGER_NAME = "enrichment_ledger_append_only"
_APPEND_ONLY_TRUNCATE_TRIGGER_NAME = "enrichment_ledger_append_only_truncate"


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
            "id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
            "source TEXT NOT NULL CHECK (source <> ''), "
            "target_node_id TEXT NOT NULL CHECK (char_length(target_node_id) = 30), "
            "attributes_added TEXT[] NOT NULL DEFAULT '{}', "
            "nodes_benefited INTEGER NOT NULL DEFAULT 1 CHECK (nodes_benefited >= 1), "
            "cost_eur NUMERIC(12,4) NOT NULL DEFAULT 0 CHECK (cost_eur >= 0), "
            "confidence DOUBLE PRECISION NOT NULL "
            "CHECK (confidence >= 0 AND confidence <= 1), "
            "evidence JSONB NOT NULL DEFAULT '{}'::jsonb, "
            "source_batch_id TEXT, "
            f"corrects_ledger_id BIGINT REFERENCES {LEDGER_TABLE}(id), "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
            ")"
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
    """Ensure the existing ledger table matches columns, key, and trigger."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position",
            (CORE_SCHEMA_NAME, "enrichment_ledger"),
        )
        columns = tuple(
            (str(row[0]), str(row[1]), str(row[2]) == "YES") for row in cursor.fetchall()
        )
        cursor.execute(
            "SELECT key_usage.column_name "
            "FROM information_schema.table_constraints AS constraints "
            "JOIN information_schema.key_column_usage AS key_usage "
            "ON constraints.constraint_name = key_usage.constraint_name "
            "AND constraints.constraint_schema = key_usage.constraint_schema "
            "WHERE constraints.table_schema = %s "
            "AND constraints.table_name = %s "
            "AND constraints.constraint_type = 'PRIMARY KEY' "
            "ORDER BY key_usage.ordinal_position",
            (CORE_SCHEMA_NAME, "enrichment_ledger"),
        )
        primary_key = tuple(str(row[0]) for row in cursor.fetchall())
        # pg_trigger, not information_schema.triggers: the latter omits
        # TRUNCATE triggers entirely (no such event in the SQL standard).
        cursor.execute(
            "SELECT trigger.tgname FROM pg_trigger AS trigger "
            "JOIN pg_class AS table_class ON trigger.tgrelid = table_class.oid "
            "JOIN pg_namespace AS schema_ns ON table_class.relnamespace = schema_ns.oid "
            "WHERE schema_ns.nspname = %s AND table_class.relname = %s "
            "AND NOT trigger.tgisinternal",
            (CORE_SCHEMA_NAME, "enrichment_ledger"),
        )
        trigger_names = {str(row[0]) for row in cursor.fetchall()}

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
    required_triggers = {_APPEND_ONLY_TRIGGER_NAME, _APPEND_ONLY_TRUNCATE_TRIGGER_NAME}
    missing_triggers = required_triggers - trigger_names
    if missing_triggers:
        message = (
            f"{LEDGER_TABLE} is missing triggers {sorted(missing_triggers)!r}; "
            "append-only enforcement is part of the schema contract"
        )
        raise LedgerSchemaContractError(message)
