from typing import Any, Literal

from pydantic import BaseModel, Field

NormalizationStatus = Literal["resolved", "provisional", "review_required", "failed"]


class NormalizationReviewFilters(BaseModel):
    query: str = ""
    status: NormalizationStatus | None = None
    manufacturer: str | None = None
    bodywork: str | None = None
    fuel: str | None = None
    transmission: str | None = None
    batch_id: str | None = None
    limit: int = Field(default=250, ge=1, le=300)
    offset: int = Field(default=0, ge=0)


class NormalizationStatusSummary(BaseModel):
    total: int = 0
    resolved: int = 0
    provisional: int = 0
    review_required: int = 0
    failed: int = 0


class NormalizationReviewFacets(BaseModel):
    manufacturers: list[str] = Field(default_factory=list)
    bodywork_forms: list[str] = Field(default_factory=list)
    fuels: list[str] = Field(default_factory=list)
    transmissions: list[str] = Field(default_factory=list)


class NormalizationReviewVehicle(BaseModel):
    source_record_id: int
    status: NormalizationStatus
    confidence: float = Field(ge=0.0, le=1.0)
    manufacturer: str | None = None
    model_family: str | None = None
    bodywork: str | None = None
    transmission: str | None = None
    energy_sources: list[str] = Field(default_factory=list)
    engine_code: str | None = None
    production_year: int | None = None
    review_reasons: list[str] = Field(default_factory=list)
    applied_rule_ids: list[str] = Field(default_factory=list)
    normalized: dict[str, Any] = Field(default_factory=dict)
    candidates: dict[str, Any] = Field(default_factory=dict)
    decision_trace: list[dict[str, Any]] = Field(default_factory=list)
    rule_matches: list[dict[str, Any]] = Field(default_factory=list)


class NormalizationReviewPage(BaseModel):
    batch_id: str | None
    total: int
    filtered_total: int
    limit: int
    offset: int
    summary: NormalizationStatusSummary
    facets: NormalizationReviewFacets
    items: list[NormalizationReviewVehicle]
