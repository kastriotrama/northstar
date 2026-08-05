from dataclasses import replace

import pytest

from ingestion.confidence_routing import (
    ConfidenceRouter,
    ConfidenceRoutingPolicy,
)
from ingestion.fuzzy_matching import (
    FuzzyMatchConfig,
    FuzzyVehicleMatcher,
    ManufacturerCandidateIndex,
    VehicleCandidate,
    VehicleMatchQuery,
)


def _match(
    query: VehicleMatchQuery,
    *candidates: VehicleCandidate,
    match_config: FuzzyMatchConfig | None = None,
):  # type: ignore[no-untyped-def]
    return FuzzyVehicleMatcher(
        ManufacturerCandidateIndex(candidates),
        match_config,
    ).match(query)


def test_complete_exact_evidence_routes_resolved_with_explanation() -> None:
    match = _match(
        VehicleMatchQuery(
            manufacturer="Volvo",
            model="XC90",
            year=2022,
            fuels=frozenset({"petrol", "electricity"}),
            engine_code="B4204T",
        ),
        VehicleCandidate(
            "KTYPE-100",
            "Volvo",
            "XC90",
            year_from=2015,
            year_to=2024,
            fuels=frozenset({"petrol", "electricity"}),
            engine_codes=frozenset({"B4204T"}),
        ),
    )

    decision = ConfidenceRouter().route(match)

    assert decision.route == "resolved"
    assert decision.confidence == 1.0
    assert decision.selected_candidate_reference == "KTYPE-100"
    assert decision.reason_codes == ("resolved_threshold_met",)
    assert [entry.sequence for entry in decision.decision_trace] == [1, 2, 3, 4, 5]
    assert decision.decision_trace[-1].rule_id == "ROUTE-RESOLVED-THRESHOLD-V1"
    assert decision.alternative_candidates[0]["candidate_reference"] == "KTYPE-100"


@pytest.mark.parametrize("conflict", ["manufacturer", "fuels", "engine_code"])
def test_required_hard_conflicts_override_a_high_statistical_score(conflict: str) -> None:
    candidate = VehicleCandidate(
        "KTYPE-100",
        "Volvo",
        "XC90",
        year_from=2015,
        year_to=2024,
        fuels=frozenset({"petrol"}),
        engine_codes=frozenset({"B4204T"}),
    )
    query = VehicleMatchQuery(
        manufacturer=None if conflict == "manufacturer" else "Volvo",
        model="XC90",
        year=2022,
        fuels=frozenset({"diesel"}) if conflict == "fuels" else frozenset({"petrol"}),
        engine_code="D5244T" if conflict == "engine_code" else "B4204T",
    )
    match = _match(query, candidate)
    if conflict == "manufacturer":
        top = replace(match.candidates[0], conflicting_fields=("manufacturer",))
        match = replace(match, candidates=(top,))

    decision = ConfidenceRouter().route(match)

    assert decision.route == "review_required"
    assert decision.selected_candidate_reference is None
    assert decision.reason_codes == (f"hard_conflict:{conflict}",)


def test_supported_but_inexact_model_routes_provisional() -> None:
    match = _match(
        VehicleMatchQuery(manufacturer="Volvo", model="X90"),
        VehicleCandidate("KTYPE-100", "Volvo", "XC90"),
    )

    decision = ConfidenceRouter().route(match)

    assert decision.route == "provisional"
    assert decision.confidence == 0.775
    assert decision.selected_candidate_reference == "KTYPE-100"
    assert decision.reason_codes == ("provisional_threshold_met",)


def test_phonetic_and_ambiguous_candidates_remain_review_only() -> None:
    phonetic = _match(
        VehicleMatchQuery(manufacturer="Toyota", model="Kamri", year=2022),
        VehicleCandidate("KTYPE-1", "Toyota", "Camry", year_from=2018, year_to=2025),
    )
    ambiguous = _match(
        VehicleMatchQuery(manufacturer="Volvo", model="V60"),
        VehicleCandidate("KTYPE-A", "Volvo", "V60"),
        VehicleCandidate("KTYPE-B", "Volvo", "V60"),
    )

    phonetic_decision = ConfidenceRouter().route(phonetic)
    ambiguous_decision = ConfidenceRouter().route(ambiguous)

    assert phonetic_decision.route == "review_required"
    assert phonetic_decision.reason_codes == ("phonetic_evidence_requires_review",)
    assert ambiguous_decision.route == "review_required"
    assert ambiguous_decision.reason_codes == ("candidate_margin_below_gate",)
    assert len(ambiguous_decision.alternative_candidates) == 2


def test_truncated_candidate_list_cannot_hide_stage_two_ambiguity() -> None:
    match = _match(
        VehicleMatchQuery(manufacturer="Volvo", model="V60"),
        VehicleCandidate("KTYPE-A", "Volvo", "V60"),
        VehicleCandidate("KTYPE-B", "Volvo", "V60"),
        match_config=FuzzyMatchConfig(max_candidates=1),
    )

    decision = ConfidenceRouter().route(match)

    assert len(match.candidates) == 1
    assert match.reason == "candidate_margin_not_met"
    assert decision.route == "review_required"
    assert decision.reason_codes == ("candidate_margin_below_gate",)


def test_no_candidate_routes_review_with_zero_confidence() -> None:
    match = _match(
        VehicleMatchQuery(manufacturer="Volvo", model="Unrelated"),
        VehicleCandidate("KTYPE-1", "Volvo", "XC90"),
    )

    decision = ConfidenceRouter().route(match)

    assert decision.route == "review_required"
    assert decision.confidence == 0.0
    assert decision.top_candidate_reference is None
    assert decision.reason_codes == ("no_candidate_above_threshold",)


@pytest.mark.parametrize(
    ("score", "expected_route"),
    [
        (0.90, "resolved"),
        (0.899999, "provisional"),
        (0.70, "provisional"),
        (0.699999, "review_required"),
    ],
)
def test_exact_threshold_boundaries_are_inclusive_and_deterministic(
    score: float,
    expected_route: str,
) -> None:
    policy = ConfidenceRoutingPolicy(
        resolved_threshold=0.90,
        provisional_threshold=0.70,
        text_weight=1.0,
        manufacturer_weight=0.0,
        context_weight=0.0,
        margin_weight=0.0,
    )
    match = _match(
        VehicleMatchQuery(manufacturer="Volvo", model="XC90"),
        VehicleCandidate("KTYPE-1", "Volvo", "XC90"),
    )
    top = match.candidates[0]
    adjusted = type(top)(
        candidate_reference=top.candidate_reference,
        candidate_type=top.candidate_type,
        manufacturer=top.manufacturer,
        model=top.model,
        confidence=score,
        text_score=score,
        context_effect=0.0,
        matched_label=top.matched_label,
        matched_fields=top.matched_fields,
        missing_fields=top.missing_fields,
        conflicting_fields=top.conflicting_fields,
        phonetic_match=False,
    )
    adjusted_match = type(match)(
        scope=match.scope,
        candidates=(adjusted,),
        eligible_for_auto_resolution=False,
        reason="test_fixture",
    )

    decision = ConfidenceRouter(policy).route(adjusted_match)

    assert decision.route == expected_route
    assert decision.confidence == score


def test_policy_rejects_invalid_thresholds_and_weights() -> None:
    with pytest.raises(ValueError, match="thresholds"):
        ConfidenceRoutingPolicy(resolved_threshold=0.6, provisional_threshold=0.7)
    with pytest.raises(ValueError, match="sum to 1.0"):
        ConfidenceRoutingPolicy(text_weight=0.8)
