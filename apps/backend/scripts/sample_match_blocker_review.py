"""Populate a bounded stakeholder review sample from a pinned local match run.

The script stores references to existing TS staging rows and sanitized matcher
candidates. It does not persist match decisions, attach aliases, or write Neo4j.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from ingestion.active_rules import load_active_rules
from ingestion.config import IngestionSettings
from ingestion.context_comparison import reviewed_context_policy
from ingestion.review_queue import CandidateMatch, enqueue_review_item
from ingestion.review_queue_migrations import run_review_queue_migrations
from ingestion.tecdoc.blocker_review import CATEGORIES, classify_match_blocker
from ingestion.tecdoc.match_run_adapters import (
    TecDocDryRunEvaluator,
    load_postgres_ktype_catalog,
)
from ingestion.tecdoc.model_aliases import ReviewedModelAliasIndex
from ingestion.tecdoc.remote_match_run import _evaluate_raw_record
from ingestion.tecdoc.source_model_rules import reviewed_source_model_policy

REVIEW_NAMESPACE = UUID("70e80cbe-2090-4c80-86e1-05ded125cbf3")


def _review_candidates(evaluation: Any) -> tuple[CandidateMatch, ...]:
    return tuple(
        CandidateMatch(
            candidate_reference=str(candidate["candidate_reference"]),
            candidate_type=str(candidate["candidate_type"]),
            confidence=float(candidate["confidence"]),
            evidence=dict(candidate.get("evidence") or {}),
        )
        for candidate in evaluation.candidate_matches
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--release-manifest", required=True, type=Path)
    parser.add_argument("--operation-id", required=True, type=UUID)
    parser.add_argument("--source-prefix", required=True)
    parser.add_argument("--scan-limit", type=int, default=20_000)
    parser.add_argument("--per-category", type=int, default=20)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.scan_limit <= 100_000 or not 1 <= args.per_category <= 100:
        raise ValueError("sample bounds are invalid")

    release = json.loads(args.release_manifest.read_text())
    pins = dict(release["pins"])
    root = args.release_manifest.parents[2]
    context_policy = reviewed_context_policy(
        json.loads((root / "ingestion/reviewed_context_policies/volvo_bodywork_reviewed_v1_20260830.json").read_text()),
        expected_version=str(pins["context_policy_version"]),
        expected_digest=str(pins["context_policy_payload_sha256"]),
    )
    source_model_policy = reviewed_source_model_policy(
        json.loads((root / "ingestion/reviewed_source_model_policies/peugeot_3008_hns_reviewed_v1_20260831.json").read_text()),
        expected_version=str(pins["source_model_policy_version"]),
        expected_digest=str(pins["source_model_policy_payload_sha256"]),
    )
    settings = IngestionSettings(_env_file=args.env_file)  # type: ignore[call-arg]
    if conninfo_to_dict(settings.database_url).get("host") not in {
        "localhost", "127.0.0.1", "::1",
    }:
        raise ValueError("match review sampling requires local PostgreSQL")

    selected: Counter[str] = Counter()
    with psycopg.connect(settings.database_url) as connection:
        run_review_queue_migrations(connection)
        rules, manufacturers = load_active_rules(connection)
        if rules.version != pins["normalization_rule_version"]:
            raise ValueError("active rules differ from the release manifest")
        catalog_version = str(pins["v6_catalog_version"])
        catalog = load_postgres_ktype_catalog(connection, batch_id=catalog_version)
        evaluator = TecDocDryRunEvaluator(
            catalog,
            manufacturers,
            ReviewedModelAliasIndex(rules),
            context_policy=context_policy,
            source_model_policy=source_model_policy,
        )
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT id, source_batch_id, raw_record FROM staging.transportstyrelsen_raw "
                "WHERE source_batch_id LIKE %s ORDER BY id LIMIT %s",
                (f"{args.source_prefix}%", args.scan_limit),
            )
            rows = tuple(cursor.fetchall())
        for row in rows:
            source_id = int(row["id"])
            evaluation = _evaluate_raw_record(
                dict(row["raw_record"]),
                source_record_id=source_id,
                rule_set=rules,
                manufacturer_rules=manufacturers,
                evaluator=evaluator,
            )
            category = classify_match_blocker(evaluation)
            if category is None or selected[category.code] >= args.per_category:
                continue
            selected[category.code] += 1
            if args.commit:
                enqueue_review_item(
                    connection,
                    review_id=uuid5(
                        REVIEW_NAMESPACE,
                        f"{args.operation_id}:{source_id}:{category.code}",
                    ),
                    source_system="Transportstyrelsen",
                    source_batch_id=str(row["source_batch_id"]),
                    source_table="staging.transportstyrelsen_raw",
                    source_record_id=source_id,
                    reason_code=f"ts_tecdoc_match_blocker:{category.code}",
                    reason_detail=",".join(evaluation.reason_codes),
                    target_entity_type=f"ts_tecdoc_match:{args.operation_id}",
                    candidate_matches=_review_candidates(evaluation),
                    confidence=evaluation.confidence,
                )
            if all(selected[item.code] >= args.per_category for item in CATEGORIES):
                break
        if args.commit:
            connection.commit()
        else:
            connection.rollback()
    print(json.dumps({
        "operation_id": str(args.operation_id),
        "scanned": len(rows),
        "selected": dict(sorted(selected.items())),
        "committed": args.commit,
        "match_decisions": 0,
        "neo4j_writes": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
