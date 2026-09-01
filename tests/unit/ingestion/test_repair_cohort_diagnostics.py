import json

from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.tecdoc.match_diagnostics import RepairCohortDiagnostics


def test_missing_means_matcher_missing_not_just_absent_normalized_field():
    diagnostics = RepairCohortDiagnostics()
    diagnostics.add(raw={"brand": "VOLVO V70", "variant": "123"}, normalized={},
                    terminal="resolved", reasons=("model_recovered_from_brand",), candidate=None, row_key="1")
    assert diagnostics.report()["missing_model_source_profiles"] == {}
    diagnostics.add(raw={"brand": "VOLVO", "variant": "123"}, normalized={},
                    terminal="review_required", reasons=("model_evidence_missing",), candidate=None, row_key="2")
    assert diagnostics.report()["missing_model_source_profiles"] == {"brand+variant": 1}


def test_bodywork_counts_keep_terminal_populations_separate_and_examples_private():
    diagnostics = RepairCohortDiagnostics()
    candidate = VehicleCandidate("1", "Test", "Model", bodyworks=frozenset({"suv"}))
    for ordinal, terminal in enumerate(("review_required", "hard_conflict", "review_required", "review_required")):
        diagnostics.add(raw={"plate": "PRIVATE-PLATE", "vin": "PRIVATE-VIN", "body_code": "AC"},
                        normalized={"manufacturer": "Test", "bodywork_form": "estate"},
                        terminal=terminal, reasons=("context_conflict:bodywork",),
                        candidate=candidate, row_key=str(ordinal))
    report = diagnostics.report()
    assert report["bodywork_conflict_terminals"] == {"review_required": 3, "hard_conflict": 1}
    assert report["groups"][0]["count"] == 4
    assert len(report["groups"][0]["examples"]) == 3
    assert "PRIVATE" not in json.dumps(report)
    assert report["groups"][0]["review_status"] == "pending_review"


def test_candidate_only_reports_rows_and_distinct_targets_separately():
    diagnostics = RepairCohortDiagnostics()
    for row in range(3):
        diagnostics.add(raw={}, normalized={}, terminal="provisional",
                        reasons=("candidate_only_not_graph_safe",),
                        candidate=VehicleCandidate("1", "Test", "Model"), row_key=str(row))
    assert diagnostics.report()["candidate_only_distinct_ktypes"] == 1
    assert diagnostics.report()["candidate_only_ktype_counts"] == {"1": 3}
