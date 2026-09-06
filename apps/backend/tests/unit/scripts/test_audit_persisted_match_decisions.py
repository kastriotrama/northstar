from scripts.audit_persisted_match_decisions import compare_decision, summarize_comparisons


def row() -> dict[str, object]:
    return {
        "decision_id": "00000000-0000-0000-0000-000000000001",
        "source_batch_id": "batch-1",
        "source_record_id": 1,
        "route": "resolved",
        "selected_candidate_reference": "100",
    }


def test_comparison_is_plate_free_and_preserves_identity_change() -> None:
    comparison = compare_decision(
        row(),
        {
            "terminal": "review_required",
            "top_candidate_reference": "200",
            "reason_codes": ("route:candidate_margin_below_gate",),
        },
    )
    assert comparison["same_candidate"] is False
    assert comparison["current_terminal"] == "review_required"
    assert comparison["current_candidate_reference"] == "200"
    assert "plate" not in comparison and "raw_record" not in comparison


def test_summary_counts_transitions_and_candidate_changes() -> None:
    first = compare_decision(
        row(),
        {
            "terminal": "resolved",
            "top_candidate_reference": "100",
            "reason_codes": ("route:resolved_threshold_met",),
        },
    )
    second = compare_decision(
        {**row(), "decision_id": "00000000-0000-0000-0000-000000000002"},
        {
            "terminal": "hard_conflict",
            "top_candidate_reference": "200",
            "reason_codes": ("conflict:fuels", "route:hard_conflict:fuels"),
        },
    )
    summary = summarize_comparisons([first, second])
    assert summary["transitions"] == {
        "resolved->hard_conflict": 1,
        "resolved->resolved": 1,
    }
    assert summary["same_candidate"] == 1
    assert summary["changed_candidate"] == 1
    assert summary["reason_counts"]["route:resolved_threshold_met"] == 1
