"""Contracts for matching statistics read from stored results.

Everything here is answered from `core.vehicle_match_results` joined to
`core.vehicles`; no request under this feature runs the matcher. One car in
full -- every candidate's values and the decision trace -- stays with the
matching lookup, which evaluates that one car live.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from api.app.features.vehicles.schemas import VehicleFilter

#: What the overview counts a car as. A person's choice outranks the matcher:
#: `chosen` / `chosen_none` are cars a person decided ("this KType" / "none of
#: these"). `not_evaluated` is a car with no stored result yet. The rest are the
#: matcher's own states (see `ingestion.vehicle_match_result_migrations`).
OverviewState = Literal[
    "resolved",
    "several",
    "one_unconfirmed",
    "none",
    "not_matchable",
    "chosen",
    "chosen_none",
    "not_evaluated",
]


class StateCount(BaseModel):
    state: OverviewState
    cars: int


class FieldCount(BaseModel):
    field: str
    cars: int


class ReasonCount(BaseModel):
    reason: str
    cars: int


class ValueCount(BaseModel):
    value: str
    cars: int


class MatchRunInfo(BaseModel):
    run_id: str
    mode: str
    status: str
    catalog_batch: str
    matcher_version: str
    target: int
    evaluated: int
    unchanged: int
    started_at: datetime
    finished_at: datetime | None


class MatchResultCounts(BaseModel):
    """Cars per state under a filter: the strip above the car list."""

    total: int
    states: list[StateCount]
    #: Cars whose vehicle changed after it was matched (see the overview).
    changed_since_matched: int


class MatchResultOverview(BaseModel):
    """Where every car of the filter stands, from stored results."""

    #: Cars the filter matches.
    total: int
    states: list[StateCount]
    #: The matcher's own terminals over the cars that have a result.
    terminals: list[ValueCount]
    #: `several`: how many possible KTypes the cars have; the top value is a
    #: floor, since the matcher returns at most `candidate_limit`.
    several_candidate_counts: dict[str, int]
    candidate_limit: int
    #: `several`: fields that would tell the possible KTypes apart ...
    several_separating_fields: list[FieldCount]
    #: ... and of those, how often the car itself lacks the value.
    several_missing_fields: list[FieldCount]
    #: `none`: fields the car conflicts with its best candidate on.
    none_conflicting_fields: list[FieldCount]
    #: `none`: cars for which the matcher found no candidate at all.
    none_without_candidates: int
    #: Why `not_matchable` cars never reached matching.
    not_matchable_reasons: list[ReasonCount]
    #: Cars whose stored result may be out of date: the vehicle changed after
    #: it was matched. They are still counted under their stored state.
    changed_since_matched: int
    #: Catalog batches and matcher versions the stored results were computed with.
    catalog_batches: list[ValueCount]
    matcher_versions: list[ValueCount]
    latest_run: MatchRunInfo | None


class MatchResultCarsRequest(VehicleFilter):
    """The Vehicles filter, narrowed to one state and optionally to one cause."""

    state: OverviewState
    #: `several`: only cars lacking this separating field.
    missing_field: str | None = Field(default=None, min_length=1, max_length=60)
    #: `several`: only cars whose possible KTypes differ on this field.
    separating_field: str | None = Field(default=None, min_length=1, max_length=60)
    #: `none`: only cars conflicting on this field.
    conflicting_field: str | None = Field(default=None, min_length=1, max_length=60)
    #: Only cars carrying this reason code.
    reason: str | None = Field(default=None, min_length=1, max_length=120)
    #: Only cars whose accepted or possible KTypes include this one.
    ktype: str | None = Field(default=None, min_length=1, max_length=40)
    #: Only cars with exactly this many possible KTypes.
    candidate_count: int | None = Field(default=None, ge=0, le=50)
    #: The last NOR ID of the previous page.
    after: str | None = Field(default=None, min_length=1, max_length=30)
    limit: int = Field(default=50, ge=1, le=500)


class MatchResultCar(BaseModel):
    """One car of a state list: who it is, and what is stored about its matching."""

    vehicle_id: str
    plate: str | None
    vin: str | None
    manufacturer: str | None
    model_family: str | None
    production_year: int | None
    state: OverviewState
    #: The matcher's own state; None for a car with no stored result.
    automatic_state: str | None
    terminal: str | None
    #: The KType in force: a person's choice, else the one the matcher accepted.
    ktype: str | None
    #: The KType the matcher accepted, whatever a person chose.
    automatic_ktype: str | None
    best_candidate_ktype: str | None
    confidence: float | None
    #: The possible KTypes, best first, with the matcher's confidence in each.
    candidate_ktypes: list[str]
    candidate_confidences: list[float]
    separating_fields: list[str]
    missing_fields: list[str]
    conflicting_fields: list[str]
    reason_codes: list[str]
    evaluated_at: datetime | None
    #: The vehicle changed after it was matched; the stored result may be out of date.
    changed_since_matched: bool


class MatchResultCarPage(BaseModel):
    state: OverviewState
    #: Cars of this state under the filter and the narrowing.
    total: int
    cars: list[MatchResultCar]
    #: Pass as `after` for the next page; None when this is the last.
    next_after: str | None
