"""Privacy-safe cohort diagnostics for TS-to-TecDoc matcher improvements."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ingestion.fuzzy_matching import VehicleCandidate

MODEL_SOURCE_FIELDS = ("brand", "model", "variant", "version", "model_no", "type_text", "eeg_type_approval")


class RepairCohortDiagnostics:
    """Rank retained-run repair groups; no rule approval or candidate inference."""

    def __init__(self) -> None:
        self._groups: dict[str, dict[str, Any]] = {}
        self._missing_profiles: Counter[str] = Counter()
        self._candidate_only: Counter[str] = Counter()
        self._model_states: Counter[str] = Counter()
        self._body_terminals: Counter[str] = Counter()

    def add(
        self, *, raw: dict[str, Any], normalized: dict[str, Any], terminal: str,
        reasons: tuple[str, ...], candidate: VehicleCandidate | None, row_key: str,
    ) -> None:
        missing = "model_evidence_missing" in reasons
        body = "context_conflict:bodywork" in reasons
        if "candidate_only_not_graph_safe" in reasons and candidate:
            self._candidate_only[candidate.candidate_reference] += 1
        if missing:
            populated = tuple(key for key in MODEL_SOURCE_FIELDS if str(raw.get(key) or "").strip())
            self._missing_profiles["+".join(populated) or "none"] += 1
            self._model_states[
                "has_identity_fields" if any(key != "brand" for key in populated) else "brand_only"
            ] += 1
        if body:
            self._body_terminals[terminal] += 1
        if not missing and not body:
            return
        evidence = {
            "kind": "model_missing" if missing else "bodywork_conflict",
            "manufacturer": normalized.get("manufacturer"),
            "normalized_model": normalized.get("model_family"),
            "raw_brand": raw.get("brand"), "raw_model": raw.get("model"),
            "ts_body_code": raw.get("body_code") if body else None,
            "ts_bodywork": normalized.get("bodywork_form") if body else None,
            "candidate_model": candidate.model if candidate and body else None,
            "candidate_bodyworks": sorted(candidate.bodyworks) if candidate and body else [],
        }
        key = hashlib.sha256(json.dumps(evidence, sort_keys=True, default=str).encode()).hexdigest()
        group = self._groups.setdefault(key, {
            "group_key": key, "evidence": evidence, "count": 0,
            "terminal_counts": Counter(), "reason_counts": Counter(),
            "top_candidate_counts": Counter(), "examples": [],
            "review_status": "pending_review",
        })
        group["count"] += 1
        group["terminal_counts"][terminal] += 1
        group["reason_counts"].update(reasons)
        if candidate:
            group["top_candidate_counts"][candidate.candidate_reference] += 1
        if len(group["examples"]) < 3:
            group["examples"].append({
                "row_key": row_key,
                "source_identity_evidence": {key: raw.get(key) for key in MODEL_SOURCE_FIELDS},
                "normalized_technical_evidence": {key: normalized.get(key) for key in (
                    "production_year", "energy_sources", "fuel_match_tokens", "engine_code",
                    "displacement_cc", "power_kw", "drive_type", "bodywork_form",
                )},
                "top_candidate": ({
                    "reference": candidate.candidate_reference,
                    "manufacturer": candidate.manufacturer, "model": candidate.model,
                    "year_from": candidate.year_from, "year_to": candidate.year_to,
                    "fuels": sorted(candidate.fuels), "engine_codes": sorted(candidate.engine_codes),
                    "displacement_cc": candidate.displacement_cc, "power_kw": candidate.power_kw,
                    "drive_type": candidate.drive_type, "bodyworks": sorted(candidate.bodyworks),
                } if candidate else None),
            })

    def report(self) -> dict[str, Any]:
        return {
            "groups": sorted(self._groups.values(), key=lambda item: (-item["count"], item["group_key"])),
            "missing_model_source_profiles": dict(self._missing_profiles),
            "missing_model_evidence_states": dict(self._model_states),
            "bodywork_conflict_terminals": dict(self._body_terminals),
            "candidate_only_ktype_counts": dict(self._candidate_only),
            "candidate_only_distinct_ktypes": len(self._candidate_only),
            "limitations": [
                "Top candidates are hypotheses, not independent ground truth.",
                "Alternative-candidate replay and counterfactual validation are not represented by aggregate reasons.",
                "Free-text source evidence remains private even though plate/VIN fields are excluded.",
                "No proposed semantic rule has been activated by these diagnostics.",
            ],
        }


@dataclass(frozen=True)
class UnresolvedMatchObservation:
    manufacturer: str | None
    normalized_model: str | None
    raw_model: str | None
    production_year: int | None
    fuels: tuple[str, ...] = ()
    displacement_cc: int | None = None
    power_kw: int | None = None
    reason_codes: tuple[str, ...] = ()
    top_candidate_reference: str | None = None

    def cohort_evidence(self) -> dict[str, Any]:
        """Return only matching evidence; source identity and PII are excluded."""

        return {
            "manufacturer": self.manufacturer,
            "normalized_model": self.normalized_model,
            "raw_model": self.raw_model,
            "production_year": self.production_year,
            "fuels": sorted(set(self.fuels)),
            "displacement_cc": self.displacement_cc,
            "power_kw": self.power_kw,
        }

    def cohort_key(self) -> str:
        encoded = json.dumps(
            self.cohort_evidence(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass
class _MutableCohort:
    evidence: dict[str, Any]
    count: int = 0
    reasons: Counter[str] = field(default_factory=Counter)
    top_candidates: Counter[str] = field(default_factory=Counter)


class UnresolvedCohortDiagnostics:
    """Aggregate unresolved observations without retaining vehicle identifiers."""

    def __init__(self) -> None:
        self._cohorts: dict[str, _MutableCohort] = {}

    def add(self, observation: UnresolvedMatchObservation) -> None:
        key = observation.cohort_key()
        cohort = self._cohorts.setdefault(
            key, _MutableCohort(evidence=observation.cohort_evidence())
        )
        cohort.count += 1
        cohort.reasons.update(observation.reason_codes or ("unspecified",))
        if observation.top_candidate_reference:
            cohort.top_candidates[observation.top_candidate_reference] += 1

    def report(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        ranked = sorted(
            self._cohorts.items(),
            key=lambda item: (-item[1].count, item[0]),
        )[:limit]
        return tuple(
            {
                "cohort_key": key,
                "count": cohort.count,
                "evidence": cohort.evidence,
                "reason_counts": dict(sorted(cohort.reasons.items())),
                "top_candidate_counts": dict(
                    sorted(
                        cohort.top_candidates.items(),
                        key=lambda item: (-item[1], item[0]),
                    )[:5]
                ),
            }
            for key, cohort in ranked
        )


@dataclass(frozen=True)
class BodyworkConflictObservation:
    """One privacy-safe TS/TecDoc bodywork disagreement."""

    manufacturer: str
    model: str
    ts_bodywork_code: str
    ts_bodywork: str
    tecdoc_bodyworks: tuple[str, ...]


class BodyworkConflictDiagnostics:
    """Rank repeated bodywork disagreements without retaining vehicle identity."""

    def __init__(self) -> None:
        self._counts: Counter[tuple[str, str, str, str, tuple[str, ...]]] = Counter()

    def add(self, observation: BodyworkConflictObservation) -> None:
        def clean(value: str) -> str:
            return " ".join(value.strip().lower().split())

        key = (
            clean(observation.manufacturer),
            clean(observation.model),
            clean(observation.ts_bodywork_code),
            clean(observation.ts_bodywork),
            tuple(sorted({clean(value) for value in observation.tecdoc_bodyworks if clean(value)})),
        )
        if not all(key[:4]) or not key[4]:
            raise ValueError("bodywork conflict evidence must not be blank")
        self._counts[key] += 1

    def report(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        ranked = sorted(self._counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
        return tuple(
            {
                "manufacturer": key[0],
                "model": key[1],
                "ts_bodywork_code": key[2].upper(),
                "ts_bodywork": key[3],
                "tecdoc_bodyworks": list(key[4]),
                "count": count,
            }
            for key, count in ranked
        )

    def compatibility_proposals(
        self,
        *,
        minimum_occurrences: int = 25,
        minimum_models: int = 3,
    ) -> tuple[dict[str, Any], ...]:
        """Propose repeated cross-model pairs for review, never activation."""

        if minimum_occurrences < 1 or minimum_models < 1:
            raise ValueError("bodywork proposal thresholds must be positive")
        occurrences: Counter[tuple[str, str, tuple[str, ...]]] = Counter()
        models: dict[tuple[str, str, tuple[str, ...]], set[tuple[str, str]]] = {}
        for key, count in self._counts.items():
            manufacturer, model, code, ts_bodywork, tecdoc_bodyworks = key
            proposal_key = (code, ts_bodywork, tecdoc_bodyworks)
            occurrences[proposal_key] += count
            models.setdefault(proposal_key, set()).add((manufacturer, model))
        accepted = (
            (key, count, len(models[key]))
            for key, count in occurrences.items()
            if count >= minimum_occurrences and len(models[key]) >= minimum_models
        )
        return tuple(
            {
                "ts_bodywork_code": key[0].upper(),
                "ts_bodywork": key[1],
                "tecdoc_bodyworks": list(key[2]),
                "occurrence_count": count,
                "distinct_manufacturer_model_count": model_count,
                "status": "pending_review",
            }
            for key, count, model_count in sorted(
                accepted, key=lambda item: (-item[1], -item[2], item[0])
            )
        )


@dataclass(frozen=True)
class CandidateCatalogCoverage:
    active_ktype_count: int
    candidate_ktype_count: int
    promoted_ktype_count: int

    def __post_init__(self) -> None:
        values = (
            self.active_ktype_count,
            self.candidate_ktype_count,
            self.promoted_ktype_count,
        )
        if any(value < 0 for value in values):
            raise ValueError("KType coverage counts must not be negative")
        if self.candidate_ktype_count > self.active_ktype_count:
            raise ValueError("candidate KTypes cannot exceed active KTypes")
        if self.promoted_ktype_count > self.candidate_ktype_count:
            raise ValueError("promoted KTypes cannot exceed candidate KTypes")

    @property
    def missing_candidate_count(self) -> int:
        return self.active_ktype_count - self.candidate_ktype_count

    @property
    def candidate_coverage(self) -> float:
        if self.active_ktype_count == 0:
            return 1.0
        return round(self.candidate_ktype_count / self.active_ktype_count, 6)

    def require_complete_candidates(self) -> None:
        if self.missing_candidate_count:
            raise RuntimeError(
                "TecDoc candidate catalog incomplete: "
                f"active={self.active_ktype_count}, "
                f"candidate={self.candidate_ktype_count}, "
                f"missing={self.missing_candidate_count}"
            )


@dataclass(frozen=True)
class TechnicalSignature:
    """KType evidence excluding its identity reference and graph status."""

    manufacturer: str
    model: str
    year_from: int | None
    year_to: int | None
    fuels: tuple[str, ...]
    engine_codes: tuple[str, ...]
    displacement_cc: int | None
    power_kw: int | None
    drive_type: str | None
    bodyworks: tuple[str, ...]

    @classmethod
    def from_candidate(cls, candidate: VehicleCandidate) -> TechnicalSignature:
        def clean(value: str) -> str:
            return " ".join(value.upper().split())

        return cls(
            manufacturer=clean(candidate.manufacturer),
            model=clean(candidate.model),
            year_from=candidate.year_from,
            year_to=candidate.year_to,
            fuels=tuple(sorted(clean(value) for value in candidate.fuels)),
            engine_codes=tuple(sorted(clean(value) for value in candidate.engine_codes)),
            displacement_cc=candidate.displacement_cc,
            power_kw=candidate.power_kw,
            drive_type=clean(candidate.drive_type) if candidate.drive_type else None,
            bodyworks=tuple(sorted(clean(value) for value in candidate.bodyworks)),
        )


def equivalent_technical_candidate_groups(
    candidates: tuple[VehicleCandidate, ...],
) -> tuple[tuple[str, ...], ...]:
    """Return KType groups that evidence cannot distinguish from one another."""

    grouped: dict[TechnicalSignature, list[str]] = {}
    for candidate in candidates:
        grouped.setdefault(TechnicalSignature.from_candidate(candidate), []).append(
            candidate.candidate_reference
        )
    return tuple(
        sorted(
            (tuple(sorted(references)) for references in grouped.values() if len(references) > 1),
            key=lambda references: (-len(references), references),
        )
    )
