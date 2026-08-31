"""Durable schema for leverage-first chunk review of TS-to-TecDoc matching."""

from __future__ import annotations

from psycopg import Connection

MATCH_CHUNK_BUILDS_TABLE = "core.match_chunk_builds"
MATCH_CHUNKS_TABLE = "core.match_chunks"
MATCH_CHUNK_MEMBERS_TABLE = "core.match_chunk_members"
MATCH_CHUNK_SAMPLES_TABLE = "core.match_chunk_samples"
MATCH_CHUNK_PROPOSALS_TABLE = "core.match_chunk_proposals"
OEM_VIN_EVIDENCE_TABLE = "staging.oem_vin_evidence"

MATCH_CHUNK_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("create_core_schema", "CREATE SCHEMA IF NOT EXISTS core"),
    ("create_staging_schema", "CREATE SCHEMA IF NOT EXISTS staging"),
    (
        "create_match_chunk_builds_table",
        f"""
        CREATE TABLE IF NOT EXISTS {MATCH_CHUNK_BUILDS_TABLE} (
            build_id UUID PRIMARY KEY,
            source_batch_id TEXT NOT NULL CHECK (btrim(source_batch_id) <> ''),
            signature_version TEXT NOT NULL CHECK (btrim(signature_version) <> ''),
            status_filter TEXT[] NOT NULL CHECK (cardinality(status_filter) > 0),
            status TEXT NOT NULL DEFAULT 'running' CHECK (
                status IN ('running', 'completed', 'failed')
            ),
            row_count BIGINT NOT NULL DEFAULT 0 CHECK (row_count >= 0),
            chunk_count BIGINT NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ,
            CONSTRAINT match_chunk_builds_terminal_state CHECK (
                (status = 'running' AND finished_at IS NULL)
                OR (status IN ('completed', 'failed') AND finished_at IS NOT NULL)
            )
        )
        """,
    ),
    (
        "create_match_chunks_table",
        f"""
        CREATE TABLE IF NOT EXISTS {MATCH_CHUNKS_TABLE} (
            chunk_id UUID PRIMARY KEY,
            build_id UUID NOT NULL REFERENCES {MATCH_CHUNK_BUILDS_TABLE}(build_id),
            signature_key TEXT NOT NULL CHECK (signature_key ~ '^[0-9a-f]{{64}}$'),
            signature JSONB NOT NULL CHECK (jsonb_typeof(signature) = 'object'),
            member_count BIGINT NOT NULL DEFAULT 0 CHECK (member_count >= 0),
            reason_profile JSONB NOT NULL DEFAULT '{{}}' CHECK (
                jsonb_typeof(reason_profile) = 'object'
            ),
            status TEXT NOT NULL DEFAULT 'open' CHECK (
                status IN ('open', 'proposed', 'approved', 'rejected', 'split')
            ),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (build_id, signature_key)
        )
        """,
    ),
    (
        "create_match_chunks_build_leverage_index",
        (
            f"CREATE INDEX IF NOT EXISTS match_chunks_build_leverage_idx "
            f"ON {MATCH_CHUNKS_TABLE} (build_id, member_count DESC, chunk_id)"
        ),
    ),
    (
        "create_match_chunk_members_table",
        f"""
        CREATE TABLE IF NOT EXISTS {MATCH_CHUNK_MEMBERS_TABLE} (
            chunk_id UUID NOT NULL REFERENCES {MATCH_CHUNKS_TABLE}(chunk_id),
            source_record_id BIGINT NOT NULL CHECK (source_record_id > 0),
            source_batch_id TEXT NOT NULL CHECK (btrim(source_batch_id) <> ''),
            normalization_status TEXT NOT NULL CHECK (
                btrim(normalization_status) <> ''
            ),
            review_reasons TEXT[] NOT NULL DEFAULT '{{}}',
            PRIMARY KEY (chunk_id, source_record_id)
        )
        """,
    ),
    (
        "create_oem_vin_evidence_table",
        f"""
        CREATE TABLE IF NOT EXISTS {OEM_VIN_EVIDENCE_TABLE} (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            request_id UUID NOT NULL UNIQUE,
            provider TEXT NOT NULL CHECK (btrim(provider) <> ''),
            vin TEXT NOT NULL CHECK (btrim(vin) <> ''),
            dataset_version TEXT NOT NULL DEFAULT 'unversioned' CHECK (
                btrim(dataset_version) <> ''
            ),
            response_payload JSONB NOT NULL CHECK (
                jsonb_typeof(response_payload) = 'object'
            ),
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (provider, vin, dataset_version)
        )
        """,
    ),
    (
        "create_oem_vin_evidence_immutability_trigger",
        f"""
        CREATE OR REPLACE FUNCTION staging.reject_oem_vin_evidence_change()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'staging.oem_vin_evidence is append-only; row % may not be %d',
                OLD.id, lower(TG_OP);
        END;
        $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS oem_vin_evidence_immutable
            ON {OEM_VIN_EVIDENCE_TABLE};
        CREATE TRIGGER oem_vin_evidence_immutable
            BEFORE UPDATE OR DELETE ON {OEM_VIN_EVIDENCE_TABLE}
            FOR EACH ROW EXECUTE FUNCTION staging.reject_oem_vin_evidence_change()
        """,
    ),
    (
        "create_match_chunk_samples_table",
        f"""
        CREATE TABLE IF NOT EXISTS {MATCH_CHUNK_SAMPLES_TABLE} (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            chunk_id UUID NOT NULL REFERENCES {MATCH_CHUNKS_TABLE}(chunk_id),
            evidence_id BIGINT NOT NULL REFERENCES {OEM_VIN_EVIDENCE_TABLE}(id),
            source_record_id BIGINT NOT NULL CHECK (source_record_id > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (chunk_id, evidence_id),
            UNIQUE (chunk_id, source_record_id)
        )
        """,
    ),
    (
        "create_match_chunk_proposals_table",
        f"""
        CREATE TABLE IF NOT EXISTS {MATCH_CHUNK_PROPOSALS_TABLE} (
            proposal_id UUID PRIMARY KEY,
            chunk_id UUID NOT NULL REFERENCES {MATCH_CHUNKS_TABLE}(chunk_id),
            proposal_source TEXT NOT NULL CHECK (
                proposal_source IN ('heuristic', 'agent', 'human')
            ),
            adjudicator_version TEXT NOT NULL CHECK (
                btrim(adjudicator_version) <> ''
            ),
            recommendation TEXT NOT NULL CHECK (
                recommendation IN (
                    'assign_ktype',
                    'split_chunk',
                    'needs_more_evidence',
                    'no_safe_match'
                )
            ),
            target_ktype_reference TEXT,
            confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            evidence JSONB NOT NULL CHECK (jsonb_typeof(evidence) = 'object'),
            reasoning TEXT NOT NULL CHECK (btrim(reasoning) <> ''),
            status TEXT NOT NULL DEFAULT 'proposed' CHECK (
                status IN ('proposed', 'approved', 'rejected')
            ),
            reviewed_by TEXT,
            review_note TEXT,
            reviewed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT match_chunk_proposals_target_required CHECK (
                recommendation <> 'assign_ktype'
                OR (
                    target_ktype_reference IS NOT NULL
                    AND btrim(target_ktype_reference) <> ''
                )
            ),
            CONSTRAINT match_chunk_proposals_review_state CHECK (
                (status = 'proposed' AND reviewed_at IS NULL AND reviewed_by IS NULL)
                OR (
                    status IN ('approved', 'rejected')
                    AND reviewed_at IS NOT NULL
                    AND reviewed_by IS NOT NULL
                    AND btrim(reviewed_by) <> ''
                )
            )
        )
        """,
    ),
    (
        "create_match_chunk_proposals_chunk_index",
        (
            f"CREATE INDEX IF NOT EXISTS match_chunk_proposals_chunk_idx "
            f"ON {MATCH_CHUNK_PROPOSALS_TABLE} (chunk_id, created_at)"
        ),
    ),
)


def run_match_chunk_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply the chunk-review schema atomically and idempotently."""

    try:
        with connection.cursor() as cursor:
            for _, statement in MATCH_CHUNK_MIGRATIONS:
                cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return tuple(name for name, _ in MATCH_CHUNK_MIGRATIONS)
