from typing import Any

from api.app.features.match_review.adjudicator import HeuristicAdjudicator

SIGNATURE: dict[str, Any] = {
    "manufacturer": "Volvo",
    "model_family": "V70",
    "production_year": 2014,
}


def _adjudicate(
    oem_samples: list[dict[str, Any]],
    tecdoc_candidates: list[dict[str, Any]] | None = None,
    varying_fields: list[str] | None = None,
) -> Any:
    return HeuristicAdjudicator().adjudicate(
        signature=SIGNATURE,
        reason_profile={"model_evidence_missing": 10},
        member_count=10,
        oem_samples=oem_samples,
        tecdoc_candidates=tecdoc_candidates or [],
        varying_fields=varying_fields or [],
    )


def test_identity_spread_splits_before_spending_on_oem() -> None:
    proposal = _adjudicate([], varying_fields=["brand", "model_no", "body_code"])

    assert proposal.recommendation == "split_chunk"
    assert "brand" in proposal.reasoning
    assert proposal.evidence["oem_sample_count"] == 0


def test_non_identity_spread_does_not_force_a_split() -> None:
    proposal = _adjudicate([], varying_fields=["body_code", "kw"])

    assert proposal.recommendation == "needs_more_evidence"


def test_too_few_samples_requests_more_evidence() -> None:
    proposal = _adjudicate([{"manufacturer": "Volvo"}])

    assert proposal.recommendation == "needs_more_evidence"
    assert "1 more" in proposal.reasoning


def test_sample_disagreement_with_signature_recommends_split() -> None:
    proposal = _adjudicate(
        [
            {"manufacturer": "Volvo", "model": "V70"},
            {"manufacturer": "Volvo", "model": "V60"},
        ]
    )

    assert proposal.recommendation == "split_chunk"
    assert "model_family" in proposal.evidence["conflicts"]


def test_sample_conflict_against_signature_recommends_split() -> None:
    proposal = _adjudicate(
        [
            {"manufacturer": "VW", "model": "V70"},
            {"manufacturer": "VW", "model": "V70"},
        ]
    )

    assert proposal.recommendation == "split_chunk"
    assert proposal.evidence["conflicts"] == ["manufacturer"]


def test_concordant_samples_with_single_candidate_assign_ktype() -> None:
    proposal = _adjudicate(
        [
            {"manufacturer": "volvo", "model": "v70", "model_year": "2014"},
            {"manufacturer": "Volvo", "model": "V70"},
        ],
        tecdoc_candidates=[{"reference": "ktype:12345"}],
    )

    assert proposal.recommendation == "assign_ktype"
    assert proposal.target_ktype_reference == "ktype:12345"


def test_concordant_samples_without_candidates_stay_safe() -> None:
    proposal = _adjudicate(
        [
            {"manufacturer": "Volvo", "model": "V70"},
            {"manufacturer": "Volvo", "model": "V70"},
        ]
    )

    assert proposal.recommendation == "no_safe_match"
    assert proposal.target_ktype_reference is None


def test_multiple_candidates_require_more_evidence() -> None:
    proposal = _adjudicate(
        [
            {"manufacturer": "Volvo", "model": "V70"},
            {"manufacturer": "Volvo", "model": "V70"},
        ],
        tecdoc_candidates=[{"reference": "ktype:1"}, {"reference": "ktype:2"}],
    )

    assert proposal.recommendation == "needs_more_evidence"
