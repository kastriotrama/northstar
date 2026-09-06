"""Application orchestration for normalization review reads."""

from __future__ import annotations

import re
from typing import Any, Protocol

from api.app.features.normalization_review.schemas import (
    NormalizationReviewFacets,
    NormalizationReviewFilters,
    NormalizationReviewPage,
    NormalizationReviewVehicle,
    NormalizationStatusSummary,
)


class ReviewRepository(Protocol):
    def get_latest_batch_id(self) -> str | None: ...

    def fetch_page(
        self, *, batch_id: str, filters: NormalizationReviewFilters
    ) -> tuple[int, list[dict[str, Any]]]: ...

    def fetch_summary(self, *, batch_id: str) -> dict[str, int]: ...

    def fetch_facets(self, *, batch_id: str) -> dict[str, list[str]]: ...


class NormalizationReviewService:
    def __init__(self, repository: ReviewRepository) -> None:
        self._repository = repository

    def list_vehicles(self, filters: NormalizationReviewFilters) -> NormalizationReviewPage:
        batch_id = filters.batch_id or self._repository.get_latest_batch_id()
        if batch_id is None:
            return NormalizationReviewPage(
                batch_id=None,
                total=0,
                filtered_total=0,
                limit=filters.limit,
                offset=filters.offset,
                summary=NormalizationStatusSummary(),
                facets=NormalizationReviewFacets(),
                items=[],
            )
        filtered_total, rows = self._repository.fetch_page(batch_id=batch_id, filters=filters)
        summary = NormalizationStatusSummary(**self._repository.fetch_summary(batch_id=batch_id))
        facets = NormalizationReviewFacets(**self._repository.fetch_facets(batch_id=batch_id))
        return NormalizationReviewPage(
            batch_id=batch_id,
            total=summary.total,
            filtered_total=filtered_total,
            limit=filters.limit,
            offset=filters.offset,
            summary=summary,
            facets=facets,
            items=[self._vehicle_from_row(row) for row in rows],
        )

    def _vehicle_from_row(self, row: dict[str, Any]) -> NormalizationReviewVehicle:
        payload = dict(row["normalized_payload"])
        normalized = dict(payload.get("normalized") or {})
        candidates = dict(payload.get("candidates") or {})
        energy_sources = normalized.get("energy_sources")
        production_year = normalized.get("production_year") or normalized.get("model_year")
        source_evidence = row.get("source_evidence") or {}
        registration_plate = source_evidence.get("plate")
        synthetic_plate = isinstance(registration_plate, str) and (
            registration_plate.startswith("TEST-")
            or re.fullmatch(r"T\d{5}", registration_plate) is not None
        )
        return NormalizationReviewVehicle(
            source_record_id=int(row["source_record_id"]),
            source_batch_id=str(row["source_batch_id"]),
            registration_plate=(
                str(registration_plate) if registration_plate not in (None, "") else None
            ),
            source_data_kind="synthetic" if synthetic_plate else "real",
            source_brand=row.get("source_brand"),
            source_evidence=source_evidence,
            status=row["status"],
            confidence=float(row["confidence"]),
            manufacturer=normalized.get("manufacturer") or candidates.get("manufacturer"),
            manufacturer_group=normalized.get("manufacturer_group"),
            model_family=normalized.get("model_family") or candidates.get("model_family"),
            bodywork=normalized.get("bodywork_form"),
            transmission=normalized.get("transmission_type"),
            energy_sources=(
                [str(value) for value in energy_sources] if isinstance(energy_sources, list) else []
            ),
            engine_code=normalized.get("engine_code"),
            production_year=int(production_year) if isinstance(production_year, int) else None,
            text_codes=list(normalized.get("text_codes") or []),
            special_vehicle_flags=list(normalized.get("special_vehicle_flags") or []),
            parts_matching_policy=normalized.get("parts_matching_policy"),
            review_reasons=list(row["review_reasons"]),
            applied_rule_ids=list(row["applied_rule_ids"]),
            candidate_rule_ids=list(payload.get("candidate_rule_ids") or []),
            normalized=normalized,
            candidates=candidates,
            decision_trace=list(payload.get("decision_trace") or []),
            rule_matches=list(payload.get("rule_matches") or []),
        )
