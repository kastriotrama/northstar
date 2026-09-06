from uuid import UUID

import pytest

from ingestion.review_queue import (
    CandidateMatch,
    enqueue_review_item,
    fetch_review_items_by_status,
    transition_review_item,
)

REVIEW_ID = UUID("509e25c2-ef15-45bd-9c50-37a26d71b0d0")


def test_candidate_match_normalizes_and_serializes() -> None:
    payload = CandidateMatch(
        candidate_reference="  MFR-source:opel  ",
        candidate_type=" Manufacturer ",
        confidence=0.82,
        evidence={"matched_fields": ["brand"]},
    ).to_payload()

    assert payload == {
        "candidate_reference": "MFR-source:opel",
        "candidate_type": "Manufacturer",
        "confidence": 0.82,
        "evidence": {"matched_fields": ["brand"]},
    }


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_candidate_rejects_invalid_confidence(confidence: float) -> None:
    with pytest.raises(ValueError, match="candidate confidence"):
        CandidateMatch("candidate", "Manufacturer", confidence).to_payload()


@pytest.mark.parametrize(
    ("source_table", "source_record_id", "message"),
    [
        ("core.enrichment_ledger", 1, "approved staging table"),
        ("staging.tecdoc_bad-name", 1, "approved staging table"),
        ("staging.transportstyrelsen_raw", 0, "at least 1"),
    ],
)
def test_enqueue_rejects_invalid_raw_reference_before_database_access(
    source_table: str,
    source_record_id: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        enqueue_review_item(
            connection=None,  # type: ignore[arg-type]
            review_id=REVIEW_ID,
            source_system="transportstyrelsen",
            source_table=source_table,
            source_record_id=source_record_id,
            reason_code="manufacturer_role_unknown",
        )


def test_enqueue_rejects_invalid_confidence_before_database_access() -> None:
    with pytest.raises(ValueError, match="confidence"):
        enqueue_review_item(
            connection=None,  # type: ignore[arg-type]
            review_id=REVIEW_ID,
            source_system="transportstyrelsen",
            source_table="staging.transportstyrelsen_raw",
            source_record_id=1,
            reason_code="powertrain_signal_conflict",
            confidence=1.01,
        )


def test_worklist_rejects_unknown_status_and_unbounded_limit() -> None:
    with pytest.raises(ValueError, match="status"):
        fetch_review_items_by_status(
            connection=None,  # type: ignore[arg-type]
            status="closed",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="limit"):
        fetch_review_items_by_status(
            connection=None,  # type: ignore[arg-type]
            status="pending",
            limit=1001,
        )


def test_terminal_transition_requires_decision_metadata() -> None:
    with pytest.raises(ValueError, match="resolved_by"):
        transition_review_item(
            connection=None,  # type: ignore[arg-type]
            item_id=1,
            status="resolved",
            resolution={"selected": "candidate"},
        )
    with pytest.raises(ValueError, match="resolution"):
        transition_review_item(
            connection=None,  # type: ignore[arg-type]
            item_id=1,
            status="rejected",
            resolved_by="reviewer",
        )


def test_non_terminal_transition_rejects_resolution_metadata() -> None:
    with pytest.raises(ValueError, match="non-terminal"):
        transition_review_item(
            connection=None,  # type: ignore[arg-type]
            item_id=1,
            status="in_review",
            resolved_by="reviewer",
        )
