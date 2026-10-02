"""Contracts for a person's KType choice on one car."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

ChoiceAction = Literal["choose", "none", "withdraw"]
ChoiceStatus = Literal["chosen", "none", "withdrawn"]
StaleReason = Literal[
    "catalog_batch_changed",
    "evidence_changed",
    "ktype_not_in_catalog",
    "ktype_not_a_candidate",
    "new_candidates",
]


class KTypeChoiceRequest(BaseModel):
    #: Minted by the client once per action and resent unchanged on a retry; it
    #: becomes the stored row's `choice_id`.
    operation_id: UUID
    action: ChoiceAction
    #: Required exactly when `action` is `choose`.
    ktype: str | None = Field(default=None, max_length=160)
    reviewer: str = Field(max_length=400)
    reason: str | None = Field(default=None, max_length=4000)
    #: `lookup.choice.choice_id` as the screen showed it (a withdrawn one too), or null.
    supersedes_choice_id: UUID | None = None
    #: `lookup.evidence_fingerprint`; required for `choose` and `none`.
    evidence_fingerprint: str | None = Field(default=None, max_length=64)

    @field_validator("reviewer")
    @classmethod
    def _reviewer(cls, value: str) -> str:
        trimmed = value.strip()
        if not 1 <= len(trimmed) <= 120:
            raise ValueError("reviewer must be 1 to 120 characters")
        return trimmed

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str | None) -> str | None:
        trimmed = (value or "").strip()
        if len(trimmed) > 1000:
            raise ValueError("reason must be at most 1000 characters")
        return trimmed or None

    @field_validator("ktype")
    @classmethod
    def _ktype(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @model_validator(mode="after")
    def _consistent(self) -> KTypeChoiceRequest:
        if (self.action == "choose") != (self.ktype is not None):
            raise ValueError("ktype is required exactly when action is 'choose'")
        if self.action != "withdraw" and not self.evidence_fingerprint:
            raise ValueError("evidence_fingerprint is required for 'choose' and 'none'")
        return self


class ChangedInput(BaseModel):
    """One matcher input that differs from what the person saw."""

    field: str
    then: Any = None
    now: Any = None


class KTypeChoiceState(BaseModel):
    """The car's current choice (the chain head), and whether it still fits."""

    status: ChoiceStatus
    choice_id: UUID
    ktype: str | None
    reviewer: str
    reason: str | None
    created_at: datetime
    #: The catalog batch and the matcher's outcome when the choice was made.
    catalog_batch: str
    automatic_terminal: str
    automatic_ktype: str | None
    #: The chosen KType's candidate entry exactly as it was shown and stored.
    chosen_candidate: dict[str, Any] | None = None
    #: True when any stale reason applies. Nothing is changed by the check.
    needs_review: bool = False
    stale_reasons: list[StaleReason] = Field(default_factory=list)
    changed_inputs: list[ChangedInput] = Field(default_factory=list)
    #: Rows in this car's chain, the current one included.
    history_count: int = 1


class KTypeChoiceHistoryEntry(BaseModel):
    choice_id: UUID
    action: ChoiceAction
    ktype: str | None
    reviewer: str
    reason: str | None
    created_at: datetime
    supersedes_choice_id: UUID | None
    catalog_batch: str
    automatic_terminal: str
    automatic_ktype: str | None
    code_version: str
    #: The stored snapshot; present only when asked for with `evidence=true`.
    evidence: dict[str, Any] | None = None


class KTypeChoiceHistory(BaseModel):
    vehicle_id: str
    current_choice_id: UUID | None
    #: From the current row backwards.
    entries: list[KTypeChoiceHistoryEntry]
