"""What an agent proposed for an unmapped TecDoc value, kept apart from rulings.

`resolution_migrations` holds what a *reviewer* decided: every row there is live,
`reviewed_by` is mandatory, and `decision` admits only `accepted` or `excluded`.
There is deliberately no third state for "nobody has looked yet", because a row
in that table exists precisely because somebody looked.

An agent working the gap list produces something else: a proposal, with a reason
and a confidence, that may or may not survive contact with a reviewer. Writing
those into the ruling table would erase the distinction between a value a human
vouched for and a value a model guessed -- and the graph's whole claim is that
the first kind is the only kind it stores.

So proposals live here, keyed exactly as a ruling is -- `(canonical_field,
comparison_key)` -- so a suggestion, the gap it answers and the ruling that
eventually settles it are provably about the same value. `applied_at` records
that a suggestion was confident enough to write itself through to the ruling
table; it stays a record of what the agent did, not a second source of truth.
"""

from __future__ import annotations

from psycopg import Connection

TECDOC_GAP_SUGGESTIONS_TABLE = "core.tecdoc_gap_suggestions"

#: `reuse` names a canonical term the vocabulary already holds; `mint` proposes a
#: term it does not; `exclude` claims the value has no canonical target at all
#: because it is out of scope. The distinction is the reviewer's first question
#: about any suggestion, so it is stored rather than inferred from whether the
#: value happens to appear elsewhere today -- the vocabulary moves, the claim
#: does not. `exclude` is its own origin rather than a `mint` with no value: an
#: exclusion asserts something about scope, not about vocabulary.
ORIGINS = ("reuse", "mint", "exclude")

#: `excluded` mirrors the ruling table: an agent may also propose that a value is
#: out of scope, and that proposal needs the same shape as a positive one.
SUGGESTION_DECISIONS = ("accepted", "excluded")

TECDOC_GAP_SUGGESTION_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("create_core_schema", "CREATE SCHEMA IF NOT EXISTS core"),
    (
        "create_tecdoc_gap_suggestions_table",
        f"""
        CREATE TABLE IF NOT EXISTS {TECDOC_GAP_SUGGESTIONS_TABLE} (
            canonical_field TEXT NOT NULL CHECK (btrim(canonical_field) <> ''),
            -- The same key `canonical_rule_proposals.comparison_key` computes, so
            -- a suggestion and the gap it answers are provably the same value.
            comparison_key TEXT NOT NULL CHECK (btrim(comparison_key) <> ''),
            source_term TEXT NOT NULL CHECK (btrim(source_term) <> ''),
            key_table TEXT,
            decision TEXT NOT NULL CHECK (decision IN ('accepted', 'excluded')),
            -- NULL for 'excluded', exactly as in the ruling table.
            suggested_value TEXT,
            origin TEXT NOT NULL CHECK (origin IN ('reuse', 'mint', 'exclude')),
            -- Why this value, in the agent's own words. A reviewer ruling on a
            -- proposal needs the reasoning, not just the verdict.
            rationale TEXT NOT NULL CHECK (btrim(rationale) <> ''),
            -- The naming standard a minted term followed, so a reviewer can
            -- check the convention rather than re-deriving it.
            convention TEXT NOT NULL DEFAULT '',
            confidence DOUBLE PRECISION NOT NULL
                CHECK (confidence >= 0.0 AND confidence <= 1.0),
            -- How many KTypes ride on this value, carried from the gap so the
            -- suggestion can be ordered by impact without a join.
            support BIGINT NOT NULL DEFAULT 0 CHECK (support >= 0),
            -- Who produced it: an agent identity and its version, never a person.
            suggested_by TEXT NOT NULL CHECK (btrim(suggested_by) <> ''),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- Set when the suggestion was confident enough to write itself into
            -- the ruling table. NULL means it is waiting for a reviewer.
            applied_at TIMESTAMPTZ,
            PRIMARY KEY (canonical_field, comparison_key),
            CONSTRAINT tecdoc_suggestion_accepted_needs_target CHECK (
                decision <> 'accepted' OR suggested_value IS NOT NULL
            ),
            CONSTRAINT tecdoc_suggestion_excluded_has_no_target CHECK (
                decision <> 'excluded' OR suggested_value IS NULL
            ),
            -- A reuse claims the vocabulary already holds the term, which is
            -- only meaningful when it names one.
            CONSTRAINT tecdoc_suggestion_reuse_names_a_target CHECK (
                origin <> 'reuse' OR suggested_value IS NOT NULL
            )
        )
        """,
    ),
    # The table is created with `IF NOT EXISTS`, so a database that already has
    # it keeps the narrower check the first version shipped. These restate the
    # constraint unconditionally rather than guessing which version is present.
    (
        "drop_tecdoc_gap_suggestion_origin_check",
        f"ALTER TABLE {TECDOC_GAP_SUGGESTIONS_TABLE} "
        "DROP CONSTRAINT IF EXISTS tecdoc_gap_suggestions_origin_check",
    ),
    (
        "widen_tecdoc_gap_suggestion_origins",
        f"ALTER TABLE {TECDOC_GAP_SUGGESTIONS_TABLE} "
        "ADD CONSTRAINT tecdoc_gap_suggestions_origin_check "
        "CHECK (origin IN ('reuse', 'mint', 'exclude'))",
    ),
    (
        "drop_tecdoc_suggestion_exclude_shape",
        f"ALTER TABLE {TECDOC_GAP_SUGGESTIONS_TABLE} "
        "DROP CONSTRAINT IF EXISTS tecdoc_suggestion_exclude_shape",
    ),
    (
        "add_tecdoc_suggestion_exclude_shape",
        # An `exclude` origin and an `excluded` decision are the same claim seen
        # from two sides; letting them disagree would make the origin unreadable.
        f"ALTER TABLE {TECDOC_GAP_SUGGESTIONS_TABLE} "
        "ADD CONSTRAINT tecdoc_suggestion_exclude_shape "
        "CHECK ((origin = 'exclude') = (decision = 'excluded'))",
    ),
    (
        "index_tecdoc_gap_suggestions_pending",
        f"""
        CREATE INDEX IF NOT EXISTS tecdoc_gap_suggestions_pending_idx
        ON {TECDOC_GAP_SUGGESTIONS_TABLE} (support DESC)
        WHERE applied_at IS NULL
        """,
    ),
)


def run_tecdoc_gap_suggestion_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply the suggestion schema atomically and idempotently."""

    applied: list[str] = []
    try:
        with connection.cursor() as cursor:
            for name, statement in TECDOC_GAP_SUGGESTION_MIGRATIONS:
                cursor.execute(statement)
                applied.append(name)
    except Exception:
        connection.rollback()
        raise
    connection.commit()
    return tuple(applied)
