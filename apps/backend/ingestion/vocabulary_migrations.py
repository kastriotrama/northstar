"""Reviewed, versioned vocabulary alignment between source systems (draft).

Transportstyrelsen and TecDoc are each normalized into our own token set by
independent code paths, and nothing reconciles the two. They agree on `petrol`
and `diesel` and disagree elsewhere -- TecDoc says `electric` where TS says
`electricity`, so no electric vehicle can ever match on fuel.

This schema makes the alignment reviewable data rather than two hardcoded
dictionaries. Postgres stays the system of record for what is approved; the
graph materialises approved rows (see `ingestion.vocabulary_alignment`).

Two relations are deliberately distinguished:

`equivalent`   the two terms denote the same concept. Safe to treat as a match.
`compatible`   the source term is broader than the target, so the pair must not
               be scored as a conflict -- but must not be scored as agreement
               either. TS has no `suv` class and files SUVs under `estate`;
               that makes `estate` compatible with `suv`, not equal to it.

Compatible rows carry the observed support count so a reviewer can see the
evidence behind a proposed alignment instead of taking it on faith.
"""

from __future__ import annotations

from psycopg import Connection

VOCABULARY_ALIGNMENT_TABLE = "core.vocabulary_alignments"
VOCABULARY_ALIGNMENT_VERSION_TABLE = "core.vocabulary_alignment_versions"

VOCABULARIES = ("fuel", "bodywork", "drive")
RELATIONS = ("equivalent", "compatible")

