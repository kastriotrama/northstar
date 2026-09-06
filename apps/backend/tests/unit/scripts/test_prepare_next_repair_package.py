import pytest

from ingestion.context_comparison import reviewed_context_policy
from scripts.prepare_next_repair_package import (
    candidate_only_targets,
    candidate_readiness,
    explicit_golf_anchor,
    vehicle_group,
    volvo_proposals,
)
from scripts.validate_local_matcher_cohort import digest


def candidate():
    return {"source_key": "variant:1", "source_row_refs": ["120:1"],
            "attributes": {"promotion_status": "candidate_only", "year_from": 2005,
                           "candidate_only_reason": "fuel_unresolved", "vehicle_fuel_type": "petrol"}}


def relationship(**attrs):
    return {"to_source_key": "engine:1", "status": "candidate",
            "attributes": {"engine_code": "E1", "engine_fuel_code": "026", "displacement_cc_from": 1998,
                           "displacement_cc_to": None, **attrs},
            "evidence": {"engine_deleted": False, "engine_source_row_ref": "155:1",
                         "ktype_source_row_refs": ["120:1"], "applicability": [{"exclude": False}]}}


def test_mixed_engine_fuel_does_not_inherit_vehicle_petrol_or_become_graph_ready():
    audit = candidate_readiness(candidate(), [relationship()], engine_fuel_labels={"026": "Petrol/Alcohol"},
                                catalog_displacements={"engine:1": {1998}})
    assert audit["ready_to_promote"] is False
    assert "mixed_engine_fuel_requires_promotion_contract" in audit["blockers"]
    assert "full_source_displacement_verification_required" in audit["blockers"]
    assert audit["engines"][0]["official_engine_fuel_label"] == "Petrol/Alcohol"
    assert audit["engines"][0]["canonical_engine_fuel"] is None
    assert audit["engines"][0]["fuel_evidence"]["components"] == ["petrol", "alcohol_unspecified"]


def test_multiple_engines_and_missing_or_conflicting_provenance_are_reported():
    other = relationship()
    other["to_source_key"] = "engine:2"
    other["evidence"]["engine_source_row_ref"] = None
    audit = candidate_readiness(candidate(), [relationship(), other], engine_fuel_labels={}, catalog_displacements={})
    assert {"engine_ambiguous", "engine_provenance_incomplete", "catalog_displacement_not_unique"} <= set(audit["blockers"])
    assert len(audit["engines"]) == 2


def test_exact_engine_evidence_still_requires_independent_promotion():
    audit = candidate_readiness(candidate(), [relationship(displacement_cc_to=1998)],
                                engine_fuel_labels={"026": "Petrol"}, catalog_displacements={})
    assert audit["blockers"] == ["explicit_promotion_required", "independent_confirmation_required"]
    assert audit["ready_to_promote"] is False


@pytest.mark.parametrize("raw,expected", [
    ({"model": "GOLF", "body_code": "AC", "type_text": "AUV"}, None),
    ({"model": "GOLF", "brand": "VW GOLF VARIANT"}, None),
    ({"brand": "VW GOLF VARIANT GL"}, "GOLF VARIANT"),
    ({"model": "GOLF VARIANT"}, "GOLF VARIANT"),
    ({"model": "GOLF PLUS", "brand": "VW GOLF VARIANT"}, None),
])
def test_model_anchors_require_explicit_noncontradictory_text(raw, expected):
    assert explicit_golf_anchor(raw) == expected


def test_private_dedup_key_uses_vin_before_plate_without_exposing_either():
    assert vehicle_group({"vin": "123", "plate": "OLD"}, "a") == vehicle_group({"vin": "123", "plate": "NEW"}, "b")
    assert "123" not in vehicle_group({"vin": "123"}, "a")


def test_volvo_manifest_keeps_hard_conflicts_and_cannot_be_loaded_as_approved():
    items = [{"group": "xc40", "row_key": "a", "raw_source_evidence": {"eeg_type_approval": "e9*x*01"},
              "evaluation": {"terminal": "hard_conflict", "reason_codes": ["conflict:power_kw"]}},
             {"group": "xc60_ii", "row_key": "b", "raw_source_evidence": {}, "evaluation": {}}]
    proposal = volvo_proposals(items)
    assert proposal["coverage_count"] == 1 and proposal["uncovered_count"] == 1
    assert proposal["rules"][0]["remaining_conflicts"] == {"conflict:power_kw": 1}
    assert proposal["rules"][0]["reviewed_by"] == ""
    # The production loader checks approval even with the correct checksum.
    with pytest.raises(ValueError):
        reviewed_context_policy(proposal, expected_version=proposal["version"], expected_digest=digest(proposal))


def test_candidate_only_cohort_selection_checks_reason_and_aggregates_ktypes():
    change = {"before": {"terminal": "review_required"}, "after": {
        "terminal": "provisional", "top_candidate_reference": "1", "reason_codes": ["candidate_only_not_graph_safe"]}}
    report = {"comparison": {"changed_records": [change, change]}}
    assert candidate_only_targets(report) == {"1": 2}
    change["after"]["reason_codes"] = []
    with pytest.raises(ValueError):
        candidate_only_targets(report)
