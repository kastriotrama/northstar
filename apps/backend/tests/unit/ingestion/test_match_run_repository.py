from uuid import uuid4

import pytest

from ingestion.match_run_repository import (
    MatchRunCounts,
    MatchRunMode,
    MatchRunPins,
    increment_match_run_blocker_counts,
    increment_match_run_reason_counts,
)


def test_pins_require_all_versions_and_positive_source_count() -> None:
    with pytest.raises(ValueError, match="pinned text"):
        MatchRunPins(uuid4(), "TS", "v1", "batch", 10, "rules", "", "policy", "sha")
    with pytest.raises(ValueError, match="positive"):
        MatchRunPins(uuid4(), "TS", "v1", "batch", 0, "rules", "catalog", "policy", "sha")


def test_alignment_version_is_a_validated_keyword_only_pin() -> None:
    # Keyword-only, so a tenth positional argument still binds to mode and can
    # never be silently swallowed by the newer pin.
    positional = MatchRunPins(
        uuid4(), "TS", "v1", "batch", 10, "rules", "catalog", "policy", "sha",
        MatchRunMode.PERSIST,
    )
    assert positional.mode is MatchRunMode.PERSIST
    assert positional.alignment_version == "unpinned-legacy"

    pinned = MatchRunPins(
        uuid4(), "TS", "v1", "batch", 10, "rules", "catalog", "policy", "sha",
        alignment_version="align-v1",
    )
    assert pinned.alignment_version == "align-v1"

    with pytest.raises(ValueError, match="pinned text"):
        MatchRunPins(
            uuid4(), "TS", "v1", "batch", 10, "rules", "catalog", "policy", "sha",
            alignment_version="  ",
        )


def test_counts_are_balanced_by_construction() -> None:
    counts = MatchRunCounts(resolved=3, provisional=2, normalization_review=1)
    assert counts.processed == 6
    assert counts.as_dict()["normalization_review"] == 1
    with pytest.raises(ValueError, match="negative"):
        MatchRunCounts(failed=-1)


def test_dry_run_is_the_safe_default() -> None:
    pins = MatchRunPins(uuid4(), "TS", "v1", "batch", 10, "rules", "catalog", "policy", "sha")
    assert pins.mode is MatchRunMode.DRY_RUN


def test_reason_counts_reject_invalid_aggregates() -> None:
    with pytest.raises(ValueError, match="reason counts"):
        increment_match_run_reason_counts(
            object(),  # type: ignore[arg-type]
            operation_id=uuid4(),
            reason_counts={"": 1},
        )
    with pytest.raises(ValueError, match="reason counts"):
        increment_match_run_reason_counts(
            object(),  # type: ignore[arg-type]
            operation_id=uuid4(),
            reason_counts={"model_missing": 0},
        )


def test_blocker_counts_reject_invalid_aggregates() -> None:
    with pytest.raises(ValueError, match="blocker counts"):
        increment_match_run_blocker_counts(
            object(),  # type: ignore[arg-type]
            operation_id=uuid4(),
            blocker_counts={"": 1},
        )