VOCABULARY_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("create_core_schema", "CREATE SCHEMA IF NOT EXISTS core"),
    (
        "create_vocabulary_alignment_versions_table",
        f"""
        CREATE TABLE IF NOT EXISTS {VOCABULARY_ALIGNMENT_VERSION_TABLE} (
            alignment_version TEXT PRIMARY KEY
                CHECK (btrim(alignment_version) <> ''),
            activation_note TEXT NOT NULL CHECK (btrim(activation_note) <> ''),
            activated_by TEXT NOT NULL CHECK (btrim(activated_by) <> ''),
            activated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    ),
    (
        "add_vocabulary_version_seal",
        (f"ALTER TABLE {VOCABULARY_ALIGNMENT_VERSION_TABLE} "
         "ADD COLUMN IF NOT EXISTS sealed BOOLEAN NOT NULL DEFAULT TRUE"),
    ),
    (
        "create_vocabulary_alignments_table",
        f"""
        CREATE TABLE IF NOT EXISTS {VOCABULARY_ALIGNMENT_TABLE} (
            id BIGSERIAL PRIMARY KEY,
            alignment_version TEXT NOT NULL
                REFERENCES {VOCABULARY_ALIGNMENT_VERSION_TABLE}(alignment_version),
            vocabulary TEXT NOT NULL
                CHECK (vocabulary IN ('fuel', 'bodywork', 'drive')),
            source_system TEXT NOT NULL
                CHECK (source_system IN ('tecdoc', 'transportstyrelsen')),
            source_term TEXT NOT NULL CHECK (btrim(source_term) <> ''),
            canonical_term TEXT NOT NULL CHECK (btrim(canonical_term) <> ''),
            relation TEXT NOT NULL CHECK (relation IN ('equivalent', 'compatible')),
            support INTEGER CHECK (support IS NULL OR support >= 0),
            evidence_note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- One ruling per term per vocabulary per version. A term cannot be
            -- both equivalent to one concept and compatible with it.
            CONSTRAINT vocabulary_alignment_unique_term UNIQUE (
                alignment_version, vocabulary, source_system,
                source_term, canonical_term
            ),
            -- A compatible row asserts a broader-than relationship learned from
            -- data, so it must say how much data. An equivalence is a naming
            -- fact and needs none.
            CONSTRAINT vocabulary_alignment_support_check CHECK (
                (relation = 'compatible' AND support IS NOT NULL)
                OR relation = 'equivalent'
            )
        )
        """,
    ),
    (
        "create_vocabulary_alignment_lookup_index",
        (
            f"CREATE INDEX IF NOT EXISTS vocabulary_alignment_lookup_idx "
            f"ON {VOCABULARY_ALIGNMENT_TABLE} "
            "(alignment_version, vocabulary, source_system, source_term)"
        ),
    ),
    # An activated alignment set is immutable, matching the guarantee already
    # given by core.translation_rule_versions. Corrections ship as a new
    # version so every match run stays reproducible against its pinned set.
    (
        "create_vocabulary_alignment_immutability_function",
        """
        CREATE OR REPLACE FUNCTION core.reject_vocabulary_alignment_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'vocabulary alignments are immutable; activate a new version';
        END;
        $$ LANGUAGE plpgsql
        """,
    ),
    (
        "create_vocabulary_alignment_immutability_trigger",
        f"""
        CREATE OR REPLACE TRIGGER vocabulary_alignments_immutable
        BEFORE UPDATE OR DELETE ON {VOCABULARY_ALIGNMENT_TABLE}
        FOR EACH ROW EXECUTE FUNCTION core.reject_vocabulary_alignment_mutation()
        """,
    ),
    (
        "create_vocabulary_alignment_seal_function",
        f"""
        CREATE OR REPLACE FUNCTION core.guard_vocabulary_alignment_seal()
        RETURNS TRIGGER AS $$
        DECLARE is_sealed BOOLEAN;
        BEGIN
            SELECT sealed INTO is_sealed FROM {VOCABULARY_ALIGNMENT_VERSION_TABLE}
                WHERE alignment_version = NEW.alignment_version FOR SHARE;
            IF is_sealed IS DISTINCT FROM FALSE THEN
                RAISE EXCEPTION 'cannot add rows to an activated vocabulary version';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """,
    ),
    (
        "create_vocabulary_alignment_insert_guard",
        f"""
        CREATE OR REPLACE TRIGGER vocabulary_alignment_insert_guard
        BEFORE INSERT ON {VOCABULARY_ALIGNMENT_TABLE}
        FOR EACH ROW EXECUTE FUNCTION core.guard_vocabulary_alignment_seal()
        """,
    ),
    (
        "create_vocabulary_version_guard_function",
        """
        CREATE OR REPLACE FUNCTION core.guard_vocabulary_version()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND OLD.sealed = FALSE AND NEW.sealed = TRUE
               AND (to_jsonb(OLD) - 'sealed') = (to_jsonb(NEW) - 'sealed') THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'vocabulary versions are immutable except for initial sealing';
        END;
        $$ LANGUAGE plpgsql
        """,
    ),
    (
        "create_vocabulary_version_guard",
        f"""
        CREATE OR REPLACE TRIGGER vocabulary_version_guard
        BEFORE UPDATE OR DELETE ON {VOCABULARY_ALIGNMENT_VERSION_TABLE}
        FOR EACH ROW EXECUTE FUNCTION core.guard_vocabulary_version()
        """,
    ),
    *tuple(
        (
            f"block_{table.split('.')[-1]}_truncate",
            (f"CREATE OR REPLACE TRIGGER vocabulary_no_truncate BEFORE TRUNCATE ON {table} "
             "FOR EACH STATEMENT EXECUTE FUNCTION core.reject_vocabulary_alignment_mutation()"),
        )
        for table in (VOCABULARY_ALIGNMENT_TABLE, VOCABULARY_ALIGNMENT_VERSION_TABLE)
    ),
)


def run_vocabulary_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply the vocabulary alignment schema atomically and idempotently."""

    applied: list[str] = []
    try:
        with connection.cursor() as cursor:
            for name, statement in VOCABULARY_MIGRATIONS:
                cursor.execute(statement)
                applied.append(name)
    except Exception:
        connection.rollback()
        raise
    connection.commit()
    return tuple(applied)
