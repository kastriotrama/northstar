from __future__ import annotations

from typing import Any, Protocol

from api.app.features.match_review.schemas import (
    MatchBlockerCategoryView,
    MatchReviewDecisionRequest,
    MatchReviewItemView,
    MatchReviewPage,
    MatchRunCountsView,
    MatchRunReviewSummary,
)
from ingestion.tecdoc.blocker_review import CATEGORIES


class MatchReviewData(Protocol):
    def ensure_schema(self) -> None: ...
    def fetch_run(self, operation_id: str | None) -> dict[str, Any] | None: ...
    def fetch_blocker_counts(self, operation_id: str) -> dict[str, int]: ...
    def fetch_review_counts(self, operation_id: str) -> dict[str, dict[str, int]]: ...
    def fetch_items(
        self, *, operation_id: str, category: str | None, status: str | None,
        limit: int, offset: int,
    ) -> tuple[int, list[dict[str, Any]]]: ...
    def decide(
        self, *, operation_id: str, item_id: int, action: str, reviewer: str,
        reason: str, selected_candidate_reference: str | None, scope: str,
    ) -> dict[str, Any]: ...


class MatchReviewService:
    def __init__(self, repository: MatchReviewData) -> None:
        self._repository = repository

    def summary(self, operation_id: str | None) -> MatchRunReviewSummary:
        self._repository.ensure_schema()
        run = self._repository.fetch_run(operation_id)
        if run is None:
            return MatchRunReviewSummary(
                blockers=[
                    MatchBlockerCategoryView(
                        code=category.code,
                        title=category.title,
                        guidance=category.guidance,
                    )
                    for category in CATEGORIES
                ]
            )
        run_id = str(run["operation_id"])
        blocker_counts = self._repository.fetch_blocker_counts(run_id)
        review_counts = self._repository.fetch_review_counts(run_id)
        expected = int(run["expected_source_rows"])
        processed = int(run["processed"])
        count_fields = MatchRunCountsView.model_fields
        return MatchRunReviewSummary(
            operation_id=run_id,
            status=str(run["status"]),
            processed=processed,
            expected_source_rows=expected,
            progress_percent=round((processed / expected * 100) if expected else 0.0, 3),
            last_batch_number=int(run["last_batch_number"]),
            candidate_catalog_version=str(run["candidate_catalog_version"]),
            policy_version=str(run["policy_version"]),
            updated_at=run["updated_at"],
            counts=MatchRunCountsView(**{name: int(run[name]) for name in count_fields}),
            blockers=[
                MatchBlockerCategoryView(
                    code=category.code,
                    title=category.title,
                    guidance=category.guidance,
                    count=blocker_counts.get(category.code, 0),
                    pending=review_counts.get(category.code, {}).get("pending", 0),
                    in_review=review_counts.get(category.code, {}).get("in_review", 0),
                    decided=sum(
                        review_counts.get(category.code, {}).get(status, 0)
                        for status in ("resolved", "rejected")
                    ),
                )
                for category in CATEGORIES
            ],
        )

    def items(
        self, *, operation_id: str, category: str | None, status: str | None,
        limit: int, offset: int,
    ) -> MatchReviewPage:
        self._repository.ensure_schema()
        if category is not None and category not in {item.code for item in CATEGORIES}:
            raise ValueError("unknown blocker category")
        total, rows = self._repository.fetch_items(
            operation_id=operation_id,
            category=category,
            status=status,
            limit=limit,
            offset=offset,
        )
        return MatchReviewPage(
            operation_id=operation_id,
            category=category,
            total=total,
            limit=limit,
            offset=offset,
            items=[MatchReviewItemView(**row) for row in rows],
        )

    def decide(
        self, operation_id: str, item_id: int, request: MatchReviewDecisionRequest
    ) -> MatchReviewItemView:
        self._repository.ensure_schema()
        return MatchReviewItemView(**self._repository.decide(
            operation_id=operation_id,
            item_id=item_id,
            action=request.action,
            reviewer=request.reviewer,
            reason=request.reason,
            selected_candidate_reference=request.selected_candidate_reference,
            scope=request.scope,
        ))
