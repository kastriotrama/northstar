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
        "alignment_version": "v1",
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
        alignment_version="v1",
        vocabulary="fuel",
        source_system="transportstyrelsen",
        source_term="electricity",
        canonical_term="electric",
        relation="equivalent",
        support=None,
    ).graph_row()

    assert row["alias_text"] == "electricity"
    assert row["canonical_term"] == "electric"
    # Identity must carry the version, so a later alignment set cannot silently
    # overwrite an earlier promoted alias.
    assert "v1" in str(row["assertion_identity"])
    assert str(row["assertion_identity"]).startswith("v1:")


def test_seed_set_is_internally_consistent() -> None:
    from ingestion.vocabulary_seed import INITIAL_FUEL_ALIGNMENT, SEED_SETS

    for version, (rows, note) in SEED_SETS.items():
        assert version.strip() and note.strip()
        assert rows, f"{version} must define rows"
        # One ruling per source term: a term cannot be both equivalent to a
        # concept and merely compatible with it.
        terms = [(r.vocabulary, r.source_system, r.source_term) for r in rows]
        assert len(terms) == len(set(terms))
        for row in rows:
            assert row.relation in {"equivalent", "compatible"}
            assert row.evidence_note.strip(), "a reviewer needs the rationale"
            # The schema enforces this too; assert it here so a bad seed fails
            # before it reaches a database.
            if row.relation == "compatible":
                assert row.support is not None

    equivalences = {r.source_term: r.canonical_term
                    for r in INITIAL_FUEL_ALIGNMENT if r.relation == "equivalent"}
    # A canonical target must not itself be a source term, or canonicalisation
    # would depend on iteration order.
    assert not set(equivalences) & set(equivalences.values())


def test_unknown_seed_version_is_rejected() -> None:
    import pytest as _pytest

    from ingestion.vocabulary_seed import apply_vocabulary_seed

    with _pytest.raises(ValueError, match="unknown alignment version"):
        apply_vocabulary_seed(
            None,  # type: ignore[arg-type]
            alignment_version="align-does-not-exist",
            activated_by="tester",
        )
