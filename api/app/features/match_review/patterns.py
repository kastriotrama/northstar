from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any


def build_review_patterns(
    items: list[dict[str, Any]],
    candidate_contexts: dict[str, dict[str, Any]],
    blocker_counts: dict[str, int],
) -> list[dict[str, Any]]:
    """Aggregate plate-free matcher issues from bounded evidence samples."""

    grouped: dict[str, list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]] = (
        defaultdict(list)
    )
    evidence_by_key: dict[str, dict[str, Any]] = {}
    for item in items:
        source = dict(item.get("source_evidence") or {})
        candidates = list(item.get("candidate_matches") or [])
        top_reference = str(candidates[0].get("candidate_reference") or "") if candidates else ""
        candidate = dict(candidate_contexts.get(top_reference) or {})
        evidence = _pattern_evidence(item, source, candidate)
        pattern_key = _pattern_key(evidence)
        evidence_by_key[pattern_key] = evidence
        grouped[pattern_key].append((item, source, candidate))

    patterns = []
    for pattern_key, rows in grouped.items():
        evidence = evidence_by_key[pattern_key]
        category = str(evidence["category"])
        examples = []
        seen_examples: set[tuple[str, str, str]] = set()
        for item, source, candidate in rows:
            example = (
                str(source.get("brand") or source.get("manufacturer") or "Unknown"),
                str(source.get("model") or "Model unavailable"),
                str(candidate.get("candidate_reference") or ""),
            )
            if example in seen_examples:
                continue
            seen_examples.add(example)
            examples.append(
                {
                    "manufacturer": example[0],
                    "model": example[1],
                    "candidate_reference": example[2] or None,
                }
            )
            if len(examples) == 4:
                break
        patterns.append(
            {
                "pattern_key": pattern_key,
                "category": category,
                "title": _pattern_title(evidence),
                "summary": _pattern_summary(evidence),
                "source_values": evidence["source_values"],
                "candidate_values": evidence["candidate_values"],
                "sample_occurrences": len(rows),
                "category_occurrences": int(blocker_counts.get(category, 0)),
                "examples": examples,
                "decision": None,
            }
        )
    return sorted(
        patterns,
        key=lambda item: (-item["sample_occurrences"], item["category"], item["title"]),
    )


def _pattern_evidence(
    item: dict[str, Any], source: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    category = str(item.get("category") or "other_match_blocker")
    reasons = [str(value) for value in item.get("reason_codes") or []]
    top_match = (item.get("candidate_matches") or [{}])[0]
    match_evidence = dict(top_match.get("evidence") or {})
    conflicts = sorted(
        {
            *[value.removeprefix("conflict:") for value in reasons if value.startswith("conflict:")],
            *[str(value) for value in match_evidence.get("conflicting_fields") or []],
        }
    )
    brand = str(source.get("brand") or source.get("manufacturer") or "").strip()
    model = str(source.get("model") or "").strip()
    source_values: dict[str, Any]
    candidate_values: dict[str, Any]
    if category == "bodywork_conflict":
        source_values = {"body_code": source.get("body_code") or "missing"}
        candidate_values = {"bodywork": candidate.get("bodyworks") or ["unknown"]}
    elif category == "hard_technical_conflict":
        source_values = {"conflicting_fields": conflicts or ["technical evidence"]}
        candidate_values = {"required_action": "independent source adjudication"}
    elif category == "candidate_margin":
        source_values = {"manufacturer": brand or "unknown", "model": model or "unknown"}
        candidate_values = {
            "candidate_count": len(item.get("candidate_matches") or []),
            "missing_separator": "engine, year, fuel, power, displacement or approval evidence",
        }
    elif category == "model_missing":
        source_values = {"manufacturer": brand or "unknown", "model": "missing"}
        candidate_values = {"required_mapping": "reviewed manufacturer-scoped model family"}
    elif category in {"model_unmatched", "partial_or_phonetic_model", "model_source_conflict"}:
        source_values = {"manufacturer": brand or "unknown", "model": model or "missing"}
        candidate_values = {
            "catalog_model": candidate.get("model") or match_evidence.get("model") or "none"
        }
    elif category == "manufacturer_scope":
        source_values = {"manufacturer": brand or "unknown"}
        candidate_values = {"required_mapping": "exact TecDoc manufacturer"}
    else:
        source_values = {"reason": reasons[0] if reasons else category}
        candidate_values = {"candidate_model": candidate.get("model") or "none"}
    return {
        "category": category,
        "source_values": source_values,
        "candidate_values": candidate_values,
    }


def _pattern_key(evidence: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"{evidence['category']}:{digest[:20]}"


def _pattern_title(evidence: dict[str, Any]) -> str:
    source = evidence["source_values"]
    target = evidence["candidate_values"]
    category = evidence["category"]
    if category == "bodywork_conflict":
        values = ", ".join(str(value).upper() for value in target["bodywork"])
        return f"TS body code {source['body_code']} → TecDoc {values}"
    if category == "candidate_margin":
        return f"{source['manufacturer']} {source['model']} · candidates too close"
    if category == "model_missing":
        return f"{source['manufacturer']} · model evidence missing"
    if category in {"model_unmatched", "partial_or_phonetic_model", "model_source_conflict"}:
        return f"{source['manufacturer']} {source['model']} → {target['catalog_model']}"
    if category == "manufacturer_scope":
        return f"Manufacturer mapping · {source['manufacturer']}"
    if category == "hard_technical_conflict":
        return f"Technical conflict · {', '.join(source['conflicting_fields'])}"
    return str(source.get("reason") or category).replace("_", " ").title()


def _pattern_summary(evidence: dict[str, Any]) -> str:
    category = evidence["category"]
    if category == "bodywork_conflict":
        return "Choose whether this registry body code is compatible with the shown TecDoc bodywork."
    if category == "hard_technical_conflict":
        return "Choose a policy only after an independent source identifies which technical assertion is wrong."
    if category == "candidate_margin":
        return "Choose which additional evidence must separate these candidate KTypes."
    if category == "model_missing":
        return "Choose a reviewed model-family recovery policy or keep this cohort unresolved."
    return "Accept the proposed mapping, keep the pattern blocked, or submit a corrected rule proposal."
