from pathlib import Path

import pytest

from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator
from ingestion.translation_dictionaries import REVIEWED_RULE_SET_VERSION, load_translation_rule_set
from scripts.replay_match_repair_evidence import (
    capture_evaluation,
    changed_accepted_records,
    load_context_policy_for_report,
    review_source_evidence,
)


def test_review_source_preserves_actual_measurement_inputs_without_plate_or_vin():
    evidence = review_source_evidence({"kw": 162, "ccm": 1969, "plate": "PRIVATE", "vin": "PRIVATE"})
    assert evidence["kw"] == 162
    assert evidence["ccm"] == 1969
    assert evidence["power_kw"] is None
    assert "plate" not in evidence and "vin" not in evidence


def test_changed_accepted_selection_keeps_gains_losses_and_changed_identities():
    def change(key, before, after):
        return {"row_key": key, "before": {"terminal": before}, "after": {"terminal": after}}
    report = {"comparison": {"changed_records": [
        change("gain", "review_required", "resolved"), change("loss", "resolved", "review_required"),
        change("identity", "resolved", "resolved"), change("review", "review_required", "provisional"),
    ]}}
    assert set(changed_accepted_records(report)) == {"gain", "loss", "identity"}


def test_evidence_replay_emits_actual_candidates_and_clears_old_decision_cache():
    evaluator = TecDocDryRunEvaluator((VehicleCandidate("1", "VOLVO", "V70"),))
    raw = {"manufacturer": "VOLVO", "brand": "VOLVO V70", "vehicle_type": "PB"}
    rules = load_translation_rule_set(REVIEWED_RULE_SET_VERSION)
    first = capture_evaluation(evaluator, raw, source_id=1, rules=rules, manufacturers={})
    second = capture_evaluation(evaluator, raw, source_id=1, rules=rules, manufacturers={})
    assert first == second
    assert first["attempts"]
    assert first["attempts"][0]["candidates"][0]["candidate_reference"] == "1"


def test_activated_report_requires_and_verifies_reviewed_context_manifest():
    root = Path(__file__).resolve().parents[3]
    manifest = root / "ingestion/reviewed_context_policies/volvo_bodywork_reviewed_v1_20260830.json"
    report = {
        "activated_context_rule_count": 47,
        "context_policy_version": "volvo-bodywork-reviewed-v1-20260830",
        "context_policy_digest": "079d4b4381bcc8050c17bb99b7f9cae0b36492c091f27311ff13724933209d14",
    }
    with pytest.raises(ValueError, match="requires its reviewed policy manifest"):
        load_context_policy_for_report(report, manifest=None, version=None, sha256=None)
    policy = load_context_policy_for_report(
        report, manifest=manifest, version="volvo-bodywork-reviewed-v1-20260830",
        sha256="4acdee26fb88c639fa29cae914e7d24bc067b963e0be65a4718a5036d2ea522a",
    )
    assert policy.version == report["context_policy_version"]
    assert len(policy.rules) == 47
