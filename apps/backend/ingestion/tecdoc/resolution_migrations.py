"""Live, reviewer-authored TecDoc mappings -- TS's `resolution_rules`, mirrored.

`canonical_rule_migrations` seals immutable, generated rule *versions*: a batch
scan, reviewed in bulk, pinned forever. That is the wrong shape for one reviewer
looking at one unmapped KT086 code and naming what it means -- exactly the gap
TS closed by giving `translation_rule_definitions` a live sibling,
`match_resolution_rules`, that a reviewer writes to directly from the browse
screen without waiting for a batch re-import.

This is that sibling for TecDoc. A row here is mutable (a reviewer can correct
themselves), keyed by the value itself rather than by which release observed
it, and carries no `support`/`evidence` for the `accepted`/`excluded` decision
-- that belongs to the generated rule that will eventually supersede this row
once the mapping is promoted into `ingestion.tecdoc.reference_data` and
re-sealed through the normal batch path. Until then, this table is what makes
a reviewer's ruling visible immediately.

`source_system` and `relation` extend that same live-rule idea to reconcile
TS and TecDoc's independently-normalized vocabularies (fuel/bodywork/drive),
replacing the separate, versioned `core.vocabulary_alignments` schema: a
reviewer adds a TS-side row and a TecDoc-side row for the same concept (TS
`electricity`, TecDoc `electric`) instead of authoring a cross-system pair
elsewhere. `relation` keeps the distinction that schema drew between two terms
denoting the *same* concept (`equivalent`, safe to score as a match) and one
term being merely broader than the other (`compatible` -- TS's undifferentiated
`2wd` is compatible with both TecDoc `fwd` and `rwd`, equivalent to neither, so
it must stay neutral: never a match, never a conflict). A `compatible` row
carries `support`, the observed population size, as evidence for that ruling;
an `equivalent` row is a naming fact and needs none.
"""

from __future__ import annotations

from psycopg import Connection

TECDOC_RESOLUTION_RULES_TABLE = "core.tecdoc_resolution_rules"

#: `accepted` names a canonical target; `excluded` is a reviewer stating a value
#: is out of scope on purpose (a motorcycle body type, say) rather than simply
#: unreviewed -- the same distinction a blank gap and a ruled-out gap need.
DECISIONS = ("accepted", "excluded")

SOURCE_SYSTEMS = ("tecdoc", "transportstyrelsen")
RELATIONS = ("equivalent", "compatible")

