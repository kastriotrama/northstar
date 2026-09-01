"""The LLM adapter treats model output as untrusted and degrades safely.

These exercise the adapter without a network call, so the behaviour is pinned
before anyone connects a real key.
"""

import json
from typing import Any

import httpx
import pytest

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


def _advisor(monkeypatch: pytest.MonkeyPatch, reply: Any) -> LlmRuleAdvisor:
    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    return LlmRuleAdvisor(api_key="test-key", model="test-model")


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


def test_valid_reply_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    advice = _advise(
        _advisor(
            monkeypatch,
            {
                "conditions": [
                    {"field": "fab_code", "operator": "equals", "values": ["VO"]}
                ],
                "target_value": "fwd",
                "confident": True,
                "reasoning": "Volvo block.",
            },
        ),
        oem=[{"drive": "fwd"}],
    )

    assert advice.conditions[0].field == "fab_code"
    assert advice.target_value == "fwd"
    assert advice.confident is True


def test_value_is_dropped_when_no_oem_evidence_backs_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model may assert a value confidently; without evidence we refuse it."""

    advice = _advise(
        _advisor(
            monkeypatch,
            {
                "conditions": [
                    {"field": "fab_code", "operator": "equals", "values": ["VO"]}
                ],
                "target_value": "fwd",
                "confident": True,
                "reasoning": "All Volvos are front-wheel drive.",
            },
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
        "not json at all",
    ],
)
def test_malformed_or_out_of_vocabulary_replies_fall_back(
    monkeypatch: pytest.MonkeyPatch, reply: Any
) -> None:
    advice = _advise(_advisor(monkeypatch, reply))

    assert advice.evidence["llm_fallback"] is True
    assert "llm unavailable" in advice.advisor
    assert advice.conditions[0].field == "is_4wd"


def test_non_canonical_target_value_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advice = _advise(
        _advisor(
            monkeypatch,
            {
                "conditions": [
                    {"field": "fab_code", "operator": "equals", "values": ["VO"]}
                ],
                "target_value": "front-wheel",
                "confident": True,
                "reasoning": "x",
            },
        ),
        oem=[{"drive": "fwd"}],
    )

    assert advice.evidence["llm_fallback"] is True


def test_transport_failure_falls_back_to_deterministic_advice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "post", boom)
    advisor = LlmRuleAdvisor(
        api_key="k", model="m", fallback=PatternRuleAdvisor()
    )

    advice = _advise(advisor)

    assert advice.evidence["llm_fallback"] is True
    assert advice.conditions[-1].field == "fab_code"


def test_prompt_carries_vocabulary_priors_and_distributions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def capture(url: str, **kwargs: Any) -> httpx.Response:
        captured.update(kwargs["json"])
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"conditions": []})}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", capture)
    _advise(LlmRuleAdvisor(api_key="k", model="m"))

    sent = json.loads(captured["messages"][1]["content"])
    assert sent["allowed_target_values"] == ["fwd", "rwd", "awd"]
    assert sent["semantically_relevant_fields_in_order"][0] == "fab_code"
    assert sent["value_distributions"]["fab_code"][0]["value"] == "VO"
    assert captured["temperature"] == 0
