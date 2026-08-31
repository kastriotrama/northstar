import pytest

from ingestion.tecdoc.engine_fingerprint_proposals import (
    EngineFingerprintObservation,
    ReviewedEngineFingerprintIndex,
    accepted_non_degrading_fingerprints,
    propose_engine_fingerprints,
)


def test_reviewed_index_resolves_only_exact_manufacturer_scoped_rule() -> None:
    observation = EngineFingerprintObservation("VW", "", "1K", "ABC", None)
    index = ReviewedEngineFingerprintIndex.from_overrides({
        "rule": {
            "kind": "engine_fingerprint_rule",
            "fingerprint_id": observation.fingerprint_id(),
            "profile": "variant_version",
            "manufacturer": "VW",
            "engine_code": "CAYC",
        }
    })
    assert index.resolve(
        manufacturer="VW", type_approval="", variant="1K", version="ABC"
    ) == "CAYC"
    assert index.resolve(
        manufacturer="AUDI", type_approval="", variant="1K", version="ABC"
    ) is None


def test_proposes_repeated_unique_catalog_engine() -> None:
    rows = (
        EngineFingerprintObservation("Volvo", "e9*2018/858*", "246", "D4", "D4204T14"),
        EngineFingerprintObservation("Volvo", "e9*2018/858*", "246", "D4", "D4204T14"),
        EngineFingerprintObservation(
            "Volvo", "e9*2018/858*", "246", "D4", None, unresolved=True
        ),
    )

    proposals = propose_engine_fingerprints(
        rows, allowed_engines_by_manufacturer={"Volvo": ("D4204T14",)}
    )

    assert len(proposals) == 1
    assert proposals[0].engine_code == "D4204T14"
    assert proposals[0].anchor_count == 2
    assert proposals[0].unresolved_count == 1


def test_rejects_conflicting_engines_or_weak_evidence() -> None:
    conflicting = (
        EngineFingerprintObservation("Volvo", "approval", "246", "D4", "D4204T14"),
        EngineFingerprintObservation("Volvo", "approval", "246", "D4", "B4204T35"),
        EngineFingerprintObservation("Volvo", "approval", "246", "D4", None, True),
    )
    weak = (
        EngineFingerprintObservation("Volvo", "", "246", "", "D4204T14"),
        EngineFingerprintObservation("Volvo", "", "246", "", None, True),
    )

    assert propose_engine_fingerprints(
        conflicting,
        allowed_engines_by_manufacturer={"Volvo": ("D4204T14", "B4204T35")},
    ) == ()
    assert propose_engine_fingerprints(
        weak, allowed_engines_by_manufacturer={"Volvo": ("D4204T14",)}
    ) == ()


def test_requires_two_anchors_and_catalog_membership() -> None:
    rows = (
        EngineFingerprintObservation("Volvo", "approval", "246", "D4", "D4204T14"),
        EngineFingerprintObservation("Volvo", "approval", "246", "D4", None, True),
    )

    assert propose_engine_fingerprints(
        rows, allowed_engines_by_manufacturer={"Volvo": ("D4204T14",)}
    ) == ()
    with pytest.raises(ValueError, match="at least two"):
        propose_engine_fingerprints(
            rows,
            allowed_engines_by_manufacturer={"Volvo": ("D4204T14",)},
            minimum_anchor_count=1,
        )


def test_acceptance_requires_resolution_and_rejects_any_degradation() -> None:
    accepted = accepted_non_degrading_fingerprints(
        {
            "safe": ("resolved", "review_required"),
            "conflicting": ("resolved", "hard_conflict"),
            "no_gain": ("review_required",),
            "failed": ("resolved", "failed"),
        }
    )

    assert accepted == ("safe",)
