"""COPY-based bulk loaders for staging tables (SCRUM-16 Story 3.1).

Loaders write raw records exactly as received -- no normalization. Each
record becomes one row: source_batch_id, a server-assigned ingested_at, and
the record itself as a JSONB payload. This is the "untouched landing zone"
contract from the Phase 1 plan; reprocessing (dedup, resume) is Story 3.4's
scope, not this module's.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from psycopg import Connection

from ingestion.staging_migrations import ALLOWED_STAGING_TABLES


def copy_raw_records(
    connection: Connection,
    *,
    table: str,
    source_batch_id: str,
    records: Iterable[dict[str, Any]],
) -> int:
    """Bulk-load raw records into a staging table via COPY.

    `table` must be one of the fully-qualified staging tables created by
    `ingestion.staging_migrations` (e.g. "staging.transportstyrelsen_raw").
    Returns the number of rows written.
    """
    if table not in ALLOWED_STAGING_TABLES:
        message = f"{table!r} is not an allowed staging table"
        raise ValueError(message)

    row_count = 0
    copy_sql = f"COPY {table} (source_batch_id, raw_record) FROM STDIN"
    with connection.cursor() as cursor, cursor.copy(copy_sql) as copy:
        for record in records:
            copy.write_row((source_batch_id, json.dumps(record)))
            row_count += 1
    connection.commit()
    return row_count


def count_batch_rows(
    connection: Connection,
    *,
    table: str,
    source_batch_id: str,
) -> int:
    """Count landed rows for one batch, for required row-count validation.

    The load contract (docs/staging-schema-design.md §6) requires that after
    every load, this count equals both the loader's returned row count and
    the number of records extracted from the source. A mismatch means the
    load failed and must be investigated before normalization runs.
    """
    if table not in ALLOWED_STAGING_TABLES:
        message = f"{table!r} is not an allowed staging table"
        raise ValueError(message)

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT count(*) FROM {table} WHERE source_batch_id = %s",
            (source_batch_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("count query returned no row")
    return int(row[0])
