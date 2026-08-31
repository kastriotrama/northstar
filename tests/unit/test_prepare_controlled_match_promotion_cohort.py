from typing import Any, cast
from uuid import uuid4

import pytest

from scripts.prepare_controlled_match_promotion_cohort import select_stable_heads


def head(*, route: str = "resolved", reference: str | None = "000000001", confidence: float = 1.0) -> dict[str, Any]:
    return {
        "decision_id": uuid4(),
        "source_entity_key": "plate:ABC123",
        "route": route,
        "confidence": confidence,
        "selected_candidate_reference": reference,
        "decision_payload": {"hard_conflicts": []},
    }


def test_selects_only_unchanged_graph_safe_resolved_heads() -> None:
    first = head()
    changed = head(reference="000000002")
    selected, counts = select_stable_heads(
        [first, changed],
        changed_decision_ids={str(changed["decision_id"])},
        catalog_types={"000000001": "TecDocKType", "000000002": "TecDocKType"},
        limit=1,
        minimum_confidence=0.975,
    )
    assert selected == [first]
    assert counts["changed_in_v6_replay"] == 1
    assert counts["selected"] == 1


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"route": "review_required"}, "not_resolved"),
        ({"reference": "000000002"}, "candidate_only_not_graph_safe"),
        ({"confidence": 0.9}, "below_confidence_gate"),
    ],
)
def test_rejects_non_promotable_heads(kwargs: dict[str, object], reason: str) -> None:
    row = head(
        route=cast(str, kwargs.get("route", "resolved")),
        reference=cast(str | None, kwargs.get("reference", "000000001")),
        confidence=cast(float, kwargs.get("confidence", 1.0)),
    )
    selected, counts = select_stable_heads(
        [row],
        changed_decision_ids=set(),
        catalog_types={
            "000000001": "TecDocKType",
            "000000002": "TecDocKTypeCandidateOnly",
        },
        limit=1,
        minimum_confidence=0.975,
    )
    assert selected == []
    assert counts[reason] == 1


def test_requires_positive_limit_and_valid_confidence() -> None:
    with pytest.raises(ValueError, match="limit"):
        select_stable_heads([], changed_decision_ids=set(), catalog_types={}, limit=0, minimum_confidence=0.9)
    with pytest.raises(ValueError, match="minimum_confidence"):
        select_stable_heads([], changed_decision_ids=set(), catalog_types={}, limit=1, minimum_confidence=2.0)
