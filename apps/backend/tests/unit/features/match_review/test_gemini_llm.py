"""The Gemini adapter parses one JSON object out, or raises `LlmError`.

Every failure mode is a normal operating condition for the callers, which
degrade to their deterministic path, so each one must raise rather than leak a
half-parsed answer.
"""

import json
from typing import Any

import httpx
import pytest

from api.app.features.match_review.integrations import GeminiJsonLlm, LlmError


def _reply(*parts: dict[str, Any]) -> dict[str, Any]:
    return {"candidates": [{"content": {"parts": list(parts)}}]}


def _llm(monkeypatch: pytest.MonkeyPatch, body: Any, status: int = 200) -> GeminiJsonLlm:
    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        _sent.update({"url": url, **kwargs})
        return httpx.Response(
            status, json=body, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    return GeminiJsonLlm(api_key="test-key", model="gemini-3.1-flash-lite")


_sent: dict[str, Any] = {}


def _complete(llm: GeminiJsonLlm) -> dict[str, Any]:
    return llm.complete_json(instructions="be careful", payload={"question": 1})


def test_answer_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = _llm(monkeypatch, _reply({"text": json.dumps({"recommendation": "split_chunk"})}))

    assert _complete(llm) == {"recommendation": "split_chunk"}


def test_request_carries_key_instructions_and_json_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sent.clear()
    llm = _llm(monkeypatch, _reply({"text": "{}"}))

    _complete(llm)

    assert _sent["url"].endswith("/models/gemini-3.1-flash-lite:generateContent")
    assert _sent["headers"]["x-goog-api-key"] == "test-key"
    body = _sent["json"]
    assert body["systemInstruction"]["parts"][0]["text"] == "be careful"
    assert json.loads(body["contents"][0]["parts"][0]["text"]) == {"question": 1}
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["temperature"] == 0


def test_thought_parts_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = _llm(
        monkeypatch,
        _reply({"text": "let me think", "thought": True}, {"text": '{"ok": true}'}),
    )

    assert _complete(llm) == {"ok": True}


@pytest.mark.parametrize(
    "body",
    [
        {"promptFeedback": {"blockReason": "SAFETY"}},
        {"candidates": []},
        _reply({"text": "  "}),
        _reply({"text": "not json at all"}),
        _reply({"text": "[1, 2]"}),
        ["not an object"],
    ],
)
def test_unusable_replies_raise(monkeypatch: pytest.MonkeyPatch, body: Any) -> None:
    llm = _llm(monkeypatch, body)

    with pytest.raises(LlmError):
        _complete(llm)


def test_http_error_raises_without_leaking_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _llm(monkeypatch, {"error": {"message": "bad key"}}, status=403)

    with pytest.raises(LlmError) as error:
        _complete(llm)

    assert "test-key" not in str(error.value)


def test_transport_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "post", boom)
    llm = GeminiJsonLlm(api_key="k", model="m")

    with pytest.raises(LlmError):
        _complete(llm)
