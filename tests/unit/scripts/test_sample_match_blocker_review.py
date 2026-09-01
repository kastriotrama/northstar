from ingestion.tecdoc.match_run_adapters import MatchEvaluation
from scripts.sample_match_blocker_review import _review_candidates


def test_review_candidates_preserve_only_sanitized_match_payload() -> None:
    result = _review_candidates(MatchEvaluation(
        "review_required",
        ("route:candidate_margin_below_gate",),
        top_candidate_reference="123",
        candidate_matches=(
            {
                "candidate_reference": "123",
                "candidate_type": "TecDocKType",
                "confidence": 0.91,
                "evidence": {"model": "V60", "conflicting_fields": []},
            },
        ),
    ))
    assert result[0].candidate_reference == "123"
    assert result[0].evidence == {"model": "V60", "conflicting_fields": []}
