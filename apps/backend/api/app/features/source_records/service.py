"""Application orchestration for raw source-record browsing."""

from __future__ import annotations

from typing import Any, Protocol

from api.app.features.source_records.schemas import (
    SourceBatch,
    SourceBatchList,
    SourceFieldInventory,
    SourceFieldStat,
    SourceRecord,
    SourceRecordDetail,
    SourceRecordFilters,
    SourceRecordNormalization,
    SourceRecordPage,
)


class SourceRecordReadRepository(Protocol):
    def fetch_page(
        self, filters: SourceRecordFilters
    ) -> tuple[list[dict[str, Any]], bool]: ...

    def estimated_total(self) -> int: ...

    def fetch_record(self, record_id: int) -> dict[str, Any] | None: ...

    def fetch_batches(self) -> list[dict[str, Any]]: ...

    def fetch_field_inventory(self) -> tuple[int, list[dict[str, Any]]]: ...


class SourceRecordService:
    def __init__(self, repository: SourceRecordReadRepository) -> None:
        self._repository = repository

    def list_records(self, filters: SourceRecordFilters) -> SourceRecordPage:
        rows, timed_out = self._repository.fetch_page(filters)
        has_more = len(rows) > filters.limit
        page_rows = rows[: filters.limit]
        items = [SourceRecord(**row) for row in page_rows]

        is_filtered = bool(
            filters.query.strip()
            or (filters.field and filters.value)
            or filters.batch_id
        )
        # An exact count under a filter is a full sequential scan; the estimate is only
        # meaningful for the unfiltered table, so a filtered page reports no total.
        total = 0 if is_filtered else self._repository.estimated_total()

        return SourceRecordPage(
            items=items,
            limit=filters.limit,
            next_cursor=items[-1].id if items and has_more else None,
            has_more=has_more,
            total=total,
            total_is_estimate=True,
            timed_out=timed_out,
        )

    def get_record(self, record_id: int) -> SourceRecordDetail | None:
        row = self._repository.fetch_record(record_id)
        if row is None:
            return None
        normalizations = [
            SourceRecordNormalization(**item) for item in row.pop("normalizations", [])
        ]
        return SourceRecordDetail(
            record=SourceRecord(**row), normalizations=normalizations
        )

    def list_batches(self) -> SourceBatchList:
        return SourceBatchList(
            items=[SourceBatch(**row) for row in self._repository.fetch_batches()]
        )

    def field_inventory(self) -> SourceFieldInventory:
        sampled, fields = self._repository.fetch_field_inventory()
        return SourceFieldInventory(
            sampled_rows=sampled,
            fields=[SourceFieldStat(**field) for field in fields],
        )