TECDOC_RESOLUTION_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("create_core_schema", "CREATE SCHEMA IF NOT EXISTS core"),
    (
        "create_tecdoc_resolution_rules_table",
        f"""
        CREATE TABLE IF NOT EXISTS {TECDOC_RESOLUTION_RULES_TABLE} (
            canonical_field TEXT NOT NULL CHECK (btrim(canonical_field) <> ''),
            -- Accent/punctuation-tolerant identity, the same key
            -- `canonical_rule_proposals.comparison_key` computes, so a row here
            -- and a row the generator scans are provably the same value.
            comparison_key TEXT NOT NULL CHECK (btrim(comparison_key) <> ''),
            source_term TEXT NOT NULL CHECK (btrim(source_term) <> ''),
            key_table TEXT,
            decision TEXT NOT NULL CHECK (decision IN ('accepted', 'excluded')),
            -- NULL for 'excluded': ruling a value out of scope names no target.
            canonical_value TEXT,
            note TEXT NOT NULL DEFAULT '',
            reviewed_by TEXT NOT NULL CHECK (btrim(reviewed_by) <> ''),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (canonical_field, comparison_key),
            CONSTRAINT tecdoc_resolution_accepted_needs_target CHECK (
                decision <> 'accepted' OR canonical_value IS NOT NULL
            ),
            CONSTRAINT tecdoc_resolution_excluded_has_no_target CHECK (
                decision <> 'excluded' OR canonical_value IS NULL
            ),
            -- An exclusion is a claim ("this is a motorcycle code, not a car
            -- body") and needs to say so; an acceptance is self-explanatory.
            CONSTRAINT tecdoc_resolution_excluded_needs_note CHECK (
                decision <> 'excluded' OR btrim(note) <> ''
            )
        )
        """,
    ),
    # Everything below reconciles TS and TecDoc's independently-normalized
    # vocabularies in this same live table, replacing `core.vocabulary_alignments`.
    # Every row created before this ran is a TecDoc ruling, so `source_system`
    # backfills correctly as 'tecdoc' and `relation` as 'equivalent' -- both
    # were the only values that existed until now.
    (
        "add_tecdoc_resolution_source_system",
        (
            f"ALTER TABLE {TECDOC_RESOLUTION_RULES_TABLE} "
            "ADD COLUMN IF NOT EXISTS source_system TEXT NOT NULL DEFAULT 'tecdoc'; "
            f"ALTER TABLE {TECDOC_RESOLUTION_RULES_TABLE} "
            "DROP CONSTRAINT IF EXISTS tecdoc_resolution_source_system_check; "
            f"ALTER TABLE {TECDOC_RESOLUTION_RULES_TABLE} "
            "ADD CONSTRAINT tecdoc_resolution_source_system_check "
            "CHECK (source_system IN ('tecdoc', 'transportstyrelsen'))"
        ),
    ),
    (
        "add_tecdoc_resolution_relation",
        (
            f"ALTER TABLE {TECDOC_RESOLUTION_RULES_TABLE} "
            "ADD COLUMN IF NOT EXISTS relation TEXT NOT NULL DEFAULT 'equivalent'; "
            f"ALTER TABLE {TECDOC_RESOLUTION_RULES_TABLE} "
            "DROP CONSTRAINT IF EXISTS tecdoc_resolution_relation_check; "
            f"ALTER TABLE {TECDOC_RESOLUTION_RULES_TABLE} "
            "ADD CONSTRAINT tecdoc_resolution_relation_check "
            "CHECK (relation IN ('equivalent', 'compatible'))"
        ),
    ),
    (
        "add_tecdoc_resolution_support",
        (
            f"ALTER TABLE {TECDOC_RESOLUTION_RULES_TABLE} "
            "ADD COLUMN IF NOT EXISTS support INTEGER; "
            f"ALTER TABLE {TECDOC_RESOLUTION_RULES_TABLE} "
            "DROP CONSTRAINT IF EXISTS tecdoc_resolution_support_check; "
            f"ALTER TABLE {TECDOC_RESOLUTION_RULES_TABLE} "
            "ADD CONSTRAINT tecdoc_resolution_support_check "
            "CHECK (support IS NULL OR support >= 0); "
            # A compatible row asserts a broader-than relationship learned from
            # data, so it must say how much data -- the same rule
            # `vocabulary_alignment_support_check` enforced.
            f"ALTER TABLE {TECDOC_RESOLUTION_RULES_TABLE} "
            "DROP CONSTRAINT IF EXISTS tecdoc_resolution_compatible_needs_support; "
            f"ALTER TABLE {TECDOC_RESOLUTION_RULES_TABLE} "
            "ADD CONSTRAINT tecdoc_resolution_compatible_needs_support "
            "CHECK (relation <> 'compatible' OR support IS NOT NULL)"
        ),
    ),
    # The original primary key assumed one target per (canonical_field,
    # comparison_key). A `compatible` row breaks that assumption on purpose --
    # TS's undifferentiated `2wd` is compatible with both TecDoc `fwd` and
    # `rwd`, never equivalent to either -- so identity moves to a surrogate id
    # and the old key becomes two partial unique indexes instead: one for the
    # single-target case (accepted/excluded/equivalent), one for compatible
    # pairs, which key on the target too since there can be more than one.
    (
        "add_tecdoc_resolution_surrogate_id",
        (
            f"ALTER TABLE {TECDOC_RESOLUTION_RULES_TABLE} "
            "ADD COLUMN IF NOT EXISTS id BIGSERIAL; "
            f"ALTER TABLE {TECDOC_RESOLUTION_RULES_TABLE} "
            "DROP CONSTRAINT IF EXISTS tecdoc_resolution_rules_pkey; "
            f"ALTER TABLE {TECDOC_RESOLUTION_RULES_TABLE} "
            "ADD CONSTRAINT tecdoc_resolution_rules_pkey PRIMARY KEY (id)"
        ),
    ),
    (
        "create_tecdoc_resolution_single_target_index",
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "tecdoc_resolution_rules_single_target_key "
            f"ON {TECDOC_RESOLUTION_RULES_TABLE} "
            "(canonical_field, source_system, comparison_key) "
            "WHERE relation <> 'compatible'"
        ),
    ),
    (
        "create_tecdoc_resolution_compatible_pair_index",
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "tecdoc_resolution_rules_compatible_pair_key "
            f"ON {TECDOC_RESOLUTION_RULES_TABLE} "
            "(canonical_field, source_system, comparison_key, canonical_value) "
            "WHERE relation = 'compatible'"
        ),
    ),
)


def run_tecdoc_resolution_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply the TecDoc live-resolution schema atomically and idempotently."""

    applied: list[str] = []
    try:
        with connection.cursor() as cursor:
            for name, statement in TECDOC_RESOLUTION_MIGRATIONS:
                cursor.execute(statement)
                applied.append(name)
    except Exception:
        connection.rollback()
        raise
    connection.commit()
    return tuple(applied)
