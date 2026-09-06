from scripts.audit_model_qualifier_loss import (
    loses_trailing_qualifier,
    model_tokens,
    observation_risks,
    specific_catalog_labels,
)


def test_detects_only_strict_trailing_qualifier_loss() -> None:
    assert loses_trailing_qualifier("C3 PICASSO", "C3")
    assert loses_trailing_qualifier("A3 SPORTBACK 35 TFSI", "A3")
    assert not loses_trailing_qualifier("320D XDRIVE", "3 Series")
    assert not loses_trailing_qualifier("C3", "C3")


def test_specific_catalog_labels_require_contiguous_source_tokens() -> None:
    labels = specific_catalog_labels(
        "C4 PICASSO 1.6 HDI",
        "C4",
        ("C4", "C4 PICASSO", "C4 GRAND PICASSO", "PICASSO C4"),
    )
    assert labels == ("C4 PICASSO",)


def test_known_unsafe_patterns_are_flagged_without_approval() -> None:
    qashqai = observation_risks(
        manufacturer="NISSAN",
        raw_model="QASHQAI+2",
        normalized_family="Qashqai",
        catalog_label="QASHQAI 2",
    )
    golf = observation_risks(
        manufacturer="VW",
        raw_model="GOLF VARIANT",
        normalized_family="Golf",
        catalog_label="GOLF VARIANT",
    )
    corsa = observation_risks(
        manufacturer="OPEL",
        raw_model="CORSA E",
        normalized_family="Corsa",
        catalog_label="CORSA E",
    )
    volvo = observation_risks(
        manufacturer="VOLVO",
        raw_model="V60 CROSS COUNTRY",
        normalized_family="V60",
        catalog_label="V60 Cross Country",
    )
    duplicated = observation_risks(
        manufacturer="OPEL",
        raw_model="MOKKA X",
        normalized_family="Mokka",
        catalog_label="MOKKA MOKKA X",
    )
    assert "plus_digit_semantics_ambiguous" in qashqai
    assert "shared_golf_base_family" in golf
    assert "generation_letter_ambiguous" in corsa
    assert "reconcile_existing_volvo_policy" in volvo
    assert "duplicated_catalog_label" in duplicated
    assert all(
        "domain_review_required" in risks
        for risks in (qashqai, golf, corsa, volvo, duplicated)
    )


def test_model_tokens_are_accent_and_punctuation_tolerant() -> None:
    assert model_tokens("CITROËN C4-PICASSO") == ("CITROEN", "C4", "PICASSO")
