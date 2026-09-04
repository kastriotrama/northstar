"""A model may advise on a chunk, but it cannot lower the evidence floor."""

from typing import Any

import pytest

from api.app.features.match_review.adjudicator import (
    HeuristicAdjudicator,
    LlmAdjudicator,
)
from api.app.features.match_review.integrations import LlmError

SIGNATURE = {"manufacturer": "volvo", "model_family": "v70", "production_year": 2008}
CANDIDATES: list[dict[str, Any]] = [
    {"reference": "KTYPE-1234", "label": "Volvo V70 2.4D"},
    {"reference": "KTYPE-9999", "label": "Volvo V70 D5"},
]
CONCORDANT: list[dict[str, Any]] = [
    {"manufacturer": "volvo", "model": "v70", "vin": "YV1***123"},
    {"manufacturer": "volvo", "model": "v70", "vin": "YV1***456"},
]


class StubLlm:
    def __init__(self, reply: dict[str, Any] | Exception) -> None:
        self._reply = reply
        self.calls: list[dict[str, Any]] = []

    @property
    def model(self) -> str:
        return "test-model"

    def complete_json(
        self, *, instructions: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append({"instructions": instructions, "payload": payload})
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


def _adjudicate(
    reply: dict[str, Any] | Exception,
    *,
    oem_samples: list[dict[str, Any]] | None = None,
    tecdoc_candidates: list[dict[str, Any]] | None = None,
    varying_fields: list[str] | None = None,
    llm: StubLlm | None = None,
) -> Any:
    adjudicator = LlmAdjudicator(llm=llm or StubLlm(reply))
    return adjudicator.adjudicate(
        signature=SIGNATURE,
        reason_profile={"no_candidate": 12},
        member_count=12,
        oem_samples=oem_samples if oem_samples is not None else CONCORDANT,
        tecdoc_candidates=tecdoc_candidates if tecdoc_candidates is not None else [],
        varying_fields=varying_fields or ["kw"],
    )


def test_version_names_the_model() -> None:
    assert LlmAdjudicator(llm=StubLlm({})).version == "llm:test-model"


def test_valid_assignment_is_accepted() -> None:
    proposal = _adjudicate(
        {
            "recommendation": "assign_ktype",
            "target_ktype_reference": "KTYPE-1234",
            "confidence": 0.82,
            "reasoning": "Both OEM samples match the 2.4D candidate.",
        },
        tecdoc_candidates=CANDIDATES[:1],
    )

    assert proposal.recommendation == "assign_ktype"
    assert proposal.target_ktype_reference == "KTYPE-1234"
    assert proposal.confidence == pytest.approx(0.82)
    assert proposal.evidence["source"] == "llm:test-model"


def test_non_assignment_recommendation_is_accepted_without_a_target() -> None:
    proposal = _adjudicate(
        {
            "recommendation": "needs_more_evidence",
            "target_ktype_reference": "KTYPE-1234",
            "confidence": 0.4,
            "reasoning": "Two candidates remain; fetch a third sample.",
        },
        tecdoc_candidates=CANDIDATES,
    )

    assert proposal.recommendation == "needs_more_evidence"
    # A target on anything but an assignment is noise: drop it.
    assert proposal.target_ktype_reference is None


def test_confidence_is_clamped() -> None:
    proposal = _adjudicate(
        {
            "recommendation": "split_chunk",
            "confidence": 4.2,
            "reasoning": "Samples disagree.",
        }
    )

    assert proposal.confidence == 1.0


def test_invented_ktype_reference_falls_back() -> None:
    proposal = _adjudicate(
        {
            "recommendation": "assign_ktype",
            "target_ktype_reference": "KTYPE-0000",
            "confidence": 0.9,
            "reasoning": "Looks like a V70.",
        },
        tecdoc_candidates=CANDIDATES[:1],
    )

    assert proposal.evidence["llm_fallback"] is True
    # The deterministic rules answered instead, and they can only name a
    # candidate that was actually supplied.
    assert proposal.target_ktype_reference == "KTYPE-1234"


def test_assignment_on_thin_evidence_falls_back() -> None:
    proposal = _adjudicate(
        {
            "recommendation": "assign_ktype",
            "target_ktype_reference": "KTYPE-1234",
            "confidence": 0.99,
            "reasoning": "One sample is plenty.",
        },
        oem_samples=CONCORDANT[:1],
        tecdoc_candidates=CANDIDATES[:1],
    )

    assert proposal.evidence["llm_fallback"] is True
    assert proposal.recommendation == "needs_more_evidence"


def test_assignment_over_disagreeing_identity_falls_back_to_a_split() -> None:
    proposal = _adjudicate(
        {
            "recommendation": "assign_ktype",
            "target_ktype_reference": "KTYPE-1234",
            "confidence": 0.9,
            "reasoning": "Close enough.",
        },
        tecdoc_candidates=CANDIDATES[:1],
        varying_fields=["brand", "model_no"],
    )

    assert proposal.recommendation == "split_chunk"
    assert proposal.evidence["llm_fallback"] is True


@pytest.mark.parametrize(
    "reply",
    [
        {"recommendation": "merge_everything", "reasoning": "x"},
        {"recommendation": "split_chunk", "reasoning": "  "},
        {"reasoning": "no recommendation"},
        {},
    ],
)
def test_malformed_replies_fall_back(reply: dict[str, Any]) -> None:
    proposal = _adjudicate(reply)

    assert proposal.evidence["llm_fallback"] is True
    assert proposal.evidence["source"] == HeuristicAdjudicator().version
    assert proposal.reasoning.startswith("AI unavailable")


def test_transport_failure_falls_back() -> None:
    proposal = _adjudicate(LlmError("Gemini request failed: ConnectError"))

    assert proposal.evidence["llm_fallback"] is True
    assert proposal.recommendation == "no_safe_match"


def test_prompt_carries_the_evidence_bundle_and_allowed_references() -> None:
    llm = StubLlm({"recommendation": "split_chunk", "reasoning": "ok", "confidence": 1})

    _adjudicate({}, tecdoc_candidates=CANDIDATES, llm=llm)

    sent = llm.calls[0]["payload"]
    assert sent["signature"] == SIGNATURE
    assert sent["member_count"] == 12
    assert sent["allowed_ktype_references"] == ["KTYPE-1234", "KTYPE-9999"]
    assert sent["oem_samples"] == CONCORDANT
    assert "Never invent a reference" in llm.calls[0]["instructions"]
