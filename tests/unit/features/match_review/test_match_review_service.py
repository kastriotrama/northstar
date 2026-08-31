from datetime import UTC, datetime
from typing import Any

from api.app.features.match_review.schemas import MatchReviewDecisionRequest
from api.app.features.match_review.service import MatchReviewService


class FakeRepository:
    def ensure_schema(self) -> None: pass

    def fetch_run(self, operation_id: str | None) -> dict[str, Any]:
        return {
            "operation_id": operation_id or "op-1", "status": "running",
            "processed": 25000, "expected_source_rows": 100000, "last_batch_number": 1,
            "candidate_catalog_version": "postgres:v6", "policy_version": "candidate-v1",
            "updated_at": datetime.now(UTC), "resolved": 1000, "provisional": 2000,
            "review_required": 21000, "unmatched": 0, "hard_conflict": 900,
            "normalization_review": 100, "policy_excluded": 0, "failed": 0,
        }

    def fetch_blocker_counts(self, operation_id: str) -> dict[str, int]:
        return {"bodywork_conflict": 81}

    def fetch_review_counts(self, operation_id: str) -> dict[str, dict[str, int]]:
        return {"bodywork_conflict": {"pending": 5, "resolved": 2}}

    def fetch_items(self, **kwargs: Any) -> tuple[int, list[dict[str, Any]]]:
        return 0, []

    def decide(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "id": kwargs["item_id"], "operation_id": kwargs["operation_id"],
            "category": "bodywork_conflict", "category_title": "Bodywork conflict",
            "category_guidance": "Review bodywork", "source_record_id": 10,
            "status": "resolved", "updated_at": datetime.now(UTC),
            "resolution": {"selected_candidate_reference": "123"},
        }


def test_summary_reports_progress_and_reviewable_categories() -> None:
    summary = MatchReviewService(FakeRepository()).summary("op-1")
    bodywork = next(item for item in summary.blockers if item.code == "bodywork_conflict")
    assert summary.progress_percent == 25.0
    assert summary.counts.review_required == 21000
    assert bodywork.count == 81
    assert bodywork.pending == 5 and bodywork.decided == 2


def test_decision_is_returned_as_immutable_match_review() -> None:
    result = MatchReviewService(FakeRepository()).decide(
        "op-1", 7,
        MatchReviewDecisionRequest(
            action="accept_top_candidate", reviewer="Ada", reason="Evidence agrees",
        ),
    )
    assert result.id == 7
    assert result.resolution["selected_candidate_reference"] == "123"
