import pytest

from scripts.freeze_matcher_holdout import freeze_groups, leakage_tokens


def test_shared_approval_revision_and_transitive_vehicle_links_cannot_leak():
    development = [(1, {"plate": "A", "vin": "V1", "eeg_type_approval": "e1*2007/46*1000*01"})]
    window = [(2, {"plate": "B", "eeg_type_approval": "e1*2007/46*1000*02"}),
              (3, {"plate": "B", "vin": "V2"}), (4, {"plate": "C", "vin": "V2"}),
              (5, {"plate": "D", "vin": "V5", "fuel1": "1"})]
    result = freeze_groups(development, window)
    assert result["eligible_count"] == 1
    assert result["excluded"] == {"linked_to_development": 3}
    assert result["rows"][0]["source_record_id"] == 5
    assert result["scored"] is False


def test_holdout_keeps_duplicates_in_one_group_and_excludes_ungroupable_rows():
    result = freeze_groups([], [(1, {"vin": "same"}), (2, {"vin": "same"}), (3, {})])
    assert result["eligible_count"] == 2 and result["group_count"] == 1
    assert result["excluded"] == {"no_grouping_evidence": 1}
    assert result["rows"][0]["group_key"] == result["rows"][1]["group_key"]


def test_freeze_is_deterministic_and_private_and_rejects_overlapping_ids():
    assert leakage_tokens({"plate": "ABC-123"}) == leakage_tokens({"plate": "abc123"})
    a = [(1, {"vin": "V1", "variant": "A", "version": "B"})]
    b = [(2, {"vin": "V2", "variant": "A", "version": "B"})]
    assert freeze_groups(a, b) == freeze_groups(a, b)
    assert freeze_groups(a, b)["eligible_count"] == 0
    with pytest.raises(ValueError, match="overlap"):
        freeze_groups(a, a)
