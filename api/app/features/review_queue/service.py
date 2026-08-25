from __future__ import annotations

from typing import Any, Protocol

from api.app.features.review_queue.schemas import (
    ReviewQueueItemView,
    ReviewQueuePage,
    ReviewStatus,
    ReviewTransitionRequest,
    RuleActivityView,
)


class QueueRepository(Protocol):
    def ensure_schema(self) -> None: ...
    def fetch_items(
        self, *, status: ReviewStatus | None, batch_id: str | None, limit: int
    ) -> list[dict[str, Any]]: ...
    def fetch_counts(self, *, batch_id: str | None) -> dict[str, int]: ...
    def fetch_rule_activity(self, *, limit: int = 200) -> list[dict[str, Any]]: ...
    def transition(
        self,
        item_id: int,
        status: ReviewStatus,
        *,
        reviewer: str | None,
        resolution: dict[str, Any] | None,
        review_draft: dict[str, Any] | None = None,
    ) -> Any: ...


class ReviewQueueService:
    def __init__(self, repository: QueueRepository) -> None:
        self._repository = repository

    def list_items(
        self, *, status: ReviewStatus | None, batch_id: str | None, limit: int
    ) -> ReviewQueuePage:
        self._repository.ensure_schema()
        return ReviewQueuePage(
            items=[
                ReviewQueueItemView(**item)
                for item in self._repository.fetch_items(
                    status=status, batch_id=batch_id, limit=limit
                )
            ],
            counts=self._repository.fetch_counts(batch_id=batch_id),
            rule_activity=(
                []
                if batch_id and batch_id.startswith("margin-calibration-")
                else [
                    RuleActivityView(**activity)
                    for activity in self._repository.fetch_rule_activity()
                ]
            ),
        )

    def transition(self, item_id: int, request: ReviewTransitionRequest) -> ReviewQueueItemView:
        self._repository.ensure_schema()
        resolution = None
        reviewer = None
        review_draft = None
        if request.status == "in_review":
            review_draft = {
                "reviewer": request.reviewer,
                "field": request.field,
                "canonical_value": request.canonical_value,
                "decision_scope": request.decision_scope,
                "rule_reference": request.rule_reference,
                "reason": request.reason,
            }
        if request.status in {"resolved", "rejected"}:
            reviewer = request.reviewer.strip() if request.reviewer else None
            if request.verdict is not None:
                resolution = {
                    "verdict": request.verdict,
                    "reason": request.reason.strip() if request.reason else None,
                }
            else:
                resolution = {
                    "decision": "accepted" if request.status == "resolved" else "rejected",
                    "field": request.field,
                    "canonical_value": request.canonical_value,
                    "decision_scope": request.decision_scope,
                    "rule_reference": request.rule_reference,
                    "reason": request.reason.strip() if request.reason else None,
                }
        self._repository.transition(
            item_id,
            request.status,
            reviewer=reviewer,
            resolution=resolution,
            review_draft=review_draft,
        )
        item = next(
            (
                row
                for row in self._repository.fetch_items(
                    status=request.status, batch_id=None, limit=1000
                )
                if row["id"] == item_id
            ),
            None,
        )
        if item is None:
            raise KeyError(f"review item {item_id} does not exist")
        return ReviewQueueItemView(**item)
