"""Durable schema for leverage-first chunk review of TS-to-TecDoc matching."""

from __future__ import annotations

from psycopg import Connection

MATCH_CHUNK_BUILDS_TABLE = "core.match_chunk_builds"
MATCH_CHUNKS_TABLE = "core.match_chunks"
MATCH_CHUNK_MEMBERS_TABLE = "core.match_chunk_members"
MATCH_CHUNK_SAMPLES_TABLE = "core.match_chunk_samples"
MATCH_CHUNK_PROPOSALS_TABLE = "core.match_chunk_proposals"
MATCH_RESOLUTION_RULES_TABLE = "core.match_resolution_rules"
MATCH_FIELD_RESOLUTIONS_TABLE = "core.match_field_resolutions"
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
    (
        "create_match_resolution_rules_table",
        f"""
        CREATE TABLE IF NOT EXISTS {MATCH_RESOLUTION_RULES_TABLE} (
            rule_id UUID PRIMARY KEY,
            build_id UUID NOT NULL REFERENCES {MATCH_CHUNK_BUILDS_TABLE}(build_id),
            source_field TEXT NOT NULL CHECK (btrim(source_field) <> ''),
            source_value TEXT NOT NULL CHECK (btrim(source_value) <> ''),
            target_field TEXT NOT NULL CHECK (btrim(target_field) <> ''),
            target_value TEXT NOT NULL CHECK (btrim(target_value) <> ''),
            conditions JSONB NOT NULL CHECK (
                jsonb_typeof(conditions) = 'array'
                AND jsonb_array_length(conditions) > 0
            ),
            author TEXT NOT NULL CHECK (btrim(author) <> ''),
            note TEXT,
            matched_rows BIGINT NOT NULL CHECK (matched_rows >= 0),
            would_resolve BIGINT NOT NULL CHECK (would_resolve >= 0),
            already_resolved BIGINT NOT NULL CHECK (already_resolved >= 0),
            status TEXT NOT NULL DEFAULT 'saved' CHECK (
                status IN ('saved', 'applied', 'retired')
            ),
            resolved_rows BIGINT NOT NULL DEFAULT 0 CHECK (resolved_rows >= 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            applied_at TIMESTAMPTZ,
            applied_by TEXT,
            retired_at TIMESTAMPTZ,
            retired_by TEXT,
            CONSTRAINT match_resolution_rules_applied_state CHECK (
                applied_at IS NULL
                OR (applied_by IS NOT NULL AND btrim(applied_by) <> '')
            ),
            CONSTRAINT match_resolution_rules_retired_state CHECK (
                (status <> 'retired' AND retired_at IS NULL)
                OR (
                    status = 'retired'
                    AND retired_at IS NOT NULL
                    AND retired_by IS NOT NULL
                    AND btrim(retired_by) <> ''
                )
            )
        )
        """,
    ),
    (
        "create_match_resolution_rules_population_index",
        (
            f"CREATE INDEX IF NOT EXISTS match_resolution_rules_population_idx "
            f"ON {MATCH_RESOLUTION_RULES_TABLE} "
            f"(build_id, source_field, source_value, created_at DESC)"
        ),
    ),
    (
        "protect_match_resolution_rule_definitions",
        f"""
        CREATE OR REPLACE FUNCTION core.reject_match_resolution_rule_change()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION
                    'resolution rule % is immutable; retire it instead of deleting',
                    OLD.rule_id;
            END IF;
            IF (
                NEW.build_id, NEW.source_field, NEW.source_value,
                NEW.target_field, NEW.target_value, NEW.conditions,
                NEW.author, NEW.created_at, NEW.matched_rows,
                NEW.would_resolve, NEW.already_resolved
            ) IS DISTINCT FROM (
                OLD.build_id, OLD.source_field, OLD.source_value,
                OLD.target_field, OLD.target_value, OLD.conditions,
                OLD.author, OLD.created_at, OLD.matched_rows,
                OLD.would_resolve, OLD.already_resolved
            ) THEN
                RAISE EXCEPTION
                    'resolution rule % is immutable; author a new rule instead',
                    OLD.rule_id;
            END IF;
            RETURN NEW;
        END;
        $$;
        DROP TRIGGER IF EXISTS match_resolution_rules_immutable
            ON {MATCH_RESOLUTION_RULES_TABLE};
        CREATE TRIGGER match_resolution_rules_immutable
            BEFORE UPDATE OR DELETE ON {MATCH_RESOLUTION_RULES_TABLE}
            FOR EACH ROW EXECUTE FUNCTION core.reject_match_resolution_rule_change()
        """,
    ),
    (
        "create_match_field_resolutions_table",
        f"""
        CREATE TABLE IF NOT EXISTS {MATCH_FIELD_RESOLUTIONS_TABLE} (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            rule_id UUID NOT NULL
                REFERENCES {MATCH_RESOLUTION_RULES_TABLE}(rule_id),
            build_id UUID NOT NULL REFERENCES {MATCH_CHUNK_BUILDS_TABLE}(build_id),
            source_record_id BIGINT NOT NULL CHECK (source_record_id > 0),
            target_field TEXT NOT NULL CHECK (btrim(target_field) <> ''),
            target_value TEXT NOT NULL CHECK (btrim(target_value) <> ''),
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            superseded_at TIMESTAMPTZ
        )
        """,
    ),
    (
        "create_match_field_resolutions_active_unique_index",
        (
            f"CREATE UNIQUE INDEX IF NOT EXISTS "
            f"match_field_resolutions_active_idx "
            f"ON {MATCH_FIELD_RESOLUTIONS_TABLE} "
            f"(source_record_id, target_field) WHERE superseded_at IS NULL"
        ),
    ),
    (
        "create_match_field_resolutions_rule_index",
        (
            f"CREATE INDEX IF NOT EXISTS match_field_resolutions_rule_idx "
            f"ON {MATCH_FIELD_RESOLUTIONS_TABLE} (rule_id)"
        ),
    ),
    (
        "create_match_field_resolutions_build_index",
        (
            f"CREATE INDEX IF NOT EXISTS match_field_resolutions_build_idx "
            f"ON {MATCH_FIELD_RESOLUTIONS_TABLE} (build_id, target_field) "
            f"WHERE superseded_at IS NULL"
        ),
    ),
    (
        "protect_match_field_resolutions",
        f"""
        CREATE OR REPLACE FUNCTION core.reject_match_field_resolution_change()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION
                    'field resolution % may not be deleted; retire its rule',
                    OLD.id;
            END IF;
            IF OLD.superseded_at IS NOT NULL THEN
                RAISE EXCEPTION
                    'resolution % is already superseded and may not change',
                    OLD.id;
            END IF;
            IF (
                NEW.rule_id, NEW.build_id, NEW.source_record_id,
                NEW.target_field, NEW.target_value, NEW.applied_at
            ) IS DISTINCT FROM (
                OLD.rule_id, OLD.build_id, OLD.source_record_id,
                OLD.target_field, OLD.target_value, OLD.applied_at
            ) THEN
                RAISE EXCEPTION
                    'field resolution % may only be superseded', OLD.id;
            END IF;
            RETURN NEW;
        END;
        $$;
        DROP TRIGGER IF EXISTS match_field_resolutions_append_only
            ON {MATCH_FIELD_RESOLUTIONS_TABLE};
        CREATE TRIGGER match_field_resolutions_append_only
            BEFORE UPDATE OR DELETE ON {MATCH_FIELD_RESOLUTIONS_TABLE}
            FOR EACH ROW
            EXECUTE FUNCTION core.reject_match_field_resolution_change()
        """,
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
