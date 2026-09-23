"""Contracts for diagnosing TS-to-TecDoc matching from the Vehicles tab.

Two views of the same evaluation. A lookup shows one car in full -- what the
matcher saw and every KType it weighed. A summary runs the same evaluation
over a filtered population and counts where it ends, so a gap is named by the
field that causes it rather than by a pile of individual cars.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from api.app.features.vehicle_filter.schemas import CarSearchRequest

#: `one`/`several`/`none` count *compatible* candidates -- KTypes the matcher
#: returned that conflict with the car on no field. `not_matchable` is a car the
#: pipeline stopped before scoring (normalization review, a policy route such as
#: a motorhome, no manufacturer or model evidence).
MatchBucket = Literal["one", "several", "none", "not_matchable"]


class MatcherInputs(BaseModel):
    """What the matcher keyed on, after rule-filled values were applied."""

    manufacturer: str
    model_values: list[str]
    production_year: int | None
    fuels: list[str]
    engine_code: str | None
    displacement_cc: int | None
    power_kw: int | None
    drive_type: str | None
    bodywork_form: str | None
    model_recovered_from: str | None


class KTypeCandidate(BaseModel):
    ktype: str
    candidate_only: bool
    confidence: float
    manufacturer: str
    model: str
    year_from: int | None
    year_to: int | None
    fuels: list[str]
    engine_codes: list[str]
    displacement_cc: int | None
    power_kw: int | None
    drive_type: str | None
    bodyworks: list[str]
    matched_fields: list[str]
    missing_fields: list[str]
    conflicting_fields: list[str]
    #: True when the candidate conflicts with the car on no field.
    compatible: bool


class VehicleMatchLookup(BaseModel):
    source_record_id: int
    plate: str | None
    vin: str | None
    catalog_batch: str
    #: The pipeline's own outcome (resolved, provisional, review_required, ...).
    terminal: str
    bucket: MatchBucket
    confidence: float | None
    top_ktype: str | None
    reason_codes: list[str]
    #: Fields rule-filled values supplied before matching, rule over derivation.
    rule_filled: list[str]
    inputs: MatcherInputs | None
    candidates: list[KTypeCandidate]
    #: The matcher returns at most this many; a full list means "this many or more".
    candidate_limit: int
    #: Fields whose values differ among the compatible candidates.
    separating_fields: list[str]
    #: Separating fields the car itself has no value for: the gap to close.
    missing_separating_fields: list[str]
    decision_trace: list[dict[str, Any]]
    #: Other records sharing this plate or VIN, newest first, when there were several.
    other_source_record_ids: list[int]


class MatchSummaryRequest(CarSearchRequest):
    """The Vehicles tab's own filter, plus how many matching cars to evaluate.

    The matcher spends ~0.1s a car (over a second for a manufacturer with
    thousands of KTypes), so the ceiling bounds a job to well under an hour.
    """

    limit: int = Field(default=2_000, ge=1, le=20_000)


class FieldCount(BaseModel):
    field: str
    cars: int


class ReasonCount(BaseModel):
    reason: str
    cars: int


class MatchExample(BaseModel):
    source_record_id: int
    plate: str | None
    manufacturer: str | None
    model_family: str | None
    candidates: int


class MatchSummary(BaseModel):
    catalog_batch: str
    #: Cars the filter matches in total.
    population: int
    #: Cars actually evaluated: the first `limit` by source_record_id.
    evaluated: int
    sampled: bool
    buckets: dict[MatchBucket, int]
    terminals: dict[str, int]
    #: How many compatible candidates the `several` cars had; the top value is
    #: a floor, since the matcher returns at most `candidate_limit`.
    several_candidate_counts: dict[str, int]
    candidate_limit: int
    #: For `none`: the fields the car conflicted with its best candidates on.
    none_conflicting_fields: list[FieldCount]
    #: For `none`: cars where no candidate cleared the matcher's threshold.
    none_without_candidates: int
    #: For `several`: fields that would tell the candidates apart ...
    several_separating_fields: list[FieldCount]
    #: ... and of those, how often the car itself lacks the value.
    several_missing_separating_fields: list[FieldCount]
    #: Why `not_matchable` cars never reached scoring.
    not_matchable_reasons: list[ReasonCount]
    examples: dict[MatchBucket, list[MatchExample]]


class MatchSummaryJob(BaseModel):
    """A summary being computed on the server; poll until `status` settles."""

    job_id: str
    status: Literal["running", "done", "failed", "cancelled"]
    #: Cars this job will evaluate: the first `limit` of the filter.
    target: int
    evaluated: int
    seconds_elapsed: float
    error: str | None
    #: Counts so far -- partial while running, final once done.
    summary: MatchSummary
