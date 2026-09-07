"""The LLM adapter treats model output as untrusted and degrades safely.

These exercise the adapter without a network call, so the behaviour is pinned
independently of which model is configured.
"""

from typing import Any

import pytest

from api.app.features.match_review.integrations import LlmError
from api.app.features.match_review.rule_advisor import (
    LlmRuleAdvisor,
    PatternRuleAdvisor,
)

DISCRIMINATORS: list[dict[str, Any]] = [
    {
        "field": "fab_code",
        "usable": True,
        "score": 0.24,
        "top_values": [{"value": "VO", "count": 44_253}],
    }
]


class StubLlm:
    """Replies with one canned object, or raises what the real adapter raises."""

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


def _advisor(reply: dict[str, Any] | Exception) -> LlmRuleAdvisor:
    return LlmRuleAdvisor(llm=StubLlm(reply))


def _advise(advisor: LlmRuleAdvisor, oem: list[dict[str, Any]] | None = None) -> Any:
    return advisor.advise(
        source_field="is_4wd",
        source_value="0",
        target_field="drive_type",
        population=191_921,
        discriminators=DISCRIMINATORS,
        field_values={"fab_code": [("VO", 44_253)]},
        oem_samples=oem or [],
    )


def test_valid_reply_is_accepted() -> None:
    advice = _advise(
        _advisor(
            {
                "conditions": [
                    {"field": "fab_code", "operator": "equals", "values": ["VO"]}
                ],
                "target_value": "fwd",
                "confident": True,
                "reasoning": "Volvo block.",
            }
        ),
        oem=[{"drive": "fwd"}],
    )

    assert advice.conditions[0].field == "fab_code"
    assert advice.target_value == "fwd"
    assert advice.confident is True
    assert advice.advisor == "llm:test-model"


def test_value_is_dropped_when_no_oem_evidence_backs_it() -> None:
    """A model may assert a value confidently; without evidence we refuse it."""

    advice = _advise(
        _advisor(
            {
                "conditions": [
                    {"field": "fab_code", "operator": "equals", "values": ["VO"]}
                ],
                "target_value": "fwd",
                "confident": True,
                "reasoning": "All Volvos are front-wheel drive.",
            }
        )
    )

    assert advice.target_value is None
    assert advice.confident is False


@pytest.mark.parametrize(
    "reply",
    [
        {"conditions": [{"field": "invented_field", "values": ["x"]}]},
        {"conditions": [{"field": "fab_code", "operator": "regex", "values": ["x"]}]},
        {"conditions": [{"field": "fab_code", "layer": "psychic", "values": ["x"]}]},
        {"conditions": []},
        {"conditions": [{"field": "fab_code", "values": []}]},
        {"no_conditions_key": True},
    ],
)
def test_malformed_or_out_of_vocabulary_replies_fall_back(
    reply: dict[str, Any],
) -> None:
    advice = _advise(_advisor(reply))

    assert advice.evidence["llm_fallback"] is True
    assert "llm unavailable" in advice.advisor
    assert advice.conditions[0].field == "is_4wd"


def test_non_canonical_target_value_falls_back() -> None:
    advice = _advise(
        _advisor(
            {
                "conditions": [
                    {"field": "fab_code", "operator": "equals", "values": ["VO"]}
                ],
                "target_value": "front-wheel",
                "confident": True,
                "reasoning": "x",
            }
        ),
        oem=[{"drive": "fwd"}],
    )

    assert advice.evidence["llm_fallback"] is True


def test_transport_failure_falls_back_to_deterministic_advice() -> None:
    advisor = LlmRuleAdvisor(
        llm=StubLlm(LlmError("Gemini request failed: ConnectError")),
        fallback=PatternRuleAdvisor(),
    )

    advice = _advise(advisor)

    assert advice.evidence["llm_fallback"] is True
    assert advice.conditions[-1].field == "fab_code"


def test_prompt_carries_vocabulary_priors_and_distributions() -> None:
    llm = StubLlm({"conditions": []})

    _advise(LlmRuleAdvisor(llm=llm))

    sent = llm.calls[0]["payload"]
    assert sent["allowed_target_values"] == ["fwd", "rwd", "awd"]
    assert sent["semantically_relevant_fields_in_order"][0] == "fab_code"
    assert sent["value_distributions"]["fab_code"][0]["value"] == "VO"
    assert "Never guess a fact about cars" in llm.calls[0]["instructions"]
