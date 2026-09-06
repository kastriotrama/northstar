from datetime import UTC, datetime
from typing import Any

from api.app.features.review_queue.schemas import ReviewTransitionRequest
from api.app.features.review_queue.service import ReviewQueueService


class FakeRepository:
    def __init__(self) -> None:
        self.draft: dict[str, Any] = {}
        self.resolution: dict[str, Any] = {}
        self.rule_activity_reads = 0

    def ensure_schema(self) -> None:
        return None

    def fetch_items(
        self, *, status: str | None, batch_id: str | None, limit: int
    ) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        return [
            {
                "id": 1,
                "review_id": "00000000-0000-0000-0000-000000000001",
                "source_record_id": 10,
                "reason_code": "match_margin_calibration",
                "status": status or "in_review",
                "created_at": now,
                "updated_at": now,
                "review_draft": self.draft,
            }
        ]

    def fetch_counts(self, *, batch_id: str | None) -> dict[str, int]:
        return {"pending": 0, "in_review": 1, "resolved": 0, "rejected": 0}

    def fetch_rule_activity(self, *, limit: int = 200) -> list[dict[str, Any]]:
        self.rule_activity_reads += 1
        return []

    def transition(
        self,
        item_id: int,
        status: str,
        *,
        reviewer: str | None,
        resolution: dict[str, Any] | None,
        review_draft: dict[str, Any] | None = None,
    ) -> object:
        self.draft = review_draft or {}
        self.resolution = resolution or {}
        return object()


def test_in_review_correction_is_persisted_and_returned() -> None:
    repository = FakeRepository()
    service = ReviewQueueService(repository)

    item = service.transition(
        1,
        ReviewTransitionRequest(
            status="in_review",
            reviewer="Ada",
            field="manufacturer",
            canonical_value="Willys",
            decision_scope="vehicle_only",
            reason="Reviewed Brand and historical model evidence",
        ),
    )

    assert item.review_draft == {
        "reviewer": "Ada",
        "field": "manufacturer",
        "canonical_value": "Willys",
        "decision_scope": "vehicle_only",
        "rule_reference": None,
        "reason": "Reviewed Brand and historical model evidence",
    }


def test_calibration_verdict_uses_fitter_resolution_contract() -> None:
    repository = FakeRepository()
    service = ReviewQueueService(repository)

    service.transition(
        1,
        ReviewTransitionRequest(
            status="resolved",
            reviewer="Ada",
            verdict="reject",
            reason="Runner-up evidence fits the vehicle",
        ),
    )

    assert repository.resolution == {
        "verdict": "reject",
        "reason": "Runner-up evidence fits the vehicle",
    }


def test_calibration_batch_skips_unrelated_global_rule_activity_query() -> None:
    repository = FakeRepository()
    service = ReviewQueueService(repository)

    page = service.list_items(
        status="pending", batch_id="margin-calibration-20260824", limit=300
    )

    assert page.rule_activity == []
    assert repository.rule_activity_reads == 0
