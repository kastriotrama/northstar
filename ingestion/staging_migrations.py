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


# One worked example proving the TecDoc staging pattern (Story 3.1). Epic 5
# adds further staging.tecdoc_<entity> tables the same way.
TECDOC_MANUFACTURER_TABLE = tecdoc_staging_table_statement("manufacturer")

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
    TECDOC_MANUFACTURER_TABLE,
    TRANSPORTSTYRELSEN_RAW_TABLE,
)

ALLOWED_STAGING_TABLES: frozenset[str] = frozenset(
    statement.qualified_table
    for statement in STAGING_MIGRATION_STATEMENTS
    if statement.qualified_table is not None
)


def run_staging_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply every staging migration statement; return applied names in order."""

    with connection.cursor() as cursor:
        for statement in STAGING_MIGRATION_STATEMENTS:
            cursor.execute(statement.sql)
    connection.commit()
    return tuple(statement.name for statement in STAGING_MIGRATION_STATEMENTS)


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
