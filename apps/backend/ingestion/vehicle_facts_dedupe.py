"""Reduce the projection to one row per vehicle.

The same car is ingested and normalized repeatedly -- plate BMT94L appears
thirteen times across `review-250-v4`, `-v5`, `passenger-250-v1`,
`atlas-5000-v1` and others, some plates eighteen times. That is 722,845 rows,
10% of the table, and it inflates every count a reviewer sees: a filter reading
"155,002 Toyotas" is really about 140,000 cars listed more than once.

Deduplication is safe here because the copies agree. Across 567,135 repeated
plates, `model_family` and `drive_type` differ in *none* of them, and only 1,623
(0.29%) differ at all, in `manufacturer`.

Which copy survives matters more than it looks. Ranking by "newest, best
normalized" alone would have dropped the row behind every single one of the
2,516 resolutions already written -- every rule result silently orphaned. So a
row carrying a live resolution wins outright, and the rest is a tie-break.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg import Connection

from ingestion.match_chunk_migrations import MATCH_FIELD_RESOLUTIONS_TABLE
from ingestion.vehicle_facts_migrations import (
    NORMALIZED_TEXT_FIELDS,
    VEHICLE_FACTS_TABLE,
)

DEFAULT_BATCH_SIZE = 100_000

# How much of a vehicle this copy managed to normalize. Only the text fields
# count: the integer ones are carried by nearly every row and would not separate
# one copy from another.
_RESOLVED_SCORE = " + ".join(
    f"(n_{field} IS NOT NULL)::int" for field in NORMALIZED_TEXT_FIELDS
)

# A row holding a live resolution always survives, whatever else it looks like.
# Then the best-normalized copy, then the newest.
SURVIVOR_RANKING = f"""
    row_number() OVER (
        PARTITION BY vf.plate
        ORDER BY (live.source_record_id IS NOT NULL) DESC,
                 ({_RESOLVED_SCORE}) DESC,
                 vf.source_record_id DESC
    )
"""


@dataclass(frozen=True)
class DedupeSummary:
    rows_removed: int
    batches: int


def build_dedupe_statement() -> str:
    """Delete one batch of superseded copies, newest-id first.

    Rows with no plate cannot be matched to a sibling and are always kept: two
    such rows exist, and guessing that they are the same car would be worse than
    carrying them twice.
    """

    return f"""
        WITH live AS (
            SELECT DISTINCT source_record_id
            FROM {MATCH_FIELD_RESOLUTIONS_TABLE}
            WHERE superseded_at IS NULL
        ),
        ranked AS (
            SELECT vf.source_record_id, {SURVIVOR_RANKING} AS rank
            FROM {VEHICLE_FACTS_TABLE} AS vf
            LEFT JOIN live ON live.source_record_id = vf.source_record_id
            WHERE vf.plate IS NOT NULL
        ),
        doomed AS (
            SELECT source_record_id FROM ranked WHERE rank > 1 LIMIT %s
        )
        DELETE FROM {VEHICLE_FACTS_TABLE} AS vf
        USING doomed
        WHERE vf.source_record_id = doomed.source_record_id
    """


def dedupe_vehicle_facts(
    connection: Connection,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int | None = None,
) -> DedupeSummary:
    """Collapse the projection to one row per plate, in committed batches."""

    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    statement = build_dedupe_statement()
    removed = 0
    batches = 0

    while max_batches is None or batches < max_batches:
        with connection.cursor() as cursor:
            cursor.execute(statement, (batch_size,))
            deleted = cursor.rowcount
        connection.commit()
        if deleted <= 0:
            break
        removed += deleted
        batches += 1

    return DedupeSummary(rows_removed=removed, batches=batches)
