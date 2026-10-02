"""Contracts for filtering the whole vehicle population.

The filter deliberately reuses `RuleCondition` from the match-review schemas
rather than defining a parallel shape. A filter that narrows a population and a
rule that resolves one are the same expression seen at two moments, and giving
them separate types is what forced a reviewer to rebuild by hand on the rule
screen what they had already expressed on the browse screen.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from api.app.features.match_review.chunk_schemas import FieldValueCount, RuleCondition


class VehicleFilter(BaseModel):
    """A conjunction of conditions, optionally restricted to unresolved rows."""

    conditions: list[RuleCondition] = Field(default_factory=list, max_length=8)
    unresolved_field: str | None = Field(
        default=None,
        max_length=60,
        description=(
            "Restrict to cars where this field is still unresolved -- neither "
            "derived by normalization nor filled by a live rule."
        ),
    )


class VehicleCount(BaseModel):
    matched_rows: int
    total_rows: int


class UnresolvedField(BaseModel):
    field: str
    unresolved: int
    share: float


class UnresolvedSummary(BaseModel):
    """What the current filter still cannot say about the cars it matched."""

    matched_rows: int
    fields: list[UnresolvedField]


class VehicleFacet(BaseModel):
    field: str
    matched_rows: int
    values: list[FieldValueCount]


class VehicleRow(BaseModel):
    source_record_id: int
    plate: str | None
    brand: str | None
    model: str | None
    variant: str | None
    version: str | None
    vehicle_year: int | None
    kw: int | None
    norm_status: str | None


class VehiclePage(BaseModel):
    items: list[VehicleRow]
    next_cursor: int | None
    has_more: bool


class VehicleFieldStatus(BaseModel):
    """One row of the record panel: what the registry said, what came of it."""

    field: str
    source_field: str | None
    source_value: str | None
    normalized_value: str | None
    resolved_value: str | None
    status: Literal["resolved", "unresolved", "rule_resolved"]


class VehicleDetail(BaseModel):
    source_record_id: int
    plate: str | None
    vin: str | None
    norm_status: str | None
    source_batch_id: str | None
    fields: list[VehicleFieldStatus]


class AdviseRequest(VehicleFilter):
    """Ask for a rule over the filtered population.

    Unlike the build-scoped advisor this replaces, the population is whatever the
    filter describes, so the model reasons about the cars on screen rather than
    a 226,529-row slice of them.
    """

    target_field: str = Field(min_length=1, max_length=60)


class GapGroup(BaseModel):
    """One shape of value, and how much of the gap it accounts for."""

    label: str
    rows: int
    distinct_values: int
    samples: list[str]


class GapGroupReport(BaseModel):
    field: str
    mode: Literal["leading_token", "character_shape", "exact"]
    unresolved_field: str
    total_rows: int
    groups: list[GapGroup]


class CarSearchRequest(VehicleFilter):
    """Structured canonical filters plus optional free text (plate, VIN, make, model)."""

    text: str = Field(default="", max_length=120)


class CanonicalVehicleRow(BaseModel):
    """One car as normalization understands it, not as the registry spelled it."""

    source_record_id: int
    plate: str | None
    vin: str | None
    manufacturer: str | None
    model_family: str | None
    drive_type: str | None
    bodywork_form: str | None
    engine_code: str | None
    power_kw: int | None
    displacement_cc: int | None
    production_year: int | None
    #: Canonical fields a live rule supplied because normalization could not.
    rule_filled: list[str]
    #: Canonical values normalization derives but no rule can target (no gap to fill).
    #: `fuel` keeps only the first of a hybrid's energy sources; the full list is in
    #: the record panel's normalized-values section.
    fuel: str | None
    transmission: str | None
    euro_class: str | None
    #: `passenger`, or why the car is not one (motorhome, special_modified,
    #: test_record, other_category). NULL until the projection is backfilled.
    #: Normalization's `vehicle_scope` (classify_vehicle_scope): `passenger`, or
    #: why not -- motorhome, special_modified, test_record, goods, trailer, bus,
    #: other -- or `unknown`. NULL until the projection is backfilled.
    vehicle_scope: str | None
    norm_status: str | None


class CarSearchPage(BaseModel):
    items: list[CanonicalVehicleRow]
    matched_rows: int | None
    next_cursor: int | None
    has_more: bool


class NormalizationMeta(BaseModel):
    status: str
    confidence: float
    applied_rule_ids: list[str]
    review_reasons: list[str]
    mapping_version: str | None
    rule_version: str | None
    pipeline_version: str | None
    updated_at: datetime | None


class FullVehicleRecord(VehicleDetail):
    """One car in full: every registry field, every normalized value."""

    ingested_at: datetime | None
    #: The registry row exactly as ingested, every key it carries.
    registry: dict[str, Any]
    #: Every value normalization derived (fuel, drive, type approval, tyres, ...).
    normalized: dict[str, Any]
    normalization: NormalizationMeta | None
