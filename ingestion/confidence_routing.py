"""Explainable composite confidence and safe matching-route decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ingestion.fuzzy_matching import FuzzyMatchResult

CONFIDENCE_POLICY_VERSION = "confidence-routing-v1"

RoutingState = Literal["resolved", "provisional", "review_required"]


def _bounded(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 6)


@dataclass(frozen=True)
class ConfidenceRoutingPolicy:
    """Injected Phase 1 identity-routing thresholds and weights."""

    version: str = CONFIDENCE_POLICY_VERSION
    resolved_threshold: float = 0.90
    provisional_threshold: float = 0.70
    minimum_candidate_margin: float = 0.08
    text_weight: float = 0.50
    manufacturer_weight: float = 0.20
    context_weight: float = 0.20
    margin_weight: float = 0.10
    hard_conflict_fields: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "manufacturer",
                "fuels",
                "engine_code",
                "model_series",
                "year",
                "displacement_cc",
                "power_kw",
            }
        )
    )

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("policy version must not be empty")
        if not 0.0 <= self.provisional_threshold <= self.resolved_threshold <= 1.0:
            raise ValueError("routing thresholds must satisfy 0 <= provisional <= resolved <= 1")
        if not 0.0 < self.minimum_candidate_margin <= 1.0:
            raise ValueError("minimum_candidate_margin must be greater than 0 and at most 1")
        weights = (
            self.text_weight,
            self.manufacturer_weight,
            self.context_weight,
            self.margin_weight,
        )
        if any(weight < 0.0 for weight in weights):
            raise ValueError("confidence weights must not be negative")
        if abs(sum(weights) - 1.0) > 1e-9:
            raise ValueError("confidence weights must sum to 1.0")
        if not self.hard_conflict_fields:
            raise ValueError("hard_conflict_fields must not be empty")


@dataclass(frozen=True)
class ConfidenceTraceEntry:
    sequence: int
    rule_id: str
    signal: str
    value: float | str | list[str]
    weight: float
    contribution: float
    explanation: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "rule_id": self.rule_id,
            "signal": self.signal,
            "value": self.value,
            "weight": self.weight,
            "contribution": self.contribution,
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class ConfidenceRoutingDecision:
    policy_version: str
    route: RoutingState
    confidence: float
    selected_candidate_reference: str | None
    top_candidate_reference: str | None
    reason_codes: tuple[str, ...]
    hard_conflicts: tuple[str, ...]
    decision_trace: tuple[ConfidenceTraceEntry, ...]
    alternative_candidates: tuple[dict[str, Any], ...]

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        if self.route == "review_required" and self.selected_candidate_reference is not None:
            raise ValueError("review_required decisions cannot select a candidate")
        if self.route != "review_required" and self.selected_candidate_reference is None:
            raise ValueError("resolved and provisional decisions require a selected candidate")
        expected = tuple(range(1, len(self.decision_trace) + 1))
        if tuple(entry.sequence for entry in self.decision_trace) != expected:
            raise ValueError("decision trace sequence must be contiguous")

    def to_payload(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "route": self.route,
            "confidence": self.confidence,
            "selected_candidate_reference": self.selected_candidate_reference,
            "top_candidate_reference": self.top_candidate_reference,
            "reason_codes": list(self.reason_codes),
            "hard_conflicts": list(self.hard_conflicts),
            "decision_trace": [entry.to_payload() for entry in self.decision_trace],
            "alternative_candidates": list(self.alternative_candidates),
        }


class ConfidenceRouter:
    """Calculate a composite score, then apply non-statistical safety gates."""

    def __init__(self, policy: ConfidenceRoutingPolicy | None = None) -> None:
        self.policy = policy or ConfidenceRoutingPolicy()

    def route(self, match_result: FuzzyMatchResult) -> ConfidenceRoutingDecision:
        alternatives = match_result.review_candidates()
        if not match_result.candidates:
            trace = (
                ConfidenceTraceEntry(
                    sequence=1,
                    rule_id="CONF-NO-CANDIDATE-V1",
                    signal="candidate_presence",
                    value=0.0,
                    weight=1.0,
                    contribution=0.0,
                    explanation="No candidate passed the Stage 2 candidate threshold.",
                ),
                ConfidenceTraceEntry(
                    sequence=2,
                    rule_id="ROUTE-REVIEW-NO-CANDIDATE-V1",
                    signal="routing_gate",
                    value="review_required",
                    weight=0.0,
                    contribution=0.0,
                    explanation="A missing identity candidate must be reviewed or remain unresolved.",
                ),
            )
            return ConfidenceRoutingDecision(
                policy_version=self.policy.version,
                route="review_required",
                confidence=0.0,
                selected_candidate_reference=None,
                top_candidate_reference=None,
                reason_codes=("no_candidate_above_threshold",),
                hard_conflicts=(),
                decision_trace=trace,
                alternative_candidates=alternatives,
            )

        top = match_result.candidates[0]
        second = match_result.candidates[1] if len(match_result.candidates) > 1 else None
        manufacturer_signal = {
            "exact_manufacturer": 1.0,
            "fuzzy_manufacturer": 0.65,
            "phonetic_manufacturer": 0.50,
            "global": 0.0,
        }[match_result.scope]

        contextual_matched = {
            field_name
            for field_name in top.matched_fields
            if field_name not in {"model", "model_phonetic"}
        }
        contextual_missing = set(top.missing_fields)
        contextual_conflicting = set(top.conflicting_fields)
        contextual_observed = contextual_matched | contextual_missing | contextual_conflicting
        context_signal = (
            (len(contextual_matched) + (0.5 * len(contextual_missing))) / len(contextual_observed)
            if contextual_observed
            else 0.5
        )

        raw_margin = 1.0 if second is None else max(0.0, top.confidence - second.confidence)
        margin_signal = min(1.0, raw_margin / self.policy.minimum_candidate_margin)
        signals = (
            (
                "CONF-TEXT-V1",
                "text_similarity",
                top.text_score,
                self.policy.text_weight,
                "Stage 2 model edit/token similarity.",
            ),
            (
                "CONF-MANUFACTURER-SCOPE-V1",
                "manufacturer_scope",
                manufacturer_signal,
                self.policy.manufacturer_weight,
                f"Manufacturer scope is {match_result.scope}.",
            ),
            (
                "CONF-CONTEXT-V1",
                "context_consistency",
                context_signal,
                self.policy.context_weight,
                "Matched, missing and conflicting year/fuel/engine context.",
            ),
            (
                "CONF-CANDIDATE-MARGIN-V1",
                "candidate_margin",
                margin_signal,
                self.policy.margin_weight,
                f"Raw top-to-runner-up margin is {round(raw_margin, 6)}.",
            ),
        )
        trace_entries = [
            ConfidenceTraceEntry(
                sequence=index,
                rule_id=rule_id,
                signal=signal,
                value=round(value, 6),
                weight=weight,
                contribution=_bounded(value * weight),
                explanation=explanation,
            )
            for index, (rule_id, signal, value, weight, explanation) in enumerate(signals, start=1)
        ]
        confidence = _bounded(sum(entry.contribution for entry in trace_entries))
        hard_conflicts = tuple(
            sorted(set(top.conflicting_fields) & self.policy.hard_conflict_fields)
        )

        route, reasons, routing_rule, routing_explanation = self._apply_gates(
            match_result=match_result,
            confidence=confidence,
            raw_margin=raw_margin,
            hard_conflicts=hard_conflicts,
        )
        trace_entries.append(
            ConfidenceTraceEntry(
                sequence=len(trace_entries) + 1,
                rule_id=routing_rule,
                signal="routing_gate",
                value=route,
                weight=0.0,
                contribution=0.0,
                explanation=routing_explanation,
            )
        )
        selected = top.candidate_reference if route != "review_required" else None
        return ConfidenceRoutingDecision(
            policy_version=self.policy.version,
            route=route,
            confidence=confidence,
            selected_candidate_reference=selected,
            top_candidate_reference=top.candidate_reference,
            reason_codes=reasons,
            hard_conflicts=hard_conflicts,
            decision_trace=tuple(trace_entries),
            alternative_candidates=alternatives,
        )

    def _apply_gates(
        self,
        *,
        match_result: FuzzyMatchResult,
        confidence: float,
        raw_margin: float,
        hard_conflicts: tuple[str, ...],
    ) -> tuple[RoutingState, tuple[str, ...], str, str]:
        if hard_conflicts:
            return (
                "review_required",
                tuple(f"hard_conflict:{field_name}" for field_name in hard_conflicts),
                "ROUTE-REVIEW-HARD-CONFLICT-V1",
                "One or more hard conflicts override the statistical score.",
            )
        if match_result.scope != "exact_manufacturer":
            return (
                "review_required",
                ("manufacturer_scope_not_exact",),
                "ROUTE-REVIEW-MANUFACTURER-SCOPE-V1",
                "Only an exact manufacturer scope may pass automatic routing.",
            )
        if match_result.candidates[0].phonetic_match:
            return (
                "review_required",
                ("phonetic_evidence_requires_review",),
                "ROUTE-REVIEW-PHONETIC-V1",
                "Phonetic recovery remains review-only.",
            )
        if match_result.reason == "candidate_margin_not_met":
            return (
                "review_required",
                ("candidate_margin_below_gate",),
                "ROUTE-REVIEW-AMBIGUOUS-V1",
                "Stage 2 detected a close runner-up outside the returned candidate limit.",
            )
        if len(match_result.candidates) > 1 and raw_margin < self.policy.minimum_candidate_margin:
            return (
                "review_required",
                ("candidate_margin_below_gate",),
                "ROUTE-REVIEW-AMBIGUOUS-V1",
                "The top candidates are too close to separate safely.",
            )
        if confidence >= self.policy.resolved_threshold:
            return (
                "resolved",
                ("resolved_threshold_met",),
                "ROUTE-RESOLVED-THRESHOLD-V1",
                "Composite confidence meets the resolved threshold.",
            )
        if confidence >= self.policy.provisional_threshold:
            return (
                "provisional",
                ("provisional_threshold_met",),
                "ROUTE-PROVISIONAL-THRESHOLD-V1",
                "Composite confidence meets only the provisional threshold.",
            )
        return (
            "review_required",
            ("provisional_threshold_not_met",),
            "ROUTE-REVIEW-THRESHOLD-V1",
            "Composite confidence is below the provisional threshold.",
        )
