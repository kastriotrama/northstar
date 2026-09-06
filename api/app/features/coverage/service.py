"""Application orchestration for normalization coverage reads."""

from __future__ import annotations

from typing import Any, Protocol

from api.app.features.coverage.schemas import (
    CoverageBatch,
    CoverageBatchList,
    FieldCoverage,
    ReviewReasonCount,
    StatusBreakdown,
    TecDocCoverageReport,
    TecDocEntityCoverage,
    TecDocFieldCoverage,
    TsCoverageReport,
)

ALL_PARTS_SUFFIX = "-all-parts"


class CoverageReadRepository(Protocol):
    def fetch_batches(self) -> list[dict[str, Any]]: ...

    def fetch_ts_coverage(self, *, batch_id: str) -> dict[str, Any]: ...

    def fetch_tecdoc_coverage(self) -> dict[str, Any]: ...


class CoverageError(ValueError):
    """Raised when a coverage request cannot be served as asked."""


class CoverageService:
    def __init__(self, repository: CoverageReadRepository) -> None:
        self._repository = repository

    def list_batches(self) -> CoverageBatchList:
        return CoverageBatchList(
            items=[CoverageBatch(**row) for row in self._repository.fetch_batches()]
        )

    def latest_batch_id(self) -> str | None:
        batches = self._repository.fetch_batches()
        return str(batches[0]["batch_id"]) if batches else None

    def ts_coverage(self, *, batch_id: str) -> TsCoverageReport:
        # An all-parts scan spans 261 batches / 6.5M rows and is a known timeout.
        # Refuse it explicitly instead of letting the request hang.
        if batch_id.endswith(ALL_PARTS_SUFFIX):
            raise CoverageError(
                "Coverage is computed per batch. Collapsing all parts of a run scans "
                "millions of rows and times out; pick a single batch part instead."
            )
        raw = self._repository.fetch_ts_coverage(batch_id=batch_id)
        rows = int(raw.get("rows") or 0)
        status_counts = raw.get("status") or {}

        fields: list[FieldCoverage] = []
        for entry in raw.get("fields") or []:
            normalized = int(entry.get("normalized") or 0)
            # Records carrying a candidate the rules could not promote to a normalized
            # value. Already excludes records normalized for the same field.
            candidates_only = int(entry.get("candidates_only") or 0)
            covered = min(normalized, rows)
            fields.append(
                FieldCoverage(
                    field=str(entry.get("field")),
                    normalized=normalized,
                    candidates_only=candidates_only,
                    missing=max(rows - covered, 0),
                    coverage=(covered / rows) if rows else 0.0,
                )
            )
        # Biggest gaps first: this page exists to show what the rules do not yet reach.
        fields.sort(key=lambda field: (-field.missing, field.field))

        return TsCoverageReport(
            batch_id=batch_id,
            rows=rows,
            status=StatusBreakdown(
                resolved=int(status_counts.get("resolved") or 0),
                provisional=int(status_counts.get("provisional") or 0),
                review_required=int(status_counts.get("review_required") or 0),
                failed=int(status_counts.get("failed") or 0),
            ),
            fields=fields,
            review_reasons=[
                ReviewReasonCount(
                    reason=str(item.get("reason")),
                    records=int(item.get("records") or 0),
                )
                for item in raw.get("review_reasons") or []
            ],
            fully_normalized_fields=sum(1 for f in fields if f.missing == 0),
            partial_fields=sum(1 for f in fields if f.missing > 0),
        )

    def tecdoc_coverage(self) -> TecDocCoverageReport:
        raw = self._repository.fetch_tecdoc_coverage()
        return TecDocCoverageReport(
            batch_id=raw.get("batch_id"),
            entities=[
                TecDocEntityCoverage(
                    entity_type=str(entity["entity_type"]),
                    rows=int(entity["rows"]),
                    fields=[TecDocFieldCoverage(**field) for field in entity["fields"]],
                )
                for entity in raw.get("entities") or []
            ],
        )
