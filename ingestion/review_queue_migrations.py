"""Idempotent PostgreSQL review-queue migrations (SCRUM-18).

The review queue is the durable boundary between normalization and canonical
graph writes. Records enter it when identity, structure, or source evidence is
too uncertain or conflicting for an automatic write.

The queue stores references to raw staging rows rather than copying raw source
payloads. This preserves one source of truth and avoids duplicating sensitive
registry data into operational worklists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from psycopg import Connection

from ingestion.ledger_migrations import CORE_SCHEMA_NAME

REVIEW_QUEUE_TABLE = f"{CORE_SCHEMA_NAME}.review_queue"
REVIEW_STATUSES = ("pending", "in_review", "resolved", "rejected")

_REVIEW_QUEUE_COLUMN_CONTRACT = (
    ("id", "bigint", False, None, "ALWAYS"),
    ("review_id", "uuid", False, None, None),
    ("source_system", "text", False, None, None),
    ("source_batch_id", "text", True, None, None),
    ("source_table", "text", False, None, None),
    ("source_record_id", "bigint", False, None, None),
    ("reason_code", "text", False, None, None),
    ("reason_detail", "text", True, None, None),
    ("target_entity_type", "text", True, None, None),
    ("candidate_matches", "jsonb", False, "empty_json_array", None),
    ("confidence", "double precision", True, None, None),
    ("status", "text", False, "pending", None),
    ("resolution", "jsonb", False, "empty_json", None),
    ("resolved_by", "text", True, None, None),
    ("created_at", "timestamp with time zone", False, "now", None),
    ("updated_at", "timestamp with time zone", False, "now", None),
    ("resolved_at", "timestamp with time zone", True, None, None),
    ("review_draft", "jsonb", False, "empty_json", None),
)
_REVIEW_QUEUE_PRIMARY_KEY = ("id",)
_REQUIRED_CONSTRAINTS = {
    "review_queue_pkey": ("p", ("PRIMARY KEY", "(id)")),
    "review_queue_review_id_key": ("u", ("UNIQUE", "(review_id)")),
    "review_queue_source_system_nonempty": (
        "c",
        ("CHECK", "btrim(source_system)", "<>"),
    ),
    "review_queue_source_table_format": (
        "c",
        ("CHECK", "staging.transportstyrelsen_raw", "staging\\.tecdoc_"),
    ),
    "review_queue_source_record_id_positive": (
        "c",
        ("CHECK", "source_record_id", ">= 1"),
    ),
    "review_queue_reason_code_nonempty": (
        "c",
        ("CHECK", "btrim(reason_code)", "<>"),
    ),
    "review_queue_candidate_matches_array": (
        "c",
        ("CHECK", "jsonb_typeof(candidate_matches)", "'array'"),
    ),
    "review_queue_confidence_range": (
        "c",
        ("CHECK", "confidence IS NULL", "confidence >=", "confidence <="),
    ),
    "review_queue_status_values": (
        "c",
        tuple(REVIEW_STATUSES),
    ),
    "review_queue_resolution_object": (
        "c",
        ("CHECK", "jsonb_typeof(resolution)", "'object'"),
    ),
    "review_queue_review_draft_object": (
        "c",
        ("CHECK", "jsonb_typeof(review_draft)", "'object'"),
    ),
    "review_queue_resolution_state": (
        "c",
        ("CHECK", "resolved_at", "resolved_by", "resolution", "status"),
    ),
    "review_queue_timestamp_order": (
        "c",
        ("CHECK", "updated_at >= created_at"),
    ),
}
_REQUIRED_INDEX_FRAGMENTS = {
    "review_queue_status_created_at_idx": ("(status, created_at, id)",),
    "review_queue_source_record_idx": ("(source_table, source_record_id)",),
    "review_queue_source_batch_status_idx": (
        "(source_batch_id, status, updated_at, id)",
    ),
}


class ReviewQueueSchemaContractError(RuntimeError):
    """Raised when the existing review queue does not match the contract."""


@dataclass(frozen=True)
class ReviewQueueMigrationStatement:
    """One named, idempotent review-queue schema statement."""

    name: str
    kind: Literal["schema", "table", "index"]
    sql: str


REVIEW_QUEUE_MIGRATION_STATEMENTS: tuple[ReviewQueueMigrationStatement, ...] = (
    ReviewQueueMigrationStatement(
        name="create_core_schema",
        kind="schema",
        sql=f"CREATE SCHEMA IF NOT EXISTS {CORE_SCHEMA_NAME}",
    ),
    ReviewQueueMigrationStatement(
        name="create_review_queue_table",
        kind="table",
        sql=(
            f"CREATE TABLE IF NOT EXISTS {REVIEW_QUEUE_TABLE} ("
            "id BIGINT GENERATED ALWAYS AS IDENTITY, "
            "review_id UUID NOT NULL, "
            "source_system TEXT NOT NULL, "
            "source_batch_id TEXT, "
            "source_table TEXT NOT NULL, "
            "source_record_id BIGINT NOT NULL, "
            "reason_code TEXT NOT NULL, "
            "reason_detail TEXT, "
            "target_entity_type TEXT, "
            "candidate_matches JSONB NOT NULL DEFAULT '[]'::jsonb, "
            "confidence DOUBLE PRECISION, "
            "status TEXT NOT NULL DEFAULT 'pending', "
            "resolution JSONB NOT NULL DEFAULT '{}'::jsonb, "
            "resolved_by TEXT, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
            "resolved_at TIMESTAMPTZ, "
            "review_draft JSONB NOT NULL DEFAULT '{}'::jsonb, "
            "CONSTRAINT review_queue_pkey PRIMARY KEY (id), "
            "CONSTRAINT review_queue_review_id_key UNIQUE (review_id), "
            "CONSTRAINT review_queue_source_system_nonempty "
            "CHECK (btrim(source_system) <> ''), "
            "CONSTRAINT review_queue_source_table_format CHECK ("
            "source_table = 'staging.transportstyrelsen_raw' OR "
            "source_table ~ '^staging\\.tecdoc_[a-z][a-z0-9_]*$'), "
            "CONSTRAINT review_queue_source_record_id_positive "
            "CHECK (source_record_id >= 1), "
            "CONSTRAINT review_queue_reason_code_nonempty "
            "CHECK (btrim(reason_code) <> ''), "
            "CONSTRAINT review_queue_candidate_matches_array "
            "CHECK (jsonb_typeof(candidate_matches) = 'array'), "
            "CONSTRAINT review_queue_confidence_range "
            "CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)), "
            "CONSTRAINT review_queue_status_values "
            "CHECK (status IN ('pending', 'in_review', 'resolved', 'rejected')), "
            "CONSTRAINT review_queue_resolution_object "
            "CHECK (jsonb_typeof(resolution) = 'object'), "
            "CONSTRAINT review_queue_review_draft_object "
            "CHECK (jsonb_typeof(review_draft) = 'object'), "
            "CONSTRAINT review_queue_resolution_state CHECK ("
            "(status IN ('resolved', 'rejected') "
            "AND resolved_at IS NOT NULL AND btrim(resolved_by) <> '' "
            "AND resolution <> '{}'::jsonb) "
            "OR "
            "(status IN ('pending', 'in_review') "
            "AND resolved_at IS NULL AND resolved_by IS NULL "
            "AND resolution = '{}'::jsonb)), "
            "CONSTRAINT review_queue_timestamp_order "
            "CHECK (updated_at >= created_at)"
            ")"
        ),
    ),
    ReviewQueueMigrationStatement(
        name="add_review_queue_review_draft",
        kind="table",
        sql=(
            f"ALTER TABLE {REVIEW_QUEUE_TABLE} "
            "ADD COLUMN IF NOT EXISTS review_draft JSONB NOT NULL DEFAULT '{}'::jsonb; "
            f"ALTER TABLE {REVIEW_QUEUE_TABLE} "
            "DROP CONSTRAINT IF EXISTS review_queue_review_draft_object; "
            f"ALTER TABLE {REVIEW_QUEUE_TABLE} "
            "ADD CONSTRAINT review_queue_review_draft_object "
            "CHECK (jsonb_typeof(review_draft) = 'object')"
        ),
    ),
    ReviewQueueMigrationStatement(
        name="review_queue_status_created_at_index",
        kind="index",
        sql=(
            "CREATE INDEX IF NOT EXISTS review_queue_status_created_at_idx "
            f"ON {REVIEW_QUEUE_TABLE} (status, created_at, id)"
        ),
    ),
    ReviewQueueMigrationStatement(
        name="review_queue_source_record_index",
        kind="index",
        sql=(
            "CREATE INDEX IF NOT EXISTS review_queue_source_record_idx "
            f"ON {REVIEW_QUEUE_TABLE} (source_table, source_record_id)"
        ),
    ),
    ReviewQueueMigrationStatement(
        name="review_queue_source_batch_status_index",
        kind="index",
        sql=(
            "CREATE INDEX IF NOT EXISTS review_queue_source_batch_status_idx "
            f"ON {REVIEW_QUEUE_TABLE} (source_batch_id, status, updated_at, id)"
        ),
    ),
)


def run_review_queue_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply the review-queue migration and verify its durable contract."""

    try:
        with connection.cursor() as cursor:
            for statement in REVIEW_QUEUE_MIGRATION_STATEMENTS:
                cursor.execute(statement.sql)
        verify_review_queue_schema_contract(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return tuple(statement.name for statement in REVIEW_QUEUE_MIGRATION_STATEMENTS)


def verify_review_queue_schema_contract(connection: Connection) -> None:
    """Reject a pre-existing queue whose shape or indexes have drifted."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, data_type, is_nullable, column_default, "
            "is_identity, identity_generation "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position",
            (CORE_SCHEMA_NAME, "review_queue"),
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
            (CORE_SCHEMA_NAME, "review_queue"),
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
            (CORE_SCHEMA_NAME, "review_queue"),
        )
        constraints = {str(row[0]): (str(row[1]), str(row[2])) for row in cursor.fetchall()}
        cursor.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
            (CORE_SCHEMA_NAME, "review_queue"),
        )
        indexes = {str(row[0]): str(row[1]) for row in cursor.fetchall()}

    if columns != _REVIEW_QUEUE_COLUMN_CONTRACT:
        raise ReviewQueueSchemaContractError(
            f"{REVIEW_QUEUE_TABLE} column contract mismatch: "
            f"expected {_REVIEW_QUEUE_COLUMN_CONTRACT!r}, got {columns!r}"
        )
    if primary_key != _REVIEW_QUEUE_PRIMARY_KEY:
        raise ReviewQueueSchemaContractError(
            f"{REVIEW_QUEUE_TABLE} primary key mismatch: "
            f"expected {_REVIEW_QUEUE_PRIMARY_KEY!r}, got {primary_key!r}"
        )
    for name, (expected_type, fragments) in _REQUIRED_CONSTRAINTS.items():
        actual = constraints.get(name)
        if (
            actual is None
            or actual[0] != expected_type
            or any(fragment not in actual[1] for fragment in fragments)
        ):
            raise ReviewQueueSchemaContractError(
                f"{REVIEW_QUEUE_TABLE} constraint {name!r} mismatch: got {actual!r}"
            )
    for name, fragments in _REQUIRED_INDEX_FRAGMENTS.items():
        definition = indexes.get(name)
        if definition is None or any(fragment not in definition for fragment in fragments):
            raise ReviewQueueSchemaContractError(
                f"{REVIEW_QUEUE_TABLE} index {name!r} mismatch: got {definition!r}"
            )


def _classify_column_default(default: str | None) -> str | None:
    if default is None:
        return None
    if default == "'[]'::jsonb":
        return "empty_json_array"
    if default == "'{}'::jsonb":
        return "empty_json"
    if default == "'pending'::text":
        return "pending"
    if default == "now()":
        return "now"
    return default
