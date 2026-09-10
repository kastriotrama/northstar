import pytest

from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.vocabulary_alignment import (
    VocabularyAlignment,
    align_catalog_fuels,
    canonical_fuels,
)


def _candidate(reference: str, fuels: frozenset[str]) -> VehicleCandidate:
    return VehicleCandidate(
        candidate_reference=reference,
        candidate_type="TecDocKType",
        manufacturer="Volvo",
        model="XC90",
        fuels=fuels,
    )


def test_canonical_fuels_maps_only_known_terms() -> None:
    mapping = {"electricity": "electric", "methane": "cng"}

    assert canonical_fuels(frozenset({"electricity"}), mapping) == frozenset({"electric"})
    # An unmapped term passes through rather than disappearing.
    assert canonical_fuels(frozenset({"petrol"}), mapping) == frozenset({"petrol"})
    assert canonical_fuels(frozenset(), mapping) == frozenset()


def test_alignment_lets_a_ts_electric_car_intersect_an_electric_ktype() -> None:
    mapping = {"electricity": "electric"}
    catalog = align_catalog_fuels((_candidate("K1", frozenset({"electric"})),), mapping)
    query = canonical_fuels(frozenset({"electricity"}), mapping)

    # Before alignment these two sets were disjoint, which is why every EV
    # conflicted on fuel.
    assert not (frozenset({"electricity"}) & frozenset({"electric"}))
    assert query & catalog[0].fuels


def test_align_catalog_is_a_no_op_without_a_mapping() -> None:
    catalog = (_candidate("K1", frozenset({"petrol"})),)

    assert align_catalog_fuels(catalog, {}) == catalog


def test_alignment_row_rejects_an_unsupported_vocabulary_or_relation() -> None:
    common = {
        "source_system": "transportstyrelsen",
        "source_term": "electricity",
        "canonical_term": "electric",
        "support": None,
    }
    with pytest.raises(ValueError, match="vocabulary"):
        VocabularyAlignment(vocabulary="colour", relation="equivalent", **common)
    with pytest.raises(ValueError, match="relation"):
        VocabularyAlignment(vocabulary="fuel", relation="sameish", **common)


def test_alignment_row_builds_a_stable_assertion_identity() -> None:
    row = VocabularyAlignment(
        vocabulary="fuel",
        source_system="transportstyrelsen",
        source_term="electricity",
        canonical_term="electric",
        relation="equivalent",
        support=None,
    ).graph_row(promoted_at="2026-09-09T00:00:00+00:00")

    assert row["alias_text"] == "electricity"
    assert row["canonical_term"] == "electric"
    # Identity is scoped to the vocabulary/relation/terms, not to when it was
    # promoted -- re-promoting the same live ruling must MERGE onto the same
    # alias node, never mint a second one.
    assert "fuel" in str(row["assertion_identity"])
