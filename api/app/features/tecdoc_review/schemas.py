from typing import Any

from pydantic import BaseModel, Field


class TecDocPromotionSummary(BaseModel):
    batch_id: str | None = None
    source_version: str | None = None
    source_rows: int = 0
    promoted_ktypes: int = 0
    manufacturers: int = 0
    model_families: int = 0
    engines: int = 0
    hierarchy_status: str = "awaiting_platform_mapping"


class TecDocVehicle(BaseModel):
    ktype: str
    alias_id: str
    variant_id: str
    source_name: str | None = None
    manufacturer: str | None = None
    model_family: str | None = None
    engine_code: str | None = None
    displacement_cc: int | None = None
    displacement_source: str | None = None
    fuel_type: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    status: str = "provisional"
    hierarchy_status: str = "awaiting_platform_mapping"
    source_row_refs: list[str] = Field(default_factory=list)
    source_keys: dict[str, str] = Field(default_factory=dict)


class TecDocReviewPage(BaseModel):
    summary: TecDocPromotionSummary
    filtered_total: int = 0
    limit: int
    offset: int
    items: list[TecDocVehicle] = Field(default_factory=list)
    promotion_rules: list[dict[str, Any]] = Field(default_factory=list)
