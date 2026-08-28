"""Replay changed accepted identities into a private, pending-review evidence packet."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psycopg
from psycopg.conninfo import conninfo_to_dict

from ingestion.active_rules import load_active_rules
from ingestion.config import IngestionSettings
from ingestion.fuzzy_matching import FuzzyMatchResult, VehicleMatchQuery
from ingestion.normalization_rules import normalize_ts_record
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator, load_postgres_ktype_catalog
from ingestion.tecdoc.model_aliases import ReviewedModelAliasIndex
from ingestion.tecdoc.remote_match_run import _evaluate_raw_record, _fetch_local_raw_page
from scripts.validate_local_matcher_cohort import digest, write_private_json


def review_source_evidence(raw: dict[str, Any]) -> dict[str, Any]:
    """Retain measurement inputs as well as names; kw/ccm are TS source fields."""
    return {key: raw.get(key) for key in (
        "manufacturer", "vehicle_type", "brand", "model", "variant", "version", "type_text",
        "model_no", "eeg_type_approval", "body_code", "body_code2", "body_code_extra", "is_4wd",
        "model_year", "production_year", "fuel1", "fuel2", "engine_code", "gearbox",
        "kw", "power_kw", "power_ps", "ccm", "displacement_cc", "displacement_l",
    )}


def changed_accepted_records(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["row_key"]: row for row in report["comparison"]["changed_records"]
            if "resolved" in {row["before"]["terminal"], row["after"]["terminal"]}}


def capture_evaluation(
    evaluator: TecDocDryRunEvaluator, raw: dict[str, Any], *, source_id: int,
    rules: Any, manufacturers: Any,
) -> dict[str, Any]:
    attempts = []
    base_match = evaluator._matcher.match
    alias_match = evaluator._alias_matcher.match

    def record(query: VehicleMatchQuery, *, aliases: bool) -> FuzzyMatchResult:
        result = alias_match(query) if aliases else base_match(query)
        attempts.append({
            "query": asdict(query), "reviewed_aliases_enabled": aliases,
            "match_reason": result.reason, "scope": result.scope,
            "candidates": result.review_candidates(),
            "routing": asdict(evaluator._router.route(result)),
        })
        return result

    evaluator._cache.clear()  # Force the real scorer to emit evidence for this row.
    with patch.object(evaluator._matcher, "match", side_effect=lambda q: record(q, aliases=False)), \
            patch.object(evaluator._alias_matcher, "match", side_effect=lambda q: record(q, aliases=True)):
        outcome = _evaluate_raw_record(raw, source_record_id=source_id, rule_set=rules,
                                       manufacturer_rules=manufacturers, evaluator=evaluator)
    return {"evaluation": asdict(outcome), "attempts": attempts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    if (report.get("activated_context_rule_count") != 0
            or report.get("activated_source_model_rule_count", 0) != 0
            or report["alignment_version"] != "unpinned-legacy"):
        raise ValueError("this evidence replay supports the no-new-rules cohort only")
    code_root = Path(report["code_root"])
    if Path(__file__).resolve().parents[1] != code_root.resolve():
        raise ValueError("run evidence replay from the completed cohort's code root")
    code_digest = digest({str(p.relative_to(code_root)): p.read_text()
                          for p in sorted((code_root / "ingestion").rglob("*.py"))})
    if code_digest != report["code_digest"]:
        raise ValueError("code differs from completed cohort")
    targets = changed_accepted_records(report)
    settings = IngestionSettings(_env_file=args.env_file)  # type: ignore[call-arg]
    if conninfo_to_dict(settings.database_url).get("host") not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("evidence replay requires local PostgreSQL")
    with psycopg.connect(settings.database_url, options="-c default_transaction_read_only=on") as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        rules, manufacturers = load_active_rules(conn)
        catalog = load_postgres_ktype_catalog(conn, batch_id=report["catalog_version"])
        rows = _fetch_local_raw_page(conn, source_batch_prefix=report["source_prefix"],
                                     after_id=0, limit=report["count"])
        if digest(rows) != report["source_digest"] or digest([asdict(c) for c in catalog]) != report["catalog_digest"]:
            raise ValueError("source/catalog differs from completed cohort")
        if digest([asdict(rules), manufacturers]) != report["rules_digest"]:
            raise ValueError("rules differ from completed cohort")
    evaluator = TecDocDryRunEvaluator(catalog, manufacturers, ReviewedModelAliasIndex(rules))
    by_reference = {candidate.candidate_reference: candidate for candidate in catalog}
    items = []
    for source_id, raw in rows:
        row_key = digest([source_id, raw])
        if row_key not in targets:
            continue
        captured = capture_evaluation(evaluator, raw, source_id=source_id,
                                      rules=rules, manufacturers=manufacturers)
        expected = dict(targets[row_key]["after"])
        expected.pop("row_key")
        # JSON reports encode tuples as lists.
        if json.loads(json.dumps(captured["evaluation"])) != expected:
            raise ValueError("changed-case replay diverged from completed cohort")
        normalized = normalize_ts_record(raw, rule_set=rules, manufacturer_entity_rules=manufacturers)
        references = {candidate["candidate_reference"] for attempt in captured["attempts"]
                      for candidate in attempt["candidates"]}
        old_reference = targets[row_key]["before"]["top_candidate_reference"]
        if old_reference:
            references.add(old_reference)
        items.append({
            "row_key": row_key, "plate": raw.get("plate"), "change": targets[row_key],
            "review_status": "pending_domain_review", "verdict": None,
            "raw_source_evidence": review_source_evidence(raw),
            "normalization": asdict(normalized), **captured,
            "catalog_candidates": [asdict(by_reference[reference]) for reference in sorted(references)
                                   if reference in by_reference],
        })
        if len(items) % 25 == 0:
            print(json.dumps({"review_cases_replayed": len(items), "total": len(targets)}), flush=True)
    if len(items) != len(targets):
        raise ValueError("incomplete review packet")
    payload = {key: report[key] for key in ("source_digest", "catalog_digest", "rules_digest", "code_digest")}
    payload.update(count=len(items), items=items, contains_private_plates=True,
                   independently_adjudicated=False, read_only=True)
    # Round-trip dataclasses' frozen sets using the same deterministic treatment as catalog digests.
    payload = json.loads(json.dumps(payload, default=lambda value: sorted(value) if isinstance(value, set | frozenset) else str(value)))
    write_private_json(args.output, payload)
    print(json.dumps({"completed_review_cases": len(items)}), flush=True)


if __name__ == "__main__":
    main()
