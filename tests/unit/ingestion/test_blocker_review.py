from ingestion.tecdoc.blocker_review import classify_match_blocker
from ingestion.tecdoc.match_run_adapters import MatchEvaluation


def test_hard_conflict_has_precedence_over_context_category() -> None:
    category = classify_match_blocker(
        MatchEvaluation(
            "hard_conflict",
            ("conflict:year", "context_conflict:bodywork"),
        )
    )
    assert category is not None
    assert category.code == "hard_technical_conflict"


def test_bodywork_and_margin_are_stable_categories() -> None:
    bodywork = classify_match_blocker(
        MatchEvaluation("review_required", ("context_conflict:bodywork",))
    )
    margin = classify_match_blocker(
        MatchEvaluation("review_required", ("route:candidate_margin_below_gate",))
    )
    assert bodywork is not None and bodywork.code == "bodywork_conflict"
    assert margin is not None and margin.code == "candidate_margin"


def test_non_blocking_terminal_has_no_category() -> None:
    assert classify_match_blocker(MatchEvaluation("resolved", ("route:resolved",))) is None
