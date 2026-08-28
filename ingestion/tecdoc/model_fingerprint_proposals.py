"""Privacy-safe proposals for repeated TS rows whose model value is missing."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

_SEPARATORS = re.compile(r"[^A-Z0-9]+")


def _key(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").upper())
    return _SEPARATORS.sub(" ", "".join(c for c in text if not unicodedata.combining(c))).strip()


@dataclass(frozen=True)
class ModelFingerprintObservation:
    manufacturer: str
    type_text: str
    type_approval: str
    variant: str
    version: str
    production_year: int | None
    fuel: str
    displacement_cc: int | None
    power_kw: int | None
    model: str | None

    def evidence_key(self) -> tuple[object, ...]:
        return (
            _key(self.manufacturer),
            _key(self.type_text),
            _key(self.type_approval),
            _key(self.variant),
            _key(self.version),
            self.production_year,
            _key(self.fuel),
            self.displacement_cc,
            self.power_kw,
        )

    def fingerprint_id(self) -> str:
        encoded = json.dumps(self.evidence_key(), ensure_ascii=True, separators=(",", ":")).encode()
        return f"ts-model-fingerprint:{hashlib.sha256(encoded).hexdigest()[:20]}"


@dataclass(frozen=True)
class ModelFingerprintProposal:
    fingerprint_id: str
    manufacturer: str
    proposed_model: str
    missing_count: int
    anchor_count: int


MODEL_FINGERPRINT_PROFILES = (
    "full",
    "approval_variant",
    "type_variant_technical",
    "variant_technical",
)


def project_model_fingerprint(
    observation: ModelFingerprintObservation,
    *,
    profile: str,
) -> ModelFingerprintObservation:
    """Project evidence into a named conservative fingerprint profile."""

    if profile not in MODEL_FINGERPRINT_PROFILES:
        raise ValueError(f"unknown model fingerprint profile: {profile}")
    if profile == "full":
        return observation
    if profile == "approval_variant":
        return replace(observation, type_text="")
    if profile == "type_variant_technical":
        return replace(observation, type_approval="")
    return replace(observation, type_text="", type_approval="")


def propose_model_fingerprints(
    observations: tuple[ModelFingerprintObservation, ...],
    *,
    allowed_models_by_manufacturer: Mapping[str, Iterable[str]],
    minimum_anchor_count: int = 2,
) -> tuple[ModelFingerprintProposal, ...]:
    """Propose only uniquely anchored fingerprints; never expose source identifiers."""

    if minimum_anchor_count < 1:
        raise ValueError("minimum_anchor_count must be positive")
    allowed = {
        _key(manufacturer): {_key(model).replace(" ", "") for model in models if _key(model)}
        for manufacturer, models in allowed_models_by_manufacturer.items()
    }
    grouped: dict[tuple[object, ...], Counter[str]] = defaultdict(Counter)
    display_manufacturers: dict[tuple[object, ...], str] = {}
    for observation in observations:
        key = observation.evidence_key()
        if not key[0] or not any(key[1:5]):
            continue
        display_manufacturers.setdefault(key, observation.manufacturer.strip())
        model = _key(observation.model).replace(" ", "")
        grouped[key][model] += 1

    proposals: list[ModelFingerprintProposal] = []
    for key, counts in grouped.items():
        missing_count = counts.get("", 0)
        anchored = [(model, count) for model, count in counts.items() if model]
        if missing_count < 1 or len(anchored) != 1:
            continue
        proposed_model, anchor_count = anchored[0]
        if anchor_count < minimum_anchor_count:
            continue
        if proposed_model not in allowed.get(str(key[0]), set()):
            continue
        proposals.append(
            ModelFingerprintProposal(
                fingerprint_id=ModelFingerprintObservation(
                    manufacturer=str(key[0]),
                    type_text=str(key[1]),
                    type_approval=str(key[2]),
                    variant=str(key[3]),
                    version=str(key[4]),
                    production_year=key[5] if isinstance(key[5], int) else None,
                    fuel=str(key[6]),
                    displacement_cc=key[7] if isinstance(key[7], int) else None,
                    power_kw=key[8] if isinstance(key[8], int) else None,
                    model=None,
                ).fingerprint_id(),
                manufacturer=display_manufacturers[key],
                proposed_model=proposed_model,
                missing_count=missing_count,
                anchor_count=anchor_count,
            )
        )
    return tuple(
        sorted(
            proposals,
            key=lambda item: (-item.missing_count, -item.anchor_count, item.fingerprint_id),
        )
    )
