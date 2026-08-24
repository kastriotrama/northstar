from typing import Any

from api.app.features.normalization_review.schemas import NormalizationReviewFilters
from api.app.features.normalization_review.service import NormalizationReviewService


class FakeRepository:
    def __init__(self, *, batch_id: str | None = "batch-250") -> None:
        self.batch_id = batch_id
        self.received_filters: NormalizationReviewFilters | None = None

    def get_latest_batch_id(self) -> str | None:
        return self.batch_id

    def fetch_page(
        self, *, batch_id: str, filters: NormalizationReviewFilters
    ) -> tuple[int, list[dict[str, Any]]]:
        assert batch_id == "batch-250"
        self.received_filters = filters
        return 1, [
            {
                "source_record_id": 42,
                "source_batch_id": "batch-2026-08",
                "status": "provisional",
                "confidence": 0.8,
                "normalized_payload": {
                    "normalized": {
                        "manufacturer": "Volvo",
                        "bodywork_form": "estate",
                        "energy_sources": ["petrol", "electricity"],
                        "transmission_type": "automatic",
                        "engine_code": "B4204T",
                        "production_year": 2024,
                    },
                    "candidates": {"model_family": "V60"},
                    "decision_trace": [{"sequence": 1, "field": "manufacturer"}],
                    "rule_matches": [{"rule_id": "BDY-110"}],
                },
                "applied_rule_ids": ["MFR-102", "BDY-110"],
                "review_reasons": [],
                "source_evidence": {"plate": "ABC123"},
            }
        ]

    def fetch_summary(self, *, batch_id: str) -> dict[str, int]:
        assert batch_id == "batch-250"
        return {
            "total": 250,
            "resolved": 80,
            "provisional": 130,
            "review_required": 40,
            "failed": 0,
        }

    def fetch_facets(self, *, batch_id: str) -> dict[str, list[str]]:
        assert batch_id == "batch-250"
        return {
            "manufacturers": ["Volvo"],
            "bodywork_forms": ["estate"],
            "fuels": ["electricity", "petrol"],
            "transmissions": ["automatic"],
        }


def test_service_builds_searchable_vehicle_page_from_sanitized_payload() -> None:
    repository = FakeRepository()
    service = NormalizationReviewService(repository)
    filters = NormalizationReviewFilters(query="V60", bodywork="estate")

    page = service.list_vehicles(filters)

    assert page.batch_id == "batch-250"
    assert page.total == 250
    assert page.filtered_total == 1
    assert repository.received_filters == filters
    vehicle = page.items[0]
    assert vehicle.manufacturer == "Volvo"
    assert vehicle.model_family == "V60"
    assert vehicle.bodywork == "estate"
    assert vehicle.energy_sources == ["petrol", "electricity"]
    assert vehicle.decision_trace == [{"sequence": 1, "field": "manufacturer"}]
    assert vehicle.registration_plate == "ABC123"
    assert vehicle.source_data_kind == "real"
    assert "vin" not in vehicle.model_dump_json()


def test_service_returns_empty_workspace_before_first_import() -> None:
    page = NormalizationReviewService(FakeRepository(batch_id=None)).list_vehicles(
        NormalizationReviewFilters()
    )

    assert page.batch_id is None
    assert page.total == 0
    assert page.items == []
