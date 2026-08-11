"""Durable TecDoc batch, identity, and canonical-candidate storage."""

from __future__ import annotations

from psycopg import Connection

TECDOC_MIGRATION_STATEMENTS: tuple[tuple[str, str], ...] = (
    ("create_core_schema", "CREATE SCHEMA IF NOT EXISTS core"),
    (
        "create_tecdoc_source_batches",
        "CREATE TABLE IF NOT EXISTS core.tecdoc_source_batches ("
        "batch_id TEXT PRIMARY KEY, source_version TEXT NOT NULL, format_version TEXT NOT NULL, "
        "license_reference TEXT NOT NULL, source_path TEXT NOT NULL, source_checksum TEXT NOT NULL, "
        "source_row_count INTEGER NOT NULL CHECK (source_row_count >= 0), "
        "status TEXT NOT NULL CHECK (status IN ('loading', 'completed', 'failed')), "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ)"
    ),
    (
        "create_tecdoc_identity_registry",
        "CREATE TABLE IF NOT EXISTS core.tecdoc_identity_registry ("
        "entity_type TEXT NOT NULL, source_key TEXT NOT NULL, node_id TEXT NOT NULL UNIQUE, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (entity_type, source_key))"
    ),
    (
        "create_tecdoc_canonical_candidates",
        "CREATE TABLE IF NOT EXISTS core.tecdoc_canonical_candidates ("
        "batch_id TEXT NOT NULL REFERENCES core.tecdoc_source_batches(batch_id), "
        "entity_type TEXT NOT NULL, source_key TEXT NOT NULL, node_id TEXT NOT NULL, "
        "attributes JSONB NOT NULL, source_row_refs TEXT[] NOT NULL DEFAULT '{}', "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "PRIMARY KEY (batch_id, entity_type, source_key))"
    ),
    (
        "create_tecdoc_candidate_relationships",
        "CREATE TABLE IF NOT EXISTS core.tecdoc_candidate_relationships ("
        "batch_id TEXT NOT NULL REFERENCES core.tecdoc_source_batches(batch_id), "
        "relationship_type TEXT NOT NULL CHECK (relationship_type IN ('USES_ENGINE')), "
        "source_assertion_key TEXT NOT NULL, from_source_key TEXT NOT NULL, "
        "from_node_id TEXT NOT NULL, to_source_key TEXT NOT NULL, to_node_id TEXT NOT NULL, "
        "attributes JSONB NOT NULL DEFAULT '{}', evidence JSONB NOT NULL DEFAULT '{}', "
        "status TEXT NOT NULL DEFAULT 'candidate' "
        "CHECK (status IN ('candidate','resolved','rejected')), "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "PRIMARY KEY (batch_id, relationship_type, source_assertion_key))"
    ),
)


def run_tecdoc_migrations(connection: Connection) -> tuple[str, ...]:
    try:
        with connection.cursor() as cursor:
            for _, statement in TECDOC_MIGRATION_STATEMENTS:
                cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return tuple(name for name, _ in TECDOC_MIGRATION_STATEMENTS)
