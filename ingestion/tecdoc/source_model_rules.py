"""Explicitly reviewed source-context → catalog-family queries, never KType decisions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from ingestion.fuzzy_matching import VehicleCandidate

_SOURCE_FIELDS = frozenset({"eeg_type_approval", "type_text", "variant", "version", "body_code"})


def _key(value: str) -> str:
    return " ".join(value.upper().split())


@dataclass(frozen=True)
class ReviewedSourceModelRule:
    rule_id: str
    manufacturer: str
    source_model: str
    target_model: str
    source_conditions: tuple[tuple[str, str], ...]
    reviewed_by: str
    evidence_ref: str

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (
            self.rule_id, self.manufacturer, self.source_model, self.target_model,
            self.reviewed_by, self.evidence_ref,
        )):
            raise ValueError("source-model rules require exact scope and review evidence")
        conditions = dict(self.source_conditions)
        if len(conditions) != len(self.source_conditions) or any(
            key not in _SOURCE_FIELDS or not value.strip() for key, value in self.source_conditions
        ):
            raise ValueError("invalid or duplicate source-model conditions")
        if "eeg_type_approval" not in conditions or not (
            "type_text" in conditions or {"variant", "version"} <= conditions.keys()
        ):
            raise ValueError("bodywork alone cannot recover a family: approval and type/variant evidence required")


@dataclass(frozen=True)
class SourceModelResolution:
    target_model: str | None = None
    rule_ids: tuple[str, ...] = ()
    conflict: bool = False


@dataclass(frozen=True)
class ReviewedSourceModelPolicy:
    version: str = "source-model-policy-v1-disabled"
    rules: tuple[ReviewedSourceModelRule, ...] = ()

    def __post_init__(self) -> None:
        if not self.version.strip() or len({rule.rule_id for rule in self.rules}) != len(self.rules):
            raise ValueError("source-model policy requires version and unique rules")

    @property
    def content_digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    def validate_catalog(self, catalog: Sequence[VehicleCandidate]) -> None:
        families = {(_key(candidate.manufacturer), _key(candidate.model)) for candidate in catalog}
        if any((_key(rule.manufacturer), _key(rule.target_model)) not in families for rule in self.rules):
            raise ValueError("source-model target must be an exact canonical family in the pinned manufacturer catalog")

    def resolve(self, *, manufacturer: str, source_model: str,
                source_evidence: Mapping[str, Any]) -> SourceModelResolution:
        applicable = tuple(rule for rule in self.rules if (
            _key(rule.manufacturer) == _key(manufacturer)
            and _key(rule.source_model) == _key(source_model)
            # These are exact values, not patterns; approval extensions are not stripped.
            and all(source_evidence.get(key) == value for key, value in rule.source_conditions)
        ))
        targets = {_key(rule.target_model) for rule in applicable}
        ids = tuple(sorted(rule.rule_id for rule in applicable))
        if len(targets) > 1:
            return SourceModelResolution(rule_ids=ids, conflict=True)
        return SourceModelResolution(applicable[0].target_model, ids) if applicable else SourceModelResolution()


def reviewed_source_model_policy(
    payload: Mapping[str, Any], *, expected_version: str, expected_digest: str,
) -> ReviewedSourceModelPolicy:
    actual = hashlib.sha256(json.dumps(dict(payload), sort_keys=True).encode()).hexdigest()
    if actual != expected_digest:
        raise ValueError("source-model manifest checksum mismatch")
    if payload.get("version") != expected_version or payload.get("status") != "approved":
        raise ValueError("source-model manifest is unknown or unapproved")
    if not isinstance(payload.get("rules"), list):
        raise TypeError("source-model manifest requires rules")
    rules = []
    for row in payload["rules"]:
        fields = ("rule_id", "manufacturer", "source_model", "target_model", "reviewed_by", "evidence_ref")
        if not isinstance(row, dict) or row.get("status") != "approved" or any(
            not isinstance(row.get(field), str) for field in fields
        ):
            raise ValueError("source-model rule is unapproved or malformed")
        conditions = row.get("source_conditions")
        if not isinstance(conditions, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in conditions.items()
        ):
            raise ValueError("source-model conditions require exact strings")
        rules.append(ReviewedSourceModelRule(**{field: row[field] for field in fields},
                                            source_conditions=tuple(sorted(conditions.items()))))
    return ReviewedSourceModelPolicy(expected_version, tuple(rules))
