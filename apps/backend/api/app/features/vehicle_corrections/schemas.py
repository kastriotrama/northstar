"""Contracts for a person's corrections of a car's data: one car, or the cars like it."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

CorrectionAction = Literal["set", "ignore", "withdraw"]
#: What a decision about many cars can do: `withdraw` is the decision's own undo.
ManyCarsAction = Literal["set", "ignore"]
CorrectionStatus = Literal["set", "ignored", "withdrawn"]
#: `list`: one comma-joined string of members from the field's `values`.
FieldType = Literal["text", "integer", "list"]
#: Which cars a correction applies to.
ScopeKind = Literal["this_car", "same_data", "like_this"]
#: Plain comparisons of one vehicle column; `is_empty` takes no values.
ScopeOperator = Literal["equals", "is_empty", "gte", "lte"]
PreviewStatus = Literal["running", "done", "failed", "cancelled"]
#: Where a check sorts one car; every checked car has exactly one.
Outcome = Literal[
    "gained", "lost", "moved", "same", "worse", "still_unresolved",
    "no_effect", "already_corrected", "not_like_this",
]
DecisionEventKind = Literal["apply", "propose"]
DecisionStatus = Literal["proposed", "applied", "withdrawn"]


def _has_control_character(value: str, *, allowed: str = "") -> bool:
    """A NUL or another control character: PostgreSQL text cannot hold NUL at all."""

    return any(
        unicodedata.category(character) == "Cc" and character not in allowed
        for character in value
    )


def _clean_reviewer(value: str) -> str:
    trimmed = value.strip()
    if not 1 <= len(trimmed) <= 120:
        raise ValueError("reviewer must be 1 to 120 characters")
    if _has_control_character(trimmed):
        raise ValueError("reviewer must not contain control characters")
    return trimmed


def _clean_reason(value: str | None) -> str | None:
    trimmed = (value or "").strip()
    if len(trimmed) > 1000:
        raise ValueError("reason must be at most 1000 characters")
    if _has_control_character(trimmed, allowed="\n\r\t"):
        raise ValueError("reason must not contain control characters")
    return trimmed or None


def _clean_value(value: str | None) -> str | None:
    # Spacing a pasted value may carry is the service's to tidy; anything
    # else of that kind is refused here, before it can reach the database.
    if value is not None and _has_control_character(value, allowed="\n\r\t"):
        raise ValueError("value must not contain control characters")
    return value


class CorrectionRequest(BaseModel):
    #: Minted by the client once per action and resent unchanged on a retry; it
    #: becomes the stored row's `correction_id`.
    operation_id: UUID
    #: One of the lookup's `correctable_fields`, or `normalization_stop` to
    #: release a car stopped before matching (`ignore`) or take that back.
    field: str
    action: CorrectionAction
    #: The value to use, always as text (an integer as its digits, a list as its
    #: members joined by commas); given exactly when `action` is `set`. The
    #: service checks it against the field's rules.
    value: str | None = None
    reviewer: str = Field(max_length=400)
    reason: str | None = Field(default=None, max_length=4000)
    #: The field's `lookup.corrections[].correction_id` as the screen showed it (a
    #: withdrawn one too), or null when the field was never corrected.
    supersedes_correction_id: UUID | None = None
    #: `lookup.evidence_fingerprint`; required for `set` and `ignore`.
    evidence_fingerprint: str | None = Field(default=None, max_length=64)
    #: A `set` or `ignore` that would take a resolved car's KType away, or move
    #: the car to another one, is refused (`confirmation_required`) until the
    #: person has seen that and sends the same request with this set.
    confirm_change: bool = False

    @field_validator("field")
    @classmethod
    def _field(cls, value: str) -> str:
        return value.strip()

    @field_validator("value")
    @classmethod
    def _value(cls, value: str | None) -> str | None:
        return _clean_value(value)

    @field_validator("reviewer")
    @classmethod
    def _reviewer(cls, value: str) -> str:
        return _clean_reviewer(value)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str | None) -> str | None:
        return _clean_reason(value)

    @model_validator(mode="after")
    def _consistent(self) -> CorrectionRequest:
        if self.action != "set":
            if (self.value or "").strip():
                raise ValueError("value is given only when action is 'set'")
            self.value = None
        if self.action != "withdraw" and not self.evidence_fingerprint:
            raise ValueError("evidence_fingerprint is required for 'set' and 'ignore'")
        return self


class CorrectionDecision(BaseModel):
    """The decision about many cars that wrote one of a car's correction rows."""

    decision_id: UUID
    #: The sentence the person saw for the cars the decision covers.
    scope_label: str
    #: Cars the decision's application wrote.
    member_count: int
    #: Who made the event that wrote this row: the application, or its withdrawal.
    reviewer: str


