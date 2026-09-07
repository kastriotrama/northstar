"""Run one locked v5/v6 comparison over the exact frozen matcher holdout."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

from ingestion.active_rules import load_active_rules
from ingestion.config import IngestionSettings
from ingestion.context_comparison import reviewed_context_policy
from ingestion.match_run_service import MatchSourceRecord
from ingestion.normalization_rules import normalize_ts_record
from ingestion.tecdoc.match_run_adapters import (
    TecDocDryRunEvaluator,
    load_postgres_ktype_catalog,
)
from ingestion.tecdoc.model_aliases import ReviewedModelAliasIndex
from ingestion.tecdoc.source_model_rules import reviewed_source_model_policy
from scripts.validate_local_matcher_cohort import digest, write_private_json


def validate_acceptance_pins(
    acceptance: dict[str, Any], *, rule_version: str, rules_digest: str,
    context_policy_version: str, context_policy_sha256: str, code_digest: str,
    source_model_policy_version: str, source_model_policy_sha256: str,
    context_policy_digest: str, source_model_policy_digest: str,
) -> None:
    """Reject a run that differs from the policy fixed before unblinding."""

    expected = {
        "rule_version": rule_version,
        "rules_digest": rules_digest,
        "context_policy_version": context_policy_version,
        "context_policy_payload_sha256": context_policy_sha256,
        "context_policy_digest": context_policy_digest,
        "source_model_policy_version": source_model_policy_version,
        "source_model_policy_payload_sha256": source_model_policy_sha256,
        "source_model_policy_digest": source_model_policy_digest,
        "expected_code_digest": code_digest,
    }
    divergent = [key for key, value in expected.items() if acceptance.get(key) != value]
    if divergent:
        raise ValueError(f"holdout run differs from acceptance pins: {', '.join(divergent)}")


def assess_holdout(
    records: list[dict[str, Any]], criteria: dict[str, Any], *, expected_count: int
) -> dict[str, Any]:
    """Apply acceptance criteria fixed before the holdout was unblinded."""

    transitions: Counter[str] = Counter()
    new_hard_conflicts = 0
    changed_resolved_identities = 0
    unsafe_resolution_gains = 0
    resolved_conflict_reasons = 0
    unresolved_candidate_changes = 0
    for row in records:
        before = row["before"]
        after = row["after"]
        transitions[f'{before["terminal"]}->{after["terminal"]}'] += 1
        identity_changed = (
            before["top_candidate_reference"] != after["top_candidate_reference"]
        )
        if after["terminal"] == "hard_conflict" and before["terminal"] != "hard_conflict":
            new_hard_conflicts += 1
        if after["terminal"] == "resolved" and identity_changed:
            changed_resolved_identities += 1
        if after["terminal"] == "resolved" and (
            before["terminal"] != "provisional" or identity_changed
        ) and before["terminal"] != "resolved":
            unsafe_resolution_gains += 1
        if after["terminal"] == "resolved" and any(
            str(reason).startswith(("conflict:", "context_conflict:"))
            for reason in after["reason_codes"]
        ):
            resolved_conflict_reasons += 1
        if identity_changed and after["terminal"] != "resolved":
            unresolved_candidate_changes += 1
    metrics = {
        "record_count": len(records),
        "new_hard_conflicts": new_hard_conflicts,
        "changed_resolved_identities": changed_resolved_identities,
        "unsafe_resolution_gains": unsafe_resolution_gains,
        "resolved_conflict_reasons": resolved_conflict_reasons,
        "unresolved_candidate_changes": unresolved_candidate_changes,
        "transition_counts": dict(sorted(transitions.items())),
    }
    failures = []
    if criteria.get("require_complete_accounting") and len(records) != expected_count:
        failures.append("incomplete_accounting")
    thresholds = {
        "new_hard_conflicts": "maximum_new_hard_conflicts",
        "changed_resolved_identities": "maximum_changed_resolved_identities",
        "unsafe_resolution_gains": "maximum_unsafe_resolution_gains",
        "resolved_conflict_reasons": "maximum_resolved_conflict_reasons",
    }
    for metric, criterion in thresholds.items():
        if int(str(metrics[metric])) > int(criteria[criterion]):
            failures.append(criterion)
    return {**metrics, "passed": not failures, "failed_criteria": failures}


def _source_record(raw: dict[str, Any], source_id: int, rules: Any, manufacturers: Any) -> MatchSourceRecord:
    outcome = normalize_ts_record(
        raw, rule_set=rules, manufacturer_entity_rules=manufacturers
    )
    return MatchSourceRecord(
        source_id,
        {
            "normalization_status": outcome.status,
            "normalized": outcome.normalized,
            "candidates": outcome.candidates,
            "review_reasons": list(outcome.review_reasons),
            "source_evidence": {
                field: raw.get(field)
                for field in (
                    "body_code", "is_4wd", "brand", "model", "variant", "version",
                    "model_no", "type_text", "eeg_type_approval",
                )
            },
        },
    )


def _load_frozen_rows(
    connection: psycopg.Connection[Any], holdout: dict[str, Any]
) -> list[tuple[int, dict[str, Any]]]:
    expected = {int(row["source_record_id"]): str(row["row_key"]) for row in holdout["rows"]}
    if len(expected) != len(holdout["rows"]):
        raise ValueError("holdout source IDs repeat")
    loaded: dict[int, dict[str, Any]] = {}
    source_ids = sorted(expected)
    with connection.cursor() as cursor:
        for offset in range(0, len(source_ids), 5_000):
            chunk = source_ids[offset:offset + 5_000]
            cursor.execute(
                "SELECT id, raw_record FROM staging.transportstyrelsen_raw "
                "WHERE source_batch_id LIKE %s AND id = ANY(%s)",
                (f'{holdout["source_prefix"]}%', chunk),
            )
            loaded.update((int(row[0]), dict(row[1])) for row in cursor.fetchall())
    if loaded.keys() != expected.keys():
        raise ValueError("frozen holdout source rows are missing or divergent")
    rows = [(source_id, loaded[source_id]) for source_id in source_ids]
    if any(digest([source_id, raw]) != expected[source_id] for source_id, raw in rows):
        raise ValueError("frozen holdout row checksum differs")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--holdout", required=True, type=Path)
    parser.add_argument("--acceptance", required=True, type=Path)
    parser.add_argument("--rule-version", required=True)
    parser.add_argument("--rules-digest", required=True)
    parser.add_argument("--context-policy", required=True, type=Path)
    parser.add_argument("--context-policy-version", required=True)
    parser.add_argument("--context-policy-sha256", required=True)
    parser.add_argument("--source-model-policy", required=True, type=Path)
    parser.add_argument("--source-model-policy-version", required=True)
    parser.add_argument("--source-model-policy-sha256", required=True)
    parser.add_argument("--expected-candidates", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    holdout_bytes = args.holdout.read_bytes()
    holdout = json.loads(holdout_bytes)
    acceptance_bytes = args.acceptance.read_bytes()
    acceptance = json.loads(acceptance_bytes)
    acceptance_sha256 = hashlib.sha256(acceptance_bytes).hexdigest()
    holdout_sha256 = hashlib.sha256(holdout_bytes).hexdigest()
    if acceptance.get("status") != "approved_before_unblinding":
        raise ValueError("holdout acceptance criteria were not approved before unblinding")
    if acceptance.get("holdout_sha256") != holdout_sha256:
        raise ValueError("holdout checksum differs from acceptance manifest")
    if holdout.get("scored") or holdout.get("eligible_count") != acceptance.get("expected_records"):
        raise ValueError("holdout is already scored or has unexpected accounting")
    if holdout.get("group_count") != acceptance.get("expected_groups"):
        raise ValueError("holdout group count differs from acceptance manifest")

    settings = IngestionSettings(_env_file=args.env_file)  # type: ignore[call-arg]
    if conninfo_to_dict(settings.database_url).get("host") not in {
        "localhost", "127.0.0.1", "::1",
    }:
        raise ValueError("frozen holdout validation requires local PostgreSQL")
    context_policy = reviewed_context_policy(
        json.loads(args.context_policy.read_text()),
        expected_version=args.context_policy_version,
        expected_digest=args.context_policy_sha256,
    )
    source_model_policy = reviewed_source_model_policy(
        json.loads(args.source_model_policy.read_text()),
        expected_version=args.source_model_policy_version,
        expected_digest=args.source_model_policy_sha256,
    )
    code_root = Path(__file__).resolve().parents[1]
    source_files = sorted((code_root / "ingestion").rglob("*.py"))
    code_digest = digest(
        {str(path.relative_to(code_root)): path.read_text() for path in source_files}
    )
    validate_acceptance_pins(
        acceptance,
        rule_version=args.rule_version,
        rules_digest=args.rules_digest,
        context_policy_version=args.context_policy_version,
        context_policy_sha256=args.context_policy_sha256,
        context_policy_digest=context_policy.content_digest,
        source_model_policy_version=args.source_model_policy_version,
        source_model_policy_sha256=args.source_model_policy_sha256,
        source_model_policy_digest=source_model_policy.content_digest,
        code_digest=code_digest,
    )
    with psycopg.connect(
        settings.database_url, options="-c default_transaction_read_only=on"
    ) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        rules, manufacturers = load_active_rules(connection)
        if rules.version != args.rule_version:
            raise ValueError("active rules differ from requested version")
        rules_digest = digest([asdict(rules), manufacturers])
        if rules_digest != args.rules_digest:
            raise ValueError("active rules digest differs from requested digest")
        before_catalog = load_postgres_ktype_catalog(
            connection, batch_id=acceptance["v5_catalog_version"]
        )
        after_catalog = load_postgres_ktype_catalog(
            connection, batch_id=acceptance["v6_catalog_version"]
        )
        if len(before_catalog) != args.expected_candidates or len(after_catalog) != args.expected_candidates:
            raise ValueError("candidate catalog count differs")
        if digest([asdict(row) for row in before_catalog]) != acceptance["v5_catalog_digest"]:
            raise ValueError("v5 catalog digest differs")
        if digest([asdict(row) for row in after_catalog]) != acceptance["v6_catalog_digest"]:
            raise ValueError("v6 catalog digest differs")
        rows = _load_frozen_rows(connection, holdout)

    alias_index = ReviewedModelAliasIndex(rules)
    before_evaluator = TecDocDryRunEvaluator(
        before_catalog, manufacturers, alias_index, context_policy=context_policy,
        source_model_policy=source_model_policy,
    )
    after_evaluator = TecDocDryRunEvaluator(
        after_catalog, manufacturers, alias_index, context_policy=context_policy,
        source_model_policy=source_model_policy,
    )
    records = []
    before_counts: Counter[str] = Counter()
    after_counts: Counter[str] = Counter()
    started = time.monotonic()
    group_by_row = {str(row["row_key"]): str(row["group_key"]) for row in holdout["rows"]}
    for ordinal, (source_id, raw) in enumerate(rows, 1):
        record = _source_record(raw, source_id, rules, manufacturers)
        before = asdict(before_evaluator.evaluate(record))
        after = asdict(after_evaluator.evaluate(record))
        row_key = digest([source_id, raw])
        before_counts[before["terminal"]] += 1
        after_counts[after["terminal"]] += 1
        records.append({
            "row_key": row_key,
            "group_key": group_by_row[row_key],
            "before": before,
            "after": after,
        })
        if ordinal % 100 == 0:
            print(json.dumps({
                "processed": ordinal,
                "before_counts": before_counts,
                "after_counts": after_counts,
                "elapsed_seconds": round(time.monotonic() - started, 1),
            }), flush=True)
    assessment = assess_holdout(
        records, acceptance["criteria"], expected_count=acceptance["expected_records"]
    )
    if code_digest != digest(
        {str(path.relative_to(code_root)): path.read_text() for path in source_files}
    ):
        raise ValueError("ingestion code changed during holdout validation")
    payload = {
        "holdout_sha256": holdout_sha256,
        "acceptance_version": acceptance["version"],
        "acceptance_sha256": acceptance_sha256,
        "code_digest": code_digest,
        "rule_version": rules.version,
        "rules_digest": rules_digest,
        "context_policy_version": context_policy.version,
        "context_policy_digest": context_policy.content_digest,
        "source_model_policy_version": source_model_policy.version,
        "source_model_policy_digest": source_model_policy.content_digest,
        "v5_catalog_version": acceptance["v5_catalog_version"],
        "v5_catalog_digest": acceptance["v5_catalog_digest"],
        "v6_catalog_version": acceptance["v6_catalog_version"],
        "v6_catalog_digest": acceptance["v6_catalog_digest"],
        "record_count": len(records),
        "group_count": holdout["group_count"],
        "before_counts": dict(before_counts),
        "after_counts": dict(after_counts),
        "assessment": assessment,
        "records": records,
        "scored": True,
        "read_only": True,
        "contains_private_plates": False,
        "contains_private_vins": False,
        "postgres_writes": 0,
        "neo4j_writes": 0,
    }
    write_private_json(args.output, payload)
    print(json.dumps({
        "completed": len(records),
        "before_counts": before_counts,
        "after_counts": after_counts,
        "assessment": assessment,
    }), flush=True)


if __name__ == "__main__":
    main()
