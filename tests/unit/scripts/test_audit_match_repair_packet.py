import pytest

from scripts.audit_match_repair_packet import audit_packet


def packet():
    after = {"terminal": "resolved", "top_candidate_reference": "1"}
    return {"count": 1, "items": [{
        "row_key": "key", "change": {"before": {"terminal": "review_required", "top_candidate_reference": None}, "after": after},
        "evaluation": after,
        "attempts": [{"query": {"year": 2020}, "routing": {"route": "resolved"}, "candidates": [{
            "candidate_reference": "1", "evidence": {"conflicting_fields": [], "phonetic_match": False,
                                                       "matched_fields": ["model", "year"], "match_scope": "exact_manufacturer"},
        }]}],
    }]}


def test_audit_prioritizes_weak_evidence_without_inventing_verdicts():
    result = audit_packet(packet())
    assert result["changes"] == {"gained_resolution": 1}
    assert result["cases"][0]["priority"] == "high"
    assert result["cases"][0]["verdict"] is None
    assert result["cases"][0]["observed_engine_code"] is False
    assert result["independently_adjudicated"] is False


@pytest.mark.parametrize("field,value", [("conflicting_fields", ["year"]), ("phonetic_match", True),
                                         ("matched_fields", ["model", "model_partial"]), ("match_scope", "global")])
def test_audit_rejects_accepted_gate_violations(field, value):
    data = packet()
    data["items"][0]["attempts"][0]["candidates"][0]["evidence"][field] = value
    with pytest.raises(ValueError):
        audit_packet(data)


def test_audit_rejects_incomplete_duplicate_and_unsupported_acceptance():
    data = packet()
    data["count"] = 2
    with pytest.raises(ValueError, match="incomplete"):
        audit_packet(data)
    data["items"] *= 2
    with pytest.raises(ValueError, match="duplicated"):
        audit_packet(data)
    data = packet()
    data["items"][0]["attempts"] = []
    with pytest.raises(ValueError, match="no supporting"):
        audit_packet(data)


def test_loss_remains_explicit_and_high_priority():
    data = packet()
    row = data["items"][0]
    row["change"]["before"], row["change"]["after"] = row["change"]["after"], row["change"]["before"]
    row["evaluation"] = row["change"]["after"]
    result = audit_packet(data)
    assert result["changes"] == {"lost_resolution": 1}
    assert result["cases"][0]["priority"] == "high"
