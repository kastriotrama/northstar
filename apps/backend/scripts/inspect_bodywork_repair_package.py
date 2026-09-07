"""Read-only full sibling evidence for the three pinned bodywork repair groups.

This is diagnostic replay, not a new ranker or a compatibility rule. Output is
private, contains plates and has no acceptance verdicts.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

from ingestion.active_rules import load_active_rules
from ingestion.config import IngestionSettings
from ingestion.fuzzy_matching import FuzzyVehicleMatcher, VehicleMatchQuery
from ingestion.normalization_rules import normalize_ts_record
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator, load_postgres_ktype_catalog
from ingestion.tecdoc.model_aliases import ReviewedModelAliasIndex
from ingestion.tecdoc.remote_match_run import _fetch_local_raw_page
from scripts.replay_match_repair_evidence import capture_evaluation, review_source_evidence
from scripts.validate_local_matcher_cohort import digest, write_private_json

# Exact populations from the ranked report, not all cars bearing these names.
GROUPS = {
    "xc40": ("VOLVO", "XC40", "XC40 (536)", "XC40"),
    "xc60_ii": ("VOLVO", "XC60", "XC60 II (246)", "XC60"),
    "golf_vii": ("VOLKSWAGEN, VW", "GOLF", "GOLF VII (5G1, BQ1, BE1, BE2)", "GOLF VII"),
}


def select_group(raw: dict[str, Any], record: dict[str, Any], candidate_model: str) -> str | None:
    if raw.get("body_code") != "AC" or "context_conflict:bodywork" not in record["reason_codes"]:
        return None
    for group, (brand, model, top_model, _) in GROUPS.items():
        if (raw.get("brand"), raw.get("model"), candidate_model) == (brand, model, top_model):
            return group
    return None


def family_evidence(
    matcher: FuzzyVehicleMatcher, query: VehicleMatchQuery, *, family: str,
    returned_references: set[str],
) -> list[dict[str, Any]]:
    """Expose the actual scorer, including siblings below candidate threshold."""
    if matcher._config.bodywork_discriminating_weight != 1.0:
        raise ValueError("sibling diagnostic requires the pinned unit bodywork weight")
    candidates, scope = matcher._index.lookup(
        query.manufacturer, similarity_threshold=matcher._config.manufacturer_scope_threshold,
    )
    if scope != "exact_manufacturer":
        raise ValueError("bodywork diagnostic requires exact manufacturer scope")
    rows = []
    for candidate in candidates:
        if not re.match(rf"^{re.escape(family)}(?:\s|$)", candidate.model.upper()):
            continue
        score = matcher._score(query, candidate, bodywork_discriminates=False)
        rows.append({
            "catalog_candidate": asdict(candidate), "score": score.to_review_payload(),
            "separation_score": score.separation_score,
            "qualifies_candidate_threshold": score.confidence >= matcher._config.candidate_threshold,
            "in_returned_candidates": candidate.candidate_reference in returned_references,
        })
    return sorted(rows, key=lambda row: (-row["separation_score"], row["catalog_candidate"]["candidate_reference"]))


def summarize_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    summaries = {}
    for group in GROUPS:
        selected = [item for item in items if item["group"] == group]
        reasons: Counter[str] = Counter()
        estate_best_conflicts: Counter[str] = Counter()
        estate_best_missing: Counter[str] = Counter()
        counts: Counter[str] = Counter()
        for item in selected:
            reasons.update(item["evaluation"]["reason_codes"])
            # Base and reviewed-alias queries are retained separately. Count
            # each row once using the highest-ranked estate across attempts.
            estates = [row for attempt in item["family_attempts"] for row in attempt["siblings"]
                       if "estate" in row["catalog_candidate"]["bodyworks"]]
            if not estates:
                counts["no_estate_in_family_catalog"] += 1
                continue
            counts["estate_in_family_catalog"] += 1
            best = min(estates, key=lambda row: (-row["separation_score"], row["catalog_candidate"]["candidate_reference"]))
            facts = best["score"]["evidence"]
            estate_best_conflicts.update(facts["conflicting_fields"])
            estate_best_missing.update(facts["missing_fields"])
            if "model_partial" in facts["matched_fields"]:
                counts["best_estate_partial_model"] += 1
            if best["qualifies_candidate_threshold"]:
                counts["best_estate_above_candidate_threshold"] += 1
            if not facts["conflicting_fields"]:
                counts["best_estate_without_reported_conflicts"] += 1
            if any(row["in_returned_candidates"] for row in estates):
                counts["estate_in_any_returned_top_n"] += 1
        summaries[group] = {
            "count": len(selected),
            "terminals": dict(Counter(item["evaluation"]["terminal"] for item in selected)),
            "reasons": dict(reasons), "sibling_counts": dict(counts),
            "best_estate_conflicts": dict(estate_best_conflicts),
            "best_estate_missing": dict(estate_best_missing),
        }
    return summaries


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
        raise ValueError("diagnostic supports the no-new-rules cohort only")
    code_root = Path(__file__).resolve().parents[1]
    source_files = sorted((code_root / "ingestion").rglob("*.py"))
    code_digest = digest({str(p.relative_to(code_root)): p.read_text() for p in source_files})
    settings = IngestionSettings(_env_file=args.env_file)  # type: ignore[call-arg]
    if conninfo_to_dict(settings.database_url).get("host") not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("diagnostic requires local PostgreSQL")
    with psycopg.connect(settings.database_url, options="-c default_transaction_read_only=on") as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        rules, manufacturers = load_active_rules(conn)
        catalog = load_postgres_ktype_catalog(conn, batch_id=report["catalog_version"])
        rows = _fetch_local_raw_page(conn, source_batch_prefix=report["source_prefix"], after_id=0, limit=report["count"])
        if digest(rows) != report["source_digest"] or digest([asdict(c) for c in catalog]) != report["catalog_digest"]:
            raise ValueError("source/catalog differs from report")
        if digest([asdict(rules), manufacturers]) != report["rules_digest"]:
            raise ValueError("rules differ from report")
    evaluator = TecDocDryRunEvaluator(catalog, manufacturers, ReviewedModelAliasIndex(rules))
    candidates = {candidate.candidate_reference: candidate for candidate in catalog}
    records = {record["row_key"]: record for record in report["records"]}
    items = []
    for source_id, raw in rows:
        row_key = digest([source_id, raw])
        record = records[row_key]
        candidate = candidates.get(record["top_candidate_reference"])
        group = select_group(raw, record, candidate.model if candidate else "")
        if group is None:
            continue
        captured = capture_evaluation(evaluator, raw, source_id=source_id, rules=rules, manufacturers=manufacturers)
        expected = {key: value for key, value in record.items() if key != "row_key"}
        if json.loads(json.dumps(captured["evaluation"])) != expected:
            raise ValueError("bodywork case differs from baseline; refresh report first")
        family_attempts = []
        for attempt in captured["attempts"]:
            matcher = evaluator._alias_matcher if attempt["reviewed_aliases_enabled"] else evaluator._matcher
            family_attempts.append({
                "query": attempt["query"], "reviewed_aliases_enabled": attempt["reviewed_aliases_enabled"],
                "siblings": family_evidence(matcher, VehicleMatchQuery(**attempt["query"]), family=GROUPS[group][3],
                                            returned_references={c["candidate_reference"] for c in attempt["candidates"]}),
            })
        normalized = normalize_ts_record(raw, rule_set=rules, manufacturer_entity_rules=manufacturers)
        items.append({
            "group": group, "row_key": row_key, "plate": raw.get("plate"),
            "raw_source_evidence": review_source_evidence(raw),
            "normalization": asdict(normalized), **captured, "family_attempts": family_attempts,
            "review_status": "pending_domain_review", "verdict": None,
        })
        if len(items) % 50 == 0:
            print(json.dumps({"bodywork_cases_replayed": len(items)}), flush=True)
    summary = summarize_items(items)
    for group, (brand, model, top_model, _) in GROUPS.items():
        expected_count = sum(entry["count"] for entry in report["repair_diagnostics"]["groups"]
                             if entry["evidence"]["kind"] == "bodywork_conflict"
                             and entry["evidence"]["raw_brand"] == brand and entry["evidence"]["raw_model"] == model
                             and entry["evidence"]["candidate_model"] == top_model
                             and entry["evidence"]["ts_body_code"] == "AC")
        if summary[group]["count"] != expected_count or not expected_count:
            raise ValueError("incomplete targeted bodywork cohort")
    if code_digest != digest({str(p.relative_to(code_root)): p.read_text() for p in source_files}):
        raise ValueError("matcher code changed during diagnostic")
    payload = {key: report[key] for key in ("source_digest", "catalog_digest", "rules_digest")}
    payload.update(code_digest=code_digest, baseline_code_digest=report["code_digest"],
                   count=len(items), summary=summary, items=items, read_only=True,
                   independently_adjudicated=False, contains_private_plates=True,
                   limitations=["No compatibility activated", "No acceptance verdicts",
                                "Sibling scores use actual policy; not an alternative accepted ranking",
                                "Best-estate summaries do not prove that the estate is correct"])
    payload = json.loads(json.dumps(payload, default=lambda value: sorted(value) if isinstance(value, set | frozenset) else str(value)))
    write_private_json(args.output, payload)
    print(json.dumps({"completed": len(items), "summary": summary}), flush=True)


if __name__ == "__main__":
    main()
