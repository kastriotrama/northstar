import json

import pytest

from api.app.features.tecdoc_connections.repository import ResolvedConnectionRepository
from api.app.features.tecdoc_connections.service import (
    ResolvedConnectionService,
    _with_engine_provenance,
)


class Repository:
    def load(self):
        return (
            {
                "vehicle_id": "TS-ONE",
                "plate": "ABC123",
                "manufacturer": "VW",
                "ts_model": "Golf",
                "tecdoc_model": "GOLF",
                "ktype": "123",
                "engine_codes": ["CAYC"],
                "confidence_route": "resolved",
            },
            {
                "vehicle_id": "TS-TWO",
                "plate": "XYZ789",
                "manufacturer": "HYUNDAI",
                "ts_model": "i30",
                "tecdoc_model": "i30",
                "ktype": "456",
                "engine_codes": ["G4FU"],
                "confidence_route": "resolved",
                "evidence": ["match:context_conflict_requires_review"],
            },
        )


def test_lists_and_searches_privacy_safe_resolved_connections() -> None:
    page = ResolvedConnectionService(Repository()).list(query="abc123", limit=25, offset=0)  # type: ignore[arg-type]
    assert page.total == 2
    assert page.filtered_total == 1
    assert page.items[0].ktype == "123"
    assert page.items[0].plate == "ABC123"
    assert page.current_resolved_total == 1
    assert page.rerun_required_total == 1
    assert "registration plate is shown" in page.privacy_note


def test_marks_stored_context_conflict_decisions_for_rerun() -> None:
    page = ResolvedConnectionService(Repository()).list(query="xyz789", limit=25, offset=0)  # type: ignore[arg-type]

    assert page.items[0].match_validation_status == "rerun_required"
    assert page.items[0].match_validation_reasons == [
        "match:context_conflict_requires_review"
    ]


def test_repository_rejects_a_showcase_without_an_items_list(tmp_path) -> None:
    showcase = tmp_path / "showcase.json"
    showcase.write_text(json.dumps({"items": {}}))

    with pytest.raises(TypeError, match="items are missing"):
        ResolvedConnectionRepository(showcase).load()


@pytest.mark.parametrize(
    ("ts_engine", "tecdoc_engines", "status", "selected", "source", "used"),
    (
        (
            "L15-B4",
            ["L15B4"],
            "source_exact",
            "L15B4",
            "ts_exact_tecdoc_allocation",
            True,
        ),
        (None, ["L15B4"], "tecdoc_only", None, "ktype_uses_engine", False),
        (None, ["L15B4", "L15Z7"], "ambiguous", None, "ktype_uses_engine", False),
        (
            "L15B4",
            ["R18Z9"],
            "contradicted",
            None,
            "ts_vs_tecdoc_conflict",
            True,
        ),
        (None, [], "unavailable", None, "none", False),
    ),
)
def test_derives_engine_provenance_without_inventing_a_vehicle_engine(
    ts_engine,
    tecdoc_engines,
    status,
    selected,
    source,
    used,
) -> None:
    result = _with_engine_provenance(
        {"ts_engine_code": ts_engine, "engine_codes": tecdoc_engines}
    )

    assert result["tecdoc_compatible_engine_codes"] == tecdoc_engines
    assert result["engine_selection_status"] == status
    assert result["selected_engine_code"] == selected
    assert result["engine_evidence_source"] == source
    assert result["engine_used_for_ktype_selection"] is used
