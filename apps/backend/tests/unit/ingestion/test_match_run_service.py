from uuid import uuid4

import pytest

from ingestion.match_run_repository import MatchRunMode, MatchRunPins
from ingestion.match_run_service import MatchSourceRecord, run_dry_match_audit


def _pins(*, mode: MatchRunMode = MatchRunMode.DRY_RUN) -> MatchRunPins:
    return MatchRunPins(uuid4(), "TS", "v1", "batch", 1, "rules", "catalog", "policy", "sha", mode)


def test_dry_loop_rejects_persist_mode_before_database_access() -> None:
    with pytest.raises(ValueError, match="requires dry_run"):
        run_dry_match_audit(
            None,  # type: ignore[arg-type]
            pins=_pins(mode=MatchRunMode.PERSIST),
            fetch_page=lambda _after, _limit: (),
            evaluate_record=lambda _record: "resolved",
        )


def test_source_record_requires_positive_identity() -> None:
    with pytest.raises(ValueError, match="positive"):
        MatchSourceRecord(0, {})


def test_page_size_is_bounded_before_database_access() -> None:
    with pytest.raises(ValueError, match="page_size"):
        run_dry_match_audit(
            None,  # type: ignore[arg-type]
            pins=_pins(),
            fetch_page=lambda _after, _limit: (),
            evaluate_record=lambda _record: "resolved",
            page_size=0,
        )
