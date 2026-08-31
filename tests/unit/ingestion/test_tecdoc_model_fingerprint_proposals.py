from ingestion.tecdoc.model_fingerprint_proposals import (
    ModelFingerprintObservation,
    project_model_fingerprint,
    propose_model_fingerprints,
)


def observation(model: str | None, *, variant: str = "ABC") -> ModelFingerprintObservation:
    return ModelFingerprintObservation(
        manufacturer="Volvo",
        type_text="X1",
        type_approval="e1*2007/46*1234",
        variant=variant,
        version="V2",
        production_year=2020,
        fuel="petrol",
        displacement_cc=1969,
        power_kw=140,
        model=model,
    )


def test_proposes_uniquely_anchored_repeated_missing_fingerprint() -> None:
    proposals = propose_model_fingerprints(
        (observation(None), observation("XC40"), observation("XC 40")),
        allowed_models_by_manufacturer={"Volvo": ("XC40",)},
    )

    assert len(proposals) == 1
    assert proposals[0].proposed_model == "XC40"
    assert proposals[0].missing_count == 1
    assert proposals[0].anchor_count == 2
    assert proposals[0].fingerprint_id.startswith("ts-model-fingerprint:")
    assert proposals[0].fingerprint_id == observation(None).fingerprint_id()


def test_rejects_conflicting_or_weak_anchors() -> None:
    conflicting = (observation(None), observation("XC40"), observation("V40"))
    weak = (observation(None, variant="DEF"), observation("XC40", variant="DEF"))

    allowed = {"Volvo": ("XC40", "V40")}
    assert propose_model_fingerprints(conflicting, allowed_models_by_manufacturer=allowed) == ()
    assert propose_model_fingerprints(weak, allowed_models_by_manufacturer=allowed) == ()


def test_requires_manufacturer_and_non_model_technical_evidence() -> None:
    empty = ModelFingerprintObservation("", "", "", "", "", None, "", None, None, None)

    assert propose_model_fingerprints((empty,), allowed_models_by_manufacturer={}) == ()


def test_rejects_anchor_not_present_in_manufacturer_catalog() -> None:
    proposals = propose_model_fingerprints(
        (observation(None), observation("XC7"), observation("XC7")),
        allowed_models_by_manufacturer={"Volvo": ("XC70",)},
    )

    assert proposals == ()


def test_fingerprint_profiles_remove_only_declared_evidence() -> None:
    original = observation(None)

    approval = project_model_fingerprint(original, profile="approval_variant")
    type_variant = project_model_fingerprint(original, profile="type_variant_technical")
    variant = project_model_fingerprint(original, profile="variant_technical")

    assert approval.type_text == ""
    assert approval.type_approval == original.type_approval
    assert type_variant.type_text == original.type_text
    assert type_variant.type_approval == ""
    assert variant.type_text == ""
    assert variant.type_approval == ""
    assert variant.variant == original.variant
    assert variant.power_kw == original.power_kw


def test_fingerprint_profile_rejects_unknown_name() -> None:
    import pytest

    with pytest.raises(ValueError, match="unknown model fingerprint profile"):
        project_model_fingerprint(observation(None), profile="unsafe")
