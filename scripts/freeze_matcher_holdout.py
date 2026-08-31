"""Freeze an unscored source-only holdout, excluding linked development groups."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

from ingestion.config import IngestionSettings
from ingestion.tecdoc.remote_match_run import _fetch_local_raw_page
from scripts.validate_local_matcher_cohort import digest, write_private_json


def leakage_tokens(raw: dict[str, Any]) -> set[str]:
    tokens = set()
    for field in ("vin", "plate"):
        value = re.sub(r"[^A-Z0-9]", "", str(raw.get(field) or "").upper())
        if value:
            tokens.add(digest([field, value]))
    approval = str(raw.get("eeg_type_approval") or "").strip().upper()
    if approval:
        # Conservatively hold out the approval family, including revisions and
        # all variants, not merely an exact row fingerprint.
        family = re.sub(r"\*\d+$", "", approval)
        tokens.add(digest(["approval_family", family]))
    variant, version = (str(raw.get(key) or "").strip().upper() for key in ("variant", "version"))
    if variant and version:
        tokens.add(digest(["variant_version", variant, version]))
    return tokens


def freeze_groups(development: list[tuple[int, dict[str, Any]]], window: list[tuple[int, dict[str, Any]]]) -> dict[str, Any]:
    all_rows = [*development, *window]
    if len({source_id for source_id, _ in all_rows}) != len(all_rows):
        raise ValueError("development and holdout source IDs overlap or repeat")
    parents = list(range(len(all_rows)))
    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    owner: dict[str, int] = {}
    row_tokens = []
    for index, (_, raw) in enumerate(all_rows):
        tokens = leakage_tokens(raw)
        row_tokens.append(tokens)
        for token in sorted(tokens):
            if token in owner:
                parents[root(index)] = root(owner[token])
            else:
                owner[token] = index
    development_groups = {root(index) for index in range(len(development))}
    grouped: dict[int, list[str]] = defaultdict(list)
    for index, (source_id, raw) in enumerate(all_rows):
        grouped[root(index)].append(digest([source_id, raw]))
    group_ids = {key: digest(sorted(values)) for key, values in grouped.items()}
    excluded: Counter[str] = Counter()
    strata: Counter[str] = Counter()
    included = []
    for index in range(len(development), len(all_rows)):
        source_id, raw = all_rows[index]
        if not row_tokens[index]:
            excluded["no_grouping_evidence"] += 1
            continue
        if root(index) in development_groups:
            excluded["linked_to_development"] += 1
            continue
        stratum = digest([str(raw.get("vehicle_type") or ""), str(raw.get("fuel1") or ""), bool(raw.get("model"))])
        strata[stratum] += 1
        included.append({"source_record_id": source_id, "row_key": digest([source_id, raw]),
                         "group_key": group_ids[root(index)], "source_stratum": stratum})
    return {"window_count": len(window), "eligible_count": len(included), "excluded": dict(excluded),
            "group_count": len({row["group_key"] for row in included}), "source_strata": dict(strata),
            "rows": included, "development_digest": digest(development), "window_digest": digest(window),
            "scored": False, "independently_adjudicated": False,
            "sampling": "next ordered source window; all disjoint groups retained, no outcome-based selection",
            "limitations": ["Not a random or nationally representative sample",
                            "Approval-family exclusion is deliberately conservative",
                            "More metadata or previous development cohorts can reveal further overlap",
                            "No labels, thresholds or matching outcomes were learned from this holdout"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development-report", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--window-size", type=int, default=50000)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not 1 <= args.window_size <= 100000:
        raise ValueError("invalid holdout window size")
    report = json.loads(args.development_report.read_text())
    settings = IngestionSettings(_env_file=args.env_file)  # type: ignore[call-arg]
    if conninfo_to_dict(settings.database_url).get("host") not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("holdout freeze requires local PostgreSQL")
    with psycopg.connect(settings.database_url, options="-c default_transaction_read_only=on") as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        development = list(_fetch_local_raw_page(conn, source_batch_prefix=report["source_prefix"], after_id=0, limit=report["count"]))
        if digest(development) != report["source_digest"]:
            raise ValueError("development source changed")
        window = list(_fetch_local_raw_page(conn, source_batch_prefix=report["source_prefix"],
                                           after_id=development[-1][0], limit=args.window_size))
        if len(window) != args.window_size:
            raise ValueError("holdout window incomplete")
    result = freeze_groups(development, window)
    result.update(source_prefix=report["source_prefix"], read_only=True,
                  source_window_start=window[0][0], source_window_end=window[-1][0])
    write_private_json(args.output, result)
    print(json.dumps({key: result[key] for key in ("window_count", "eligible_count", "excluded", "group_count", "scored")}))


if __name__ == "__main__":
    main()
