from typing import Any

import pytest

from api.app.features.coverage.service import CoverageError, CoverageService


class FakeRepository:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {}
        self.requested_batch: str | None = None

    def fetch_batches(self) -> list[dict[str, Any]]:
        return [
            {"batch_id": "run-part-002", "records": 25000, "finished_at": None},
            {"batch_id": "run-part-001", "records": 25000, "finished_at": None},
        ]

    def fetch_ts_coverage(self, *, batch_id: str) -> dict[str, Any]:
        self.requested_batch = batch_id
        return self.payload

    def fetch_tecdoc_coverage(self) -> dict[str, Any]:
        return {
            "batch_id": "tecdoc-batch",
            "entities": [
                {
                    "entity_type": "vehicle_variant",
                    "rows": 100,
                    "fields": [
                        {
                            "entity_type": "vehicle_variant",
                            "field": "engine_code",
                            "present": 60,
                            "missing": 40,
                            "coverage": 0.6,
                        }
                    ],
                }
            ],
        }


def _payload() -> dict[str, Any]:
    return {
        "rows": 1000,
        "status": {"resolved": 800, "provisional": 150, "review_required": 50},
        "fields": [
            {"field": "manufacturer", "normalized": 1000, "candidates_only": 0},
            {"field": "model_family", "normalized": 700, "candidates_only": 120},
            {"field": "engine_code", "normalized": 500, "candidates_only": 0},
        ],
        "review_reasons": [{"reason": "manufacturer_missing", "records": 12}],
    }


def test_ts_coverage_reports_gaps_and_candidate_only_counts() -> None:
    service = CoverageService(FakeRepository(_payload()))

    report = service.ts_coverage(batch_id="run-part-001")

    assert report.rows == 1000
    assert report.status.resolved == 800
    assert report.status.failed == 0

    by_field = {field.field: field for field in report.fields}
    assert by_field["manufacturer"].missing == 0
    assert by_field["manufacturer"].coverage == pytest.approx(1.0)
    assert by_field["model_family"].missing == 300
    assert by_field["model_family"].candidates_only == 120
    assert by_field["engine_code"].missing == 500

    assert report.fully_normalized_fields == 1
    assert report.partial_fields == 2


def test_ts_coverage_orders_largest_gaps_first() -> None:
    service = CoverageService(FakeRepository(_payload()))

    report = service.ts_coverage(batch_id="run-part-001")

    assert [field.field for field in report.fields] == [
        "engine_code",
        "model_family",
        "manufacturer",
    ]


def test_ts_coverage_refuses_an_all_parts_selector() -> None:
    # A collapsed all-parts view spans 261 batches / 6.5M rows and is a known timeout.
    repository = FakeRepository(_payload())
    service = CoverageService(repository)

    with pytest.raises(CoverageError):
        service.ts_coverage(batch_id="run-all-parts")

    assert repository.requested_batch is None


def test_latest_batch_id_uses_the_job_ledger_ordering() -> None:
    service = CoverageService(FakeRepository())

    assert service.latest_batch_id() == "run-part-002"


def test_tecdoc_coverage_maps_entities() -> None:
    service = CoverageService(FakeRepository())

    report = service.tecdoc_coverage()

    assert report.batch_id == "tecdoc-batch"
    assert report.entities[0].entity_type == "vehicle_variant"
    assert report.entities[0].fields[0].missing == 40
