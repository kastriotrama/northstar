"""Outbound provider boundaries: OEM VIN data, and the JSON model calls.

The concrete OEM provider (name and contract pending confirmation) is reached
only through this adapter so the service layer stays provider-agnostic and
every response can be cached immutably before interpretation. VINs must never
be logged here, nor sent to the model: callers mask them first.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

import httpx


class OemProviderError(RuntimeError):
    """Raised when the provider call fails after the configured retries."""


class OemProviderNotConfiguredError(OemProviderError):
    """Raised when no OEM VIN provider credentials are configured."""


class OemVinProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def dataset_version(self) -> str: ...

    def fetch_vehicle(self, vin: str) -> dict[str, Any]: ...


class HttpOemVinProvider:
    """Generic authenticated JSON lookup: GET {base_url}/vehicles/{vin}."""

    def __init__(
        self,
        *,
        provider_name: str,
        base_url: str,
        api_key: str,
        dataset_version: str,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._provider_name = provider_name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._dataset_version = dataset_version
        self._timeout_seconds = timeout_seconds

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def dataset_version(self) -> str:
        return self._dataset_version

    def fetch_vehicle(self, vin: str) -> dict[str, Any]:
        try:
            response = httpx.get(
                f"{self._base_url}/vehicles/{vin}",
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as error:
            raise OemProviderError(
                f"OEM provider {self._provider_name} request failed: "
                f"{type(error).__name__}"
            ) from error
        if not isinstance(payload, dict):
            raise OemProviderError(
                f"OEM provider {self._provider_name} returned a non-object payload"
            )
        return payload


class UnconfiguredOemVinProvider:
    """Placeholder used until provider credentials are configured."""

    @property
    def provider_name(self) -> str:
        return "unconfigured"

    @property
    def dataset_version(self) -> str:
        return "unversioned"

    def fetch_vehicle(self, vin: str) -> dict[str, Any]:
        raise OemProviderNotConfiguredError(
            "No OEM VIN provider is configured. Set OEM_VIN_PROVIDER_NAME, "
            "OEM_VIN_PROVIDER_BASE_URL and OEM_VIN_PROVIDER_API_KEY."
        )


def mask_vin(vin: str) -> str:
    """Keep only the first and last three characters for display."""

    compact = vin.strip()
    if len(compact) <= 6:
        return "*" * len(compact)
    return f"{compact[:3]}{'*' * (len(compact) - 6)}{compact[-3:]}"


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class LlmError(RuntimeError):
    """Raised when a model call fails or returns something unusable."""


class JsonLlm(Protocol):
    """One question, one JSON object back. No streaming, no tools, no state."""

    @property
    def model(self) -> str: ...

    def complete_json(
        self, *, instructions: str, payload: dict[str, Any]
    ) -> dict[str, Any]: ...


class GeminiJsonLlm:
    """Google Gemini `generateContent`, constrained to a single JSON object.

    Callers hand over the same evidence bundle a human reviewer would see and
    validate whatever comes back against their own allowlists: everything past
    this boundary is untrusted input. Failures raise `LlmError` so each caller
    can decide how to degrade rather than surfacing transport detail.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = GEMINI_BASE_URL,
        timeout_seconds: float = 30.0,
        max_output_tokens: int = 2048,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens

    @property
    def model(self) -> str:
        return self._model

    def complete_json(
        self, *, instructions: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{self._base_url}/models/{self._model}:generateContent",
                headers={"x-goog-api-key": self._api_key},
                json={
                    "systemInstruction": {"parts": [{"text": instructions}]},
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": json.dumps(payload)}],
                        }
                    ],
                    "generationConfig": {
                        "temperature": 0,
                        "responseMimeType": "application/json",
                        "maxOutputTokens": self._max_output_tokens,
                    },
                },
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as error:
            # The URL carries no secret, but the key does travel in a header:
            # report the failure kind only, never the request.
            raise LlmError(
                f"Gemini request failed: {type(error).__name__}"
            ) from error
        except ValueError as error:
            raise LlmError("Gemini returned a non-JSON response") from error
        return _parse_gemini_reply(body, model=self._model)


def _parse_gemini_reply(body: Any, *, model: str) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise LlmError(f"{model} returned a non-object response")
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        # Also the shape of a safety block or an exhausted token budget.
        raise LlmError(f"{model} returned no candidate: {body.get('promptFeedback')}")
    parts = candidates[0].get("content", {}).get("parts")
    if not isinstance(parts, list):
        raise LlmError(f"{model} returned no content parts")
    # Reasoning models emit thought parts alongside the answer; only the
    # answer parts carry the JSON.
    text = "".join(
        str(part.get("text", ""))
        for part in parts
        if isinstance(part, dict) and not part.get("thought")
    ).strip()
    if not text:
        raise LlmError(f"{model} returned an empty answer")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise LlmError(f"{model} returned malformed JSON") from error
    if not isinstance(parsed, dict):
        raise LlmError(f"{model} returned {type(parsed).__name__}, not an object")
    return parsed
