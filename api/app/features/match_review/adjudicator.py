"""Chunk adjudication boundary.

An adjudicator sees the assembled evidence bundle for one chunk — technical
signature, reason profile, OEM sample payloads and (when available) TecDoc
candidate summaries — and returns a structured proposal. Increment 1 ships a
deterministic heuristic; `LlmAdjudicator` implements the same protocol over a
model, and falls back to the heuristic whenever the model cannot be trusted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from api.app.features.match_review.integrations import JsonLlm

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


RECOMMENDATIONS = frozenset(
    {"assign_ktype", "split_chunk", "needs_more_evidence", "no_safe_match"}
)

ADJUDICATOR_INSTRUCTIONS = """\
You adjudicate one chunk of Swedish vehicle-register rows that NorthStar's
matcher grouped under a single technical signature. One decision is applied to
every member, so a wrong decision is wrong thousands of times over.

Evidence you are given: the shared `signature`, the `reason_profile` of why the
members failed to match, `varying_fields` (source fields that are NOT constant
across the chunk), paid `oem_samples` fetched per VIN, and `tecdoc_candidates`.

Rules:
1. Recommend exactly one of: assign_ktype, split_chunk, needs_more_evidence,
   no_safe_match.
2. `assign_ktype` requires at least two concordant OEM samples AND exactly the
   one TecDoc candidate they support; put that candidate's `reference` verbatim
   in `target_ktype_reference`. Never invent a reference.
3. If members disagree on identity (brand, model, model_no, variant,
   type_text), the signature grouped different vehicles: recommend
   split_chunk, and say which fields disagree.
4. If the OEM samples contradict the signature or each other, recommend
   split_chunk.
5. If evidence is thin, recommend needs_more_evidence and name the evidence
   that would settle it. This is a good answer; guessing is not.
6. Never assert a fact about a vehicle that the supplied evidence does not
   contain.

Reply with one JSON object: recommendation, target_ktype_reference (string or
null), confidence (0-1), reasoning (one short paragraph a reviewer can check).
"""


class LlmAdjudicator:
    """Optional LLM adapter over the same evidence bundle the heuristic sees.

    The proposal it returns is still only a proposal: it is written to the
    proposals table as `agent` and needs human approval before any chunk moves.
    Model output is untrusted, so the evidence floors that protect a chunk-wide
    decision are re-checked here rather than trusted to the prompt, and any
    transport, parsing or validation failure degrades to the deterministic
    adjudicator instead of failing the screen.
    """

    def __init__(
        self,
        *,
        llm: JsonLlm,
        fallback: MatchAdjudicator | None = None,
    ) -> None:
        self._llm = llm
        self._fallback = fallback or HeuristicAdjudicator()

    @property
    def version(self) -> str:
        return f"llm:{self._llm.model}"

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
        identity_spread = sorted(IDENTITY_FIELDS.intersection(varying_fields))
        allowed_references = {
            reference
            for candidate in tecdoc_candidates
            if (reference := str(candidate.get("reference") or "").strip())
        }
        prompt = {
            "signature": signature,
            "member_count": member_count,
            "reason_profile": reason_profile,
            "varying_fields": varying_fields,
            "identity_fields_that_disagree": identity_spread,
            "oem_samples": oem_samples,
            "tecdoc_candidates": tecdoc_candidates,
            "allowed_ktype_references": sorted(allowed_references),
        }
        evidence: dict[str, Any] = {
            "member_count": member_count,
            "oem_sample_count": len(oem_samples),
            "tecdoc_candidate_count": len(tecdoc_candidates),
            "reason_profile": reason_profile,
            "varying_fields": varying_fields,
            "source": self.version,
        }
        try:
            payload = self._llm.complete_json(
                instructions=ADJUDICATOR_INSTRUCTIONS, payload=prompt
            )
            recommendation = str(payload.get("recommendation", "")).strip()
            if recommendation not in RECOMMENDATIONS:
                raise ValueError(f"unknown recommendation {recommendation!r}")
            reasoning = str(payload.get("reasoning", "")).strip()
            if not reasoning:
                raise ValueError("model returned no reasoning")
            reference = str(payload.get("target_ktype_reference") or "").strip()
            if recommendation == "assign_ktype":
                # The evidence floors are the point of this screen; a model
                # cannot talk its way past them.
                if identity_spread:
                    raise ValueError(
                        "assignment proposed while identity fields disagree: "
                        + ", ".join(identity_spread)
                    )
                if len(oem_samples) < MIN_CONCORDANT_SAMPLES:
                    raise ValueError(
                        f"assignment proposed on {len(oem_samples)} OEM sample(s)"
                    )
                if reference not in allowed_references:
                    raise ValueError(f"unknown ktype reference {reference!r}")
            else:
                reference = ""
            return AdjudicationProposal(
                recommendation=recommendation,
                confidence=_confidence(payload.get("confidence")),
                reasoning=reasoning,
                evidence=evidence,
                target_ktype_reference=reference or None,
            )
        except Exception:  # noqa: BLE001 - any failure degrades to heuristics
            proposal = self._fallback.adjudicate(
                signature=signature,
                reason_profile=reason_profile,
                member_count=member_count,
                oem_samples=oem_samples,
                tecdoc_candidates=tecdoc_candidates,
                varying_fields=varying_fields,
            )
            # The stored adjudicator_version names the configured adjudicator,
            # so say in the reasoning itself which one actually answered.
            return AdjudicationProposal(
                recommendation=proposal.recommendation,
                confidence=proposal.confidence,
                reasoning=f"AI unavailable, deterministic rules answered: {proposal.reasoning}",
                evidence={
                    **proposal.evidence,
                    "llm_fallback": True,
                    "source": self._fallback.version,
                },
                target_ktype_reference=proposal.target_ktype_reference,
            )


def _confidence(value: Any) -> float:
    """Clamp rather than reject: the number is soft, the recommendation is not."""

    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.5
    return min(1.0, max(0.0, confidence))
