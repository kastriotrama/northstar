"""Idempotent PostgreSQL staging schema migrations (SCRUM-16).

Staging is an untouched landing zone: raw source records land here via COPY
exactly as extracted, with no transformation. Normalization (Epic 4) reads
from staging; nothing in this module writes to the graph.

Statement names are a stable public contract asserted by the doc contract
tests in docs/staging-schema-design.md. Every statement uses IF NOT EXISTS
so the migration can run repeatedly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from psycopg import Connection

STAGING_SCHEMA_NAME = "staging"

_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

_RAW_LANDING_COLUMNS_SQL = (
    "id BIGSERIAL PRIMARY KEY, "
    "source_batch_id TEXT NOT NULL, "
    "ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
    "raw_record JSONB NOT NULL"
)

_RAW_LANDING_COLUMN_CONTRACT = (
    ("id", "bigint", False, "sequence"),
    ("source_batch_id", "text", False, None),
    ("ingested_at", "timestamp with time zone", False, "now"),
    ("raw_record", "jsonb", False, None),
)
_RAW_LANDING_PRIMARY_KEY = ("id",)


class StagingSchemaContractError(RuntimeError):
    """Raised when an existing staging table does not match the shared shape."""


@dataclass(frozen=True)
class StagingMigrationStatement:
    """One named, idempotent staging schema statement."""

    name: str
    kind: Literal["schema", "table"]
    sql: str
    qualified_table: str | None = None


CREATE_STAGING_SCHEMA = StagingMigrationStatement(
    name="create_staging_schema",
    kind="schema",
    sql=f"CREATE SCHEMA IF NOT EXISTS {STAGING_SCHEMA_NAME}",
)


def tecdoc_staging_table_statement(entity_name: str) -> StagingMigrationStatement:
    """Build the CREATE TABLE statement for one TecDoc staging entity.

    Every `staging.tecdoc_<entity>` table follows the identical
    landing-zone shape: a raw JSONB payload plus batch/ingestion metadata.
    Add a new TecDoc entity by calling this function with its name -- do
    not hand-write new DDL.
    """
    if not _IDENTIFIER_PATTERN.match(entity_name):
        message = (
            "entity_name must be a lowercase snake_case identifier "
            f"matching {_IDENTIFIER_PATTERN.pattern!r}, got {entity_name!r}"
        )
        raise ValueError(message)

    table_name = f"tecdoc_{entity_name}"
    qualified_table = f"{STAGING_SCHEMA_NAME}.{table_name}"
    return StagingMigrationStatement(
        name=f"create_staging_{table_name}_table",
        kind="table",
        sql=(
            f"CREATE TABLE IF NOT EXISTS {qualified_table} ({_RAW_LANDING_COLUMNS_SQL})"
        ),
        qualified_table=qualified_table,
    )


TECDOC_ENTITY_NAMES: tuple[str, ...] = (
    "manufacturer",
    "model_family",
    "platform",
    "vehicle_variant",
    "engine",
    "transmission",
    "bodywork",
    "ktype_alias",
)
TECDOC_TABLE_STATEMENTS: tuple[StagingMigrationStatement, ...] = tuple(
    tecdoc_staging_table_statement(name) for name in TECDOC_ENTITY_NAMES
)
# Backwards-compatible name retained for the original staging contract tests.
TECDOC_MANUFACTURER_TABLE = TECDOC_TABLE_STATEMENTS[0]

TRANSPORTSTYRELSEN_RAW_TABLE = StagingMigrationStatement(
    name="create_staging_transportstyrelsen_raw_table",
    kind="table",
    sql=(
        f"CREATE TABLE IF NOT EXISTS {STAGING_SCHEMA_NAME}.transportstyrelsen_raw "
        f"({_RAW_LANDING_COLUMNS_SQL})"
    ),
    qualified_table=f"{STAGING_SCHEMA_NAME}.transportstyrelsen_raw",
)

STAGING_MIGRATION_STATEMENTS: tuple[StagingMigrationStatement, ...] = (
    CREATE_STAGING_SCHEMA,
    *TECDOC_TABLE_STATEMENTS,
    TRANSPORTSTYRELSEN_RAW_TABLE,
)

ALLOWED_STAGING_TABLES: frozenset[str] = frozenset(
    statement.qualified_table
    for statement in STAGING_MIGRATION_STATEMENTS
    if statement.qualified_table is not None
)


def run_staging_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply every staging migration statement; return applied names in order."""

    try:
        with connection.cursor() as cursor:
            for statement in STAGING_MIGRATION_STATEMENTS:
                cursor.execute(statement.sql)
        verify_staging_schema_contract(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return tuple(statement.name for statement in STAGING_MIGRATION_STATEMENTS)


def _classify_column_default(default: str | None) -> str | None:
    if default is None:
        return None
    if default.startswith("nextval("):
        return "sequence"
    if default == "now()":
        return "now"
    return default


def verify_staging_schema_contract(connection: Connection) -> None:
    """Ensure every registered staging table has the exact shared contract."""

    for qualified_table in sorted(ALLOWED_STAGING_TABLES):
        schema_name, table_name = qualified_table.split(".", 1)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s "
                "ORDER BY ordinal_position",
                (schema_name, table_name),
            )
            columns = tuple(
                (
                    str(row[0]),
                    str(row[1]),
                    str(row[2]) == "YES",
                    _classify_column_default(
                        None if row[3] is None else str(row[3])
                    ),
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
                (schema_name, table_name),
            )
            primary_key = tuple(str(row[0]) for row in cursor.fetchall())

        if columns != _RAW_LANDING_COLUMN_CONTRACT:
            message = (
                f"{qualified_table} column contract mismatch: "
                f"expected {_RAW_LANDING_COLUMN_CONTRACT!r}, got {columns!r}"
            )
            raise StagingSchemaContractError(message)
        if primary_key != _RAW_LANDING_PRIMARY_KEY:
            message = (
                f"{qualified_table} primary key mismatch: "
                f"expected {_RAW_LANDING_PRIMARY_KEY!r}, got {primary_key!r}"
            )
            raise StagingSchemaContractError(message)


def fetch_staging_schema_names(connection: Connection) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name = %s",
            (STAGING_SCHEMA_NAME,),
        )
        return {row[0] for row in cursor.fetchall()}


def fetch_staging_table_names(connection: Connection) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            (STAGING_SCHEMA_NAME,),
        )
        return {row[0] for row in cursor.fetchall()}
