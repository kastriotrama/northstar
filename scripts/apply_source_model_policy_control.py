"""Apply an exact reviewed source-model policy to pinned full-cohort controls."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

from ingestion.active_rules import load_active_rules
from ingestion.config import IngestionSettings
from ingestion.context_comparison import reviewed_context_policy
from ingestion.normalization_rules import normalize_ts_record
from ingestion.tecdoc.match_run_adapters import (
    TecDocDryRunEvaluator,
    load_postgres_ktype_catalog,
)
from ingestion.tecdoc.model_aliases import ReviewedModelAliasIndex
from ingestion.tecdoc.remote_match_run import _evaluate_raw_record, _fetch_local_raw_page
from ingestion.tecdoc.source_model_rules import reviewed_source_model_policy
from scripts.validate_local_matcher_cohort import (
    compare_catalog_activation_reports,
    digest,
    write_private_json,
)


def apply_replacements(
    base: dict[str, Any], replacements: dict[str, dict[str, Any]], *,
    policy_version: str, policy_digest: str, base_sha256: str,
) -> dict[str, Any]:
    """Replace only exact replayed rows and recompute full accounting."""

    records = base.get("records")
    if not isinstance(records, list) or len(records) != base.get("count"):
        raise ValueError("base report accounting is incomplete")
    if any(not isinstance(row, dict) for row in records):
        raise TypeError("base report records must be objects")
    typed_records: list[dict[str, Any]] = records
    base_keys = {str(row["row_key"]) for row in typed_records}
    if len(base_keys) != len(records) or not replacements.keys() <= base_keys:
        raise ValueError("replacement row keys are duplicate or outside the base report")
    updated = [
        replacements[row_key] if row_key in replacements else row
        for row in typed_records
        for row_key in (str(row["row_key"]),)
    ]
    if any(str(old["row_key"]) != str(new["row_key"]) for old, new in zip(typed_records, updated, strict=True)):
        raise ValueError("source-model replay changed cohort identity")
    counts: Counter[str] = Counter(str(row["terminal"]) for row in updated)
    reasons: Counter[str] = Counter(
        str(reason) for row in updated for reason in row["reason_codes"]
    )
    report = {
        key: value for key, value in base.items()
        if key not in {"records", "counts", "reason_counts", "repair_diagnostics", "comparison"}
    }
    report.update(
        records=updated,
        counts=dict(counts),
        reason_counts=dict(reasons),
        source_model_policy_version=policy_version,
        source_model_policy_digest=policy_digest,
        activated_source_model_rule_count=2,
        selectively_replayed_source_model_rows=len(replacements),
        derived_from_report_sha256=base_sha256,
        selective_replay_proof=(
            "Every raw row was normalized and checked against the exact reviewed policy; "
            "only applicable rows were re-evaluated and all other evaluations are immutable."
        ),
    )
    if sum(counts.values()) != base["count"]:
        raise ValueError("derived report accounting is incomplete")
    return report


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--before-report", required=True, type=Path)
    parser.add_argument("--after-report", required=True, type=Path)
    parser.add_argument("--context-policy", required=True, type=Path)
    parser.add_argument("--context-policy-version", required=True)
    parser.add_argument("--context-policy-sha256", required=True)
    parser.add_argument("--source-model-policy", required=True, type=Path)
    parser.add_argument("--source-model-policy-version", required=True)
    parser.add_argument("--source-model-policy-sha256", required=True)
    parser.add_argument("--before-output", required=True, type=Path)
    parser.add_argument("--after-output", required=True, type=Path)
    parser.add_argument("--comparison-output", required=True, type=Path)
    args = parser.parse_args()

    before = json.loads(args.before_report.read_text())
    after = json.loads(args.after_report.read_text())
    for pin in ("source_prefix", "rule_version", "count", "source_digest", "rules_digest"):
        if before.get(pin) != after.get(pin):
            raise ValueError(f"base controls differ: {pin}")
    context_policy = reviewed_context_policy(
        json.loads(args.context_policy.read_text()),
        expected_version=args.context_policy_version,
        expected_digest=args.context_policy_sha256,
    )
    source_policy = reviewed_source_model_policy(
        json.loads(args.source_model_policy.read_text()),
        expected_version=args.source_model_policy_version,
        expected_digest=args.source_model_policy_sha256,
    )
    settings = IngestionSettings(_env_file=args.env_file)  # type: ignore[call-arg]
    if conninfo_to_dict(settings.database_url).get("host") not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("selective source-model replay requires local PostgreSQL")
    with psycopg.connect(
        settings.database_url, options="-c default_transaction_read_only=on"
    ) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        rules, manufacturers = load_active_rules(connection)
        raw_rows = _fetch_local_raw_page(
            connection, source_batch_prefix=before["source_prefix"], after_id=0,
            limit=before["count"],
        )
        if digest(raw_rows) != before["source_digest"]:
            raise ValueError("local source rows differ from base controls")
        if digest([asdict(rules), manufacturers]) != before["rules_digest"]:
            raise ValueError("active rules differ from base controls")
        before_catalog = load_postgres_ktype_catalog(
            connection, batch_id=before["catalog_version"]
        )
        after_catalog = load_postgres_ktype_catalog(
            connection, batch_id=after["catalog_version"]
        )
    if digest([asdict(row) for row in before_catalog]) != before["catalog_digest"]:
        raise ValueError("before catalog differs from base control")
    if digest([asdict(row) for row in after_catalog]) != after["catalog_digest"]:
        raise ValueError("after catalog differs from base control")
    if any(report.get("context_policy_digest") != context_policy.content_digest for report in (before, after)):
        raise ValueError("base controls use a different context policy")

    alias_index = ReviewedModelAliasIndex(rules)
    evaluators = (
        TecDocDryRunEvaluator(
            before_catalog, manufacturers, alias_index, context_policy=context_policy,
            source_model_policy=source_policy,
        ),
        TecDocDryRunEvaluator(
            after_catalog, manufacturers, alias_index, context_policy=context_policy,
            source_model_policy=source_policy,
        ),
    )
    replacements: tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]] = ({}, {})
    for source_id, raw in raw_rows:
        normalized = normalize_ts_record(
            raw, rule_set=rules, manufacturer_entity_rules=manufacturers
        )
        evidence = {
            key: raw.get(key) for key in (
                "eeg_type_approval", "type_text", "variant", "version", "body_code"
            )
        }
        resolution = source_policy.resolve(
            manufacturer=str(normalized.normalized.get("manufacturer") or ""),
            source_model=str(raw.get("model") or ""),
            source_evidence=evidence,
        )
        if resolution.target_model is None:
            continue
        row_key = digest([source_id, raw])
        for target, evaluator in zip(replacements, evaluators, strict=True):
            target[row_key] = {
                "row_key": row_key,
                **asdict(_evaluate_raw_record(
                    raw, source_record_id=source_id, rule_set=rules,
                    manufacturer_rules=manufacturers, evaluator=evaluator,
                )),
            }
    if not replacements[0] or replacements[0].keys() != replacements[1].keys():
        raise ValueError("reviewed source-model policy matched no stable cohort")

    derived_before = apply_replacements(
        before, replacements[0], policy_version=source_policy.version,
        policy_digest=source_policy.content_digest, base_sha256=_sha256(args.before_report),
    )
    derived_after = apply_replacements(
        after, replacements[1], policy_version=source_policy.version,
        policy_digest=source_policy.content_digest, base_sha256=_sha256(args.after_report),
    )
    comparison = compare_catalog_activation_reports(derived_before, derived_after)
    comparison["selectively_replayed_source_model_rows"] = len(replacements[0])
    comparison["source_model_policy_version"] = source_policy.version
    comparison["source_model_policy_digest"] = source_policy.content_digest
    comparison["source_model_policy_repair_rows"] = [
        {
            "row_key": row_key,
            "before": replacements[0][row_key],
            "after": replacements[1][row_key],
        }
        for row_key in sorted(replacements[0])
    ]
    write_private_json(args.before_output, derived_before)
    write_private_json(args.after_output, derived_after)
    write_private_json(args.comparison_output, comparison)
    print(json.dumps({
        "replayed": len(replacements[0]),
        "before_counts": derived_before["counts"],
        "after_counts": derived_after["counts"],
        "changed": comparison["changed_record_count"],
        "identity_changes": comparison["selected_identity_change_count"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
