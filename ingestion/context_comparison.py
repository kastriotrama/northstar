"""Source-scoped comparison evidence; compatibility never asserts equivalence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ComparisonState = Literal["equivalent", "compatible", "unknown", "conflicting"]
ContextField = Literal["bodywork", "drive_type"]
CONTEXT_COMPARISON_VERSION = "context-comparison-v1"
SOURCE_CONTEXT_FIELDS = frozenset({"body_code", "is_4wd", "eeg_type_approval", "variant", "version"})


def _key(value: str) -> str:
    return " ".join(value.upper().split())


@dataclass(frozen=True)
class ContextComparison:
    state: ComparisonState
    rule_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewedContextRule:
    rule_id: str
    field: ContextField
    manufacturer: str
    model: str
    source_value: str
    allowed_values: tuple[str, ...]
    source_conditions: tuple[tuple[str, str], ...]
    reviewed_by: str
    evidence_ref: str

    def __post_init__(self) -> None:
        if self.field not in {"bodywork", "drive_type"}:
            raise ValueError("unsupported context field")
        if not all(value.strip() for value in (
            self.rule_id, self.manufacturer, self.model, self.reviewed_by, self.evidence_ref,
        )):
            raise ValueError("reviewed context rules require scope and approval evidence")
        if not self.allowed_values or any(not value.strip() for value in self.allowed_values):
            raise ValueError("allowed values must not be empty")
        if not self.source_conditions or any(
            key not in SOURCE_CONTEXT_FIELDS or not value.strip()
            for key, value in self.source_conditions
        ):
            raise ValueError("reviewed compatibility requires explicit source conditions")
        if len(dict(self.source_conditions)) != len(self.source_conditions):
            raise ValueError("duplicate source condition")


@dataclass(frozen=True)
class ContextComparisonPolicy:
    version: str = CONTEXT_COMPARISON_VERSION
    rules: tuple[ReviewedContextRule, ...] = ()
    _rules_by_scope: Mapping[
        tuple[ContextField, str, str, str], tuple[ReviewedContextRule, ...]
    ] = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("context policy must be versioned")
        if len({rule.rule_id for rule in self.rules}) != len(self.rules):
            raise ValueError("duplicate context rule ID")
        indexed: dict[
            tuple[ContextField, str, str, str], list[ReviewedContextRule]
        ] = {}
        for rule in self.rules:
            scope = (
                rule.field,
                _key(rule.source_value),
                _key(rule.manufacturer),
                _key(rule.model),
            )
            indexed.setdefault(scope, []).append(rule)
        object.__setattr__(
            self,
            "_rules_by_scope",
            {scope: tuple(rows) for scope, rows in indexed.items()},
        )

    @property
    def content_digest(self) -> str:
        payload = {
            "version": self.version,
            "rules": [asdict(rule) for rule in self.rules],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def compare(
        self, *, field: ContextField, source_value: str | None,
        candidate_values: frozenset[str], manufacturer: str, model: str,
        source_evidence: tuple[tuple[str, str], ...] = (),
    ) -> ContextComparison:
        source = _key(source_value or "")
        target = frozenset(_key(value) for value in candidate_values if value.strip())
        # An exact source assertion is not overridden by a broader rule.
        if source and source in target:
            return ContextComparison("equivalent")
        evidence = dict(source_evidence)
        scoped = self._rules_by_scope.get(
            (field, source, _key(manufacturer), _key(model)), ()
        )
        applicable = tuple(
            rule
            for rule in scoped
            if all(
                evidence.get(key) == value for key, value in rule.source_conditions
            )
        )
        if applicable:
            allowed = {frozenset(_key(value) for value in rule.allowed_values) for rule in applicable}
            if len(allowed) != 1:
                # Conflicting reviewed rules never silently union their scope.
                return ContextComparison("conflicting", tuple(sorted(r.rule_id for r in applicable)))
            state: ComparisonState = (
                "unknown" if not target else
                "compatible" if next(iter(allowed)) & target else "conflicting"
            )
            return ContextComparison(state, tuple(sorted(rule.rule_id for rule in applicable)))
        return ContextComparison("conflicting" if source and target else "unknown")


def reviewed_context_policy(
    payload: Mapping[str, Any], *, expected_version: str, expected_digest: str,
) -> ContextComparisonPolicy:
    """Load an explicitly pinned reviewed manifest; no discovery or auto-activation."""
    actual_digest = hashlib.sha256(json.dumps(dict(payload), sort_keys=True).encode()).hexdigest()
    if actual_digest != expected_digest:
        raise ValueError("context manifest checksum mismatch")
    if payload.get("version") != expected_version or payload.get("status") != "approved":
        raise ValueError("context version is unknown or unapproved")
    rules = payload.get("rules")
    if not isinstance(rules, list):
        raise TypeError("context manifest requires rules")
    parsed = []
    for row in rules:
        if not isinstance(row, dict) or row.get("status") != "approved":
            raise ValueError("context manifest contains unapproved rules")
        fields = ("rule_id", "field", "manufacturer", "model", "source_value", "reviewed_by", "evidence_ref")
        if any(not isinstance(row.get(key), str) for key in fields):
            raise ValueError("context rule text fields must be strings")
        allowed = row.get("allowed_values")
        conditions = row.get("source_conditions")
        if not isinstance(allowed, list) or any(not isinstance(value, str) for value in allowed):
            raise ValueError("context allowed values must be a string list")
        if not isinstance(conditions, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in conditions.items()
        ):
            raise ValueError("context source conditions must be strings")
        parsed.append(ReviewedContextRule(
            **{key: row[key] for key in fields}, allowed_values=tuple(allowed),
            source_conditions=tuple(sorted(conditions.items())),
        ))
    return ContextComparisonPolicy(expected_version, tuple(parsed))
