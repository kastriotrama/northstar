import pytest

from ingestion.fuzzy_matching import (
    FuzzyMatchConfig,
    FuzzyVehicleMatcher,
    ManufacturerCandidateIndex,
    VehicleCandidate,
    VehicleMatchQuery,
)
from scripts.inspect_bodywork_repair_package import family_evidence, select_group, summarize_items


def test_group_selection_does_not_expand_to_other_raw_brands_or_body_codes():
    raw = {"brand": "VOLKSWAGEN, VW", "model": "GOLF", "body_code": "AC"}
    record = {"reason_codes": ["context_conflict:bodywork"]}
    model = "GOLF VII (5G1, BQ1, BE1, BE2)"
    assert select_group(raw, record, model) == "golf_vii"
    assert select_group({**raw, "brand": "VOLKSWAGEN, VW  AU"}, record, model) is None
    assert select_group({**raw, "body_code": "AB"}, record, model) is None
    assert select_group(raw, {"reason_codes": []}, model) is None
    assert select_group(raw, record, "GOLF VIII") is None


def matcher(config=None):
    return FuzzyVehicleMatcher(ManufacturerCandidateIndex((
        VehicleCandidate("1", "VW", "GOLF VII", model_aliases=("GOLF",), bodyworks=frozenset({"hatchback"})),
        VehicleCandidate("2", "VW", "GOLF VII Variant", bodyworks=frozenset({"estate"}), power_kw=500),
        VehicleCandidate("3", "VW", "GOLF VIII", bodyworks=frozenset({"estate"})),
        VehicleCandidate("4", "Ford", "GOLF VII", bodyworks=frozenset({"estate"})),
    )), config)


def test_sibling_evidence_includes_below_threshold_not_other_generations_or_manufacturers():
    scorer = matcher()
    query = VehicleMatchQuery("GOLF", manufacturer="VW", bodywork="estate", power_kw=100)
    rows = family_evidence(scorer, query, family="GOLF VII", returned_references={"1"})
    assert {row["catalog_candidate"]["candidate_reference"] for row in rows} == {"1", "2"}
    estate = next(row for row in rows if row["catalog_candidate"]["candidate_reference"] == "2")
    assert not estate["in_returned_candidates"]
    assert not estate["qualifies_candidate_threshold"]
    assert "power_kw" in estate["score"]["evidence"]["conflicting_fields"]
    assert estate["score"] == scorer._score(query, scorer._index._all[1], bodywork_discriminates=False).to_review_payload()


def test_sibling_evidence_rejects_unpinned_weight_and_manufacturer_scope():
    query = VehicleMatchQuery("GOLF", manufacturer="VW")
    with pytest.raises(ValueError, match="unit bodywork weight"):
        family_evidence(matcher(FuzzyMatchConfig(bodywork_discriminating_weight=2)), query,
                        family="GOLF VII", returned_references=set())
    with pytest.raises(ValueError, match="exact manufacturer"):
        family_evidence(matcher(), VehicleMatchQuery("GOLF"), family="GOLF VII", returned_references=set())


def test_summary_counts_rows_not_duplicate_attempts_and_does_not_claim_acceptance():
    siblings = family_evidence(matcher(), VehicleMatchQuery("GOLF", manufacturer="VW", bodywork="estate"),
                               family="GOLF VII", returned_references={"1"})
    summary = summarize_items([{
        "group": "golf_vii", "evaluation": {"terminal": "review_required", "reason_codes": ["context_conflict:bodywork"]},
        "family_attempts": [{"siblings": siblings}, {"siblings": siblings}],
    }])["golf_vii"]
    assert summary["count"] == 1
    assert summary["sibling_counts"]["estate_in_family_catalog"] == 1
    assert summary["sibling_counts"]["best_estate_partial_model"] == 1
    assert summary["terminals"] == {"review_required": 1}
