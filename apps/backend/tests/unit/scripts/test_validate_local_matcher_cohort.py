import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.validate_local_matcher_cohort import (
    compare_catalog_activation_reports,
    compare_reports,
    digest,
    write_private_json,
)


def report() -> dict:
    return {
        "source_digest": "s", "catalog_digest": "c", "rules_digest": "r",
        "alignment_version": "unpinned-legacy", "count": 1,
        "counts": {"review_required": 1}, "reason_counts": {},
        "records": [{"row_key": "key", "terminal": "review_required", "top_candidate_reference": "1"}],
    }


@pytest.mark.parametrize("pin", ["source_digest", "catalog_digest", "rules_digest", "count", "alignment_version"])
def test_changed_inputs_are_not_a_valid_comparison(pin: str) -> None:
    before = report()
    after = deepcopy(before)
    after[pin] = "different"
    with pytest.raises(ValueError, match="comparison inputs differ|incomplete cohort accounting"):
        compare_reports(before, after)


def test_comparison_reports_changed_ktype_even_when_route_is_unchanged() -> None:
    before = report()
    after = deepcopy(before)
    after["records"][0]["top_candidate_reference"] = "2"
    result = compare_reports(before, after)
    assert result["transitions"] == {"review_required->review_required": 1}
    assert len(result["changed_records"]) == 1
    assert result["independently_adjudicated"] is False


def test_output_is_private_and_cannot_overwrite_prior_evidence(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    write_private_json(path, report())
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text()) == report()
    with pytest.raises(FileExistsError):
        write_private_json(path, {})


def test_digests_are_stable_for_unordered_sets() -> None:
    assert digest({"a", "b"}) == digest(frozenset({"b", "a"}))


def test_truncated_report_is_rejected() -> None:
    before = report()
    before["records"] = []
    with pytest.raises(ValueError, match="incomplete cohort accounting"):
        compare_reports(before, report())


def test_catalog_activation_comparison_allows_only_catalog_pin_to_change() -> None:
    before = report()
    before.update(
        catalog_version="v5",
        source_prefix="source-",
        rule_version="rules-v1",
        context_policy_version="context-v1",
        source_model_policy_version="models-v1",
    )
    after = deepcopy(before)
    after["catalog_version"] = "v6"
    after["catalog_digest"] = "new-catalog"
    after["records"][0] = {
        **after["records"][0],
        "terminal": "resolved",
        "top_candidate_reference": "42",
    }
    after["counts"] = {"resolved": 1}

    comparison = compare_catalog_activation_reports(before, after)

    assert comparison["transitions"] == {"review_required->resolved": 1}
    assert comparison["changed_record_count"] == 1
    assert comparison["selected_identity_change_count"] == 1


def test_catalog_activation_comparison_rejects_source_change() -> None:
    before = report()
    before.update(
        catalog_version="v5",
        source_prefix="source-",
        rule_version="rules-v1",
        context_policy_version="context-v1",
        source_model_policy_version="models-v1",
    )
    after = deepcopy(before)
    after["catalog_version"] = "v6"
    after["catalog_digest"] = "new-catalog"
    after["source_digest"] = "changed-source"

    with pytest.raises(ValueError, match="source_digest"):
        compare_catalog_activation_reports(before, after)
