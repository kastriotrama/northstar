"""Report how much of the staged Transportstyrelsen dump reached normalization.

The audit is PostgreSQL read-only. It emits no plates/VINs, reads only counts
and field names, and never writes staging, normalization, or the graph.

Three questions are answered from one connection:

1. how many raw TS rows were imported per staged batch;
2. how many of those rows have a normalization result, and how they routed
   (resolved / provisional / review_required / failed);
3. which values stayed unresolved -- fields that fell to `candidates` instead of
   accepted `normalized` values, and the review reasons behind them.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import psycopg

from ingestion.config import get_ingestion_settings
from ingestion.normalization_migrations import NORMALIZATION_RESULTS_TABLE
from ingestion.normalization_repository import SOURCE_TABLE

STATUSES = ("resolved", "provisional", "review_required", "failed")

# Staged batches are written as `<name>-part-<n>`; collapsing the suffix keeps a
# 261-part dump on one line instead of 261.
BATCH_PATTERN = "regexp_replace(source_batch_id, '(part|chunk)[-_]?[0-9]+$', 'part-N')"


def _rows(connection: psycopg.Connection, sql: str) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return cursor.fetchall()


def collect_coverage(connection: psycopg.Connection) -> dict[str, Any]:
    """Return per-batch import and normalization counts plus unresolved fields."""

    staged = {
        str(pattern): int(count)
        for pattern, count in _rows(
            connection,
            f"SELECT {BATCH_PATTERN} AS pattern, count(*) FROM {SOURCE_TABLE} GROUP BY 1",
        )
    }
    # A row may be normalized more than once -- uniqueness is per (source row,
    # mapping/rule/pipeline version), so a replay adds results. Coverage counts
    # distinct source rows; the status columns count result rows, which is why a
    # replayed batch can report more results than it imported.
    covered = {
        str(pattern): int(count)
        for pattern, count in _rows(
            connection,
            f"SELECT {BATCH_PATTERN} AS pattern, count(DISTINCT source_record_id) "
            f"FROM {NORMALIZATION_RESULTS_TABLE} GROUP BY 1",
        )
    }
    routed: dict[str, dict[str, int]] = {}
    for pattern, status, count in _rows(
        connection,
        f"SELECT {BATCH_PATTERN} AS pattern, status, count(*) "
        f"FROM {NORMALIZATION_RESULTS_TABLE} GROUP BY 1, 2",
    ):
        routed.setdefault(str(pattern), {})[str(status)] = int(count)

    batches: list[dict[str, Any]] = []
    for pattern, imported in sorted(staged.items(), key=lambda item: -item[1]):
        counts = routed.get(pattern, {})
        normalized = covered.get(pattern, 0)
        batches.append(
            {
                "batch_pattern": pattern,
                "imported": imported,
                "normalized": normalized,
                "not_normalized": imported - normalized,
                **{status: counts.get(status, 0) for status in STATUSES},
            }
        )

    totals: dict[str, Any] = {
        key: sum(int(batch[key]) for batch in batches)
        for key in ("imported", "normalized", "not_normalized", *STATUSES)
    }
    return {
        "batches": batches,
        "totals": totals,
        "unresolved_fields": [
            {"field": str(field), "records": int(count)}
            for field, count in _rows(
                connection,
                "SELECT k, count(*) AS n "
                f"FROM {NORMALIZATION_RESULTS_TABLE} r, "
                "LATERAL jsonb_object_keys(r.normalized_payload->'candidates') k "
                "GROUP BY 1 ORDER BY 2 DESC",
            )
        ],
        "review_reasons": [
            {"reason": str(reason), "records": int(count)}
            for reason, count in _rows(
                connection,
                "SELECT reason, count(*) AS n "
                f"FROM {NORMALIZATION_RESULTS_TABLE} r, "
                "LATERAL unnest(r.review_reasons) reason "
                "GROUP BY 1 ORDER BY 2 DESC",
            )
        ],
    }


def render_text(report: dict[str, Any]) -> str:
    lines = ["Imported vs normalized, by staged batch", ""]
    header = (
        f"{'batch pattern':<52}{'imported':>11}{'normalized':>11}"
        f"{'pending':>11}{'resolved':>10}{'provisional':>12}{'review':>9}"
    )
    lines.append(header)
    for batch in [*report["batches"], {"batch_pattern": "TOTAL", **report["totals"]}]:
        lines.append(
            f"{str(batch['batch_pattern'])[:52]:<52}"
            f"{batch['imported']:>11,}{batch['normalized']:>11,}"
            f"{batch['not_normalized']:>11,}{batch['resolved']:>10,}"
            f"{batch['provisional']:>12,}{batch['review_required']:>9,}"
        )
    lines += ["", "Unresolved values (fell to candidates, not accepted)", ""]
    for item in report["unresolved_fields"]:
        lines.append(f"{item['records']:>11,}  {item['field']}")
    lines += ["", "Review reasons", ""]
    for item in report["review_reasons"]:
        lines.append(f"{item['records']:>11,}  {item['reason']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit the raw report.")
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override DATABASE_URL, e.g. to audit the remote database.",
    )
    args = parser.parse_args(argv)

    database_url = args.database_url or get_ingestion_settings().database_url
    with psycopg.connect(database_url) as connection:
        report = collect_coverage(connection)
    print(json.dumps(report, indent=2) if args.json else render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
