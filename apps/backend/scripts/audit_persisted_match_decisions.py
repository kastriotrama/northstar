"""Re-evaluate current persisted decision heads against a pinned matcher policy.

The audit is PostgreSQL read-only. It emits no plates/VINs and never advances a
decision head, persists a new decision, attaches an alias, or writes Neo4j.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from ingestion.active_rules import load_active_rules
from ingestion.config import IngestionSettings
from ingestion.context_comparison import reviewed_context_policy
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator, load_postgres_ktype_catalog
from ingestion.tecdoc.model_aliases import ReviewedModelAliasIndex
from ingestion.tecdoc.remote_match_run import _evaluate_raw_record
from scripts.validate_local_matcher_cohort import digest, write_private_json


def compare_decision(
    row: dict[str, Any], evaluation: dict[str, Any]
) -> dict[str, Any]:
    """Return a plate-free comparison between one current head and one replay."""

    previous_reference = str(row["selected_candidate_reference"])
    current_reference = evaluation.get("top_candidate_reference")
    return {
        "decision_id": str(row["decision_id"]),
        "source_batch_id": str(row["source_batch_id"]),
        "source_record_id": int(row["source_record_id"]),
        "previous_terminal": str(row["route"]),
        "current_terminal": str(evaluation["terminal"]),
        "previous_candidate_reference": previous_reference,
        "current_candidate_reference": current_reference,
        "same_candidate": current_reference == previous_reference,
        "reason_codes": list(evaluation["reason_codes"]),
    }


def summarize_comparisons(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate transition and identity changes without source identifiers."""

    transitions: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for item in comparisons:
        transitions[f'{item["previous_terminal"]}->{item["current_terminal"]}'] += 1
        reason_counts.update(item["reason_codes"])
    return {
        "count": len(comparisons),
        "transitions": dict(sorted(transitions.items())),
        "same_candidate": sum(item["same_candidate"] for item in comparisons),
        "changed_candidate": sum(not item["same_candidate"] for item in comparisons),
        "reason_counts": dict(reason_counts.most_common()),
    }


def _load_current_heads(connection: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT d.decision_id, d.source_batch_id, d.source_record_id, d.route, "
            "d.selected_candidate_reference, d.candidate_catalog_version, d.policy_version, "
            "raw.raw_record "
            "FROM core.match_decision_heads h "
            "JOIN core.match_routing_decisions d ON d.decision_id=h.decision_id "
            "LEFT JOIN staging.transportstyrelsen_raw raw "
            "ON raw.id=d.source_record_id AND raw.source_batch_id=d.source_batch_id "
            "WHERE h.source_system='Transportstyrelsen' "
            "AND h.source_version='ts-v323-20260817' "
            "ORDER BY d.source_record_id, d.decision_id"
        )
        return [dict(row) for row in cursor.fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--catalog-version", required=True)
    parser.add_argument("--expected-candidates", required=True, type=int)
    parser.add_argument("--expected-decisions", required=True, type=int)
    parser.add_argument("--rule-version", required=True)
    parser.add_argument("--context-policy", required=True, type=Path)
    parser.add_argument("--context-policy-version", required=True)
    parser.add_argument("--context-policy-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    settings = IngestionSettings(_env_file=args.env_file)  # type: ignore[call-arg]
    if conninfo_to_dict(settings.database_url).get("host") not in {
        "localhost", "127.0.0.1", "::1",
    }:
        raise ValueError("persisted-decision audit requires local PostgreSQL")
    context_policy = reviewed_context_policy(
        json.loads(args.context_policy.read_text()),
        expected_version=args.context_policy_version,
        expected_digest=args.context_policy_sha256,
    )
    code_root = Path(__file__).resolve().parents[1]
    source_files = sorted((code_root / "ingestion").rglob("*.py"))
    code_digest = digest(
        {str(path.relative_to(code_root)): path.read_text() for path in source_files}
    )
    with psycopg.connect(
        settings.database_url, options="-c default_transaction_read_only=on"
    ) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        rules, manufacturers = load_active_rules(connection)
        if rules.version != args.rule_version:
            raise ValueError("active rules differ from requested version")
        catalog = load_postgres_ktype_catalog(connection, batch_id=args.catalog_version)
        if len(catalog) != args.expected_candidates:
            raise ValueError("catalog count differs from requested count")
        rows = _load_current_heads(connection)
        if len(rows) != args.expected_decisions:
            raise ValueError("current decision count differs from requested count")
        if any(row["raw_record"] is None for row in rows):
            raise ValueError("one or more current decisions have no retained raw source row")
        old_catalogs = sorted({str(row["candidate_catalog_version"]) for row in rows})
        old_policies = sorted({str(row["policy_version"]) for row in rows})

    evaluator = TecDocDryRunEvaluator(
        catalog, manufacturers, ReviewedModelAliasIndex(rules),
        context_policy=context_policy,
    )
    comparisons: list[dict[str, Any]] = []
    started = time.monotonic()
    for ordinal, row in enumerate(rows, 1):
        evaluation = _evaluate_raw_record(
            dict(row["raw_record"]), source_record_id=int(row["source_record_id"]),
            rule_set=rules, manufacturer_rules=manufacturers, evaluator=evaluator,
        )
        comparisons.append(compare_decision(row, asdict(evaluation)))
        if ordinal % 100 == 0:
            summary = summarize_comparisons(comparisons)
            print(json.dumps({
                "processed": ordinal,
                "transitions": summary["transitions"],
                "changed_candidate": summary["changed_candidate"],
                "elapsed_seconds": round(time.monotonic() - started, 1),
            }), flush=True)

    if code_digest != digest(
        {str(path.relative_to(code_root)): path.read_text() for path in source_files}
    ):
        raise ValueError("matcher code changed during persisted-decision audit")
    summary = summarize_comparisons(comparisons)
    changed = [
        item for item in comparisons
        if item["current_terminal"] != item["previous_terminal"] or not item["same_candidate"]
    ]
    payload = {
        **summary,
        "changed_records": changed,
        "changed_record_count": len(changed),
        "old_candidate_catalog_versions": old_catalogs,
        "old_policy_versions": old_policies,
        "new_candidate_catalog_version": args.catalog_version,
        "new_catalog_digest": digest([asdict(candidate) for candidate in catalog]),
        "rule_version": rules.version,
        "rules_digest": digest([asdict(rules), manufacturers]),
        "context_policy_version": context_policy.version,
        "context_policy_digest": context_policy.content_digest,
        "code_digest": code_digest,
        "contains_private_plates": False,
        "read_only": True,
        "decisions_persisted": 0,
        "aliases_written": 0,
        "neo4j_writes": 0,
        "independently_adjudicated": False,
    }
    write_private_json(args.output, payload)
    print(json.dumps({
        "completed": summary["count"],
        "transitions": summary["transitions"],
        "changed_candidate": summary["changed_candidate"],
        "changed_record_count": len(changed),
    }), flush=True)


if __name__ == "__main__":
    main()
