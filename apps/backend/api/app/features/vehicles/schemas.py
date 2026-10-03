"""Contracts for the Vehicles tab: NorthStar vehicles from `core.vehicles`.

A vehicle here is one physical car, keyed by its `NOR-` ID, carrying the value
that won each field across every provider (Transportstyrelsen, AIS, reviewers,
learned rules). Plates and VINs are identifiers of it with a history, not its
key. The TS screen's contracts -- one row per TS record, with the registry
string beside the canonical value -- stay in `vehicle_filter` under
`/v1/ts-records`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

VehicleOperator = Literal["equals", "not_equals", "starts_with", "contains", "gte", "lte"]


class VehicleCondition(BaseModel):
    """One clause on one vehicle column. Values are OR-ed; clauses are AND-ed.

    There is no `layer`: a vehicle column already holds the merged value, so
    there is no registry-versus-canonical choice to make.
    """

    field: str = Field(min_length=1, max_length=60)
    values: list[str] = Field(min_length=1, max_length=50)
    operator: VehicleOperator = "equals"

    @model_validator(mode="after")
    def _one_bound(self) -> VehicleCondition:
        if self.operator in {"gte", "lte"} and len(self.values) != 1:
            raise ValueError(f"{self.operator} takes exactly one value")
        return self


class VehicleFilter(BaseModel):
    conditions: list[VehicleCondition] = Field(default_factory=list, max_length=16)
    text: str = Field(
        default="",
        max_length=120,
        description="Plate, VIN, NOR ID, a previous plate, or make/model words.",
    )


class VehicleFieldInfo(BaseModel):
    """One column of the vehicle record, for building filters and the record panel."""

    field: str
    label: str
    group: str
    sql_type: str
    filterable: bool
    reviewable: bool


class VehicleRow(BaseModel):
    """One vehicle in the list: identity, lifecycle and the values people filter on."""

    vehicle_id: str
    plate: str | None
    vin: str | None
    registry_status: str
    vehicle_scope: str | None
    manufacturer: str | None
    model_family: str | None
    production_year: int | None
    power_kw: int | None
    displacement_cc: int | None
    engine_code: str | None
    fuel: str | None
    transmission: str | None
    drive_type: str | None
    bodywork_form: str | None
    colour: str | None
    ktype: str | None
    #: `manual` when a person chose the KType, `manual_none` for "none of these".
    match_state: str | None = None
    #: The matcher's stored state for the car (`resolved`, `several`,
    #: `one_unconfirmed`, `none`, `not_matchable`); None when it has no stored result.
    match_result: str | None = None
    #: The KType the matcher accepted, from the stored result. `ktype` above is
    #: a person's choice only.
    automatic_ktype: str | None = None
    #: Fields whose value a person asserted: a reviewer's rule, or a correction
    #: of this one car.
    review_fields: list[str]
    #: Fields a learned enrichment rule filled because no source stated them.
    rule_fields: list[str]


class VehiclePage(BaseModel):
    items: list[VehicleRow]
    #: Sent on the first page only.
    matched_rows: int | None
    #: Keyset cursor: the last row's `vehicle_id`. Never an offset.
    next_cursor: str | None
    has_more: bool


class FacetValue(BaseModel):
    value: str
    count: int


class VehicleFacet(BaseModel):
    field: str
    values: list[FacetValue]


class ValueSource(BaseModel):
    """Where one value came from: `<source>[:<ref>][@<observed_on>]`, parsed."""

    source: str
    ref: str | None
    observed_on: date | None
    #: True when the value came from the record that created the vehicle.
    origin: bool


class ValueAlternative(BaseModel):
    """A value a source stated that lost -- kept so a retired rule can fall back."""

    value: Any
    source: ValueSource


class VehicleFieldValue(BaseModel):
    field: str
    label: str
    group: str
    value: Any
    #: None when the field is empty.
    source: ValueSource | None
    alternatives: list[ValueAlternative]


class VehicleIdentifier(BaseModel):
    kind: Literal["vin", "chassis", "plate"]
    value: str
    valid_from: date | None
    valid_to: date | None
    current: bool
    source: str
    source_ref: str | None


class VehicleSourceLink(BaseModel):
    source_system: str
    source_record_key: str
    observed_on: date | None
    link_method: str
    linked_at: datetime


class EnrichmentRuleInfo(BaseModel):
    """A learned rule one of this vehicle's values came from."""

    rule_id: str
    rule_family: str
    target_field: str
    key_fields: list[str]
    key_values: list[str]
    value: str
    support: int
    agreement: float
    status: str


class VehicleRecord(BaseModel):
    """One vehicle in full: every value with its source, identifiers and links."""

    vehicle_id: str
    origin_source: str
    origin_observed_on: date | None
    ts_record_id: int | None
    registry_status: str
    created_at: datetime
    updated_at: datetime
    fields: list[VehicleFieldValue]
    #: Current first, then by when they ended, newest first.
    identifiers: list[VehicleIdentifier]
    source_links: list[VehicleSourceLink]
    #: How many links the vehicle has in total; `source_links` may be capped.
    source_link_count: int
    rules: list[EnrichmentRuleInfo]
