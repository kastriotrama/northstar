import pytest

from scripts.audit_mixed_fuel_hard_conflict_review import (
    MINI_REJECTION,
    OVERLAP_REMOVAL,
    PEUGEOT_REJECTION,
    audit_review,
    classify_hard_conflict_case,
)


def _item(before: str, after: str, old: str, new: str, old_reasons: list[str], new_reasons: list[str]) -> dict:
    return {"change": {"before": {"terminal": before, "top_candidate_reference": old,
                                    "reason_codes": old_reasons},
                       "after": {"terminal": after, "top_candidate_reference": new,
                                  "reason_codes": new_reasons}}}


def test_classifies_only_the_three_reviewed_hard_conflict_cohorts() -> None:
    assert classify_hard_conflict_case(_item(
        "hard_conflict", "provisional", "K1", "K1", ["conflict:fuels"], []
    )) == OVERLAP_REMOVAL
    assert classify_hard_conflict_case(_item(
        "review_required", "hard_conflict", "000121650", "000156880", [],
        ["conflict:power_kw"],
    )) == PEUGEOT_REJECTION
    assert classify_hard_conflict_case(_item(
        "hard_conflict", "hard_conflict", "000156380", "000100572",
        ["conflict:year"], ["conflict:fuels"],
    )) == MINI_REJECTION
    assert classify_hard_conflict_case(_item(
        "review_required", "resolved", "K1", "K1", [], []
    )) is None
    with pytest.raises(ValueError, match="outside"):
        classify_hard_conflict_case(_item(
            "hard_conflict", "hard_conflict", "other", "different", [],
            ["conflict:year"],
        ))


def test_audit_requires_exact_counts_and_never_approves_identity() -> None:
    items = [
        _item("hard_conflict", "provisional", "K1", "K1", ["conflict:fuels"], []),
        _item("review_required", "hard_conflict", "000121650", "000156880", [],
              ["conflict:power_kw"]),
        _item("hard_conflict", "hard_conflict", "000156380", "000100572",
              ["conflict:year"], ["conflict:fuels"]),
    ]
    manifest = {
        "version": "review-v1", "status": "reviewed", "source_packet_sha256": "abc",
        "runtime_activation": False, "match_identity_approved": False,
        "independently_adjudicated": False,
        "groups": [
            {"group_id": OVERLAP_REMOVAL, "expected_count": 1, "decision": "approve"},
            {"group_id": PEUGEOT_REJECTION, "expected_count": 1, "decision": "reject"},
            {"group_id": MINI_REJECTION, "expected_count": 1, "decision": "reject"},
        ],
    }
    result = audit_review({"count": 3, "items": items}, manifest, packet_sha256="abc")
    assert result["reviewed_case_count"] == 3
    assert result["transition_counts"] == {
        "hard_conflict->hard_conflict": 1,
        "hard_conflict->provisional": 1,
        "review_required->hard_conflict": 1,
    }
    assert result["match_identity_approved"] is False
    with pytest.raises(ValueError, match="counts differ"):
        manifest["groups"][0]["expected_count"] = 2
        audit_review({"count": 3, "items": items}, manifest, packet_sha256="abc")
