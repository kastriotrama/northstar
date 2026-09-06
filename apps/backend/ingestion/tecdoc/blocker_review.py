"""Stable TS-to-TecDoc blocker categories for audit and human review."""

from __future__ import annotations

from dataclasses import dataclass

from ingestion.tecdoc.match_run_adapters import MatchEvaluation


@dataclass(frozen=True)
class MatchBlockerCategory:
    code: str
    title: str
    guidance: str


CATEGORIES: tuple[MatchBlockerCategory, ...] = (
    MatchBlockerCategory(
        "hard_technical_conflict",
        "Hard technical conflict",
        "Keep unresolved unless independent evidence proves which source field is wrong.",
    ),
    MatchBlockerCategory(
        "bodywork_conflict",
        "Bodywork conflict",
        "Compare the TS body code with TecDoc bodywork and approve only a scoped ontology rule.",
    ),
    MatchBlockerCategory(
        "candidate_margin",
        "Candidates too close",
        "Choose a KType only when engine, year, fuel, power, displacement, or approval evidence separates it.",
    ),
    MatchBlockerCategory(
        "model_source_conflict",
        "Model evidence conflicts",
        "Resolve disagreement between registry model fields before selecting a KType.",
    ),
    MatchBlockerCategory(
        "model_missing",
        "Model evidence missing",
        "Supply a reviewed manufacturer-scoped model family or keep the vehicle unresolved.",
    ),
    MatchBlockerCategory(
        "model_unmatched",
        "Model not found in catalog",
        "Review exact model-family aliases without weakening the candidate threshold.",
    ),
    MatchBlockerCategory(
        "partial_or_phonetic_model",
        "Partial or phonetic model",
        "Require corroborating technical evidence; text similarity alone is not sufficient.",
    ),
    MatchBlockerCategory(
        "manufacturer_scope",
        "Manufacturer scope unresolved",
        "Approve an exact manufacturer mapping before comparing KTypes.",
    ),
    MatchBlockerCategory(
        "normalization_review",
        "Normalization needs review",
        "Correct or approve the normalized TS evidence before KType matching.",
    ),
    MatchBlockerCategory(
        "other_match_blocker",
        "Other matcher blocker",
        "Inspect the complete evidence and keep unresolved when no reviewed rule applies.",
    ),
)

CATEGORY_BY_CODE = {category.code: category for category in CATEGORIES}


def classify_match_blocker(evaluation: MatchEvaluation) -> MatchBlockerCategory | None:
    """Return one stable primary category without hiding the original reasons."""

    if evaluation.terminal not in {
        "review_required",
        "hard_conflict",
        "normalization_review",
        "unmatched",
    }:
        return None
    reasons = set(evaluation.reason_codes)
    if evaluation.terminal == "hard_conflict" or any(
        reason.startswith(("conflict:", "route:hard_conflict:"))
        for reason in reasons
    ):
        return CATEGORY_BY_CODE["hard_technical_conflict"]
    if "context_conflict:bodywork" in reasons:
        return CATEGORY_BY_CODE["bodywork_conflict"]
    if any("candidate_margin" in reason for reason in reasons):
        return CATEGORY_BY_CODE["candidate_margin"]
    if "model_source_evidence_conflict" in reasons or "source_model_rules_conflict" in reasons:
        return CATEGORY_BY_CODE["model_source_conflict"]
    if "model_evidence_missing" in reasons:
        return CATEGORY_BY_CODE["model_missing"]
    if "match:no_candidate_above_threshold" in reasons:
        return CATEGORY_BY_CODE["model_unmatched"]
    if any("partial_model" in reason or "phonetic" in reason for reason in reasons):
        return CATEGORY_BY_CODE["partial_or_phonetic_model"]
    if any("manufacturer" in reason for reason in reasons):
        return CATEGORY_BY_CODE["manufacturer_scope"]
    if evaluation.terminal == "normalization_review":
        return CATEGORY_BY_CODE["normalization_review"]
    return CATEGORY_BY_CODE["other_match_blocker"]
