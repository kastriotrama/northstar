"""Read-only, version-pinned cohort evaluation with private per-record results.

Run once per code root, then compare with compare_reports(). No rules, decisions,
progress rows or graph data are written. The report contains no plates or VINs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, default=lambda item: sorted(item) if isinstance(item, set | frozenset) else str(item),
    ).encode()).hexdigest()


def compare_reports(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    for report in (before, after):
        if len(report["records"]) != report["count"] or sum(report["counts"].values()) != report["count"]:
            raise ValueError("incomplete cohort accounting")
        if len({row["row_key"] for row in report["records"]}) != report["count"]:
            raise ValueError("duplicate cohort records")
    for pin in ("source_digest", "catalog_digest", "rules_digest", "count", "alignment_version"):
        if before[pin] != after[pin]:
            raise ValueError(f"comparison inputs differ: {pin}")
    transitions: Counter[str] = Counter()
    changed = []
    for old, new in zip(before["records"], after["records"], strict=True):
        if old["row_key"] != new["row_key"]:
            raise ValueError("comparison record order differs")
        transitions[f'{old["terminal"]}->{new["terminal"]}'] += 1
        if old["terminal"] != new["terminal"] or old["top_candidate_reference"] != new["top_candidate_reference"]:
            changed.append({"row_key": old["row_key"], "before": old, "after": new})
    return {
        "count": before["count"], "before_counts": before["counts"],
        "after_counts": after["counts"], "transitions": dict(transitions),
        "changed_records": changed,
        "before_reason_counts": before["reason_counts"],
        "after_reason_counts": after["reason_counts"],
        "independently_adjudicated": False,
        "source_digest": before["source_digest"], "catalog_digest": before["catalog_digest"],
        "rules_digest": before["rules_digest"], "alignment_version": before["alignment_version"],
        "before_code_root": before.get("code_root"), "after_code_root": after.get("code_root"),
    }


def compare_catalog_activation_reports(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    """Compare the same frozen cohort across an intentional catalog revision."""

    for report in (before, after):
        if len(report["records"]) != report["count"] or sum(
            report["counts"].values()
        ) != report["count"]:
            raise ValueError("incomplete cohort accounting")
        if len({row["row_key"] for row in report["records"]}) != report["count"]:
            raise ValueError("duplicate cohort records")
    for pin in (
        "source_digest",
        "rules_digest",
        "count",
        "alignment_version",
        "source_prefix",
        "rule_version",
        "context_policy_version",
        "source_model_policy_version",
    ):
        if before.get(pin) != after.get(pin):
            raise ValueError(f"catalog activation inputs differ: {pin}")
    before_by_key = {row["row_key"]: row for row in before["records"]}
    after_by_key = {row["row_key"]: row for row in after["records"]}
    if before_by_key.keys() != after_by_key.keys():
        raise ValueError("catalog activation cohort keys differ")
    transitions: Counter[str] = Counter()
    changed: list[dict[str, Any]] = []
    selected_identity_changes = 0
    for row_key, old in before_by_key.items():
        new = after_by_key[row_key]
        transitions[f'{old["terminal"]}->{new["terminal"]}'] += 1
        identity_changed = (
            old["top_candidate_reference"] != new["top_candidate_reference"]
        )
        selected_identity_changes += identity_changed
        if old["terminal"] != new["terminal"] or identity_changed:
            changed.append(
                {
                    "row_key": row_key,
                    "identity_changed": identity_changed,
                    "before": old,
                    "after": new,
                }
            )
    return {
        "count": before["count"],
        "before_catalog_version": before["catalog_version"],
        "after_catalog_version": after["catalog_version"],
        "before_catalog_digest": before["catalog_digest"],
        "after_catalog_digest": after["catalog_digest"],
        "before_counts": before["counts"],
        "after_counts": after["counts"],
        "transitions": dict(sorted(transitions.items())),
        "changed_record_count": len(changed),
        "selected_identity_change_count": selected_identity_changes,
        "changed_records": changed,
        "before_reason_counts": before["reason_counts"],
        "after_reason_counts": after["reason_counts"],
        "independently_adjudicated": False,
        "source_digest": before["source_digest"],
        "rules_digest": before["rules_digest"],
        "alignment_version": before["alignment_version"],
    }


def write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite existing evidence, even when a caller reuses a filename.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(payload, stream, sort_keys=True, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code-root", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--source-prefix", required=True)
    parser.add_argument("--catalog-version", required=True)
    parser.add_argument("--rule-version", required=True)
    parser.add_argument("--expected-candidates", type=int, required=True)
    parser.add_argument("--limit", type=int, default=20_000)
    parser.add_argument("--after-id", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument("--context-policy", type=Path)
    parser.add_argument("--context-policy-version")
    parser.add_argument("--context-policy-sha256")
    parser.add_argument("--source-model-policy", type=Path)
    parser.add_argument("--source-model-policy-version")
    parser.add_argument("--source-model-policy-sha256")
    args = parser.parse_args()
    if not 1 <= args.limit <= 100_000 or args.after_id < 0:
        raise ValueError("invalid cohort bounds")
    sys.path.insert(0, str(args.code_root.resolve()))
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    from ingestion.active_rules import load_active_rules
    from ingestion.config import IngestionSettings
    from ingestion.context_comparison import ContextComparisonPolicy, reviewed_context_policy
    from ingestion.fuzzy_matching import MODEL_RECOVERY_VERSION
    from ingestion.normalization_rules import PIPELINE_VERSION, normalize_ts_record
    from ingestion.tecdoc.match_diagnostics import RepairCohortDiagnostics
    from ingestion.tecdoc.match_run_adapters import (
        TecDocDryRunEvaluator,
        load_postgres_ktype_catalog,
    )
    from ingestion.tecdoc.model_aliases import ReviewedModelAliasIndex
    from ingestion.tecdoc.remote_match_run import _evaluate_raw_record, _fetch_local_raw_page
    from ingestion.tecdoc.source_model_rules import (
        ReviewedSourceModelPolicy,
        reviewed_source_model_policy,
    )

    settings = IngestionSettings(_env_file=args.env_file)  # type: ignore[call-arg]
    source_policy_args = (args.source_model_policy, args.source_model_policy_version, args.source_model_policy_sha256)
    if any(source_policy_args) and not all(source_policy_args):
        raise ValueError("source-model rules require explicit manifest, version and checksum")
    source_model_policy = (
        reviewed_source_model_policy(json.loads(args.source_model_policy.read_text()),
                                     expected_version=args.source_model_policy_version,
                                     expected_digest=args.source_model_policy_sha256)
        if args.source_model_policy else ReviewedSourceModelPolicy()
    )
    policy_args = (args.context_policy, args.context_policy_version, args.context_policy_sha256)
    if any(policy_args) and not all(policy_args):
        raise ValueError("context rules require an explicit manifest, version and checksum")
    context_policy = (
        reviewed_context_policy(json.loads(args.context_policy.read_text()),
                                expected_version=args.context_policy_version,
                                expected_digest=args.context_policy_sha256)
        if args.context_policy else ContextComparisonPolicy()
    )
    if conninfo_to_dict(settings.database_url).get("host") not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("cohort validation requires an explicitly local database")
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
        raw_rows = _fetch_local_raw_page(
            connection, source_batch_prefix=args.source_prefix, after_id=args.after_id,
            limit=args.limit,
        )
        if len(raw_rows) != args.limit:
            raise ValueError("cohort count differs from requested count")
        source_files = sorted((args.code_root / "ingestion").rglob("*.py"))
        code_digest = digest({str(path.relative_to(args.code_root)): path.read_text() for path in source_files})
        report: dict[str, Any] = {
            "code_root": str(args.code_root), "count": len(raw_rows),
            "source_prefix": args.source_prefix, "catalog_version": args.catalog_version,
            "rule_version": rules.version, "alignment_version": "unpinned-legacy",
            "source_digest": digest(raw_rows),
            "catalog_digest": digest([asdict(candidate) for candidate in catalog]),
            "rules_digest": digest([asdict(rules), manufacturers]),
            "read_only": True,
            "code_digest": code_digest,
            "pipeline_version": PIPELINE_VERSION,
            "matcher_change": MODEL_RECOVERY_VERSION,
            "context_policy_version": context_policy.version,
            "context_policy_digest": context_policy.content_digest,
            "activated_context_rule_count": len(context_policy.rules),
            "source_model_policy_version": source_model_policy.version,
            "source_model_policy_digest": source_model_policy.content_digest,
            "activated_source_model_rule_count": len(source_model_policy.rules),
        }
        baseline = json.loads(args.baseline_report.read_text()) if args.baseline_report else None
        if baseline is not None:
            for pin in ("source_digest", "catalog_digest", "rules_digest", "count", "alignment_version"):
                if baseline[pin] != report[pin]:
                    raise ValueError(f"baseline inputs differ: {pin}")
        print(json.dumps({"phase": "inputs_verified", "count": len(raw_rows), "code_digest": code_digest}), flush=True)
        evaluator = TecDocDryRunEvaluator(catalog, manufacturers, ReviewedModelAliasIndex(rules),
                                         context_policy=context_policy, source_model_policy=source_model_policy)
        diagnostics = RepairCohortDiagnostics()
        catalog_by_reference = {candidate.candidate_reference: candidate for candidate in catalog}
        records = []
        counts: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        started = time.monotonic()
        for ordinal, (source_id, raw) in enumerate(raw_rows, 1):
            evaluation = _evaluate_raw_record(
                raw, source_record_id=source_id, rule_set=rules,
                manufacturer_rules=manufacturers, evaluator=evaluator,
            )
            counts[evaluation.terminal] += 1
            reasons.update(evaluation.reason_codes)
            row_key = digest([source_id, raw])
            records.append({"row_key": row_key, **asdict(evaluation)})
            normalized = normalize_ts_record(raw, rule_set=rules, manufacturer_entity_rules=manufacturers)
            diagnostics.add(raw=raw, normalized=dict(normalized.normalized), terminal=evaluation.terminal,
                            reasons=evaluation.reason_codes, row_key=row_key,
                            candidate=catalog_by_reference.get(evaluation.top_candidate_reference or ""))
            if ordinal % 100 == 0:
                print(json.dumps({"processed": ordinal, "counts": counts,
                                  "elapsed_seconds": round(time.monotonic() - started, 1)}), flush=True)
        report.update(records=records, counts=dict(counts), reason_counts=dict(reasons))
        report["repair_diagnostics"] = diagnostics.report()
        if baseline is not None:
            report["comparison"] = compare_reports(baseline, report)
        if code_digest != digest({str(path.relative_to(args.code_root)): path.read_text() for path in source_files}):
            raise ValueError("matcher code changed during validation")
        write_private_json(args.output, report)
        print(json.dumps({"completed": len(records), "counts": counts}), flush=True)


if __name__ == "__main__":
    main()
