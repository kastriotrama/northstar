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
                "why_blocked": evidence["why_blocked"],
                "decision_question": evidence["decision_question"],
                "evidence_gaps": evidence["evidence_gaps"],
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


def explain_review_item(item: dict[str, Any]) -> dict[str, Any]:
    """Build human-readable gate explanation without copying private raw data."""

    source = dict(item.get("source_evidence") or {})
    candidates = list(item.get("candidate_matches") or [])
    top = dict(candidates[0] if candidates else {})
    candidate_evidence = dict(top.get("evidence") or {})
    category = str(item.get("category") or "other_match_blocker")
    explanation = _explanation(
        category,
        {"source": source},
        {"candidate": candidate_evidence},
    )
    relevant = {
        "model": (source.get("model"), candidate_evidence.get("model")),
        "bodywork": (source.get("body_code"), "conflict" if "bodywork" in candidate_evidence.get("conflicting_fields", []) else "not returned"),
        "fuel": (" / ".join(str(source.get(key)) for key in ("fuel1", "fuel2") if source.get(key)), "conflict" if "fuels" in candidate_evidence.get("conflicting_fields", []) else "not returned"),
        "year": (source.get("vehicle_year") or source.get("model_year"), "conflict" if "year" in candidate_evidence.get("conflicting_fields", []) else "not returned"),
        "engine": (source.get("engine_code"), "conflict" if "engine" in candidate_evidence.get("conflicting_fields", []) else "not returned"),
    }
    if candidate_evidence.get("model"):
        relevant["model"] = (source.get("model"), candidate_evidence["model"])
    missing = list(explanation["evidence_gaps"])
    return {
        "blocker_explanation": explanation["why_blocked"],
        "decision_question": explanation["decision_question"],
        "evidence_gaps": missing,
        "field_comparison": {
            key: {"ts": ts or "missing", "tecdoc": tecdoc}
            for key, (ts, tecdoc) in relevant.items()
            if ts or tecdoc != "not returned"
        },
    }


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
        **_explanation(category, source_values, candidate_values),
    }


def _explanation(
    category: str, source_values: dict[str, Any], candidate_values: dict[str, Any]
) -> dict[str, Any]:
    if category == "bodywork_conflict":
        return {
            "why_blocked": "The registry body code and the TecDoc bodywork vocabulary do not compare as equivalent under the active ontology.",
            "decision_question": "Should this exact source body-code relationship be treated as compatible for a reviewed scope?",
            "evidence_gaps": ["Repeated model-family coverage", "Independent bodywork terminology evidence", "No conflicting door or purpose code"],
        }
    if category == "model_missing":
        return {
            "why_blocked": "No usable model-family evidence survived normalization, so the catalog cannot be scoped safely.",
            "decision_question": "Which reviewed manufacturer-scoped model-family assertion should recover this cohort?",
            "evidence_gaps": ["Complete TS model text", "Type approval or variant/version bridge", "Unique catalog family"],
        }
    if category == "model_unmatched":
        return {
            "why_blocked": "The normalized manufacturer and model did not produce a catalog candidate above the evidence threshold.",
            "decision_question": "Is there an exact reviewed alias, or should the vehicle remain unresolved?",
            "evidence_gaps": ["Exact manufacturer-scoped alias", "Catalog family spelling or generation", "Independent approval evidence"],
        }
    if category == "partial_or_phonetic_model":
        return {
            "why_blocked": "Only partial or phonetic model similarity was found; text similarity alone is not identity evidence.",
            "decision_question": "What independent technical field separates the proposed family from its alternatives?",
            "evidence_gaps": ["Engine or type approval", "Year and bodywork agreement", "Unique candidate margin"],
        }
    if category == "candidate_margin":
        return {
            "why_blocked": "Several KTypes remain too close after the available technical gates.",
            "decision_question": "Which additional field should be required before selecting one KType?",
            "evidence_gaps": ["Engine code or engine set", "Type approval or variant/version", "Power, displacement, fuel or year separator"],
        }
    if category == "hard_technical_conflict":
        return {
            "why_blocked": "A hard conflict means at least one TS/TecDoc technical assertion disagrees; the matcher will not guess.",
            "decision_question": "Which source assertion is independently verified as authoritative?",
            "evidence_gaps": ["Independent technical source", "Source-specific correction", "Proof that the change is not cohort-wide overreach"],
        }
    if category == "model_source_conflict":
        return {
            "why_blocked": "The raw model fields disagree with one another or with a reviewed source-model policy.",
            "decision_question": "Which raw field is authoritative for this manufacturer-scoped rule?",
            "evidence_gaps": ["Repeated source-field agreement", "Reviewed manufacturer policy", "Unambiguous catalog family"],
        }
    if category == "manufacturer_scope":
        return {
            "why_blocked": "Manufacturer scope is not exact enough to compare KTypes safely.",
            "decision_question": "Which exact TecDoc manufacturer entity should this source value map to?",
            "evidence_gaps": ["Exact entity mapping", "Repeated source spelling evidence"],
        }
    if category == "normalization_review":
        return {
            "why_blocked": "A normalized source field still needs review before matching.",
            "decision_question": "What corrected normalized value should be saved as a reviewed rule?",
            "evidence_gaps": ["Source field correction", "Rule scope and evidence reference"],
        }
    return {
        "why_blocked": "No reviewed matcher rule explains this route, so the row remains unresolved.",
        "decision_question": "What evidence-backed rule, if any, should govern this pattern?",
        "evidence_gaps": ["Complete source evidence", "Repeated cohort evidence", "Independent confirmation"],
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
