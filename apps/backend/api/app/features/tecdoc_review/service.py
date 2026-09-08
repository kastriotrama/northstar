from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from api.app.features.tecdoc_review.gaps import (
    GAP_VALUE_SPECS,
    RESOLVABLE_FIELDS,
    TecDocResolveError,
    blocked_reason,
)
from api.app.features.tecdoc_review.predicate import GAP_FIELDS
from api.app.features.tecdoc_review.schemas import (
    TecDocEntity,
    TecDocEntityPage,
    TecDocGapValue,
    TecDocGapValuesResponse,
    TecDocPromotionSummary,
    TecDocResolution,
    TecDocReviewPage,
    TecDocUnresolvedField,
    TecDocUnresolvedSummary,
    TecDocVehicle,
    TecDocVehicleCondition,
    TecDocVehicleCount,
    TecDocVehicleFacet,
    TecDocVehicleFacetValue,
)
from ingestion.tecdoc.canonical_rule_proposals import comparison_key
from ingestion.tecdoc.canonical_vocabulary import canonical_values


class ReviewRepository(Protocol):
    def latest_batch(self) -> dict[str, Any] | None: ...
    def fetch_vehicles(
        self,
        *,
        batch_id: str,
        query: str,
        limit: int,
        offset: int,
        conditions: Sequence[Any] = (),
        unresolved_field: str | None = None,
    ) -> tuple[int, list[dict[str, Any]]]: ...
    def fetch_entities(
        self, *, batch_id: str, kind: str, query: str, limit: int, offset: int
    ) -> tuple[int, list[dict[str, Any]]]: ...
    def count_vehicles(
        self,
        *,
        batch_id: str,
        query: str,
        conditions: Sequence[Any] = (),
        unresolved_field: str | None = None,
    ) -> int: ...
    def total_vehicles(self, *, batch_id: str) -> int: ...
    def facet_vehicles(
        self,
        *,
        batch_id: str,
        query: str,
        conditions: Sequence[Any],
        unresolved_field: str | None,
        field: str,
        limit: int,
    ) -> list[tuple[str, int]]: ...
    def unresolved_summary(
        self,
        *,
        batch_id: str,
        query: str,
        conditions: Sequence[Any],
    ) -> tuple[int, list[tuple[str, int]]]: ...
    def gap_values(
        self, *, batch_id: str, canonical_field: str, limit: int
    ) -> list[tuple[str, str | None, int]]: ...
    def fetch_resolutions(self, *, canonical_field: str) -> dict[str, dict[str, Any]]: ...
    def upsert_resolution(
        self,
        *,
        canonical_field: str,
        comparison_key: str,
        source_term: str,
        key_table: str | None,
        decision: str,
        canonical_value: str | None,
        note: str,
        reviewed_by: str,
    ) -> dict[str, Any]: ...


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

    def list_vehicles(
        self,
        *,
        query: str,
        limit: int,
        offset: int,
        conditions: Sequence[TecDocVehicleCondition] = (),
        unresolved_field: str | None = None,
    ) -> TecDocReviewPage:
        batch = self._repository.latest_batch()
        if batch is None:
            return TecDocReviewPage(summary=TecDocPromotionSummary(), limit=limit, offset=offset)
        total, rows = self._repository.fetch_vehicles(
            batch_id=str(batch["batch_id"]),
            query=query,
            limit=limit,
            offset=offset,
            conditions=conditions,
            unresolved_field=unresolved_field,
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

    def count_vehicles(
        self,
        *,
        query: str,
        conditions: Sequence[TecDocVehicleCondition] = (),
        unresolved_field: str | None = None,
    ) -> TecDocVehicleCount:
        batch = self._repository.latest_batch()
        if batch is None:
            return TecDocVehicleCount()
        batch_id = str(batch["batch_id"])
        matched = self._repository.count_vehicles(
            batch_id=batch_id, query=query, conditions=conditions, unresolved_field=unresolved_field
        )
        total = self._repository.total_vehicles(batch_id=batch_id)
        return TecDocVehicleCount(matched_rows=matched, total_rows=total)

    def unresolved_vehicle_summary(
        self, *, query: str, conditions: Sequence[TecDocVehicleCondition] = ()
    ) -> TecDocUnresolvedSummary:
        """What the filtered KTypes still cannot say about themselves.

        Row-level twin of the value-level coverage `canonical_rule_proposals`
        reports: the same three canonical fields (`energy_sources`,
        `bodywork_form`, `drive_type`), counted per matched KType instead of
        per distinct release value.
        """

        batch = self._repository.latest_batch()
        if batch is None:
            return TecDocUnresolvedSummary()
        matched, counts = self._repository.unresolved_summary(
            batch_id=str(batch["batch_id"]), query=query, conditions=conditions
        )
        fields = [
            TecDocUnresolvedField(
                field=field,
                label=GAP_FIELDS[field][0],
                unresolved=count,
                share=round(count / matched, 4) if matched else 0.0,
            )
            for field, count in counts
            if count > 0
        ]
        fields.sort(key=lambda entry: entry.unresolved, reverse=True)
        return TecDocUnresolvedSummary(matched_rows=matched, fields=fields)

    def facet_vehicles(
        self,
        *,
        query: str,
        conditions: Sequence[TecDocVehicleCondition],
        unresolved_field: str | None,
        field: str,
        limit: int,
    ) -> TecDocVehicleFacet:
        batch = self._repository.latest_batch()
        if batch is None:
            return TecDocVehicleFacet(field=field)
        batch_id = str(batch["batch_id"])
        values = self._repository.facet_vehicles(
            batch_id=batch_id,
            query=query,
            conditions=conditions,
            unresolved_field=unresolved_field,
            field=field,
            limit=limit,
        )
        matched = self._repository.count_vehicles(
            batch_id=batch_id, query=query, conditions=conditions, unresolved_field=unresolved_field
        )
        return TecDocVehicleFacet(
            field=field,
            matched_rows=matched,
            values=[TecDocVehicleFacetValue(value=value, count=count) for value, count in values],
        )

    def gap_values(self, *, canonical_field: str, limit: int) -> TecDocGapValuesResponse:
        """The distinct raw values behind one canonical field's gap.

        Merges the algorithmic count from the promoted batch with whatever a
        reviewer has already ruled on live, so a value someone resolved a
        moment ago stops reading as an open gap without waiting for a new
        sealed rule version.
        """

        if canonical_field not in GAP_VALUE_SPECS:
            raise TecDocResolveError(f"{canonical_field!r} has no value-level gap to browse")
        spec = GAP_VALUE_SPECS[canonical_field]
        options = sorted(canonical_values(canonical_field))
        batch = self._repository.latest_batch()
        if batch is None:
            return TecDocGapValuesResponse(
                canonical_field=canonical_field,
                key_table=spec.key_table,
                canonical_options=options,
            )
        batch_id = str(batch["batch_id"])
        raw = self._repository.gap_values(
            batch_id=batch_id, canonical_field=canonical_field, limit=limit
        )
        reviewed = self._repository.fetch_resolutions(canonical_field=canonical_field)
        values = [
            TecDocGapValue(
                source_term=source_term,
                label=label,
                key_table=spec.key_table,
                support=support,
                blocked_reason=blocked_reason(canonical_field, source_term),
                resolution=TecDocResolution(**reviewed[key])
                if (key := comparison_key(source_term)) in reviewed
                else None,
            )
            for source_term, label, support in raw
        ]
        return TecDocGapValuesResponse(
            canonical_field=canonical_field,
            key_table=spec.key_table,
            canonical_options=options,
            values=values,
        )

    def resolve(
        self,
        *,
        canonical_field: str,
        source_term: str,
        decision: str,
        canonical_value: str | None,
        note: str,
        reviewed_by: str,
    ) -> TecDocResolution:
        """Write one reviewer's ruling on one value.

        Validated the same way the schema already constrains a sealed
        `tecdoc_rules` row -- an acceptance must name a real canonical target,
        an exclusion must name none and must say why -- plus the one check a
        sealed row cannot make: whether the value is a mixed descriptor, which
        no single-target resolution is allowed to overrule.
        """

        if canonical_field not in RESOLVABLE_FIELDS:
            raise TecDocResolveError(
                f"{canonical_field!r} has no canonical vocabulary to resolve toward"
            )
        if not source_term.strip():
            raise TecDocResolveError("a resolution needs the value it rules on")
        if not reviewed_by.strip():
            raise TecDocResolveError("a resolution needs who is ruling on it")
        reason = blocked_reason(canonical_field, source_term)
        if reason is not None:
            raise TecDocResolveError(
                f"{source_term!r} is a {reason.replace('_', ' ')} and cannot be resolved "
                "to a single target"
            )
        if decision == "accepted":
            if not canonical_value or canonical_value not in canonical_values(canonical_field):
                raise TecDocResolveError(
                    f"{canonical_value!r} is not a canonical {canonical_field} value"
                )
        elif decision == "excluded":
            if canonical_value:
                raise TecDocResolveError("an excluded value names no canonical target")
            if not note.strip():
                raise TecDocResolveError("excluding a value needs a note saying why")
        else:
            raise TecDocResolveError(f"{decision!r} is not a known resolution decision")

        spec = GAP_VALUE_SPECS[canonical_field]
        stored = self._repository.upsert_resolution(
            canonical_field=canonical_field,
            comparison_key=comparison_key(source_term),
            source_term=source_term,
            key_table=spec.key_table,
            decision=decision,
            canonical_value=canonical_value,
            note=note,
            reviewed_by=reviewed_by,
        )
        return TecDocResolution(**stored)

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
        transmission = dict(row.get("transmission_attributes") or {})
        bodywork = dict(row.get("bodywork_attributes") or {})
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
            transmission_code=transmission.get("transmission_code"),
            transmission_type_code=transmission.get("tecdoc_transmission_type_code"),
            transmission_type_name=transmission.get("transmission_type_name"),
            transmission_speeds=transmission.get("speeds"),
            transmission_link_status=str(
                variant.get("transmission_link_status") or "allocation_missing"
            ),
            bodywork_code=bodywork.get("tecdoc_body_type_code")
            or variant.get("tecdoc_body_type_code"),
            bodywork_name=bodywork.get("canonical_name")
            or variant.get("tecdoc_bodywork_official_label"),
            bodywork_status=str(variant.get("bodywork_link_status") or "code_missing"),
            drive_type=variant.get("drive_type"),
            drive_code=variant.get("tecdoc_drive_type_code"),
            drive_official_label=variant.get("tecdoc_drive_official_label"),
            drive_status=str(variant.get("drive_normalization_status") or "review_required"),
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
                variant.get("hierarchy_link_status")
                or "model_family_linked_platform_optional"
            ),
            source_row_refs=list(row["source_row_refs"] or []),
            source_keys={
                key: value
                for key, value in {
                    "alias": source_key,
                    "variant": alias.get("target_source_key"),
                    "engine": variant.get("engine_source_key"),
                    "transmission": variant.get("transmission_source_key"),
                    "bodywork": variant.get("bodywork_source_key"),
                    "model_family": variant.get("model_family_source_key"),
                    "manufacturer": variant.get("manufacturer_source_key"),
                }.items()
                if value
            },
        )
