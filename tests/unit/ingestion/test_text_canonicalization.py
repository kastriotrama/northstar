from copy import deepcopy

import pytest

from ingestion.normalization_pipeline import NormalizationContext
from ingestion.text_canonicalization import (
    TextCanonicalizationTransformer,
    canonicalize_text,
)


@pytest.mark.parametrize(
    ("field_name", "source", "expected"),
    [
        ("model", "  Ｖ６０\u00a0 Recharge  ", "V60 Recharge"),
        ("manufacturer", "A\u030ahlén\u2019s", "Åhlén's"),
        ("ktype_manufacturer", "  Mercedes\u2011Benz ", "Mercedes-Benz"),
        ("brand", "Mercedes\u2011Benz", "Mercedes-Benz"),
        ("body_code", " ac ", "AC"),
        ("eu_category", " m1\t", "M1"),
        ("fuel1", " el ", "EL"),
        ("build_date", " 2024  01  31 ", "2024 01 31"),
    ],
)
def test_canonicalize_text_is_unicode_whitespace_and_field_aware(
    field_name: str,
    source: str,
    expected: str,
) -> None:
    assert canonicalize_text(field_name, source) == expected


@pytest.mark.parametrize(
    "source",
    ["CX-5", "C40/XC40", "O'Connor", "V60 (Recharge)", "E:Class"],
)
def test_name_punctuation_is_preserved(source: str) -> None:
    assert canonicalize_text("model", source) == source


def test_transformer_changes_only_the_working_copy_and_never_identifiers() -> None:
    raw = {
        "manufacturer": "  Volvo\u00a0Car Corporation ",
        "plate": "Ab-C 123",
        "vin": "WVW-ZZZ 1J",
    }
    original = deepcopy(raw)
    context = NormalizationContext(raw_record=raw)

    TextCanonicalizationTransformer().apply(context)

    assert context.raw_record == original
    assert context.canonical_record["manufacturer"] == "Volvo Car Corporation"
    assert context.canonical_record["plate"] == "Ab-C 123"
    assert context.canonical_record["vin"] == "WVW-ZZZ 1J"
    assert [entry.field for entry in context.decision_trace] == ["manufacturer"]


def test_blank_and_unknown_fields_are_not_promoted_to_text() -> None:
    assert canonicalize_text("manufacturer", " \u00a0 ") is None
    assert canonicalize_text("owner_name", "Do not process") is None
    assert canonicalize_text("model", 123) is None
