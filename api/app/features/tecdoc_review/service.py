from __future__ import annotations

from typing import Any, Protocol

from api.app.features.tecdoc_review.schemas import (
    TecDocEntity,
    TecDocEntityPage,
    TecDocPromotionSummary,
    TecDocReviewPage,
    TecDocVehicle,
)


class ReviewRepository(Protocol):
    def latest_batch(self) -> dict[str, Any] | None: ...
    def fetch_vehicles(
        self, *, batch_id: str, query: str, limit: int, offset: int
    ) -> tuple[int, list[dict[str, Any]]]: ...
    def fetch_entities(
        self, *, batch_id: str, kind: str, query: str, limit: int, offset: int
    ) -> tuple[int, list[dict[str, Any]]]: ...


PROMOTION_RULES = [
    {
        "label": "Vehicle facts available",
        "outcome": "Table 120 is authoritative for KType-level vehicle facts.",
    },
    {
        "label": "Engine allocation handled safely",
        "outcome": "Table 155 is linked only when Table 125 supplies one unambiguous engine.",
    },
    {
        "label": "Official fuel evidence",
        "outcome": "Canonical fuel or the original KT 182 code is preserved.",
    },
    {
        "label": "Displacement policy",
        "outcome": "Technical displacement is retained; electric engine type 040 is exempt.",
    },
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
            summary=TecDocPromotionSummary(**batch),
            filtered_total=total,
            limit=limit,
            offset=offset,
            items=items,
            promotion_rules=PROMOTION_RULES,
        )

    def list_entities(self, *, kind: str, query: str, limit: int, offset: int) -> TecDocEntityPage:
        batch = self._repository.latest_batch()
        if batch is None:
            return TecDocEntityPage(kind=kind, limit=limit, offset=offset)
        total, rows = self._repository.fetch_entities(
            batch_id=str(batch["batch_id"]),
            kind=kind,
            query=query,
            limit=limit,
            offset=offset,
        )
        return TecDocEntityPage(
            kind=kind,
            batch_id=str(batch["batch_id"]),
            filtered_total=total,
            limit=limit,
            offset=offset,
            items=[TecDocEntity(**row) for row in rows],
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
            alias_id=str(row["alias_id"]),
            variant_id=str(row["variant_id"]),
            source_name=variant.get("source_name"),
            manufacturer=manufacturer.get("canonical_name"),
            model_family=family.get("canonical_name"),
            engine_code=engine.get("engine_code"),
            displacement_cc=engine.get("displacement_cc") or variant.get("displacement_cc"),
            displacement_source=engine.get("displacement_source")
            or variant.get("displacement_source"),
            fuel_type=engine.get("fuel_type") or variant.get("fuel_type"),
            engine_link_status=str(variant.get("engine_link_status") or "linked"),
            tecdoc_fuel_code=variant.get("tecdoc_fuel_code"),
            tecdoc_engine_type_code=variant.get("tecdoc_engine_type_code"),
            year_from=variant.get("year_from"),
            year_to=variant.get("year_to"),
            hierarchy_status=str(
                variant.get("hierarchy_link_status") or "awaiting_platform_mapping"
            ),
            source_row_refs=list(row["source_row_refs"] or []),
            source_keys={
                key: value
                for key, value in {
                    "alias": source_key,
                    "variant": alias.get("target_source_key"),
                    "engine": variant.get("engine_source_key"),
                    "model_family": variant.get("model_family_source_key"),
                    "manufacturer": variant.get("manufacturer_source_key"),
                }.items()
                if value
            },
        )
