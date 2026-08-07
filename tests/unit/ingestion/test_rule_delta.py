from datetime import UTC, datetime
from pathlib import Path

import pytest

from ingestion.rule_delta import (
    RuleDeltaError,
    RuleVersionSnapshot,
    compute_rule_delta,
    export_rule_delta,
    render_rule_delta_sql,
)


def _snapshot(
    version: str,
    overrides: dict[str, dict[str, object]],
    *,
    note: str = "Reviewed activation",
) -> RuleVersionSnapshot:
    return RuleVersionSnapshot(
        version=version,
        base_rule_version="ts-translation-v4",
        overrides=overrides,
        activation_note=note,
        activated_at=datetime(2026, 8, 6, 12, 30, 45, 123456, tzinfo=UTC),
    )


def test_rule_delta_contains_added_and_changed_definitions() -> None:
    baseline = _snapshot("rules-v1", {"A": {"decision": "proposed"}})
    target = _snapshot(
        "rules-v2",
        {
            "A": {"decision": "accepted"},
            "B": {"kind": "manufacturer_entity", "canonical_name": "Volvo"},
        },
    )

    assert compute_rule_delta(baseline, target) == target.overrides


def test_rule_delta_rejects_removals_and_empty_changes() -> None:
    baseline = _snapshot("rules-v1", {"A": {"decision": "accepted"}})

    with pytest.raises(RuleDeltaError, match="removes_existing_definitions"):
        compute_rule_delta(baseline, _snapshot("rules-v2", {}))
    with pytest.raises(RuleDeltaError, match="has_no_changes"):
        compute_rule_delta(baseline, _snapshot("rules-v2", baseline.overrides))


def test_rendered_sql_is_deterministic_exact_and_guarded() -> None:
    baseline = _snapshot("rules-v1", {"A": {"decision": "proposed"}})
    target = _snapshot(
        "rules-v2",
        {
            "B": {
                "canonical_name": "Škoda",
                "change_note": "Stakeholder's reviewed value",
            },
            "A": {"decision": "accepted"},
        },
        note="Stakeholder's approved activation",
    )

    first = render_rule_delta_sql(baseline, target)
    second = render_rule_delta_sql(baseline, target)

    assert first == second
    assert "\\set ON_ERROR_STOP on" in first
    assert "LOCK TABLE core.translation_rule_versions" in first
    assert "Refusing activation: expected latest version" in first
    assert "exists with conflicting content" in first
    assert "expected_activated_at" in first
    assert "2026-08-06T12:30:45.123456+00:00" in first
    assert "Stakeholder''s approved activation" in first
    assert '"A":{"decision":"accepted"}' in first
    assert '"canonical_name":"Škoda"' in first
    assert "Target SHA-256:" in first


def test_export_rejects_a_non_sql_destination() -> None:
    with pytest.raises(RuleDeltaError, match="output_must_be_sql"):
        export_rule_delta(  # type: ignore[arg-type]
            None,
            baseline_version="rules-v1",
            target_version=None,
            output_path=Path("rules.txt"),
        )
