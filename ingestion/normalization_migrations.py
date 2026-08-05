"""Durable, idempotent storage for Transportstyrelsen normalization results."""

from __future__ import annotations

from psycopg import Connection

NORMALIZATION_RESULTS_TABLE = "core.normalization_results"
TRANSLATION_RULE_DRAFTS_TABLE = "core.translation_rule_drafts"
TRANSLATION_RULE_VERSIONS_TABLE = "core.translation_rule_versions"

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
            pipeline_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('resolved', 'provisional', 'review_required', 'failed')
            ),
            normalized_payload JSONB NOT NULL,
            applied_rule_ids TEXT[] NOT NULL DEFAULT '{{}}',
            review_reasons TEXT[] NOT NULL DEFAULT '{{}}',
            confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT normalization_results_source_version_key UNIQUE (
                source_table,
                source_record_id,
                mapping_version,
                rule_version,
                pipeline_version
            )
        )
        """,
    ),
    (
        "add_normalization_pipeline_version",
        f"""
        ALTER TABLE {NORMALIZATION_RESULTS_TABLE}
        ADD COLUMN IF NOT EXISTS pipeline_version TEXT NOT NULL
        DEFAULT 'normalization-pipeline-v0'
        """,
    ),
    (
        "drop_legacy_normalization_source_version_constraint",
        f"""
        DO $$
        DECLARE legacy_constraint TEXT;
        BEGIN
            SELECT constraint_name
            INTO legacy_constraint
            FROM information_schema.table_constraints
            WHERE table_schema = 'core'
              AND table_name = 'normalization_results'
              AND constraint_type = 'UNIQUE'
              AND constraint_name <> 'normalization_results_source_version_key'
              AND constraint_name IN (
                  SELECT tc.constraint_name
                  FROM information_schema.table_constraints AS tc
                  JOIN information_schema.constraint_column_usage AS ccu
                    ON ccu.constraint_schema = tc.constraint_schema
                   AND ccu.constraint_name = tc.constraint_name
                  WHERE tc.table_schema = 'core'
                    AND tc.table_name = 'normalization_results'
                    AND tc.constraint_type = 'UNIQUE'
                  GROUP BY tc.constraint_name
                  HAVING array_agg(
                      ccu.column_name::TEXT
                      ORDER BY ccu.column_name::TEXT
                  ) =
                    ARRAY[
                        'mapping_version',
                        'rule_version',
                        'source_record_id',
                        'source_table'
                    ]::TEXT[]
              )
            LIMIT 1;
            IF legacy_constraint IS NOT NULL THEN
                EXECUTE format(
                    'ALTER TABLE {NORMALIZATION_RESULTS_TABLE} DROP CONSTRAINT %I',
                    legacy_constraint
                );
            END IF;
        END
        $$;
        """,
    ),
    (
        "create_normalization_source_version_constraint",
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.table_constraints
                WHERE table_schema = 'core'
                  AND table_name = 'normalization_results'
                  AND constraint_name = 'normalization_results_source_version_key'
            ) THEN
                ALTER TABLE {NORMALIZATION_RESULTS_TABLE}
                ADD CONSTRAINT normalization_results_source_version_key UNIQUE (
                    source_table,
                    source_record_id,
                    mapping_version,
                    rule_version,
                    pipeline_version
                );
            END IF;
        END
        $$;
        """,
    ),
    (
        "create_normalization_results_batch_status_index",
        f"CREATE INDEX IF NOT EXISTS normalization_results_batch_status_idx ON {NORMALIZATION_RESULTS_TABLE} (source_batch_id, status)",
    ),
    (
        "create_normalization_results_source_index",
        f"CREATE INDEX IF NOT EXISTS normalization_results_source_idx ON {NORMALIZATION_RESULTS_TABLE} (source_table, source_record_id)",
    ),
    (
        "create_translation_rule_drafts_table",
        f"""
        CREATE TABLE IF NOT EXISTS {TRANSLATION_RULE_DRAFTS_TABLE} (
            rule_id TEXT PRIMARY KEY,
            canonical_value TEXT,
            decision TEXT NOT NULL CHECK (decision IN ('accepted', 'proposed')),
            display_value TEXT,
            change_note TEXT NOT NULL CHECK (length(trim(change_note)) >= 5),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    ),
    (
        "create_translation_rule_versions_table",
        f"""
        CREATE TABLE IF NOT EXISTS {TRANSLATION_RULE_VERSIONS_TABLE} (
            version TEXT PRIMARY KEY,
            base_rule_version TEXT NOT NULL,
            overrides JSONB NOT NULL CHECK (jsonb_typeof(overrides) = 'object'),
            activation_note TEXT NOT NULL CHECK (length(trim(activation_note)) >= 5),
            activated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    ),
    (
        "protect_translation_rule_versions",
        f"""
        CREATE OR REPLACE FUNCTION core.reject_translation_rule_version_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'translation rule versions are immutable';
        END
        $$;
        DROP TRIGGER IF EXISTS translation_rule_versions_immutable
            ON {TRANSLATION_RULE_VERSIONS_TABLE};
        CREATE TRIGGER translation_rule_versions_immutable
        BEFORE UPDATE OR DELETE ON {TRANSLATION_RULE_VERSIONS_TABLE}
        FOR EACH ROW EXECUTE FUNCTION core.reject_translation_rule_version_mutation()
        """,
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
