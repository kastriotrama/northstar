from datetime import UTC, datetime
from typing import Any

from api.app.features.source_records.schemas import SourceRecordFilters
from api.app.features.source_records.service import SourceRecordService

INGESTED_AT = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def _row(record_id: int) -> dict[str, Any]:
    return {
        "id": record_id,
        "source_batch_id": "batch-a",
        "ingested_at": INGESTED_AT,
        "raw_record": {"plate": f"ABC{record_id}", "brand": "VOLVO"},
    }


class FakeRepository:
    def __init__(self, rows: list[dict[str, Any]], *, timed_out: bool = False) -> None:
        self.rows = rows
        self.timed_out = timed_out
        self.received: SourceRecordFilters | None = None

    def fetch_page(self, filters: SourceRecordFilters) -> tuple[list[dict[str, Any]], bool]:
        self.received = filters
        return self.rows, self.timed_out

    def estimated_total(self) -> int:
        return 7_255_433

    def fetch_record(self, record_id: int) -> dict[str, Any] | None:
        if record_id != 1:
            return None
        row = _row(1)
        row["normalizations"] = [
            {
                "source_batch_id": "batch-a",
                "status": "resolved",
                "confidence": 0.9,
                "normalized": {"manufacturer": "Volvo"},
                "candidates": {},
                "applied_rule_ids": ["MFR-1"],
                "review_reasons": [],
                "updated_at": INGESTED_AT,
            }
        ]
        return row

    def fetch_batches(self) -> list[dict[str, Any]]:
        return [
            {
                "batch_id": "batch-a",
                "records": 100,
                "status": "completed",
                "finished_at": INGESTED_AT,
            }
        ]

    def fetch_field_inventory(self) -> tuple[int, list[dict[str, Any]]]:
        return 20_000, [
            {
                "field": "plate",
                "present": 20_000,
                "non_null": 20_000,
                "fill_rate": 1.0,
                "examples": ["ABC123"],
            }
        ]


def test_page_returns_a_cursor_when_more_rows_exist() -> None:
    # The repository fetches limit+1 rows so the service can detect a further page
    # without running a count over 7.25M rows.
    repository = FakeRepository([_row(1), _row(2), _row(3)])
    service = SourceRecordService(repository)

    page = service.list_records(SourceRecordFilters(limit=2))

    assert [item.id for item in page.items] == [1, 2]
    assert page.has_more is True
    assert page.next_cursor == 2


def test_last_page_reports_no_cursor() -> None:
    service = SourceRecordService(FakeRepository([_row(1), _row(2)]))

    page = service.list_records(SourceRecordFilters(limit=2))

    assert page.has_more is False
    assert page.next_cursor is None


def test_unfiltered_page_reports_the_planner_estimate() -> None:
    service = SourceRecordService(FakeRepository([_row(1)]))

    page = service.list_records(SourceRecordFilters(limit=10))

    assert page.total == 7_255_433
    assert page.total_is_estimate is True


def test_filtered_page_reports_no_total() -> None:
    # Counting under a filter is a full sequential scan, so no total is claimed.
    service = SourceRecordService(FakeRepository([_row(1)]))

    page = service.list_records(SourceRecordFilters(limit=10, query="volvo"))

    assert page.total == 0


def test_timed_out_search_is_surfaced() -> None:
    service = SourceRecordService(FakeRepository([], timed_out=True))

    page = service.list_records(SourceRecordFilters(limit=10, query="nothing"))

    assert page.timed_out is True
    assert page.items == []


def test_record_detail_includes_normalization_results() -> None:
    service = SourceRecordService(FakeRepository([]))

    detail = service.get_record(1)

    assert detail is not None
    assert detail.record.raw_record["plate"] == "ABC1"
    assert detail.normalizations[0].normalized == {"manufacturer": "Volvo"}


def test_missing_record_returns_none() -> None:
    service = SourceRecordService(FakeRepository([]))

    assert service.get_record(999) is None


def test_field_inventory_is_reported_with_its_sample_size() -> None:
    service = SourceRecordService(FakeRepository([]))

    inventory = service.field_inventory()

    assert inventory.sampled_rows == 20_000
    assert inventory.fields[0].field == "plate"
