from scripts.audit_match_promotion_freshness import summarize_promotion_freshness


def test_summarizes_fresh_stale_and_pending_promotions() -> None:
    heads = [
        {"decision_id": "one", "route": "resolved", "selected_candidate_reference": "K1"},
        {"decision_id": "two", "route": "resolved", "selected_candidate_reference": "K2"},
        {"decision_id": "three", "route": "resolved", "selected_candidate_reference": "K3"},
        {"decision_id": "four", "route": "resolved", "selected_candidate_reference": "K4"},
    ]
    changes = [
        {
            "decision_id": "two",
            "current_terminal": "review_required",
            "current_candidate_reference": "K2",
        },
        {
            "decision_id": "three",
            "current_terminal": "resolved",
            "current_candidate_reference": "K33",
        },
    ]
    aliases = [
        {"decision_id": "one", "target_references": ["K1"]},
        {"decision_id": "two", "target_references": ["K2"]},
        {"decision_id": "three", "target_references": ["K3"]},
    ]

    assert summarize_promotion_freshness(heads, changes, aliases) == {
        "decision_heads": 4,
        "replayed_terminal_counts": {"resolved": 3, "review_required": 1},
        "graph_aliases": 3,
        "graph_alias_states": {
            "fresh_resolved": 1,
            "stale_target_mismatch": 1,
            "stale_terminal:review_required": 1,
        },
        "fresh_aliases": 1,
        "stale_aliases_requiring_retirement": 2,
        "resolved_decisions_pending_promotion": 2,
        "unknown_changed_decisions": 0,
    }


def test_counts_unknown_and_ambiguous_graph_state_as_stale() -> None:
    heads = [
        {"decision_id": "one", "route": "resolved", "selected_candidate_reference": "K1"},
    ]
    aliases = [
        {"decision_id": "one", "target_references": ["K1", "K2"]},
        {"decision_id": "missing", "target_references": ["K1"]},
    ]
    changes = [{"decision_id": "also-missing", "current_terminal": "resolved"}]

    summary = summarize_promotion_freshness(heads, changes, aliases)

    assert summary["graph_alias_states"] == {
        "stale_target_cardinality": 1,
        "unknown_decision": 1,
    }
    assert summary["stale_aliases_requiring_retirement"] == 2
    assert summary["unknown_changed_decisions"] == 1
