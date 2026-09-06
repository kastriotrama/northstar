from decimal import Decimal
from uuid import UUID

import pytest

from ingestion.ledger import fetch_entries_for_node, record_ledger_entry

VALID_NODE_ID = "VEH-01ARZ3NDEKTSV4RRFFQ69G5FAV"
VALID_EVENT_ID = UUID("a0a52e77-350f-4076-8b46-c068dd547807")


def test_record_rejects_invalid_node_id_before_touching_connection() -> None:
    with pytest.raises(ValueError, match="not a canonical node id"):
        record_ledger_entry(
            connection=None,  # type: ignore[arg-type]
            event_id=VALID_EVENT_ID,
            source="tecdoc",
            target_node_id="not-a-node-id",
            confidence=1.0,
        )


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_record_rejects_out_of_range_confidence(confidence: float) -> None:
    with pytest.raises(ValueError, match="confidence"):
        record_ledger_entry(
            connection=None,  # type: ignore[arg-type]
            event_id=VALID_EVENT_ID,
            source="tecdoc",
            target_node_id=VALID_NODE_ID,
            confidence=confidence,
        )


def test_record_rejects_empty_source() -> None:
    with pytest.raises(ValueError, match="source"):
        record_ledger_entry(
            connection=None,  # type: ignore[arg-type]
            event_id=VALID_EVENT_ID,
            source="  ",
            target_node_id=VALID_NODE_ID,
            confidence=1.0,
        )


def test_record_rejects_non_positive_nodes_benefited() -> None:
    with pytest.raises(ValueError, match="nodes_benefited"):
        record_ledger_entry(
            connection=None,  # type: ignore[arg-type]
            event_id=VALID_EVENT_ID,
            source="tecdoc",
            target_node_id=VALID_NODE_ID,
            confidence=1.0,
            nodes_benefited=0,
        )


def test_record_rejects_negative_cost() -> None:
    with pytest.raises(ValueError, match="cost_eur"):
        record_ledger_entry(
            connection=None,  # type: ignore[arg-type]
            event_id=VALID_EVENT_ID,
            source="tecdoc",
            target_node_id=VALID_NODE_ID,
            confidence=1.0,
            cost_eur=Decimal(-1),
        )


def test_fetch_rejects_invalid_node_id() -> None:
    with pytest.raises(ValueError, match="not a canonical node id"):
        fetch_entries_for_node(
            connection=None,  # type: ignore[arg-type]
            target_node_id="ENG-short",
        )
