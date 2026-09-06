"""Schemas for browsing raw Transportstyrelsen staging rows."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# Keys promoted to columns in the browse grid. Everything else stays inside raw_record.
TS_SUMMARY_FIELDS: tuple[str, ...] = (
    "plate",
    "vin",
    "brand",
    "manufacturer",
    "model",
    "variant",
    "version",
    "vehicle_year",
    "vehicle_type",
    "eu_category",
    "gearbox",
    "fuel1",
    "body_code",
    "kw",
    "ccm",
)

# Fields the free-text search scans. Deliberately short: staging.transportstyrelsen_raw carries
# no expression or GIN index, so every extra field widens an already sequential scan.
TS_SEARCH_FIELDS: tuple[str, ...] = (
    "plate",
    "vin",
    "brand",
    "manufacturer",
    "model",
    "variant",
    "version",
)


class SourceRecordFilters(BaseModel):
    query: str = ""
    field: str | None = None
    value: str | None = None
    batch_id: str | None = None
    cursor: int | None = None
    limit: int = Field(default=100, ge=1, le=200)


class SourceRecord(BaseModel):
    id: int
    source_batch_id: str
    ingested_at: datetime
    raw_record: dict[str, Any] = Field(default_factory=dict)


class SourceRecordPage(BaseModel):
    items: list[SourceRecord] = Field(default_factory=list)
    limit: int
    next_cursor: int | None = None
    has_more: bool = False
    total: int = 0
    total_is_estimate: bool = True
    timed_out: bool = False
    summary_fields: list[str] = Field(default_factory=lambda: list(TS_SUMMARY_FIELDS))


class SourceRecordNormalization(BaseModel):
    source_batch_id: str
    status: str
    confidence: float = 0.0
    normalized: dict[str, Any] = Field(default_factory=dict)
    candidates: dict[str, Any] = Field(default_factory=dict)
    applied_rule_ids: list[str] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class SourceRecordDetail(BaseModel):
    record: SourceRecord
    normalizations: list[SourceRecordNormalization] = Field(default_factory=list)


class SourceBatch(BaseModel):
    batch_id: str
    records: int = 0
    status: str | None = None
    finished_at: datetime | None = None


class SourceBatchList(BaseModel):
    items: list[SourceBatch] = Field(default_factory=list)


class SourceFieldStat(BaseModel):
    field: str
    present: int
    non_null: int
    fill_rate: float
    examples: list[str] = Field(default_factory=list)


class SourceFieldInventory(BaseModel):
    sampled_rows: int
    fields: list[SourceFieldStat] = Field(default_factory=list)
