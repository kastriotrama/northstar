import pytest

from northstar.alias_identity import (
    ASSERTION_IDENTITY_VERSION,
    build_assertion_identity,
)


def test_identity_uses_versioned_compact_json() -> None:
    assert build_assertion_identity(
        "transportstyrelsen", "vehicle-abc123:plate:0"
    ) == 'v1:["transportstyrelsen","vehicle-abc123:plate:0"]'


def test_identity_is_deterministic() -> None:
    first = build_assertion_identity("tecdoc", "vehicle-82931:k_type:0")
    second = build_assertion_identity("tecdoc", "vehicle-82931:k_type:0")

    assert first == second
    assert first.startswith(f"{ASSERTION_IDENTITY_VERSION}:")


def test_component_boundaries_prevent_delimiter_collisions() -> None:
    assert build_assertion_identity("a:b", "c") != build_assertion_identity("a", "b:c")


def test_unicode_is_preserved_canonically() -> None:
    assert build_assertion_identity("manuál", "fordon:åäö") == (
        'v1:["manuál","fordon:åäö"]'
    )


@pytest.mark.parametrize(
    ("source_system", "source_assertion_key"),
    [
        ("", "assertion"),
        ("   ", "assertion"),
        ("source", ""),
        ("source", "\t"),
    ],
)
def test_empty_components_are_rejected(
    source_system: str,
    source_assertion_key: str,
) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        build_assertion_identity(source_system, source_assertion_key)


@pytest.mark.parametrize(
    ("source_system", "source_assertion_key"),
    [(None, "assertion"), ("source", None)],
)
def test_non_string_components_are_rejected(
    source_system: str,
    source_assertion_key: str,
) -> None:
    with pytest.raises(TypeError, match="must be a string"):
        build_assertion_identity(source_system, source_assertion_key)
