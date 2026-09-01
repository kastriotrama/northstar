"""Chunk adjudication boundary.

An adjudicator sees the assembled evidence bundle for one chunk — technical
signature, reason profile, OEM sample payloads and (when available) TecDoc
candidate summaries — and returns a structured proposal. Increment 1 ships a
deterministic heuristic; the LLM agent adapter implements the same protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

MIN_CONCORDANT_SAMPLES = 2

# Source fields that carry model identity. If these disagree across a chunk's
# members, the signature grouped materially different vehicles and no single
# decision is safe — detectable without paying the OEM provider.
IDENTITY_FIELDS = frozenset({"brand", "model", "model_no", "variant", "type_text"})

_COMPARABLE_FIELDS: tuple[tuple[str, str], ...] = (
    ("manufacturer", "manufacturer"),
    ("model_family", "model"),
    ("production_year", "model_year"),
)


@dataclass(frozen=True)
class AdjudicationProposal:
    recommendation: str
    confidence: float
    reasoning: str
    evidence: dict[str, Any] = field(default_factory=dict)
    target_ktype_reference: str | None = None


class MatchAdjudicator(Protocol):
    @property
    def version(self) -> str: ...

    def adjudicate(
        self,
        *,
        signature: dict[str, Any],
        reason_profile: dict[str, int],
        member_count: int,
        oem_samples: list[dict[str, Any]],
        tecdoc_candidates: list[dict[str, Any]],
        varying_fields: list[str],
    ) -> AdjudicationProposal: ...


def _comparable(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


class HeuristicAdjudicator:
    """Deterministic evidence-sufficiency and concordance rules."""

    @property
    def version(self) -> str:
        return "heuristic-1"

    def adjudicate(
        self,
        *,
        signature: dict[str, Any],
        reason_profile: dict[str, int],
        member_count: int,
        oem_samples: list[dict[str, Any]],
        tecdoc_candidates: list[dict[str, Any]],
        varying_fields: list[str],
    ) -> AdjudicationProposal:
        evidence: dict[str, Any] = {
            "member_count": member_count,
            "oem_sample_count": len(oem_samples),
            "tecdoc_candidate_count": len(tecdoc_candidates),
            "reason_profile": reason_profile,
            "varying_fields": varying_fields,
        }
        identity_spread = sorted(IDENTITY_FIELDS.intersection(varying_fields))
        if identity_spread:
            return AdjudicationProposal(
                recommendation="split_chunk",
                confidence=0.95,
                reasoning=(
                    "Members disagree on identity evidence ("
                    + ", ".join(identity_spread)
                    + f") across {member_count} rows, so one decision cannot "
                    "cover them. Split before spending any OEM lookup."
                ),
                evidence=evidence,
            )
        if len(oem_samples) < MIN_CONCORDANT_SAMPLES:
            missing = MIN_CONCORDANT_SAMPLES - len(oem_samples)
            return AdjudicationProposal(
                recommendation="needs_more_evidence",
                confidence=1.0,
                reasoning=(
                    f"Only {len(oem_samples)} OEM sample(s) recorded; fetch "
                    f"{missing} more before a chunk-wide decision is safe."
                ),
                evidence=evidence,
            )
        conflicts = self._signature_conflicts(signature, oem_samples)
        if conflicts:
            evidence["conflicts"] = conflicts
            return AdjudicationProposal(
                recommendation="split_chunk",
                confidence=0.9,
                reasoning=(
                    "OEM samples disagree with the chunk signature or with "
                    "each other on: " + ", ".join(sorted(conflicts)) + ". The "
                    "signature groups heterogeneous vehicles and must be split."
                ),
                evidence=evidence,
            )
        if len(tecdoc_candidates) == 1:
            candidate = tecdoc_candidates[0]
            reference = str(candidate.get("reference") or "").strip()
            if reference:
                evidence["selected_candidate"] = candidate
                return AdjudicationProposal(
                    recommendation="assign_ktype",
                    confidence=0.8,
                    reasoning=(
                        f"{len(oem_samples)} concordant OEM samples confirm the "
                        "signature and exactly one TecDoc candidate remains."
                    ),
                    evidence=evidence,
                    target_ktype_reference=reference,
                )
        if len(tecdoc_candidates) > 1:
            return AdjudicationProposal(
                recommendation="needs_more_evidence",
                confidence=0.7,
                reasoning=(
                    f"OEM samples are concordant but {len(tecdoc_candidates)} "
                    "TecDoc candidates remain; more discriminating evidence "
                    "is required."
                ),
                evidence=evidence,
            )
        return AdjudicationProposal(
            recommendation="no_safe_match",
            confidence=0.6,
            reasoning=(
                "OEM samples are concordant but no TecDoc candidate was "
                "provided; candidate generation is required before assignment."
            ),
            evidence=evidence,
        )

    def _signature_conflicts(
        self, signature: dict[str, Any], oem_samples: list[dict[str, Any]]
    ) -> list[str]:
        conflicts: set[str] = set()
        for signature_field, oem_field in _COMPARABLE_FIELDS:
            expected = _comparable(signature.get(signature_field))
            observed = {
                value
                for sample in oem_samples
                if (value := _comparable(sample.get(oem_field))) is not None
            }
            if not observed:
                continue
            samples_disagree = len(observed) > 1
            contradicts_signature = expected is not None and expected not in observed
            if samples_disagree or contradicts_signature:
                conflicts.add(signature_field)
        return sorted(conflicts)
