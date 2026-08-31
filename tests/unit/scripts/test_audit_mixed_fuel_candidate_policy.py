from copy import deepcopy

import pytest

from scripts.audit_mixed_fuel_candidate_policy import audit_candidate_policy


def _change(before: dict, after: dict) -> dict:
    return {"row_key": "row-1", "before": before, "after": after}


def _inputs() -> tuple[dict, dict, dict, dict, dict]:
    before = {
        "terminal": "hard_conflict", "top_candidate_reference": "K1",
        "reason_codes": ["conflict:fuels"], "row_key": "row-1",
    }
    after = {
        "terminal": "provisional", "top_candidate_reference": "K1",
        "reason_codes": ["match:scored"], "row_key": "row-1",
    }
    change = _change(before, after)
    comparison = {
        "count": 20_000,
        "changed_records": [{"row_key": "row-1", "before": before, "after": after}],
        "transitions": {"hard_conflict->provisional": 1},
        "selected_identity_change_count": 0,
        "before_counts": {"hard_conflict": 1}, "after_counts": {"provisional": 1},
        "before_catalog_version": "v5", "before_catalog_digest": "v5-sha",
        "after_catalog_version": "v6", "after_catalog_digest": "v6-sha",
        "source_digest": "source", "rules_digest": "rules",
        "alignment_version": "alignment",
    }
    packet = {"count": 1, "items": [{"change": change}]}
    hard = {
        "version": "hard-v1", "status": "reviewed", "source_packet_sha256": "packet",
        "runtime_activation": False, "match_identity_approved": False,
        "independently_adjudicated": False,
        "groups": [{
            "group_id": "approve-overlap-removes-false-fuel-conflict",
            "decision": "approve_v6_conflict_removal_only", "expected_count": 1,
        }],
    }
    remaining = {
        "version": "remaining-v1", "status": "reviewed",
        "source_packet_sha256": "packet", "approved_for_frozen_holdout": True,
        "runtime_activation": False, "direct_match_identity_approval": False,
        "independently_adjudicated": False, "groups": [],
    }
    acceptance = {"version": "accept-v1", "status": "approved_before_unblinding"}
    return comparison, packet, hard, remaining, acceptance


def test_approves_only_an_exact_fully_reviewed_control() -> None:
    result = audit_candidate_policy(*_inputs(), packet_sha256="packet")
    assert result["status"] == "approved_for_frozen_holdout"
    assert result["reviewed_change_count"] == 1
    assert result["runtime_activation"] is False


def test_rejects_a_control_that_differs_from_the_review_packet() -> None:
    inputs = list(_inputs())
    comparison = deepcopy(inputs[0])
    comparison["changed_records"][0]["after"]["terminal"] = "resolved"
    inputs[0] = comparison
    with pytest.raises(ValueError, match="differs from reviewed evidence"):
        audit_candidate_policy(*inputs, packet_sha256="packet")


def test_accepts_exact_reviewed_peugeot_repair_removed_from_final_changes() -> None:
    comparison, packet, hard, remaining, acceptance = _inputs()
    old = {
        "terminal": "review_required", "top_candidate_reference": "000121650",
        "reason_codes": [], "row_key": "row-1",
    }
    rejected = {
        "terminal": "hard_conflict", "top_candidate_reference": "000156880",
        "reason_codes": ["conflict:power_kw"], "row_key": "row-1",
    }
    packet["items"][0]["change"] = _change(old, rejected)
    comparison["changed_records"] = []
    comparison["selected_identity_change_count"] = 0
    comparison["source_model_policy_version"] = "models-v1"
    comparison["source_model_policy_digest"] = "models-digest"
    comparison["source_model_policy_repair_rows"] = [
        {"row_key": "row-1", "before": old, "after": deepcopy(old)}
    ]
    hard["groups"][0] = {
        "group_id": "reject-peugeot-3008-iii-hybrid-hard-conflict",
        "decision": "reject_v6_candidate_change_and_hard_conflict",
        "expected_count": 1,
    }
    acceptance.update(
        source_model_policy_version="models-v1",
        source_model_policy_digest="models-digest",
    )

    result = audit_candidate_policy(
        comparison, packet, hard, remaining, acceptance, packet_sha256="packet"
    )
    assert result["suppressed_rejected_hard_conflicts"] == 1
    assert result["final_control_change_count"] == 0
