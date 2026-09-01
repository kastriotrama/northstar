import pytest

from scripts.audit_mixed_fuel_remaining_review import (
    ELIGIBILITY_GAIN,
    IDENTITY_REJECTION,
    PROVISIONAL_DOWNGRADE,
    RESOLVED_DOWNGRADE,
    audit_review,
    classify_remaining_case,
)


def _item(before: str, after: str, old: str | None, new: str | None) -> dict:
    return {"change": {"before": {"terminal": before, "top_candidate_reference": old},
                       "after": {"terminal": after, "top_candidate_reference": new}}}


def test_classifies_the_four_remaining_review_cohorts() -> None:
    assert classify_remaining_case(_item("provisional", "resolved", "K1", "K1")) == ELIGIBILITY_GAIN
    assert classify_remaining_case(_item("resolved", "review_required", "K2", "K2")) == RESOLVED_DOWNGRADE
    assert classify_remaining_case(_item("provisional", "review_required", "K3", "K3")) == PROVISIONAL_DOWNGRADE
    assert classify_remaining_case(_item("review_required", "review_required", "K4", "K5")) == IDENTITY_REJECTION
    assert classify_remaining_case(_item("hard_conflict", "review_required", "K1", "K1")) is None
    with pytest.raises(ValueError, match="outside"):
        classify_remaining_case(_item("resolved", "resolved", "K1", "K2"))


def test_audit_requires_exact_complete_counts() -> None:
    items = [
        _item("provisional", "resolved", "K1", "K1"),
        _item("resolved", "review_required", "K2", "K2"),
        _item("provisional", "review_required", "K3", "K3"),
        _item("review_required", "review_required", "K4", "K5"),
        _item("hard_conflict", "review_required", "K6", "K6"),
    ]
    manifest = {
        "version": "review-v1", "status": "reviewed", "source_packet_sha256": "abc",
        "approved_for_frozen_holdout": True, "runtime_activation": False,
        "direct_match_identity_approval": False, "independently_adjudicated": False,
        "groups": [
            {"group_id": ELIGIBILITY_GAIN, "expected_count": 1},
            {"group_id": RESOLVED_DOWNGRADE, "expected_count": 1},
            {"group_id": PROVISIONAL_DOWNGRADE, "expected_count": 1},
            {"group_id": IDENTITY_REJECTION, "expected_count": 1},
        ],
    }
    result = audit_review({"count": 5, "items": items}, manifest, packet_sha256="abc")
    assert result["reviewed_case_count"] == 4
    assert result["hard_conflict_cases_excluded"] == 1
    assert result["approved_for_frozen_holdout"] is True
    with pytest.raises(ValueError, match="counts differ"):
        manifest["groups"][0]["expected_count"] = 2
        audit_review({"count": 5, "items": items}, manifest, packet_sha256="abc")
