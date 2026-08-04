"""Idempotent PostgreSQL ingest-job bookkeeping migrations (SCRUM-19)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from psycopg import Connection

CORE_SCHEMA_NAME = "core"
JOB_RUNS_TABLE = f"{CORE_SCHEMA_NAME}.ingest_job_runs"
JOB_RUN_STATUSES = ("running", "completed", "failed")

_JOB_RUN_COLUMN_CONTRACT = (
    ("id", "bigint", False, None, "ALWAYS"),
    ("job_name", "text", False, None, None),
    ("batch_id", "text", False, None, None),
    ("status", "text", False, "running", None),
    ("records_processed", "bigint", False, "zero", None),
    ("records_succeeded", "bigint", False, "zero", None),
    ("records_failed", "bigint", False, "zero", None),
    ("error_code", "text", True, None, None),
    ("error_summary", "text", True, None, None),
    ("started_at", "timestamp with time zone", False, "now", None),
    ("finished_at", "timestamp with time zone", True, None, None),
    ("updated_at", "timestamp with time zone", False, "now", None),
)
_JOB_RUN_PRIMARY_KEY = ("id",)
_REQUIRED_CONSTRAINTS = {
    "ingest_job_runs_pkey": ("p", ("PRIMARY KEY", "(id)")),
    "ingest_job_runs_job_batch_key": (
        "u",
        ("UNIQUE", "(job_name, batch_id)"),
    ),
    "ingest_job_runs_job_name_nonempty": (
        "c",
        ("CHECK", "btrim(job_name)", "<>"),
    ),
    "ingest_job_runs_batch_id_nonempty": (
        "c",
        ("CHECK", "btrim(batch_id)", "<>"),
    ),
    "ingest_job_runs_status_values": (
        "c",
        ("CHECK", "status", "running", "completed", "failed"),
    ),
    "ingest_job_runs_counts_nonnegative": (
        "c",
        (
            "CHECK",
            "records_processed >= 0",
            "records_succeeded >= 0",
            "records_failed >= 0",
        ),
    ),
    "ingest_job_runs_counts_balance": (
        "c",
        ("CHECK", "records_processed", "records_succeeded + records_failed"),
    ),
    "ingest_job_runs_finished_state": (
        "c",
        ("CHECK", "status = 'running'", "finished_at IS NULL"),
    ),
    "ingest_job_runs_error_state": (
        "c",
        (
            "CHECK",
            "status = 'failed'",
            "error_code IS NOT NULL",
            "error_summary IS NOT NULL",
        ),
    ),
}
_REQUIRED_INDEX_FRAGMENTS = {
    "ingest_job_runs_status_started_at_idx": ("(status, started_at, id)",),
}


class JobBookkeepingSchemaContractError(RuntimeError):
    """Raised when the existing job-runs table does not match the contract."""


@dataclass(frozen=True)
class JobBookkeepingMigrationStatement:
    """One named, idempotent job-bookkeeping schema statement."""

    name: str
    kind: Literal["schema", "table", "index"]
    sql: str


JOB_BOOKKEEPING_MIGRATION_STATEMENTS: tuple[JobBookkeepingMigrationStatement, ...] = (
    JobBookkeepingMigrationStatement(
        name="create_core_schema",
        kind="schema",
        sql=f"CREATE SCHEMA IF NOT EXISTS {CORE_SCHEMA_NAME}",
    ),
    JobBookkeepingMigrationStatement(
        name="create_ingest_job_runs_table",
        kind="table",
        sql=(
            f"CREATE TABLE IF NOT EXISTS {JOB_RUNS_TABLE} ("
            "id BIGINT GENERATED ALWAYS AS IDENTITY, "
            "job_name TEXT NOT NULL, "
            "batch_id TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'running', "
            "records_processed BIGINT NOT NULL DEFAULT 0, "
            "records_succeeded BIGINT NOT NULL DEFAULT 0, "
            "records_failed BIGINT NOT NULL DEFAULT 0, "
            "error_code TEXT, "
            "error_summary TEXT, "
            "started_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
            "finished_at TIMESTAMPTZ, "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
            "CONSTRAINT ingest_job_runs_pkey PRIMARY KEY (id), "
            "CONSTRAINT ingest_job_runs_job_batch_key UNIQUE (job_name, batch_id), "
            "CONSTRAINT ingest_job_runs_job_name_nonempty "
            "CHECK (btrim(job_name) <> ''), "
            "CONSTRAINT ingest_job_runs_batch_id_nonempty "
            "CHECK (btrim(batch_id) <> ''), "
            "CONSTRAINT ingest_job_runs_status_values "
            "CHECK (status IN ('running', 'completed', 'failed')), "
            "CONSTRAINT ingest_job_runs_counts_nonnegative "
            "CHECK (records_processed >= 0 AND records_succeeded >= 0 "
            "AND records_failed >= 0), "
            "CONSTRAINT ingest_job_runs_counts_balance "
            "CHECK (records_processed = records_succeeded + records_failed), "
            "CONSTRAINT ingest_job_runs_finished_state "
            "CHECK ((status = 'running' AND finished_at IS NULL) "
            "OR (status IN ('completed', 'failed') AND finished_at IS NOT NULL)), "
            "CONSTRAINT ingest_job_runs_error_state "
            "CHECK ((status = 'failed' AND error_code IS NOT NULL "
            "AND btrim(error_code) <> '' AND char_length(error_code) <= 128 "
            "AND error_summary IS NOT NULL AND btrim(error_summary) <> '' "
            "AND char_length(error_summary) <= 500) "
            "OR (status IN ('running', 'completed') "
            "AND error_code IS NULL AND error_summary IS NULL))"
            ")"
        ),
    ),
    JobBookkeepingMigrationStatement(
        name="ingest_job_runs_status_started_at_index",
        kind="index",
        sql=(
            "CREATE INDEX IF NOT EXISTS ingest_job_runs_status_started_at_idx "
            f"ON {JOB_RUNS_TABLE} (status, started_at, id)"
        ),
    ),
)


def run_job_bookkeeping_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply and verify the job-bookkeeping schema in one transaction."""

    try:
        with connection.cursor() as cursor:
            for statement in JOB_BOOKKEEPING_MIGRATION_STATEMENTS:
                cursor.execute(statement.sql)
        verify_job_bookkeeping_schema_contract(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return tuple(statement.name for statement in JOB_BOOKKEEPING_MIGRATION_STATEMENTS)


def verify_job_bookkeeping_schema_contract(connection: Connection) -> None:
    """Ensure the existing table, constraints, and index match the contract."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, data_type, is_nullable, column_default, "
            "is_identity, identity_generation "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position",
            (CORE_SCHEMA_NAME, "ingest_job_runs"),
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
            "WHERE constraints.table_schema = %s "
            "AND constraints.table_name = %s "
            "AND constraints.constraint_type = 'PRIMARY KEY' "
            "ORDER BY key_usage.ordinal_position",
            (CORE_SCHEMA_NAME, "ingest_job_runs"),
        )
        primary_key = tuple(str(row[0]) for row in cursor.fetchall())
        cursor.execute(
            "SELECT constraint_name, constraint_type, pg_get_constraintdef(pg_constraint.oid) "
            "FROM information_schema.table_constraints "
            "JOIN pg_constraint ON pg_constraint.conname = constraint_name "
            "JOIN pg_namespace ON pg_namespace.oid = pg_constraint.connamespace "
            "AND pg_namespace.nspname = constraint_schema "
            "WHERE table_schema = %s AND table_name = %s",
            (CORE_SCHEMA_NAME, "ingest_job_runs"),
        )
        constraints = {
            str(row[0]): (
                {"PRIMARY KEY": "p", "UNIQUE": "u", "CHECK": "c"}.get(str(row[1]), ""),
                str(row[2]),
            )
            for row in cursor.fetchall()
        }
        cursor.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
            (CORE_SCHEMA_NAME, "ingest_job_runs"),
        )
        indexes = {str(row[0]): str(row[1]) for row in cursor.fetchall()}

    if columns != _JOB_RUN_COLUMN_CONTRACT:
        raise JobBookkeepingSchemaContractError(
            "core.ingest_job_runs column contract mismatch: "
            f"expected {_JOB_RUN_COLUMN_CONTRACT!r}, got {columns!r}"
        )
    if primary_key != _JOB_RUN_PRIMARY_KEY:
        raise JobBookkeepingSchemaContractError(
            "core.ingest_job_runs primary key contract mismatch"
        )
    for name, (kind, fragments) in _REQUIRED_CONSTRAINTS.items():
        actual = constraints.get(name)
        if (
            actual is None
            or actual[0] != kind
            or not all(fragment in actual[1] for fragment in fragments)
        ):
            raise JobBookkeepingSchemaContractError(
                f"core.ingest_job_runs constraint {name} does not match the contract"
            )
    for name, fragments in _REQUIRED_INDEX_FRAGMENTS.items():
        definition = indexes.get(name, "")
        if not all(fragment in definition for fragment in fragments):
            raise JobBookkeepingSchemaContractError(
                f"core.ingest_job_runs index {name} does not match the contract"
            )


def _classify_column_default(default: str | None) -> str | None:
    if default is None:
        return None
    if default == "'running'::text":
        return "running"
    if default in ("0", "0::bigint"):
        return "zero"
    if default == "now()":
        return "now"
    return default
