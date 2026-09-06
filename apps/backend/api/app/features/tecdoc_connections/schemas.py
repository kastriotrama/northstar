from typing import Literal

from pydantic import BaseModel, Field

EngineSelectionStatus = Literal[
    "source_exact",
    "uniquely_supported",
    "ambiguous",
    "tecdoc_only",
    "contradicted",
    "unavailable",
]
EngineEvidenceSource = Literal[
    "ts_exact_tecdoc_allocation",
    "reviewed_fingerprint",
    "ktype_uses_engine",
    "ts_vs_tecdoc_conflict",
    "none",
]
MatchValidationStatus = Literal["current_resolved", "rerun_required"]


class ResolvedConnection(BaseModel):
    vehicle_id: str
    plate: str
    manufacturer: str
    ts_model: str | None = None
    tecdoc_model: str
    ktype: str
    year: int | None = None
    fuels: list[str] = Field(default_factory=list)
    engine_codes: list[str] = Field(default_factory=list)
    ts_engine_code: str | None = None
    tecdoc_compatible_engine_codes: list[str] = Field(default_factory=list)
    selected_engine_code: str | None = None
    engine_selection_status: EngineSelectionStatus = "unavailable"
    engine_evidence_source: EngineEvidenceSource = "none"
    engine_used_for_ktype_selection: bool = False
    match_validation_status: MatchValidationStatus = "current_resolved"
    match_validation_reasons: list[str] = Field(default_factory=list)
    tecdoc_year_from: int | None = None
    tecdoc_year_to: int | None = None
    tecdoc_fuels: list[str] = Field(default_factory=list)
    tecdoc_displacement_cc: int | None = None
    tecdoc_power_kw: int | None = None
    tecdoc_bodyworks: list[str] = Field(default_factory=list)
    tecdoc_drive_type: str | None = None
    displacement_cc: int | None = None
    power_kw: int | None = None
    bodywork: str | None = None
    drive_type: str | None = None
    confidence_route: str
    evidence: list[str] = Field(default_factory=list)
    routing_reasons: list[str] = Field(default_factory=list)


class ResolvedConnectionPage(BaseModel):
    total: int
    filtered_total: int
    limit: int
    offset: int
    privacy_note: str
    current_resolved_total: int = 0
    rerun_required_total: int = 0
    items: list[ResolvedConnection] = Field(default_factory=list)
