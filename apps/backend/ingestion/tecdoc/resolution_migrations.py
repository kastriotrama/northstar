"""Live, reviewer-authored TecDoc mappings -- TS's `resolution_rules`, mirrored.

`canonical_rule_migrations` seals immutable, generated rule *versions*: a batch
scan, reviewed in bulk, pinned forever. That is the wrong shape for one reviewer
looking at one unmapped KT086 code and naming what it means -- exactly the gap
TS closed by giving `translation_rule_definitions` a live sibling,
`match_resolution_rules`, that a reviewer writes to directly from the browse
screen without waiting for a batch re-import.

This is that sibling for TecDoc. A row here is mutable (a reviewer can correct
themselves), keyed by the value itself rather than by which release observed
it, and carries no `support`/`evidence` -- those belong to the generated rule
that will eventually supersede this row once the mapping is promoted into
`ingestion.tecdoc.reference_data` and re-sealed through the normal batch path.
Until then, this table is what makes a reviewer's ruling visible immediately.
"""

from __future__ import annotations

from psycopg import Connection

TECDOC_RESOLUTION_RULES_TABLE = "core.tecdoc_resolution_rules"

#: `accepted` names a canonical target; `excluded` is a reviewer stating a value
#: is out of scope on purpose (a motorcycle body type, say) rather than simply
#: unreviewed -- the same distinction a blank gap and a ruled-out gap need.
DECISIONS = ("accepted", "excluded")

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
