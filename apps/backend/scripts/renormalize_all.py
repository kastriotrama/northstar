"""Re-normalize every staged Transportstyrelsen batch under the current pipeline.

Resumable and non-duplicating. A batch already fully normalized under the
current ``PIPELINE_VERSION`` is skipped, so an interrupted sweep can simply be
restarted.

Normalization identity includes the pipeline version, so a re-run inserts a new
result beside the old one rather than replacing it. Every consumer already reads
``DISTINCT ON (source_record_id) ... ORDER BY updated_at DESC, id DESC``, so the
older rows are history and nothing reads them. This sweep prunes them per batch,
leaving exactly one result per staged row.
"""

from __future__ import annotations

import argparse
import time

import psycopg

from ingestion.active_rules import load_active_rules
from ingestion.config import get_ingestion_settings
from ingestion.normalization_migrations import NORMALIZATION_RESULTS_TABLE
from ingestion.normalization_repository import SOURCE_TABLE
from ingestion.normalization_rules import PIPELINE_VERSION
from ingestion.normalization_service import normalize_batch

BATCH_INVENTORY = f"""
SELECT s.source_batch_id, count(*) AS staged,
       count(*) FILTER (WHERE r.pipeline_version = %s) AS done
FROM {SOURCE_TABLE} s
LEFT JOIN {NORMALIZATION_RESULTS_TABLE} r
  ON r.source_record_id = s.id AND r.source_table = %s
GROUP BY 1 ORDER BY 1
"""

# A completed run is never re-claimed; a failed one is retried. Reopening is the
# sanctioned path, and the reason is recorded rather than silently overwritten.
REOPEN = """
UPDATE core.ingest_job_runs
   SET status = 'failed', finished_at = now(), updated_at = now(),
       error_code = 'pipeline_version_reopen',
       error_summary = %s
 WHERE job_name = 'normalize' AND batch_id = %s AND status = 'completed'
"""

PRUNE = f"""
WITH scoped AS (
    SELECT r.id, r.source_record_id, r.updated_at
    FROM {NORMALIZATION_RESULTS_TABLE} r
    JOIN {SOURCE_TABLE} s ON s.id = r.source_record_id AND s.source_batch_id = %s
    WHERE r.source_table = %s
),
keep AS (
    SELECT DISTINCT ON (source_record_id) id
    FROM scoped ORDER BY source_record_id, updated_at DESC, id DESC
)
DELETE FROM {NORMALIZATION_RESULTS_TABLE}
 WHERE id IN (SELECT id FROM scoped) AND id NOT IN (SELECT id FROM keep)
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="Stop after N batches (0 = all).")
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()

    url = str(get_ingestion_settings().database_url)
    with psycopg.connect(url, connect_timeout=60) as connection:
        connection.execute("SET statement_timeout = 0")
        with connection.cursor() as cursor:
            cursor.execute(BATCH_INVENTORY, (PIPELINE_VERSION, SOURCE_TABLE))
            inventory = cursor.fetchall()

    pending = [(b, staged) for b, staged, done in inventory if done < staged]
    total_rows = sum(staged for _, staged in pending)
    print(
        f"pipeline {PIPELINE_VERSION}: {len(pending)} batches pending, {total_rows:,} rows",
        flush=True,
    )
    if arguments.dry_run:
        return 0

    started = time.monotonic()
    processed = pruned_total = 0
    for index, (batch_id, staged) in enumerate(pending, start=1):
        if arguments.limit and index > arguments.limit:
            break
        with psycopg.connect(url, connect_timeout=60) as connection:
            connection.execute("SET statement_timeout = 0")
            rule_set, entity_rules = load_active_rules(connection)
            with connection.cursor() as cursor:
                cursor.execute(REOPEN, (f"Reopened for {PIPELINE_VERSION}", batch_id))
            connection.commit()
            summary = normalize_batch(
                connection,
                batch_id=batch_id,
                rule_set=rule_set,
                manufacturer_entity_rules=entity_rules,
            )
            connection.commit()
            with connection.cursor() as cursor:
                cursor.execute(PRUNE, (batch_id, SOURCE_TABLE))
                pruned = cursor.rowcount
            connection.commit()
        processed += summary.processed
        pruned_total += max(pruned, 0)
        elapsed = time.monotonic() - started
        rate = processed / elapsed if elapsed else 0
        remaining = (total_rows - processed) / rate / 3600 if rate else 0
        print(
            f"[{index}/{len(pending)}] {batch_id} "
            f"processed={summary.processed} resolved={summary.resolved} "
            f"provisional={summary.provisional} review={summary.review_required} "
            f"pruned={max(pruned, 0)} | {processed:,} rows, "
            f"{rate:.0f} rows/s, ~{remaining:.1f}h left",
            flush=True,
        )
    print(
        f"done: {processed:,} rows normalized, {pruned_total:,} superseded rows pruned", flush=True
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
