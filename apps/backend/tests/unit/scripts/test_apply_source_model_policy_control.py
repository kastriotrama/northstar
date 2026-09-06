from copy import deepcopy

import pytest

from scripts.apply_source_model_policy_control import apply_replacements


def _report() -> dict:
    return {
        "count": 2,
        "counts": {"hard_conflict": 1, "review_required": 1},
        "reason_counts": {"old": 2},
        "repair_diagnostics": {"stale": True},
        "records": [
            {"row_key": "one", "terminal": "hard_conflict", "reason_codes": ["old"]},
            {"row_key": "two", "terminal": "review_required", "reason_codes": ["old"]},
        ],
    }


def test_replaces_only_replayed_rows_and_reconciles_counts() -> None:
    result = apply_replacements(
        _report(),
        {"one": {"row_key": "one", "terminal": "review_required", "reason_codes": ["new"]}},
        policy_version="policy-v1", policy_digest="digest", base_sha256="base",
    )
    assert result["counts"] == {"review_required": 2}
    assert result["reason_counts"] == {"new": 1, "old": 1}
    assert result["selectively_replayed_source_model_rows"] == 1
    assert "repair_diagnostics" not in result
    assert result["records"][1] == _report()["records"][1]


def test_rejects_replacements_outside_the_pinned_cohort() -> None:
    report = deepcopy(_report())
    with pytest.raises(ValueError, match="outside the base report"):
        apply_replacements(
            report,
            {"other": {"row_key": "other", "terminal": "resolved", "reason_codes": ["new"]}},
            policy_version="policy-v1", policy_digest="digest", base_sha256="base",
        )
