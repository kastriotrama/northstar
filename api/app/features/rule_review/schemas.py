from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RuleDraftRequest(BaseModel):
    canonical_value: str | None = Field(default=None, max_length=120)
    decision: Literal["accepted", "proposed"]
    display_value: str | None = Field(default=None, max_length=160)
    change_note: str = Field(min_length=5, max_length=500)


class RuleView(BaseModel):
    rule_id: str
    area: str
    source_fields: list[str]
    source_terms: list[str]
    canonical_field: str
    base_canonical_value: str | None
    active_canonical_value: str | None
    effective_canonical_value: str | None
    canonical_options: list[str]
    active_decision: str
    effective_decision: str
    active_display_value: str | None = None
    effective_display_value: str | None = None
    vehicle_scopes: list[str]
    manufacturers: list[str]
    has_draft: bool
    change_note: str | None = None


class ManufacturerEntityDraftRequest(BaseModel):
    canonical_name: str | None = Field(default=None, max_length=120)
    entity_role: Literal[
        "vehicle_manufacturer", "bodybuilder_converter", "corporate_group", "unknown"
    ]
    base_behavior: Literal["use_entity", "use_base_manufacturer", "require_evidence_review"]
    change_note: str = Field(min_length=5, max_length=500)


class ManufacturerEntityView(BaseModel):
    entity_id: str
    source_field: str
    source_term: str
    active_canonical_name: str | None = None
    effective_canonical_name: str | None = None
    active_entity_role: str
    effective_entity_role: str
    active_base_behavior: str
    effective_base_behavior: str
    occurrences: int = 0
    base_manufacturers: list[str] = Field(default_factory=list)
    has_draft: bool = False
    is_discovered: bool = False
    change_note: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    match_type: str = "exact"
    reviewed_examples: list[str] = Field(default_factory=list)


class RuleListResponse(BaseModel):
    base_version: str
    active_version: str
    active_at: datetime | None = None
    draft_count: int
    rules: list[RuleView]
    manufacturer_entities: list[ManufacturerEntityView] = Field(default_factory=list)
    review_reason_summary: dict[str, int] = Field(default_factory=dict)


class RuleActivationRequest(BaseModel):
    note: str = Field(min_length=5, max_length=500)


class RuleActivationResponse(BaseModel):
    version: str
    activated_rules: int
    activated_at: datetime


class ReprocessRequest(BaseModel):
    source_batch_id: str = Field(min_length=1, max_length=200)


class BatchSummaryView(BaseModel):
    total: int
    resolved: int
    provisional: int
    review_required: int
    failed: int


class ReprocessResponse(BaseModel):
    source_batch_id: str
    new_batch_id: str
    rule_version: str
    before: BatchSummaryView
    after: BatchSummaryView
