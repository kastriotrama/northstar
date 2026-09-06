import pytest

from scripts.validate_frozen_matcher_holdout import (
    assess_holdout,
    validate_acceptance_pins,
)


def _row(before: str, after: str, old: str | None, new: str | None,
         reasons: tuple[str, ...] = ()) -> dict:
    return {
        "before": {"terminal": before, "top_candidate_reference": old, "reason_codes": ()},
        "after": {"terminal": after, "top_candidate_reference": new, "reason_codes": reasons},
    }


CRITERIA = {
    "require_complete_accounting": True,
    "maximum_new_hard_conflicts": 0,
    "maximum_changed_resolved_identities": 0,
    "maximum_unsafe_resolution_gains": 0,
    "maximum_resolved_conflict_reasons": 0,
}


def test_accepts_stable_provisional_gain_and_conservative_downgrades() -> None:
    records = [
        _row("provisional", "resolved", "K1", "K1"),
        _row("resolved", "review_required", "K2", "K2"),
        _row("review_required", "review_required", "K3", "K4"),
    ]
    result = assess_holdout(records, CRITERIA, expected_count=3)
    assert result["passed"] is True
    assert result["unresolved_candidate_changes"] == 1


def test_rejects_new_conflicts_changed_resolution_and_unsafe_gain() -> None:
    records = [
        _row("review_required", "hard_conflict", "K1", "K2"),
        _row("provisional", "resolved", "K2", "K3"),
        _row("review_required", "resolved", "K4", "K4"),
        _row("resolved", "resolved", "K5", "K5", ("conflict:fuels",)),
    ]
    result = assess_holdout(records, CRITERIA, expected_count=4)
    assert result["passed"] is False
    assert set(result["failed_criteria"]) == {
        "maximum_new_hard_conflicts",
        "maximum_changed_resolved_identities",
        "maximum_unsafe_resolution_gains",
        "maximum_resolved_conflict_reasons",
    }


def test_requires_complete_accounting() -> None:
    result = assess_holdout([], CRITERIA, expected_count=1)
    assert result["passed"] is False
    assert result["failed_criteria"] == ["incomplete_accounting"]


def test_acceptance_requires_every_frozen_run_pin() -> None:
    acceptance = {
        "rule_version": "rules-v1",
        "rules_digest": "rules-digest",
        "context_policy_version": "context-v1",
        "context_policy_payload_sha256": "context-sha",
        "context_policy_digest": "context-digest",
        "source_model_policy_version": "models-v1",
        "source_model_policy_payload_sha256": "models-sha",
        "source_model_policy_digest": "models-digest",
        "expected_code_digest": "code-sha",
    }
    validate_acceptance_pins(
        acceptance,
        rule_version="rules-v1",
        rules_digest="rules-digest",
        context_policy_version="context-v1",
        context_policy_sha256="context-sha",
        context_policy_digest="context-digest",
        source_model_policy_version="models-v1",
        source_model_policy_sha256="models-sha",
        source_model_policy_digest="models-digest",
        code_digest="code-sha",
    )
    with pytest.raises(ValueError, match="expected_code_digest"):
        validate_acceptance_pins(
            acceptance,
            rule_version="rules-v1",
            rules_digest="rules-digest",
            context_policy_version="context-v1",
            context_policy_sha256="context-sha",
            context_policy_digest="context-digest",
            source_model_policy_version="models-v1",
            source_model_policy_sha256="models-sha",
            source_model_policy_digest="models-digest",
            code_digest="different",
        )
