"""Outbound OEM VIN data provider boundary.

The concrete provider (name and contract pending confirmation) is reached only
through this adapter so the service layer stays provider-agnostic and every
response can be cached immutably before interpretation. VINs must never be
logged here.
"""

from __future__ import annotations

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
