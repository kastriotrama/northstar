from typing import Any

from pydantic import BaseModel, Field


class TecDocPromotionSummary(BaseModel):
    batch_id: str | None = None
    source_version: str | None = None
    source_rows: int = 0
    promoted_ktypes: int = 0
    engine_linked_ktypes: int = 0
    facts_only_ktypes: int = 0
    manufacturers: int = 0
    model_families: int = 0
    engines: int = 0
    hierarchy_status: str = "model_family_linked_platform_optional"


class TecDocVehicle(BaseModel):
    ktype: str
    alias_id: str
    variant_id: str
    source_name: str | None = None
    manufacturer: str | None = None
    model_family: str | None = None
    engine_code: str | None = None
    transmission_code: str | None = None
    transmission_type_code: str | None = None
    transmission_type_name: str | None = None
    transmission_speeds: int | None = None
    transmission_link_status: str = "allocation_missing"
    bodywork_code: str | None = None
    bodywork_name: str | None = None
    bodywork_status: str = "code_missing"
    drive_type: str | None = None
    drive_code: str | None = None
    drive_official_label: str | None = None
    drive_status: str = "review_required"
    displacement_cc: int | None = None
    displacement_source: str | None = None
    fuel_type: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    status: str = "provisional"
    engine_link_status: str = "linked"
    tecdoc_fuel_code: str | None = None
    tecdoc_engine_type_code: str | None = None
    hierarchy_status: str = "model_family_linked_platform_optional"
    source_row_refs: list[str] = Field(default_factory=list)
    source_keys: dict[str, str] = Field(default_factory=dict)


class TecDocReviewPage(BaseModel):
    summary: TecDocPromotionSummary
    filtered_total: int = 0
    limit: int
    offset: int
    items: list[TecDocVehicle] = Field(default_factory=list)
    promotion_rules: list[dict[str, Any]] = Field(default_factory=list)


class TecDocEntity(BaseModel):
    source_key: str
    name: str
    vehicle_count: int
    sample_ktypes: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class TecDocEntityPage(BaseModel):
    kind: str
    batch_id: str | None = None
    filtered_total: int = 0
    limit: int
    offset: int
    items: list[TecDocEntity] = Field(default_factory=list)