class CorrectionState(BaseModel):
    """The head of one field's correction chain, as the lookup shows it."""

    field: str
    #: `set`: `value` is in force. `ignored`: the car's own value is not used.
    #: `withdrawn`: nothing is in force; the next correction supersedes this row.
    status: CorrectionStatus
    correction_id: UUID
    value: str | None
    reviewer: str
    reason: str | None
    created_at: datetime
    #: What the matcher used for the field when the correction was made, and
    #: where that value came from.
    previous_value: str | None
    previous_source: str | None
    #: Set when a decision covering many cars wrote the row; null for one car's.
    group_id: UUID | None = None
    #: Rows in this field's chain, the current one included.
    history_count: int = 1
    #: The decision behind `group_id`; null for a correction of this one car.
    decision: CorrectionDecision | None = None


class CorrectableField(BaseModel):
    """One field a person may correct, with what the matcher uses for it today."""

    field: str
    label: str
    type: FieldType
    #: The closed vocabulary (for a `list`, the members to pick from); empty
    #: when any value is accepted.
    values: list[str] = Field(default_factory=list)
    #: The matcher's evidence keys that belong to the field. A candidate's key
    #: `k` is the field's when `k == key` or `k` starts with `key + "_"`.
    evidence_keys: list[str]
    #: What the matcher uses for this car today, as text; null when the car has
    #: no value or its value is ignored.
    current_value: str | None
    #: `registry` (the car's own record), or what supplied the value instead:
    #: `ais`, `review`, `rule`, `derived` or `correction`.
    current_source: str
    #: Values the listed candidates carry for the field, in candidate order,
    #: without the current one. Each is a value the field accepts.
    suggestions: list[str] = Field(default_factory=list)


class CorrectionHistoryEntry(BaseModel):
    correction_id: UUID
    action: CorrectionAction
    value: str | None
    reviewer: str
    reason: str | None
    created_at: datetime
    supersedes_correction_id: UUID | None
    previous_value: str | None
    previous_source: str | None
    group_id: UUID | None
    #: The catalog batch and the matcher's outcome when the correction was made.
    catalog_batch: str
    automatic_terminal: str
    automatic_ktype: str | None
    code_version: str


class CorrectionFieldHistory(BaseModel):
    field: str
    #: The chain's head: the first entry, a withdrawn one too.
    current_correction_id: UUID | None
    #: From the current row backwards.
    entries: list[CorrectionHistoryEntry]


class CorrectionHistory(BaseModel):
    vehicle_id: str
    #: One entry per corrected field, by field name; empty when nobody corrected.
    fields: list[CorrectionFieldHistory]


# ------------------------------------------------------------------ which cars (scopes)


