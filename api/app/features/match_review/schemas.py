from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class MatchRunCountsView(BaseModel):
    resolved: int = 0
    provisional: int = 0
    review_required: int = 0
    unmatched: int = 0
    hard_conflict: int = 0
    normalization_review: int = 0
    policy_excluded: int = 0
    failed: int = 0


class MatchBlockerCategoryView(BaseModel):
    code: str
    title: str
    guidance: str
    count: int = 0
    pending: int = 0
    in_review: int = 0
    decided: int = 0


class MatchRunReviewSummary(BaseModel):
    operation_id: str | None = None
    status: str = "not_started"
    processed: int = 0
    expected_source_rows: int = 0
    progress_percent: float = 0.0
    last_batch_number: int = 0
    candidate_catalog_version: str | None = None
    policy_version: str | None = None
    updated_at: datetime | None = None
    counts: MatchRunCountsView = Field(default_factory=MatchRunCountsView)
    blockers: list[MatchBlockerCategoryView] = Field(default_factory=list)


class MatchReviewItemView(BaseModel):
    id: int
    operation_id: str
    category: str
    category_title: str
    category_guidance: str
    source_record_id: int
    source_batch_id: str | None = None
    source_evidence: dict[str, Any] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)
    candidate_matches: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float | None = None
    status: Literal["pending", "in_review", "resolved", "rejected"]
    resolution: dict[str, Any] = Field(default_factory=dict)
    resolved_by: str | None = None
    updated_at: datetime


class MatchReviewPage(BaseModel):
    operation_id: str
    category: str | None = None
    total: int
    limit: int
    offset: int
    items: list[MatchReviewItemView]


class MatchReviewDecisionRequest(BaseModel):
    action: Literal["accept_top_candidate", "select_candidate", "keep_unresolved"]
    reviewer: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=5, max_length=1000)
    selected_candidate_reference: str | None = Field(default=None, max_length=160)
    scope: Literal["vehicle_only", "category_proposal"] = "vehicle_only"

    @model_validator(mode="after")
    def validate_candidate_selection(self) -> "MatchReviewDecisionRequest":
        if self.action == "select_candidate" and not self.selected_candidate_reference:
            raise ValueError("selected_candidate_reference is required")
        if self.action != "select_candidate" and self.selected_candidate_reference is not None:
            raise ValueError("selected_candidate_reference is only valid when selecting a candidate")
        return self


class MatchReviewPatternExample(BaseModel):
    manufacturer: str
    model: str
    candidate_reference: str | None = None


class MatchReviewPatternDecision(BaseModel):
    decision_id: str
    action: Literal["accept_pattern", "keep_blocked", "change_rule"]
    selected_values: list[str] = Field(default_factory=list)
    reviewer: str
    reason: str
    created_at: datetime


class MatchReviewPatternView(BaseModel):
    pattern_key: str
    category: str
    title: str
    summary: str
    source_values: dict[str, Any] = Field(default_factory=dict)
    candidate_values: dict[str, Any] = Field(default_factory=dict)
    sample_occurrences: int = 0
    category_occurrences: int = 0
    examples: list[MatchReviewPatternExample] = Field(default_factory=list)
    decision: MatchReviewPatternDecision | None = None


class MatchReviewPatternPage(BaseModel):
    operation_id: str
    category: str | None = None
    patterns: list[MatchReviewPatternView] = Field(default_factory=list)


class MatchReviewPatternDecisionRequest(BaseModel):
    action: Literal["accept_pattern", "keep_blocked", "change_rule"]
    reviewer: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=5, max_length=1000)
    selected_values: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_rule_change(self) -> "MatchReviewPatternDecisionRequest":
        cleaned = [value.strip() for value in self.selected_values if value.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("selected_values must be unique")
        if self.action == "change_rule" and not cleaned:
            raise ValueError("change_rule requires at least one corrected value")
        if self.action == "keep_blocked" and cleaned:
            raise ValueError("keep_blocked cannot select target values")
        self.selected_values = cleaned
        return self
