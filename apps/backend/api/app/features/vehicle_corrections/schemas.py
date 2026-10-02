"""Contracts for a person's corrections of one car's data."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

CorrectionAction = Literal["set", "ignore", "withdraw"]
CorrectionStatus = Literal["set", "ignored", "withdrawn"]
#: `list`: one comma-joined string of members from the field's `values`.
FieldType = Literal["text", "integer", "list"]


def _has_control_character(value: str, *, allowed: str = "") -> bool:
    """A NUL or another control character: PostgreSQL text cannot hold NUL at all."""

    return any(
        unicodedata.category(character) == "Cc" and character not in allowed
        for character in value
    )


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

    @field_validator("field")
    @classmethod
    def _field(cls, value: str) -> str:
        return value.strip()

    @field_validator("value")
    @classmethod
    def _value(cls, value: str | None) -> str | None:
        # Spacing a pasted value may carry is the service's to tidy; anything
        # else of that kind is refused here, before it can reach the database.
        if value is not None and _has_control_character(value, allowed="\n\r\t"):
            raise ValueError("value must not contain control characters")
        return value

    @field_validator("reviewer")
    @classmethod
    def _reviewer(cls, value: str) -> str:
        trimmed = value.strip()
        if not 1 <= len(trimmed) <= 120:
            raise ValueError("reviewer must be 1 to 120 characters")
        if _has_control_character(trimmed):
            raise ValueError("reviewer must not contain control characters")
        return trimmed

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str | None) -> str | None:
        trimmed = (value or "").strip()
        if len(trimmed) > 1000:
            raise ValueError("reason must be at most 1000 characters")
        if _has_control_character(trimmed, allowed="\n\r\t"):
            raise ValueError("reason must not contain control characters")
        return trimmed or None

    @model_validator(mode="after")
    def _consistent(self) -> CorrectionRequest:
        if self.action != "set":
            if (self.value or "").strip():
                raise ValueError("value is given only when action is 'set'")
            self.value = None
        if self.action != "withdraw" and not self.evidence_fingerprint:
            raise ValueError("evidence_fingerprint is required for 'set' and 'ignore'")
        return self


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
