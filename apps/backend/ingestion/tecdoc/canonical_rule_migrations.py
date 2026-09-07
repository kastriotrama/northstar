"""Versioned, immutable TecDoc normalization rules, symmetric with the TS side.

TS rules are content in PostgreSQL: `core.translation_rule_definitions` holds
the rows, `core.translation_rule_definition_versions` seals a version, and a
trigger makes a sealed version mean one thing forever. TecDoc's mappings were
Python dict literals -- no version, no review trail, no way for a pinned run to
say which mapping it used.

That asymmetry is not about the data. Both sources are normalized into the same
canonical vocabulary and written to the same graph, so a TecDoc mapping decides
a stored value exactly as a TS rule does, and needs the same guarantees.

The schema below is deliberately the mirror image of `rule_definition_migrations`
-- same seal-on-import, same immutability trigger, same content fingerprint --
so the two sources can be reviewed and pinned by one set of habits. It differs
in only what TecDoc genuinely has and TS does not: a `key_table` (TecDoc's own
KT number, the authority a rule is read from) and `support`, the number of rows
in the scanned release that carry the source value.
"""

from __future__ import annotations

from psycopg import Connection

TECDOC_RULE_VERSIONS_TABLE = "core.tecdoc_rule_versions"
TECDOC_RULES_TABLE = "core.tecdoc_rules"

#: Origin of a rule's canonical target.
#:   reviewed_mapping  read from a reviewed lookup in `tecdoc.reference_data`
#:   generated         proposed by scanning the release; carries support + evidence
DERIVATIONS = ("reviewed_mapping", "generated")

#: A generated rule is never accepted by the act of generating it.
DECISIONS = ("accepted", "proposed", "rejected")

