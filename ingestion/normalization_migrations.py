"""Durable, idempotent storage for Transportstyrelsen normalization results."""

from __future__ import annotations

from psycopg import Connection

NORMALIZATION_RESULTS_TABLE = "core.normalization_results"

NORMALIZATION_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("create_core_schema", "CREATE SCHEMA IF NOT EXISTS core"),
    (
        "create_normalization_results_table",
        f"""
        CREATE TABLE IF NOT EXISTS {NORMALIZATION_RESULTS_TABLE} (
            id BIGSERIAL PRIMARY KEY,
            normalization_id UUID NOT NULL UNIQUE,
            source_system TEXT NOT NULL,
            source_batch_id TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_record_id BIGINT NOT NULL,
            mapping_version TEXT NOT NULL,
            rule_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('resolved', 'provisional', 'review_required', 'failed')
            ),
            normalized_payload JSONB NOT NULL,
            applied_rule_ids TEXT[] NOT NULL DEFAULT '{{}}',
            review_reasons TEXT[] NOT NULL DEFAULT '{{}}',
            confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (source_table, source_record_id, mapping_version, rule_version)
        )
        """,
    ),
    (
        "create_normalization_results_batch_status_index",
        f"CREATE INDEX IF NOT EXISTS normalization_results_batch_status_idx "
        f"ON {NORMALIZATION_RESULTS_TABLE} (source_batch_id, status)",
    ),
    (
        "create_normalization_results_source_index",
        f"CREATE INDEX IF NOT EXISTS normalization_results_source_idx "
        f"ON {NORMALIZATION_RESULTS_TABLE} (source_table, source_record_id)",
    ),
)


def run_normalization_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply normalization-result migrations atomically."""

    try:
        with connection.cursor() as cursor:
            for _, statement in NORMALIZATION_MIGRATIONS:
                cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return tuple(name for name, _ in NORMALIZATION_MIGRATIONS)
