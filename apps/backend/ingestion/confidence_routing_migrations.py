"""Durable, immutable persistence for Stage 2 confidence-routing decisions."""

from __future__ import annotations

from psycopg import Connection

MATCH_ROUTING_TABLE = "core.match_routing_decisions"
MATCH_DECISION_HEAD_TABLE = "core.match_decision_heads"
MATCH_DECISION_SUPERSESSION_TABLE = "core.match_decision_supersessions"

CONFIDENCE_ROUTING_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("create_core_schema", "CREATE SCHEMA IF NOT EXISTS core"),
    (
        "create_match_routing_decisions_table",
        f"""
        CREATE TABLE IF NOT EXISTS {MATCH_ROUTING_TABLE} (
            id BIGSERIAL PRIMARY KEY,
            decision_id UUID NOT NULL UNIQUE,
            source_system TEXT NOT NULL CHECK (btrim(source_system) <> ''),
            source_batch_id TEXT NOT NULL CHECK (btrim(source_batch_id) <> ''),
            source_table TEXT NOT NULL CHECK (
                source_table ~ '^staging\\.[a-z][a-z0-9_]*$'
            ),
            source_record_id BIGINT NOT NULL CHECK (source_record_id > 0),
            candidate_catalog_version TEXT NOT NULL CHECK (
                btrim(candidate_catalog_version) <> ''
            ),
            policy_version TEXT NOT NULL CHECK (btrim(policy_version) <> ''),
            route TEXT NOT NULL CHECK (
                route IN ('resolved', 'provisional', 'review_required')
            ),
            confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            selected_candidate_reference TEXT,
            decision_payload JSONB NOT NULL CHECK (
                jsonb_typeof(decision_payload) = 'object'
                AND decision_payload ? 'decision_trace'
                AND decision_payload ? 'alternative_candidates'
                AND decision_payload ? 'route'
                AND decision_payload ? 'policy_version'
                AND jsonb_typeof(decision_payload->'decision_trace') = 'array'
                AND jsonb_typeof(decision_payload->'alternative_candidates') = 'array'
                AND decision_payload->>'route' = route
                AND decision_payload->>'policy_version' = policy_version
            ),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT match_routing_selected_candidate_check CHECK (
                (route = 'review_required' AND selected_candidate_reference IS NULL)
                OR
                (
                    route IN ('resolved', 'provisional')
                    AND selected_candidate_reference IS NOT NULL
                    AND btrim(selected_candidate_reference) <> ''
                )
            ),
            CONSTRAINT match_routing_source_version_key UNIQUE (
                source_system,
                source_batch_id,
                source_table,
                source_record_id,
                candidate_catalog_version,
                policy_version
            )
        )
        """,
    ),
    (
        "create_match_routing_batch_route_index",
        (
            f"CREATE INDEX IF NOT EXISTS match_routing_batch_route_idx "
            f"ON {MATCH_ROUTING_TABLE} (source_batch_id, route)"
        ),
    ),
    (
        "create_match_routing_source_index",
        (
            f"CREATE INDEX IF NOT EXISTS match_routing_source_idx "
            f"ON {MATCH_ROUTING_TABLE} (source_table, source_record_id)"
        ),
    ),
    (
        "create_match_decision_heads_table",
        f"""
        CREATE TABLE IF NOT EXISTS {MATCH_DECISION_HEAD_TABLE} (
            source_system TEXT NOT NULL CHECK (btrim(source_system) <> ''),
            source_version TEXT NOT NULL CHECK (btrim(source_version) <> ''),
            source_entity_key TEXT NOT NULL CHECK (btrim(source_entity_key) <> ''),
            decision_id UUID NOT NULL UNIQUE REFERENCES {MATCH_ROUTING_TABLE}(decision_id),
            selected_candidate_reference TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (source_system, source_version, source_entity_key)
        )
        """,
    ),
    (
        "create_match_decision_supersessions_table",
        f"""
        CREATE TABLE IF NOT EXISTS {MATCH_DECISION_SUPERSESSION_TABLE} (
            predecessor_decision_id UUID PRIMARY KEY
                REFERENCES {MATCH_ROUTING_TABLE}(decision_id),
            successor_decision_id UUID NOT NULL
                REFERENCES {MATCH_ROUTING_TABLE}(decision_id),
            reason TEXT NOT NULL CHECK (btrim(reason) <> ''),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (predecessor_decision_id <> successor_decision_id)
        )
        """,
    ),
)


def run_confidence_routing_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply the confidence-routing schema atomically and idempotently."""

    try:
        with connection.cursor() as cursor:
            for _, statement in CONFIDENCE_ROUTING_MIGRATIONS:
                cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return tuple(name for name, _ in CONFIDENCE_ROUTING_MIGRATIONS)
