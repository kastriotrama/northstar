"""Schemas for field-level normalization coverage."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CoverageBatch(BaseModel):
    batch_id: str
    records: int = 0
    finished_at: datetime | None = None


class CoverageBatchList(BaseModel):
    items: list[CoverageBatch] = Field(default_factory=list)


class FieldCoverage(BaseModel):
    field: str
    normalized: int = 0
    candidates_only: int = 0
    missing: int = 0
    coverage: float = 0.0


class StatusBreakdown(BaseModel):
    resolved: int = 0
    provisional: int = 0
    review_required: int = 0
    failed: int = 0


class ReviewReasonCount(BaseModel):
    reason: str
    records: int = 0


class TsCoverageReport(BaseModel):
    batch_id: str
    rows: int = 0
    status: StatusBreakdown = Field(default_factory=StatusBreakdown)
    fields: list[FieldCoverage] = Field(default_factory=list)
    review_reasons: list[ReviewReasonCount] = Field(default_factory=list)
    fully_normalized_fields: int = 0
    partial_fields: int = 0


class TecDocFieldCoverage(BaseModel):
    entity_type: str
    field: str
    present: int = 0
    missing: int = 0
    coverage: float = 0.0


class TecDocEntityCoverage(BaseModel):
    entity_type: str
    rows: int = 0
    fields: list[TecDocFieldCoverage] = Field(default_factory=list)


class TecDocCoverageReport(BaseModel):
    batch_id: str | None = None
    entities: list[TecDocEntityCoverage] = Field(default_factory=list)
