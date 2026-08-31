from ingestion.tecdoc.canonical_promotion import _candidate_only_vehicle_candidates
from ingestion.tecdoc.dat_extraction import TecDocHierarchyRecord


def record() -> TecDocHierarchyRecord:
    return TecDocHierarchyRecord(
        manufacturer_id="000005",
        manufacturer_name="AUDI",
        manufacturer_groups=("PC",),
        model_id="00001",
        model_name="A4",
        ktype_id="000012345",
        ktype_name="2.0 TFSI",
        year_from="202001",
        year_to=None,
        power_kw=140,
        displacement_cc=1984,
        fuel_type_code="001",
        engine_type_code="001",
        drive_type_code="001",
        transmission_type_code="002",
        body_type_code="003",
        engines=(),
        source_row_refs=("100:1", "110:1", "120:1"),
    )


def test_candidate_only_retains_identity_and_marks_promotion_boundary() -> None:
    candidates = _candidate_only_vehicle_candidates(
        record(), reason="engine_ambiguous", vehicle_fuel_type="lpg"
    )

    assert [candidate.entity_type for candidate in candidates] == [
        "manufacturer",
        "model_family",
        "vehicle_variant",
        "alias",
    ]
    variant = candidates[2]
    alias = candidates[3]
    assert variant.attributes["candidate_only_reason"] == "engine_ambiguous"
    assert variant.attributes["promotion_status"] == "candidate_only"
    assert variant.attributes["year_from"] == 2020
    assert variant.attributes["displacement_cc"] == 1984
    assert variant.attributes["vehicle_fuel_type"] == "lpg"
    assert alias.attributes["alias_text"] == "000012345"
    assert alias.attributes["target_source_key"] == "variant:000012345"


def test_candidate_only_never_fabricates_engine_identity() -> None:
    candidates = _candidate_only_vehicle_candidates(
        record(), reason="displacement_unresolved"
    )

    assert all(candidate.entity_type != "engine" for candidate in candidates)
    assert "engine_source_key" not in candidates[2].attributes
