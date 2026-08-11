from __future__ import annotations

from typing import Any, Protocol

from api.app.features.tecdoc_review.schemas import (
    TecDocPromotionSummary,
    TecDocReviewPage,
    TecDocVehicle,
)


class ReviewRepository(Protocol):
    def latest_batch(self) -> dict[str, Any] | None: ...
    def fetch_vehicles(self, *, batch_id: str, query: str, limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]: ...


PROMOTION_RULES = [
    {"label": "One active engine", "outcome": "Multiple or missing engines stay outside the graph."},
    {"label": "Official fuel code", "outcome": "Unsupported TecDoc fuel meanings require review."},
    {"label": "Reliable displacement", "outcome": "Use exact Table 155 or complete-source Table 120 consensus."},
    {"label": "Production start year", "outcome": "A missing start year prevents automatic promotion."},
]


class TecDocReviewService:
    def __init__(self, repository: ReviewRepository) -> None:
        self._repository = repository

    def list_vehicles(self, *, query: str, limit: int, offset: int) -> TecDocReviewPage:
        batch = self._repository.latest_batch()
        if batch is None:
            return TecDocReviewPage(summary=TecDocPromotionSummary(), limit=limit, offset=offset)
        total, rows = self._repository.fetch_vehicles(
            batch_id=str(batch["batch_id"]), query=query, limit=limit, offset=offset
        )
        items = [self._vehicle(row) for row in rows]
        return TecDocReviewPage(
            summary=TecDocPromotionSummary(**batch), filtered_total=total, limit=limit,
            offset=offset, items=items, promotion_rules=PROMOTION_RULES,
        )

    @staticmethod
    def _vehicle(row: dict[str, Any]) -> TecDocVehicle:
        variant = dict(row["variant_attributes"] or {})
        manufacturer = dict(row["manufacturer_attributes"] or {})
        family = dict(row["family_attributes"] or {})
        engine = dict(row["engine_attributes"] or {})
        alias = dict(row["alias_attributes"] or {})
        source_key = str(row["source_key"])
        return TecDocVehicle(
            ktype=str(alias.get("alias_text") or source_key.removeprefix("ktype:")),
            alias_id=str(row["alias_id"]), variant_id=str(row["variant_id"]),
            source_name=variant.get("source_name"), manufacturer=manufacturer.get("canonical_name"),
            model_family=family.get("canonical_name"), engine_code=engine.get("engine_code"),
            displacement_cc=engine.get("displacement_cc"), displacement_source=engine.get("displacement_source"),
            fuel_type=engine.get("fuel_type"), year_from=variant.get("year_from"), year_to=variant.get("year_to"),
            hierarchy_status=str(variant.get("hierarchy_link_status") or "awaiting_platform_mapping"),
            source_row_refs=list(row["source_row_refs"] or []),
            source_keys={key: value for key, value in {
                "alias": source_key, "variant": alias.get("target_source_key"),
                "engine": variant.get("engine_source_key"), "model_family": variant.get("model_family_source_key"),
                "manufacturer": variant.get("manufacturer_source_key"),
            }.items() if value},
        )
