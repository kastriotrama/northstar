from pydantic import BaseModel, Field


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
    items: list[ResolvedConnection] = Field(default_factory=list)
