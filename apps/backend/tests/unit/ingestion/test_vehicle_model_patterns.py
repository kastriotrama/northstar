"""Reviewed patterns: which model a registry text's model word names, in TS's own spelling."""

import pytest

from ingestion.vehicle_model_patterns import pattern_model

VOLVO = {"740", "240", "940", "760", "140", "V70", "Amazon"}
BMW = {"1 Series", "3 Series", "5 Series", "X3", "Z3"}
MERCEDES = {"C-Class", "E-Class", "S-Class", "M-Class", "SLK"}
SAAB = {"900", "9000", "9-3"}


@pytest.mark.parametrize(
    ("manufacturer", "token", "vocabulary", "expected"),
    [
        # A word TS already names this make's cars by, in TS's spelling.
        ("Toyota", "COROLLA", {"Corolla", "RAV4"}, "Corolla"),
        ("Saab", "9000", SAAB, "9000"),
        ("Saab", "9-3", SAAB, "9-3"),
        # Saab's 1950s 93 is not the 9-3: exact words only.
        ("Saab", "93", SAAB, None),
        ("Volvo", "V70", VOLVO, "V70"),
        # Volvo type codes: series from the first two digits.
        ("Volvo", "744-883", VOLVO, "740"),
        ("Volvo", "745", VOLVO, "740"),
        ("Volvo", "245-883", VOLVO, "240"),
        ("Volvo", "945-811", VOLVO, "940"),
        ("Volvo", "1421341", VOLVO, "140"),
        ("Volvo", "704", VOLVO, "760"),
        ("Volvo", "965", VOLVO, None),  # 960 is not in this vocabulary
        ("Volvo", "13134", VOLVO, None),
        # BMW: series and engine.
        ("BMW", "320I", BMW, "3 Series"),
        ("BMW", "525TDS", BMW, "5 Series"),
        ("BMW", "118D", BMW, "1 Series"),
        ("BMW", "2002", BMW, None),
        ("BMW", "X3", BMW, "X3"),
        # Mercedes-Benz: class letter, alone or with its engine.
        ("Mercedes-Benz", "C", MERCEDES, "C-Class"),
        ("Mercedes-Benz", "E220CDI", MERCEDES, "E-Class"),
        ("Mercedes-Benz", "ML", MERCEDES, "M-Class"),
        ("Mercedes-Benz", "SLK", MERCEDES, "SLK"),
        ("Mercedes-Benz", "A", MERCEDES, None),  # A-Class not in this vocabulary
        ("Mercedes-Benz", "190", MERCEDES, None),
        # A pattern belongs to its make only.
        ("Volvo", "320I", VOLVO, None),
        ("Peugeot", "C", {"307"}, None),
        ("Volvo", "", VOLVO, None),
    ],
)
def test_pattern_model(manufacturer: str, token: str, vocabulary: set[str], expected: str | None) -> None:
    assert pattern_model(manufacturer, token, vocabulary) == expected
