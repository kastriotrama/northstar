"""Durable, immutable rule content keyed to a version.

`core.translation_rule_versions` records only a pointer -- a
`base_rule_version` string naming a rule set defined in Python -- plus an
`overrides` object that can modify a rule already present in that catalog. It
cannot introduce one, so every new rule has had to ship as code.

That makes a version string a promise the database cannot keep. Content behind
`ts-translation-v7` grew from 231 rules to 1,261 across three commits while
every activated row kept pointing at the same name, so a pinned run no longer
means what it meant when it was pinned.

Storing the content itself removes the ambiguity: a version is the set of rows
carrying it, activated once and immutable after. Corrections ship as a new
version, exactly as `core.vocabulary_alignments` already requires.
"""

from __future__ import annotations

from psycopg import Connection

RULE_DEFINITIONS_TABLE = "core.translation_rule_definitions"
RULE_DEFINITION_VERSIONS_TABLE = "core.translation_rule_definition_versions"

RULE_DEFINITION_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("create_core_schema", "CREATE SCHEMA IF NOT EXISTS core"),
    (
        "create_rule_definition_versions_table",
        f"""
        CREATE TABLE IF NOT EXISTS {RULE_DEFINITION_VERSIONS_TABLE} (
            rule_version TEXT PRIMARY KEY CHECK (btrim(rule_version) <> ''),
            source_note TEXT NOT NULL CHECK (btrim(source_note) <> ''),
            imported_by TEXT NOT NULL CHECK (btrim(imported_by) <> ''),
            rule_count INTEGER NOT NULL CHECK (rule_count > 0),
            -- Content hash of the imported rules. `rule_count` alone cannot
            -- tell a changed rule from an unchanged one, so a re-import of
            -- edited content under a sealed version would read as a no-op.
            content_fingerprint TEXT,
            -- Sealed on the import that populates the version, so a partially
            -- written version can finish but a complete one can never grow.
            sealed BOOLEAN NOT NULL DEFAULT FALSE,
            imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    ),
    (
        "add_rule_definition_version_fingerprint",
        (
            f"ALTER TABLE {RULE_DEFINITION_VERSIONS_TABLE} "
            "ADD COLUMN IF NOT EXISTS content_fingerprint TEXT"
        ),
    ),
    (
        "create_rule_definitions_table",
        f"""
        CREATE TABLE IF NOT EXISTS {RULE_DEFINITIONS_TABLE} (
            rule_version TEXT NOT NULL
                REFERENCES {RULE_DEFINITION_VERSIONS_TABLE}(rule_version),
            rule_id TEXT NOT NULL CHECK (btrim(rule_id) <> ''),
            area TEXT NOT NULL CHECK (btrim(area) <> ''),
            source_fields TEXT[] NOT NULL CHECK (cardinality(source_fields) > 0),
            source_terms TEXT[] NOT NULL CHECK (cardinality(source_terms) > 0),
            canonical_field TEXT NOT NULL CHECK (btrim(canonical_field) <> ''),
            canonical_value TEXT,
            decision TEXT NOT NULL CHECK (decision IN ('accepted', 'proposed')),
            display_value TEXT,
            vehicle_scopes TEXT[] NOT NULL DEFAULT '{{}}',
            manufacturers TEXT[] NOT NULL DEFAULT '{{}}',
            requires_electrification BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (rule_version, rule_id)
        )
        """,
    ),
    (
        "create_rule_definitions_area_index",
        (
            f"CREATE INDEX IF NOT EXISTS translation_rule_definitions_area_idx "
            f"ON {RULE_DEFINITIONS_TABLE} (rule_version, area, canonical_field)"
        ),
    ),
    (
        "create_rule_definition_immutability_function",
        """
        CREATE OR REPLACE FUNCTION core.reject_rule_definition_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'translation rule definitions are immutable; import a new version';
        END;
        $$ LANGUAGE plpgsql
        """,
    ),
    (
        "create_rule_definition_immutability_trigger",
        f"""
        CREATE OR REPLACE TRIGGER translation_rule_definitions_immutable
        BEFORE UPDATE OR DELETE ON {RULE_DEFINITIONS_TABLE}
        FOR EACH ROW EXECUTE FUNCTION core.reject_rule_definition_mutation()
        """,
    ),
    (
        "create_rule_definition_seal_function",
        f"""
        CREATE OR REPLACE FUNCTION core.guard_rule_definition_seal()
        RETURNS TRIGGER AS $$
        DECLARE is_sealed BOOLEAN;
        BEGIN
            SELECT sealed INTO is_sealed FROM {RULE_DEFINITION_VERSIONS_TABLE}
                WHERE rule_version = NEW.rule_version FOR SHARE;
            IF is_sealed IS DISTINCT FROM FALSE THEN
                RAISE EXCEPTION 'cannot add rules to a sealed rule version';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """,
    ),
    (
        "create_rule_definition_insert_guard",
        f"""
        CREATE OR REPLACE TRIGGER translation_rule_definitions_insert_guard
        BEFORE INSERT ON {RULE_DEFINITIONS_TABLE}
        FOR EACH ROW EXECUTE FUNCTION core.guard_rule_definition_seal()
        """,
    ),
    (
        "create_rule_definition_version_guard_function",
        """
        CREATE OR REPLACE FUNCTION core.guard_rule_definition_version()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND OLD.sealed = FALSE AND NEW.sealed = TRUE
               AND (to_jsonb(OLD) - 'sealed') = (to_jsonb(NEW) - 'sealed') THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION
                'rule definition versions are immutable except for initial sealing';
        END;
        $$ LANGUAGE plpgsql
        """,
    ),
    (
        "create_rule_definition_version_guard",
        f"""
        CREATE OR REPLACE TRIGGER translation_rule_definition_versions_guard
        BEFORE UPDATE OR DELETE ON {RULE_DEFINITION_VERSIONS_TABLE}
        FOR EACH ROW EXECUTE FUNCTION core.guard_rule_definition_version()
        """,
    ),
)


def run_rule_definition_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply the rule-definition schema atomically and idempotently."""

    applied: list[str] = []
    try:
        with connection.cursor() as cursor:
            for name, statement in RULE_DEFINITION_MIGRATIONS:
                cursor.execute(statement)
                applied.append(name)
    except Exception:
        connection.rollback()
        raise
    connection.commit()
    return tuple(applied)
