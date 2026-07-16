"""Canonical identity encoding for external Alias assertions."""

from __future__ import annotations

import json

ASSERTION_IDENTITY_VERSION = "v1"


def build_assertion_identity(
    source_system: str,
    source_assertion_key: str,
) -> str:
    """Return a versioned, collision-safe identity for one source assertion."""

    _validate_component("source_system", source_system)
    _validate_component("source_assertion_key", source_assertion_key)
    payload = json.dumps(
        [source_system, source_assertion_key],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"{ASSERTION_IDENTITY_VERSION}:{payload}"


def _validate_component(name: str, value: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
