"""Catalog-gated engine-code proposals from repeated TS approval evidence."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

_SEPARATORS = re.compile(r"[^A-Z0-9]+")


def _key(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").upper())
    return _SEPARATORS.sub("", "".join(c for c in text if not unicodedata.combining(c)))


@dataclass(frozen=True)
class EngineFingerprintObservation:
    manufacturer: str
    type_approval: str
    variant: str
    version: str
    engine_code: str | None
    unresolved: bool = False

    def evidence_key(self) -> tuple[str, str, str, str]:
        return (
            _key(self.manufacturer),
            _key(self.type_approval),
            _key(self.variant),
            _key(self.version),
        )

    def fingerprint_id(self) -> str:
        encoded = json.dumps(self.evidence_key(), separators=(",", ":")).encode()
        return f"ts-engine-fingerprint:{hashlib.sha256(encoded).hexdigest()[:20]}"


@dataclass(frozen=True)
class EngineFingerprintProposal:
    fingerprint_id: str
    manufacturer: str
    engine_code: str
    anchor_count: int
    unresolved_count: int


def propose_engine_fingerprints(
    observations: tuple[EngineFingerprintObservation, ...],
    *,
    allowed_engines_by_manufacturer: Mapping[str, Iterable[str]],
    minimum_anchor_count: int = 2,
) -> tuple[EngineFingerprintProposal, ...]:
    """Propose only repeated fingerprints with one catalog-valid engine code."""

    if minimum_anchor_count < 2:
        raise ValueError("minimum_anchor_count must be at least two")
    allowed = {
        _key(manufacturer): {_key(engine) for engine in engines if _key(engine)}
        for manufacturer, engines in allowed_engines_by_manufacturer.items()
    }
    anchors: dict[tuple[str, str, str, str], Counter[str]] = defaultdict(Counter)
    unresolved: Counter[tuple[str, str, str, str]] = Counter()
    display: dict[tuple[str, str, str, str], tuple[str, str]] = {}
    for observation in observations:
        key = observation.evidence_key()
        # Approval alone is accepted; otherwise variant and version must both exist.
        if not key[0] or (not key[1] and not (key[2] and key[3])):
            continue
        if observation.engine_code:
            engine = _key(observation.engine_code)
            anchors[key][engine] += 1
            display.setdefault(key, (observation.manufacturer.strip(), observation.engine_code.strip()))
        if observation.unresolved:
            unresolved[key] += 1

    proposals: list[EngineFingerprintProposal] = []
    for key, unresolved_count in unresolved.items():
        engine_counts = anchors.get(key, Counter())
        if len(engine_counts) != 1:
            continue
        engine_key, anchor_count = next(iter(engine_counts.items()))
        if anchor_count < minimum_anchor_count or engine_key not in allowed.get(key[0], set()):
            continue
        manufacturer, engine_code = display[key]
        fingerprint = EngineFingerprintObservation(
            manufacturer, key[1], key[2], key[3], None
        ).fingerprint_id()
        proposals.append(
            EngineFingerprintProposal(
                fingerprint,
                manufacturer,
                engine_code,
                anchor_count,
                unresolved_count,
            )
        )
    return tuple(
        sorted(
            proposals,
            key=lambda item: (-item.unresolved_count, -item.anchor_count, item.fingerprint_id),
        )
    )


def accepted_non_degrading_fingerprints(
    outcomes_by_fingerprint: Mapping[str, Iterable[str]],
) -> tuple[str, ...]:
    """Accept only fingerprints that resolve a row and never create a hard failure."""

    accepted: list[str] = []
    for fingerprint_id, outcomes in outcomes_by_fingerprint.items():
        terminals = tuple(outcomes)
        if not terminals:
            continue
        if "resolved" not in terminals:
            continue
        if any(terminal in {"hard_conflict", "failed"} for terminal in terminals):
            continue
        accepted.append(fingerprint_id)
    return tuple(sorted(accepted))


@dataclass(frozen=True)
class ReviewedEngineFingerprintRule:
    fingerprint_id: str
    profile: str
    manufacturer: str
    engine_code: str


class ReviewedEngineFingerprintIndex:
    """Resolve only explicitly reviewed, manufacturer-scoped fingerprints."""

    def __init__(self, rules: Iterable[ReviewedEngineFingerprintRule] = ()) -> None:
        self._rules = {(rule.profile, rule.fingerprint_id): rule for rule in rules}

    @classmethod
    def from_overrides(cls, overrides: Mapping[str, Any]) -> ReviewedEngineFingerprintIndex:
        rules = []
        for definition in overrides.values():
            if not isinstance(definition, Mapping) or definition.get("kind") != "engine_fingerprint_rule":
                continue
            rules.append(ReviewedEngineFingerprintRule(
                fingerprint_id=str(definition["fingerprint_id"]),
                profile=str(definition["profile"]),
                manufacturer=str(definition["manufacturer"]),
                engine_code=str(definition["engine_code"]),
            ))
        return cls(rules)

    def resolve(self, *, manufacturer: str, type_approval: object,
                variant: object, version: object) -> str | None:
        observations = {
            "approval_only": EngineFingerprintObservation(
                manufacturer, str(type_approval or ""), "", "", None
            ),
            "variant_version": EngineFingerprintObservation(
                manufacturer, "", str(variant or ""), str(version or ""), None
            ),
        }
        matches = {
            rule.engine_code
            for profile, observation in observations.items()
            if (rule := self._rules.get((profile, observation.fingerprint_id()))) is not None
            and _key(rule.manufacturer) == _key(manufacturer)
        }
        return next(iter(matches)) if len(matches) == 1 else None