TECDOC_RULE_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("create_core_schema", "CREATE SCHEMA IF NOT EXISTS core"),
    (
        "create_tecdoc_rule_versions_table",
        f"""
        CREATE TABLE IF NOT EXISTS {TECDOC_RULE_VERSIONS_TABLE} (
            rule_version TEXT PRIMARY KEY CHECK (btrim(rule_version) <> ''),
            -- Which TecDoc release the rules were generated from. A mapping is
            -- only true of the catalog it was read from: repinning the release
            -- must produce a new version rather than silently reinterpret this
            -- one.
            tecdoc_release TEXT NOT NULL CHECK (btrim(tecdoc_release) <> ''),
            source_note TEXT NOT NULL CHECK (btrim(source_note) <> ''),
            generated_by TEXT NOT NULL CHECK (btrim(generated_by) <> ''),
            rule_count INTEGER NOT NULL CHECK (rule_count > 0),
            -- Content hash over every stored field. `rule_count` cannot tell an
            -- edited rule from an unchanged one, so a re-import of altered
            -- content under a sealed version would read as a no-op.
            content_fingerprint TEXT NOT NULL CHECK (btrim(content_fingerprint) <> ''),
            sealed BOOLEAN NOT NULL DEFAULT FALSE,
            generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    ),
    (
        "create_tecdoc_rules_table",
        f"""
        CREATE TABLE IF NOT EXISTS {TECDOC_RULES_TABLE} (
            rule_version TEXT NOT NULL
                REFERENCES {TECDOC_RULE_VERSIONS_TABLE}(rule_version),
            rule_id TEXT NOT NULL CHECK (btrim(rule_id) <> ''),
            area TEXT NOT NULL CHECK (btrim(area) <> ''),
            -- The TecDoc entity and attribute the value was read from, e.g.
            -- ('engine', 'fuel_type'). Both are needed: the same attribute name
            -- means different things on different entities.
            entity_type TEXT NOT NULL CHECK (btrim(entity_type) <> ''),
            source_field TEXT NOT NULL CHECK (btrim(source_field) <> ''),
            source_term TEXT NOT NULL CHECK (btrim(source_term) <> ''),
            key_table TEXT,
            canonical_field TEXT NOT NULL CHECK (btrim(canonical_field) <> ''),
            -- NULL is meaningful and must stay writable: it is a value observed
            -- in the release that no reviewed mapping covers. Forcing a guess
            -- here is precisely the failure this table exists to prevent.
            canonical_value TEXT,
            decision TEXT NOT NULL CHECK (
                decision IN ('accepted', 'proposed', 'rejected')
            ),
            derivation TEXT NOT NULL CHECK (
                derivation IN ('reviewed_mapping', 'generated')
            ),
            support BIGINT NOT NULL CHECK (support >= 0),
            evidence JSONB NOT NULL DEFAULT '{{}}'
                CHECK (jsonb_typeof(evidence) = 'object'),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (rule_version, rule_id),
            -- One ruling per observed term per field per version. The same
            -- term cannot map to two canonical values in one sealed version.
            CONSTRAINT tecdoc_rules_unique_term UNIQUE (
                rule_version, entity_type, source_field, source_term, canonical_field
            ),
            -- An accepted rule must say what it maps to. Only a proposal is
            -- allowed to stand with the target unresolved.
            CONSTRAINT tecdoc_rules_accepted_needs_target CHECK (
                decision <> 'accepted' OR canonical_value IS NOT NULL
            ),
            -- A generated rule must carry the evidence it was generated from;
            -- a reviewed mapping is a naming fact and needs none.
            CONSTRAINT tecdoc_rules_generated_needs_support CHECK (
                derivation <> 'generated' OR support > 0
            )
        )
        """,
    ),
    (
        "create_tecdoc_rules_lookup_index",
        (
            f"CREATE INDEX IF NOT EXISTS tecdoc_rules_lookup_idx "
            f"ON {TECDOC_RULES_TABLE} (rule_version, canonical_field, decision)"
        ),
    ),
    (
        "create_tecdoc_rules_unmapped_index",
        (
            f"CREATE INDEX IF NOT EXISTS tecdoc_rules_unmapped_idx "
            f"ON {TECDOC_RULES_TABLE} (rule_version, support DESC) "
            "WHERE canonical_value IS NULL"
        ),
    ),
    (
        "create_tecdoc_rule_immutability_function",
        """
        CREATE OR REPLACE FUNCTION core.reject_tecdoc_rule_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'tecdoc rules are immutable; generate a new version';
        END;
        $$ LANGUAGE plpgsql
        """,
    ),
    (
        "create_tecdoc_rule_immutability_trigger",
        f"""
        CREATE OR REPLACE TRIGGER tecdoc_rules_immutable
        BEFORE UPDATE OR DELETE ON {TECDOC_RULES_TABLE}
        FOR EACH ROW EXECUTE FUNCTION core.reject_tecdoc_rule_mutation()
        """,
    ),
    (
        "create_tecdoc_rule_seal_function",
        f"""
        CREATE OR REPLACE FUNCTION core.guard_tecdoc_rule_seal()
        RETURNS TRIGGER AS $$
        DECLARE is_sealed BOOLEAN;
        BEGIN
            SELECT sealed INTO is_sealed FROM {TECDOC_RULE_VERSIONS_TABLE}
                WHERE rule_version = NEW.rule_version FOR SHARE;
            IF is_sealed IS DISTINCT FROM FALSE THEN
                RAISE EXCEPTION 'cannot add rules to a sealed tecdoc rule version';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """,
    ),
    (
        "create_tecdoc_rule_insert_guard",
        f"""
        CREATE OR REPLACE TRIGGER tecdoc_rules_insert_guard
        BEFORE INSERT ON {TECDOC_RULES_TABLE}
        FOR EACH ROW EXECUTE FUNCTION core.guard_tecdoc_rule_seal()
        """,
    ),
    (
        "create_tecdoc_rule_version_guard_function",
        """
        CREATE OR REPLACE FUNCTION core.guard_tecdoc_rule_version()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND OLD.sealed = FALSE AND NEW.sealed = TRUE
               AND (to_jsonb(OLD) - 'sealed') = (to_jsonb(NEW) - 'sealed') THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION
                'tecdoc rule versions are immutable except for initial sealing';
        END;
        $$ LANGUAGE plpgsql
        """,
    ),
    (
        "create_tecdoc_rule_version_guard",
        f"""
        CREATE OR REPLACE TRIGGER tecdoc_rule_versions_guard
        BEFORE UPDATE OR DELETE ON {TECDOC_RULE_VERSIONS_TABLE}
        FOR EACH ROW EXECUTE FUNCTION core.guard_tecdoc_rule_version()
        """,
    ),
)


def run_tecdoc_rule_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply the TecDoc rule schema atomically and idempotently."""

    applied: list[str] = []
    try:
        with connection.cursor() as cursor:
            for name, statement in TECDOC_RULE_MIGRATIONS:
                cursor.execute(statement)
                applied.append(name)
    except Exception:
        connection.rollback()
        raise
    connection.commit()
    return tuple(applied)
