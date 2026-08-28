"""Durable manifests and checkpoints for full TS-to-TecDoc matching runs."""

from __future__ import annotations

from psycopg import Connection

MATCH_RUNS_TABLE = "core.match_runs"
MATCH_RUN_CHECKPOINTS_TABLE = "core.match_run_checkpoints"
MATCH_RUN_REASON_COUNTS_TABLE = "core.match_run_reason_counts"

MATCH_RUN_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("create_core_schema", "CREATE SCHEMA IF NOT EXISTS core"),
    (
        "create_match_runs_table",
        f"""
        CREATE TABLE IF NOT EXISTS {MATCH_RUNS_TABLE} (
            operation_id UUID PRIMARY KEY,
            source_system TEXT NOT NULL CHECK (btrim(source_system) <> ''),
            source_version TEXT NOT NULL CHECK (btrim(source_version) <> ''),
            source_batch_prefix TEXT NOT NULL CHECK (btrim(source_batch_prefix) <> ''),
            expected_source_rows BIGINT NOT NULL CHECK (expected_source_rows > 0),
            normalization_rule_version TEXT NOT NULL CHECK (
                btrim(normalization_rule_version) <> ''
            ),
            candidate_catalog_version TEXT NOT NULL CHECK (
                btrim(candidate_catalog_version) <> ''
            ),
            policy_version TEXT NOT NULL CHECK (btrim(policy_version) <> ''),
            code_revision TEXT NOT NULL CHECK (btrim(code_revision) <> ''),
            mode TEXT NOT NULL CHECK (mode IN ('dry_run', 'persist')),
            status TEXT NOT NULL DEFAULT 'running' CHECK (
                status IN ('running', 'completed', 'failed')
            ),
            last_batch_number INTEGER NOT NULL DEFAULT 0 CHECK (last_batch_number >= 0),
            last_source_record_id BIGINT NOT NULL DEFAULT 0 CHECK (
                last_source_record_id >= 0
            ),
            last_source_cursor TEXT NOT NULL DEFAULT '',
            processed BIGINT NOT NULL DEFAULT 0 CHECK (processed >= 0),
            resolved BIGINT NOT NULL DEFAULT 0 CHECK (resolved >= 0),
            provisional BIGINT NOT NULL DEFAULT 0 CHECK (provisional >= 0),
            review_required BIGINT NOT NULL DEFAULT 0 CHECK (review_required >= 0),
            unmatched BIGINT NOT NULL DEFAULT 0 CHECK (unmatched >= 0),
            hard_conflict BIGINT NOT NULL DEFAULT 0 CHECK (hard_conflict >= 0),
            normalization_review BIGINT NOT NULL DEFAULT 0 CHECK (
                normalization_review >= 0
            ),
            policy_excluded BIGINT NOT NULL DEFAULT 0 CHECK (policy_excluded >= 0),
            failed BIGINT NOT NULL DEFAULT 0 CHECK (failed >= 0),
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT match_runs_terminal_state CHECK (
                (status = 'running' AND finished_at IS NULL)
                OR (status IN ('completed', 'failed') AND finished_at IS NOT NULL)
            ),
            CONSTRAINT match_runs_processed_bound CHECK (
                processed <= expected_source_rows
            ),
            CONSTRAINT match_runs_accounting_balance CHECK (
                processed = resolved + provisional + review_required + unmatched
                    + hard_conflict + normalization_review + policy_excluded + failed
            )
        )
        """,
    ),
    # Added after the first audits ran. Existing rows predate the pin and are
    # marked explicitly rather than back-dated to a version they never used,
    # so a historical run can never be mistaken for an aligned one.
    (
        "add_match_runs_alignment_version",
        f"""
        ALTER TABLE {MATCH_RUNS_TABLE}
        ADD COLUMN IF NOT EXISTS alignment_version TEXT NOT NULL
            DEFAULT 'unpinned-legacy'
            CHECK (btrim(alignment_version) <> '')
        """,
    ),
    (
        "create_match_run_checkpoints_table",
        f"""
        CREATE TABLE IF NOT EXISTS {MATCH_RUN_CHECKPOINTS_TABLE} (
            operation_id UUID NOT NULL REFERENCES {MATCH_RUNS_TABLE}(operation_id),
            batch_number INTEGER NOT NULL CHECK (batch_number > 0),
            last_source_record_id BIGINT NOT NULL CHECK (last_source_record_id > 0),
            last_source_cursor TEXT NOT NULL CHECK (btrim(last_source_cursor) <> ''),
            processed BIGINT NOT NULL CHECK (processed > 0),
            counters JSONB NOT NULL CHECK (jsonb_typeof(counters) = 'object'),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (operation_id, batch_number),
            UNIQUE (operation_id, last_source_record_id)
            ,UNIQUE (operation_id, last_source_cursor)
        )
        """,
    ),
    (
        "add_match_runs_source_cursor",
        (
            f"ALTER TABLE {MATCH_RUNS_TABLE} ADD COLUMN IF NOT EXISTS "
            "last_source_cursor TEXT NOT NULL DEFAULT ''"
        ),
    ),
    (
        "add_match_run_checkpoints_source_cursor",
        (
            f"ALTER TABLE {MATCH_RUN_CHECKPOINTS_TABLE} ADD COLUMN IF NOT EXISTS "
            "last_source_cursor TEXT NOT NULL DEFAULT ''"
        ),
    ),
    (
        "create_match_run_checkpoint_cursor_index",
        (
            f"CREATE UNIQUE INDEX IF NOT EXISTS match_run_checkpoint_cursor_idx "
            f"ON {MATCH_RUN_CHECKPOINTS_TABLE} (operation_id, last_source_cursor)"
        ),
    ),
    (
        "create_match_runs_status_index",
        (
            f"CREATE INDEX IF NOT EXISTS match_runs_status_idx "
            f"ON {MATCH_RUNS_TABLE} (status, started_at, operation_id)"
        ),
    ),
    (
        "create_match_run_reason_counts_table",
        f"""
        CREATE TABLE IF NOT EXISTS {MATCH_RUN_REASON_COUNTS_TABLE} (
            operation_id UUID NOT NULL REFERENCES {MATCH_RUNS_TABLE}(operation_id),
            reason_code TEXT NOT NULL CHECK (btrim(reason_code) <> ''),
            occurrence_count BIGINT NOT NULL CHECK (occurrence_count >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (operation_id, reason_code)
        )
        """,
    ),
)


def run_match_run_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply the match-run schema atomically and idempotently."""

    try:
        with connection.cursor() as cursor:
            for _, statement in MATCH_RUN_MIGRATIONS:
                cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return tuple(name for name, _ in MATCH_RUN_MIGRATIONS)