class ScopeCondition(BaseModel):
    """One plain comparison of one vehicle column. Values are OR-ed."""

    field: str = Field(min_length=1, max_length=60)
    operator: ScopeOperator = "equals"
    values: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("field")
    @classmethod
    def _field(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _shaped(self) -> ScopeCondition:
        if any(
            not value.strip() or len(value) > 200 or _has_control_character(value)
            for value in self.values
        ):
            raise ValueError("a value must be 1 to 200 characters without control characters")
        if self.operator == "is_empty" and self.values:
            raise ValueError("is_empty takes no values")
        if self.operator == "equals" and not self.values:
            raise ValueError("equals needs at least one value")
        if self.operator in {"gte", "lte"} and len(self.values) != 1:
            raise ValueError(f"{self.operator} takes exactly one value")
        return self


class ScopesRequest(BaseModel):
    """The correction a person entered, before choosing which cars it applies to."""

    field: str
    action: ManyCarsAction
    #: Given exactly when `action` is `set`, as in the one-car request.
    value: str | None = None

    @field_validator("field")
    @classmethod
    def _field(cls, value: str) -> str:
        return value.strip()

    @field_validator("value")
    @classmethod
    def _value(cls, value: str | None) -> str | None:
        return _clean_value(value)

    @model_validator(mode="after")
    def _consistent(self) -> ScopesRequest:
        if self.action != "set":
            if (self.value or "").strip():
                raise ValueError("value is given only when action is 'set'")
            self.value = None
        return self


class NarrowableField(BaseModel):
    """A column a scope may be narrowed on, prefilled with the car's own value."""

    field: str
    label: str
    #: The car's own value as text; null when it has none (narrow with `is_empty`).
    value: str | None


class ScopeOption(BaseModel):
    """One answer to "which cars does this apply to?"."""

    kind: ScopeKind
    #: The scope in plain words; for `same_data` with its count.
    label: str
    #: Cars the scope holds, this car included; null when counting took too long.
    count: int | None = None
    #: The count did not finish in time: narrow the scope before checking it.
    too_broad: bool = False
    #: `like_this` only: which rung of the field's ladder, 0 the narrowest. Send
    #: it back with the check.
    rung: int | None = None
    #: The scope as conditions on vehicle columns, the manufacturer first.
    conditions: list[ScopeCondition] = Field(default_factory=list)
    narrowable: list[NarrowableField] = Field(default_factory=list)


class ScopeOptions(BaseModel):
    scopes: list[ScopeOption]


class PreviewScope(BaseModel):
    """The scope a check runs on: one of the options, optionally narrowed."""

    kind: Literal["same_data", "like_this"]
    #: `like_this`: the option's `rung`. Left out, the option's `conditions` say
    #: which rung is meant; with neither it is the narrowest (0).
    rung: int | None = Field(default=None, ge=0, le=20)
    #: `like_this`: the chosen option's `conditions` as the scopes call returned them.
    conditions: list[ScopeCondition] | None = Field(default=None, max_length=24)
    #: Further conditions, each on one of the option's `narrowable` fields.
    narrow: list[ScopeCondition] = Field(default_factory=list, max_length=12)


class PreviewRequest(BaseModel):
    field: str
    action: ManyCarsAction
    value: str | None = None
    scope: PreviewScope
    #: `lookup.evidence_fingerprint` of the car the correction was entered on.
    #: When given, the check is refused (`evidence_changed`) if that car's
    #: matching is no longer what the screen showed.
    evidence_fingerprint: str | None = Field(default=None, max_length=64)

    @field_validator("field")
    @classmethod
    def _field(cls, value: str) -> str:
        return value.strip()

    @field_validator("value")
    @classmethod
    def _value(cls, value: str | None) -> str | None:
        return _clean_value(value)

    @model_validator(mode="after")
    def _consistent(self) -> PreviewRequest:
        if self.action != "set":
            if (self.value or "").strip():
                raise ValueError("value is given only when action is 'set'")
            self.value = None
        return self


# ------------------------------------------------------------------------ the check


class MatchState(BaseModel):
    """Where the matcher ends for a car."""

    terminal: str
    #: The KType the car resolves to; null unless `terminal` is `resolved`.
    ktype: str | None


class PreviewScopeView(BaseModel):
    kind: Literal["same_data", "like_this"]
    label: str
    rung: int | None = None
    conditions: list[ScopeCondition] = Field(default_factory=list)


class PreviewCounts(BaseModel):
    """Checked cars by outcome. `lost`, `moved` and `worse` are the harmed cars."""

    gained: int = 0
    lost: int = 0
    moved: int = 0
    worse: int = 0
    same: int = 0
    still_unresolved: int = 0
    no_effect: int = 0
    already_corrected: int = 0
    not_like_this: int = 0
    #: Evaluated cars that carry a person's KType choice (or "none of these").
    with_choice: int = 0
    #: Of those, cars the matcher would then resolve to another KType than the chosen one.
    choice_would_disagree: int = 0
    #: The `still_unresolved` cars by the terminal they would end on.
    still_unresolved_by_terminal: dict[str, int] = Field(default_factory=dict)


class EngineCheck(BaseModel):
    """The engine proxy on the `gained` cars: does the KType's engine list hold the car's code?

    Every gained car is `unchecked` when the corrected field is the engine code
    itself: the proxy would then check the correction against itself.
    """

    agree: int = 0
    differ: int = 0
    unchecked: int = 0


class CorrectionPreview(BaseModel):
    """A check of what a correction would do to the cars of a scope; poll until it settles."""

    preview_id: str
    status: PreviewStatus
    field: str
    action: ManyCarsAction
    value: str | None
    scope: PreviewScopeView
    #: Cars the scope held when the check started.
    affected: int
    #: At most this many cars are checked (`CORRECTION_PREVIEW_MAX_CARS`).
    cap: int
    checked: int
    #: Every affected car was checked: no cap, no time-out, not stopped.
    complete: bool
    #: Why the check ended before every car: `cap`, `time_limit` or `stopped`.
    stopped_by: Literal["cap", "time_limit", "stopped"] | None = None
    counts: PreviewCounts
    engine_check: EngineCheck
    #: Cars an application would write without the harmed ones.
    would_write: int
    can_apply: bool
    #: Why not: `not_all_cars_checked`, `nothing_to_apply`,
    #: `harms_more_than_it_fixes`, `preview_expired`.
    blocked_by: list[str] = Field(default_factory=list)
    catalog_batch: str = ""
    seconds_elapsed: float = 0.0
    #: Set when `status` is `failed`.
    error: str | None = None


class PreviewCar(BaseModel):
    vehicle_id: str
    plate: str | None
    outcome: Outcome
    #: Null for `no_effect`, `already_corrected` and `not_like_this`: such a
    #: car is not evaluated.
    before: MatchState | None = None
    after: MatchState | None = None


class PreviewCarPage(BaseModel):
    preview_id: str
    #: The outcome asked for; null when every checked car was asked for.
    outcome: Outcome | None
    total: int
    offset: int
    cars: list[PreviewCar]


# ----------------------------------------------------------------------- decisions


class DecisionRequest(BaseModel):
    #: Minted by the client once per action and resent unchanged on a retry; it
    #: becomes the decision's id.
    operation_id: UUID
    preview_id: str = Field(min_length=1, max_length=64)
    #: `apply` writes the checked cars; `propose` stores the decision and its
    #: measurement and writes no car.
    event: DecisionEventKind
    #: Also write the cars the check found harmed (`lost`, `moved`, `worse`).
    include_changed: bool = False
    reviewer: str = Field(max_length=400)
    #: Required to apply.
    reason: str | None = Field(default=None, max_length=4000)

    @field_validator("preview_id")
    @classmethod
    def _preview_id(cls, value: str) -> str:
        return value.strip()

    @field_validator("reviewer")
    @classmethod
    def _reviewer(cls, value: str) -> str:
        return _clean_reviewer(value)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str | None) -> str | None:
        return _clean_reason(value)


