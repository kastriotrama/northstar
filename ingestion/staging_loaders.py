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


class BatchRowCountMismatchError(RuntimeError):
    """Raised when source, written, and landed batch counts do not agree."""


def copy_raw_records(
    connection: Connection,
    *,
    table: str,
    source_batch_id: str,
    expected_source_count: int,
    records: Iterable[dict[str, Any]],
) -> int:
    """Bulk-load and atomically validate raw records via COPY.

    `table` must be one of the fully-qualified staging tables created by
    `ingestion.staging_migrations` (e.g. "staging.transportstyrelsen_raw").
    The transaction commits only when source, written, and landed counts agree.
    Returns the validated number of rows written.
    """
    if table not in ALLOWED_STAGING_TABLES:
        message = f"{table!r} is not an allowed staging table"
        raise ValueError(message)
    if not source_batch_id.strip():
        raise ValueError("source_batch_id must not be empty")
    if expected_source_count < 0:
        raise ValueError("expected_source_count must be non-negative")

    written_count = 0
    try:
        copy_sql = f"COPY {table} (source_batch_id, raw_record) FROM STDIN"
        with connection.cursor() as cursor, cursor.copy(copy_sql) as copy:
            for record in records:
                copy.write_row((source_batch_id, json.dumps(record)))
                written_count += 1

        landed_count = count_batch_rows(
            connection,
            table=table,
            source_batch_id=source_batch_id,
        )
        if not expected_source_count == written_count == landed_count:
            message = (
                f"Batch {source_batch_id!r} row-count mismatch: "
                f"source={expected_source_count}, written={written_count}, "
                f"landed={landed_count}"
            )
            raise BatchRowCountMismatchError(message)

        connection.commit()
    except Exception:
        connection.rollback()
        raise

    return written_count


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
