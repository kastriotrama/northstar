from typing import Any, Literal

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


# --- filtering the vehicle population, ported from `/v1/vehicles` -----------------------
#
# `layer` is accepted for wire compatibility with the shared TS `RuleCondition`
# shape (so the frontend's `FilterState`/chip editor can be reused verbatim) but
# is unused: TecDoc has no source/normalized distinction, only one projection.


class TecDocVehicleCondition(BaseModel):
    field: str = Field(max_length=60)
    operator: Literal["equals", "not_equals", "starts_with", "contains", "gte", "lte"]
    layer: str = "source"
    values: list[str] = Field(default_factory=list, max_length=20)


class TecDocVehicleFilter(BaseModel):
    query: str = Field(default="", max_length=120)
    conditions: list[TecDocVehicleCondition] = Field(default_factory=list, max_length=20)
    unresolved_field: str | None = Field(default=None, max_length=40)


class TecDocVehicleCount(BaseModel):
    matched_rows: int = 0
    total_rows: int = 0


class TecDocUnresolvedField(BaseModel):
    field: str
    label: str
    unresolved: int
    share: float = 0.0


class TecDocUnresolvedSummary(BaseModel):
    matched_rows: int = 0
    fields: list[TecDocUnresolvedField] = Field(default_factory=list)


class TecDocVehicleFacetValue(BaseModel):
    value: str
    count: int


class TecDocVehicleFacet(BaseModel):
    field: str
    matched_rows: int = 0
    values: list[TecDocVehicleFacetValue] = Field(default_factory=list)


# --- the value-level gap: which raw codes/labels have no canonical target ---------------


class TecDocResolution(BaseModel):
    """A reviewer's live ruling on one value, from `core.tecdoc_resolution_rules`.

    `source_system`/`relation`/`support` only carry meaning on a synonym
    row (`canonical_field` in `fuel`/`bodywork`/`drive`) -- a TecDoc gap
    resolution always reports `source_system="tecdoc"`,
    `relation="equivalent"`, `support=None`.
    """

    decision: Literal["accepted", "excluded"]
    canonical_value: str | None = None
    note: str = ""
    reviewed_by: str
    updated_at: str
    source_system: Literal["transportstyrelsen", "tecdoc"] = "tecdoc"
    relation: Literal["equivalent", "compatible"] = "equivalent"
    support: int | None = None


class TecDocGapValue(BaseModel):
    source_term: str
    label: str | None = None
    key_table: str | None = None
    support: int
    #: Set when this value must not be resolved with a single target -- a mixed
    #: TecDoc descriptor today. Present means the Resolve action is refused.
    blocked_reason: str | None = None
    resolution: TecDocResolution | None = None


class TecDocGapValuesResponse(BaseModel):
    canonical_field: str
    key_table: str | None = None
    canonical_options: list[str] = Field(default_factory=list)
    values: list[TecDocGapValue] = Field(default_factory=list)


# --- one row's fields, each with its outcome -- the per-KType detail panel --------------


class TecDocVehicleFieldStatus(BaseModel):
    """One canonical field on one KType: what it says, what came of it.

    Mirrors `vehicle_filter.schemas.VehicleFieldStatus` on the TS side -- same
    three states -- so the two review screens' record panels behave alike.
    """

    canonical_field: str
    source_term: str | None
    label: str | None = None
    canonical_value: str | None = None
    status: Literal["resolved", "unresolved", "rule_resolved"]
    #: Set when `source_term` must not be resolved with a single target -- see
    #: `TecDocGapValue.blocked_reason`. Only ever set on an unresolved field.
    blocked_reason: str | None = None


class TecDocVehicleDetail(BaseModel):
    source_key: str
    manufacturer: str | None = None
    model_family: str | None = None
    fields: list[TecDocVehicleFieldStatus] = Field(default_factory=list)


class TecDocResolveRequest(BaseModel):
    """One reviewer ruling, written into `core.tecdoc_resolution_rules`.

    `energy_sources`/`bodywork_form`/`drive_type`/`transmission_type` are
    TecDoc's own gap fields -- ruling on a raw code/label TecDoc's own data
    holds. `fuel`/`bodywork`/`drive` are cross-system synonym fields --
    declaring that a TS term and a TecDoc term denote the same (`equivalent`)
    or a broader-than (`compatible`) concept; see `service.py`'s `resolve()`
    for how the two are validated differently. `source_system`/`relation`/
    `support` are ignored on a gap field (always tecdoc/equivalent/None
    there) and required in shape (not value) on a synonym field.
    """

    canonical_field: Literal[
        "energy_sources", "bodywork_form", "drive_type", "transmission_type",
        "fuel", "bodywork", "drive",
    ]
    source_term: str = Field(max_length=200)
    decision: Literal["accepted", "excluded"]
    canonical_value: str | None = Field(default=None, max_length=80)
    note: str = Field(default="", max_length=500)
    reviewed_by: str = Field(max_length=80)
    source_system: Literal["transportstyrelsen", "tecdoc"] = "tecdoc"
    relation: Literal["equivalent", "compatible"] = "equivalent"
    support: int | None = Field(default=None, ge=1)
