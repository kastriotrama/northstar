"""Build and refresh the flat vehicle projection from the JSONB sources.

The projection is derived, never authored: every column comes from
`staging.transportstyrelsen_raw`, `core.normalization_results` or the live rows
of `core.match_field_resolutions`. Dropping and rebuilding it therefore loses
nothing, which is what makes it safe to reshape later.

Refreshes page by `source_record_id`, committing each page on its own, so a
backfill over 7.26M rows is resumable rather than one enormous transaction.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass

from psycopg import Connection

from ingestion.match_chunk_migrations import MATCH_FIELD_RESOLUTIONS_TABLE
from ingestion.normalization_migrations import NORMALIZATION_RESULTS_TABLE
from ingestion.vehicle_facts_migrations import (
    NORMALIZED_INTEGER_FIELDS,
    NORMALIZED_TEXT_FIELDS,
    RESOLVABLE_FIELDS,
    SOURCE_INTEGER_COLUMNS,
    SOURCE_TEXT_COLUMNS,
    VEHICLE_FACTS_TABLE,
)

STAGING_TABLE = "staging.transportstyrelsen_raw"

DEFAULT_PAGE_SIZE = 50_000

# A backfill of this table once filled the host disk and died mid-page. The data
# was derived so nothing was lost, but a full disk is a database-wide problem,
# not a problem for the job that happens to hit it first. So the job now stops
# while there is still room to breathe rather than taking Postgres down with it.
DEFAULT_MIN_FREE_BYTES = 700 * 1024 * 1024


@dataclass(frozen=True)
class RefreshSummary:
    """What a refresh actually wrote, for the CLI to print and tests to assert."""

    rows_written: int
    pages: int
    highest_source_record_id: int
    stopped_for_disk: bool = False


def _text(expression: str) -> str:
    """Empty strings are absence, not a value, so they normalize to NULL."""

    return f"nullif(btrim({expression}), '')"


def _integer(expression: str) -> str:
    """Cast through a digit guard: registry text that is not a number is absent.

    A plain `::int` aborts the whole page on one malformed row, which across
    7.26M rows of hand-entered registry data is a certainty, not a risk.
    """

    guarded = _text(expression)
    return f"(CASE WHEN {guarded} ~ '^-?[0-9]+$' THEN ({guarded})::bigint END)::int"


def projected_columns() -> tuple[tuple[str, str], ...]:
    """Every projected column, paired with the expression that produces it."""

    columns: list[tuple[str, str]] = [
        ("source_record_id", "nr.source_record_id"),
        ("plate", _text("raw.raw_record ->> 'plate'")),
        ("vin", _text("raw.raw_record ->> 'vin'")),
    ]
    columns.extend(
        (name, _text(f"raw.raw_record ->> '{name}'")) for name in SOURCE_TEXT_COLUMNS
    )
    columns.extend(
        (name, _integer(f"raw.raw_record ->> '{name}'"))
        for name in SOURCE_INTEGER_COLUMNS
    )
    columns.extend(
        (f"n_{name}", _text(f"norm.payload ->> '{name}'"))
        for name in NORMALIZED_TEXT_FIELDS
    )
    columns.extend(
        (f"n_{name}", _integer(f"norm.payload ->> '{name}'"))
        for name in NORMALIZED_INTEGER_FIELDS
    )
    # The overlay is rebuilt from the ledger rather than carried forward, so a
    # retired rule stops showing here the moment its rows are re-read.
    columns.extend((f"r_{name}", _text(f"res.{name}")) for name in NORMALIZED_TEXT_FIELDS)
    columns.extend(
        (f"r_{name}", _integer(f"res.{name}")) for name in NORMALIZED_INTEGER_FIELDS
    )
    columns.extend(
        [
            ("norm_status", _text("nr.status")),
            ("confidence", "nr.confidence::real"),
            ("source_batch_id", _text("nr.source_batch_id")),
            ("refreshed_at", "now()"),
        ]
    )
    return tuple(columns)


def _resolution_columns() -> str:
    """One column per resolvable field, holding its live resolution if any."""

    return ",\n                       ".join(
        f"max(target_value) FILTER (WHERE target_field = '{field}') AS {field}"
        for field in RESOLVABLE_FIELDS
    )


def build_refresh_statement() -> str:
    """One statement per page: select, upsert, and report the new cursor.

    The page is a CTE so the keyset read happens once; taking the cursor from
    the upsert's own RETURNING avoids walking the same rows a second time just
    to learn where the page ended.
    """

    columns = projected_columns()
    names = [name for name, _ in columns]
    selected = ",\n                   ".join(
        f"{expression} AS {name}" for name, expression in columns
    )
    assignments = ",\n                ".join(
        f"{name} = EXCLUDED.{name}" for name in names if name != "source_record_id"
    )
    return f"""
        WITH page AS (
            SELECT {selected}
            FROM {NORMALIZATION_RESULTS_TABLE} AS nr
            JOIN {STAGING_TABLE} AS raw ON raw.id = nr.source_record_id
            CROSS JOIN LATERAL (
                SELECT nr.normalized_payload -> 'normalized' AS payload
            ) AS norm
            LEFT JOIN LATERAL (
                SELECT {_resolution_columns()}
                FROM {MATCH_FIELD_RESOLUTIONS_TABLE}
                WHERE source_record_id = nr.source_record_id
                  AND superseded_at IS NULL
            ) AS res ON true
            -- source_table is pinned so the keyset read can use the composite
            -- index on (source_table, source_record_id). Without it Postgres
            -- has no index for this ordering and falls back to a parallel seq
            -- scan plus a sort of every remaining row to take the top page --
            -- quadratic across a backfill, and the sort spills to temp files
            -- large enough to fill the volume.
            WHERE nr.source_table = %s
              AND nr.source_record_id > %s
            ORDER BY nr.source_record_id
            LIMIT %s
        ),
        upserted AS (
            INSERT INTO {VEHICLE_FACTS_TABLE} ({", ".join(names)})
            SELECT {", ".join(names)} FROM page
            ON CONFLICT (source_record_id) DO UPDATE SET
                {assignments}
            RETURNING source_record_id
        )
        SELECT count(*)::bigint, coalesce(max(source_record_id), 0)::bigint
        FROM upserted
    """


ProgressCallback = Callable[[int, int], None]


def _free_space(path: str) -> int:
    """Free bytes on the volume holding `path`, or "plenty" if unknowable.

    A refresh must not fail because the free-space probe did; the guard is a
    safety net, and a net that throws is worse than no net.
    """

    try:
        return shutil.disk_usage(path).free
    except OSError:
        return DEFAULT_MIN_FREE_BYTES + 1


def refresh_vehicle_facts(
    connection: Connection,
    *,
    since_source_record_id: int = 0,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int | None = None,
    progress: ProgressCallback | None = None,
    free_bytes: int | None = DEFAULT_MIN_FREE_BYTES,
    disk_path: str = "/",
) -> RefreshSummary:
    """Project rows into the facts table, resuming from a cursor.

    Passing `since_source_record_id` continues an interrupted backfill; 0 walks
    everything. Re-running is safe: every page upserts by primary key.
    """

    if page_size < 1:
        raise ValueError("page_size must be positive")

    statement = build_refresh_statement()
    cursor_position = since_source_record_id
    rows_written = 0
    pages = 0
    stopped_for_disk = False

    while max_pages is None or pages < max_pages:
        if free_bytes is not None and _free_space(disk_path) < free_bytes:
            # Stopping is safe and resumable: the cursor below is the caller's
            # `--since` for the next attempt, once there is room.
            stopped_for_disk = True
            break
        with connection.cursor() as cursor:
            cursor.execute(statement, (STAGING_TABLE, cursor_position, page_size))
            row = cursor.fetchone()
        connection.commit()

        written = int(row[0]) if row else 0
        if written <= 0:
            break

        cursor_position = int(row[1])
        rows_written += written
        pages += 1
        if progress is not None:
            progress(rows_written, cursor_position)

    return RefreshSummary(
        rows_written=rows_written,
        pages=pages,
        highest_source_record_id=cursor_position,
        stopped_for_disk=stopped_for_disk,
    )
