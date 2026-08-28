import hashlib
import json
from dataclasses import replace

import pytest

from ingestion.context_comparison import (
    ContextComparisonPolicy,
    ReviewedContextRule,
    reviewed_context_policy,
)
from ingestion.fuzzy_matching import (
    FuzzyVehicleMatcher,
    ManufacturerCandidateIndex,
    VehicleCandidate,
    VehicleMatchQuery,
)
from ingestion.match_run_service import MatchSourceRecord
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator


def rule(**changes):
    return replace(ReviewedContextRule(
        "TEST-BODY", "bodywork", "Test", "Model", "estate", ("suv",),
        (("body_code", "AC"),), "synthetic-test-reviewer", "synthetic-test-evidence",
    ), **changes)


@pytest.mark.parametrize(("source", "target", "state"), [
    ("estate", {"estate"}, "equivalent"), ("estate", {"suv"}, "conflicting"),
    (None, {"suv"}, "unknown"), ("estate", set(), "unknown"),
])
def test_default_policy_never_invents_compatibility(source, target, state):
    result = ContextComparisonPolicy().compare(
        field="bodywork", source_value=source, candidate_values=frozenset(target),
        manufacturer="Test", model="Model",
    )
    assert result.state == state


@pytest.mark.parametrize(("make", "model", "raw", "state"), [
    ("Test", "Model", "AC", "compatible"), ("Other", "Model", "AC", "conflicting"),
    ("Test", "Other", "AC", "conflicting"), ("Test", "Model", "AB", "conflicting"),
])
def test_compatibility_requires_all_scope_conditions(make, model, raw, state):
    result = ContextComparisonPolicy(rules=(rule(),)).compare(
        field="bodywork", source_value="estate", candidate_values=frozenset({"suv"}),
        manufacturer=make, model=model, source_evidence=(("body_code", raw),),
    )
    assert result.state == state


def test_compatibility_is_directional_and_not_positive_evidence():
    policy = ContextComparisonPolicy(rules=(rule(),))
    candidate = VehicleCandidate("1", "Test", "Model", bodyworks=frozenset({"suv"}))
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((candidate,)), context_policy=policy)
    query = VehicleMatchQuery("Model", manufacturer="Test", bodywork="estate",
                              source_context=(("body_code", "AC"),))
    score = matcher._score(query, candidate)
    assert "bodywork_compatible_not_confirmed" in score.missing_fields
    assert "bodywork" not in score.matched_fields
    assert score.context_effect == 0
    assert score.context_rule_ids == ("TEST-BODY",)
    assert score.context_policy_digest == policy.content_digest
    reverse = policy.compare(field="bodywork", source_value="suv",
                             candidate_values=frozenset({"estate"}), manufacturer="Test",
                             model="Model", source_evidence=(("body_code", "AC"),))
    assert reverse.state == "conflicting"


def test_verified_non_4wd_set_never_selects_front_or_rear_drive():
    policy = ContextComparisonPolicy(rules=(rule(
        field="drive_type", source_value="", allowed_values=("fwd", "rwd"),
        source_conditions=(("is_4wd", "0"),),
    ),))
    for drive in ("fwd", "rwd", "awd"):
        result = policy.compare(field="drive_type", source_value=None,
                                candidate_values=frozenset({drive}), manufacturer="Test",
                                model="Model", source_evidence=(("is_4wd", "0"),))
        assert result.state == ("conflicting" if drive == "awd" else "compatible")
    assert policy.compare(field="drive_type", source_value=None,
                          candidate_values=frozenset({"fwd"}), manufacturer="Test",
                          model="Model").state == "unknown"


def test_ambiguous_rules_fail_closed():
    policy = ContextComparisonPolicy(rules=(rule(), rule(rule_id="different", allowed_values=("mpv",))))
    assert policy.compare(field="bodywork", source_value="estate",
                          candidate_values=frozenset({"suv"}), manufacturer="Test",
                          model="Model", source_evidence=(("body_code", "AC"),)).state == "conflicting"


@pytest.mark.parametrize("changes", [
    {"reviewed_by": ""}, {"evidence_ref": ""}, {"manufacturer": ""},
    {"model": ""}, {"allowed_values": ()}, {"source_conditions": ()},
    {"source_conditions": (("plate", "ABC123"),)},
])
def test_rules_require_explicit_review_and_scope(changes):
    with pytest.raises(ValueError):
        rule(**changes)


def test_evaluator_context_cache_does_not_leak_across_raw_values():
    candidate = VehicleCandidate("1", "Test", "Model", bodyworks=frozenset({"suv"}))
    evaluator = TecDocDryRunEvaluator((candidate,), context_policy=ContextComparisonPolicy(rules=(rule(),)))
    def evaluate(code):
        return evaluator.evaluate(MatchSourceRecord(1, {
            "normalized": {"manufacturer": "Test", "model_family": "Model", "bodywork_form": "estate"},
            "source_evidence": {"body_code": code},
        }))
    assert "context_conflict:bodywork" not in evaluate("AC").reason_codes
    assert "context_conflict:bodywork" in evaluate("AB").reason_codes


def test_policy_hash_changes_when_reviewed_content_changes():
    assert ContextComparisonPolicy(rules=(rule(),)).content_digest != ContextComparisonPolicy(
        rules=(rule(allowed_values=("mpv",)),)
    ).content_digest


def test_manifest_requires_approval_version_and_exact_content_pin():
    payload = {"version": "test-v1", "status": "approved", "rules": []}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    assert reviewed_context_policy(payload, expected_version="test-v1", expected_digest=digest).rules == ()
    with pytest.raises(ValueError, match="checksum"):
        reviewed_context_policy(payload, expected_version="test-v1", expected_digest="wrong")
    with pytest.raises(ValueError, match="unknown or unapproved"):
        reviewed_context_policy(payload, expected_version="unknown", expected_digest=digest)
    payload["status"] = "proposed"
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    with pytest.raises(ValueError, match="unknown or unapproved"):
        reviewed_context_policy(payload, expected_version="test-v1", expected_digest=digest)


def test_reviewed_manifest_loads_real_scoped_rule_and_rejects_proposed_row():
    payload = {"version": "test-v2", "status": "approved", "rules": [{
        "status": "approved", "rule_id": "test", "field": "bodywork", "manufacturer": "Test",
        "model": "Model", "source_value": "estate", "allowed_values": ["suv"],
        "source_conditions": {"body_code": "AC"}, "reviewed_by": "test-reviewer",
        "evidence_ref": "synthetic-fixture",
    }]}
    def load():
        checksum = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return reviewed_context_policy(payload, expected_version="test-v2", expected_digest=checksum)
    policy = load()
    assert policy.compare(field="bodywork", source_value="estate", candidate_values=frozenset({"suv"}),
                          manufacturer="Test", model="Model", source_evidence=(("body_code", "AC"),)).state == "compatible"
    payload["rules"][0]["status"] = "proposed"
    with pytest.raises(ValueError, match="unapproved rules"):
        load()


def test_raw_drive_constraint_cannot_override_exact_conflicting_drive_fact():
    policy = ContextComparisonPolicy(rules=(rule(
        field="drive_type", source_value="", allowed_values=("fwd", "rwd"),
        source_conditions=(("is_4wd", "0"),),
    ),))
    assert policy.compare(field="drive_type", source_value="awd", candidate_values=frozenset({"fwd"}),
                          manufacturer="Test", model="Model", source_evidence=(("is_4wd", "0"),)).state == "conflicting"
