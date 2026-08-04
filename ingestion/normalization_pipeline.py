"""Deterministic transformer pipeline and redaction-safe decision trace contracts."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

TraceTarget = Literal["normalized", "candidate", "review"]
SENSITIVE_OUTPUT_FIELDS = frozenset(
    {
        "plate",
        "registration_number",
        "vin",
        "vehicle_identification_number",
    }
)


@dataclass(frozen=True)
class DecisionTraceEntry:
    """One explainable, sanitized decision made by a transformer."""

    sequence: int
    transformer_id: str
    target: TraceTarget
    field: str
    rule_ids: tuple[str, ...]
    before: Any
    after: Any
    confidence_effect: float

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("trace sequence must be positive")
        if not self.transformer_id.strip():
            raise ValueError("transformer_id must not be empty")
        if not self.field.strip():
            raise ValueError("trace field must not be empty")
        if not -1.0 <= self.confidence_effect <= 1.0:
            raise ValueError("confidence_effect must be between -1.0 and 1.0")
        if self.field.casefold() in SENSITIVE_OUTPUT_FIELDS:
            raise ValueError(f"sensitive field {self.field!r} cannot enter the decision trace")

    def to_payload(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "transformer_id": self.transformer_id,
            "target": self.target,
            "field": self.field,
            "rule_ids": list(self.rule_ids),
            "before": self.before,
            "after": self.after,
            "confidence_effect": self.confidence_effect,
        }


@dataclass
class NormalizationContext:
    """Mutable in-memory state shared by an ordered transformer sequence."""

    raw_record: dict[str, Any]
    normalized: dict[str, Any] = field(default_factory=dict)
    candidates: dict[str, Any] = field(default_factory=dict)
    applied_rule_ids: list[str] = field(default_factory=list)
    candidate_rule_ids: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)
    decision_trace: list[DecisionTraceEntry] = field(default_factory=list)

    def record_change(
        self,
        *,
        transformer_id: str,
        target: TraceTarget,
        field_name: str,
        rule_ids: tuple[str, ...],
        before: Any,
        after: Any,
        confidence_effect: float,
    ) -> None:
        self.decision_trace.append(
            DecisionTraceEntry(
                sequence=len(self.decision_trace) + 1,
                transformer_id=transformer_id,
                target=target,
                field=field_name,
                rule_ids=tuple(dict.fromkeys(rule_ids)),
                before=before,
                after=after,
                confidence_effect=confidence_effect,
            )
        )


class Transformer(Protocol):
    """A deterministic normalization stage."""

    transformer_id: str
    order: int

    def apply(self, context: NormalizationContext) -> None:
        """Apply one stage to the shared record context."""


class NormalizationPipeline:
    """Validate and execute transformers in a stable, explicit order."""

    def __init__(
        self,
        *,
        version: str,
        transformers: Sequence[Transformer],
    ) -> None:
        if not version.strip():
            raise ValueError("pipeline version must not be empty")
        ids = [transformer.transformer_id for transformer in transformers]
        orders = [transformer.order for transformer in transformers]
        if len(ids) != len(set(ids)):
            raise ValueError("transformer IDs must be unique")
        if len(orders) != len(set(orders)):
            raise ValueError("transformer orders must be unique")
        if any(not transformer_id.strip() for transformer_id in ids):
            raise ValueError("transformer IDs must not be empty")
        self.version = version
        self.transformers = tuple(
            sorted(transformers, key=lambda transformer: transformer.order)
        )

    def run(self, raw_record: dict[str, Any]) -> NormalizationContext:
        raw_snapshot = deepcopy(raw_record)
        context = NormalizationContext(raw_record=deepcopy(raw_record))
        for transformer in self.transformers:
            transformer.apply(context)
            if context.raw_record != raw_snapshot:
                raise RuntimeError(
                    f"transformer {transformer.transformer_id!r} mutated the raw record"
                )
        return context
