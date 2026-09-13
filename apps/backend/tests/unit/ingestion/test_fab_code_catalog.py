"""Guards for the derived fabrikatkod catalogue.

Transportstyrelsen publishes no fabrikatkod list, so this mapping is derived
from the production register. These tests lock in the properties that made it
safe to derive: no catch-all codes, no ambiguous makes, and stable anchors.
"""

from ingestion.normalization_rules import (
    _FAB_CODE_MANUFACTURERS,
    _resolve_fab_manufacturer,
)

# Codes whose dominant manufacturer covered <95% of their rows. They are
# excluded deliberately and must not creep back in without a decision.
AMBIGUOUS_CODES = ("ÖV", "DQ", "MG", "LQ", "POL", "BC", "AN", "RO", "CUA")


def test_catalogue_covers_the_high_volume_makes() -> None:
    for code, manufacturer in {
        "VO": "Volvo",
        "VW": "Volkswagen",
        "TO": "Toyota",
        "MB": "Mercedes-Benz",
        "SA": "Saab",
        "KG": "Kia",
        "AU": "Audi",
    }.items():
        assert _FAB_CODE_MANUFACTURERS[code] == manufacturer


def test_ambiguous_codes_stay_out() -> None:
    """`ÖV` is Övrigt — a catch-all, not a make; Lexus/Polestar bleed into Toyota/Volvo."""

    for code in AMBIGUOUS_CODES:
        assert code not in _FAB_CODE_MANUFACTURERS


def test_resolver_is_case_and_whitespace_insensitive() -> None:
    assert _resolve_fab_manufacturer(" vo ") == "Volvo"
    assert _resolve_fab_manufacturer("VO") == "Volvo"


def test_unknown_and_empty_codes_resolve_to_nothing() -> None:
    assert _resolve_fab_manufacturer("ZZZZ") is None
    assert _resolve_fab_manufacturer(None) is None
    assert _resolve_fab_manufacturer("") is None


def test_codes_are_uppercase_and_plausibly_sized() -> None:
    for code in _FAB_CODE_MANUFACTURERS:
        assert code == code.upper()
        assert 1 <= len(code) <= 3


def test_catalogue_is_substantially_more_than_the_original_seven() -> None:
    assert len(_FAB_CODE_MANUFACTURERS) > 100
