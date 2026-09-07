"""Contracts for filtering the whole vehicle population.

The filter deliberately reuses `RuleCondition` from the match-review schemas
rather than defining a parallel shape. A filter that narrows a population and a
rule that resolves one are the same expression seen at two moments, and giving
them separate types is what forced a reviewer to rebuild by hand on the rule
screen what they had already expressed on the browse screen.
"""

from __future__ import annotations

from typing import Literal

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