class SkippedCars(BaseModel):
    """Checked cars an application left out when it looked again under their locks."""

    #: What the matcher is handed for the car is not what was checked.
    changed_since_check: int = 0
    #: A person's correction of the field appeared after the check.
    corrected_meanwhile: int = 0


class DecisionResult(BaseModel):
    decision_id: UUID
    status: DecisionStatus
    #: Cars written; 0 for a proposal.
    written: int
    skipped: SkippedCars
    #: What the check found, car by car.
    counts: PreviewCounts
    scope_label: str
    #: The written cars by the outcome the check gave them.
    written_by_outcome: dict[str, int] = Field(default_factory=dict)


class DecisionWithdrawRequest(BaseModel):
    operation_id: UUID
    reviewer: str = Field(max_length=400)
    #: Required: the database refuses a withdrawal without one.
    reason: str | None = Field(default=None, max_length=4000)

    @field_validator("reviewer")
    @classmethod
    def _reviewer(cls, value: str) -> str:
        return _clean_reviewer(value)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str | None) -> str | None:
        return _clean_reason(value)


class DecisionWithdrawal(BaseModel):
    decision_id: UUID
    status: DecisionStatus
    #: Cars whose correction was taken back.
    withdrawn: int
    #: Cars a person changed since: left as they are.
    left_changed: int
    #: Cars the decision had written.
    member_count: int
    scope_label: str
    #: The cars left as they are, by why: `changed_by_person`.
    skipped: dict[str, int] = Field(default_factory=dict)


class DecisionEvent(BaseModel):
    event_id: UUID
    event: Literal["propose", "apply", "withdraw"]
    reviewer: str
    reason: str | None
    created_at: datetime


class DecisionSummary(BaseModel):
    decision_id: UUID
    status: DecisionStatus
    field: str
    action: ManyCarsAction
    value: str | None
    scope_label: str
    #: `{kind, rung, conditions, anchor_value}` as stored with the decision.
    scope: dict[str, Any]
    manufacturer: str
    model_family: str | None
    #: Who decided, why and when: the decision's first event.
    reviewer: str
    reason: str | None
    created_at: datetime
    #: Cars the decision's application wrote; 0 for a proposal.
    member_count: int
    #: The check the decision rests on, as its latest event stored it.
    measurement: dict[str, Any]
    #: Every event, the first one first.
    events: list[DecisionEvent]


class DecisionList(BaseModel):
    decisions: list[DecisionSummary]
