from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

ReviewStatus = Literal["pending", "in_review", "resolved", "rejected"]
DecisionScope = Literal["vehicle_only", "translation_rule", "manufacturer_entity"]


class ReviewQueueItemView(BaseModel):
    id: int
    review_id: str
    source_batch_id: str | None = None
    source_record_id: int
    reason_code: str
    reason_detail: str | None = None
    target_entity_type: str | None = None
    candidate_matches: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float | None = None
    status: ReviewStatus
    resolution: dict[str, Any] = Field(default_factory=dict)
    resolved_by: str | None = None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None
    source_evidence: dict[str, Any] = Field(default_factory=dict)
    normalized: dict[str, Any] = Field(default_factory=dict)
    candidates: dict[str, Any] = Field(default_factory=dict)
    review_reasons: list[str] = Field(default_factory=list)
    review_draft: dict[str, Any] = Field(default_factory=dict)


class RuleActivityView(BaseModel):
    rule_id: str
    rule_kind: Literal["translation_rule", "manufacturer_entity"]
    action: Literal["draft", "activated"]
    previous_value: str | None = None
    new_value: str | None = None
    change_note: str
    changed_at: datetime
    changed_by: str | None = None
    related_review_item_id: int | None = None
    version: str | None = None


class ReviewQueuePage(BaseModel):
    items: list[ReviewQueueItemView]
    counts: dict[str, int]
    rule_activity: list[RuleActivityView] = Field(default_factory=list)


class ReviewTransitionRequest(BaseModel):
    status: ReviewStatus
    reviewer: str | None = Field(default=None, max_length=120)
    field: str | None = Field(default=None, max_length=80)
    canonical_value: str | None = Field(default=None, max_length=200)
    decision_scope: DecisionScope | None = None
    rule_reference: str | None = Field(default=None, max_length=160)
    reason: str | None = Field(default=None, max_length=1000)
    verdict: Literal["accept", "reject", "unsure"] | None = None

    @model_validator(mode="after")
    def validate_terminal_decision(self) -> "ReviewTransitionRequest":
        if self.verdict is not None and self.status != "resolved":
            raise ValueError("a calibration verdict must resolve the review item")
        if self.status in {"resolved", "rejected"}:
            if not self.reviewer or not self.reviewer.strip():
                raise ValueError("reviewer is required")
            if not self.reason or len(self.reason.strip()) < 5:
                raise ValueError("a review reason of at least 5 characters is required")
        if self.status == "resolved" and self.verdict is None:
            if not self.field or not self.canonical_value or not self.decision_scope:
                raise ValueError("field, canonical_value, and decision_scope are required")
            if self.decision_scope != "vehicle_only" and not self.rule_reference:
                raise ValueError("rule_reference is required for a reusable decision")
        return self
