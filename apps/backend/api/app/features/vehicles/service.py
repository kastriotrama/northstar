"""The Vehicles tab's reading of `core.vehicles`: lists, facets and one car in full.

Read-only. Values are written by the providers' imports and by rules, all through
`vehicle_core_merge`; this module only says what won and where it came from.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from api.app.features.vehicles.repository import VehicleRepository
from api.app.features.vehicles.schemas import (
    EnrichmentRuleInfo,
    FacetValue,
    ValueAlternative,
    ValueSource,
    VehicleCondition,
    VehicleFacet,
    VehicleFieldInfo,
    VehicleFieldValue,
    VehicleIdentifier,
    VehiclePage,
    VehicleRecord,
    VehicleRow,
    VehicleSourceLink,
    VehicleTotal,
)
from ingestion.vehicle_core_fields import CORE_FIELDS, SOURCE_RULE, parse_source_ref
from ingestion.vehicle_core_query import VehicleTerm, is_vehicle_id


class VehicleNotFoundError(LookupError):
    """No vehicle carries that NOR ID."""


class InvalidVehicleIdError(ValueError):
    """The text is not shaped like a NOR ID at all."""


def terms(conditions: Sequence[VehicleCondition]) -> list[VehicleTerm]:
    return [(condition.field, condition.operator, tuple(condition.values)) for condition in conditions]


def field_catalog() -> list[VehicleFieldInfo]:
    return [
        VehicleFieldInfo(
            field=field.name,
            label=field.label,
            group=field.group,
            sql_type=field.sql_type,
            filterable=field.filterable,
            reviewable=field.reviewable,
        )
        for field in CORE_FIELDS
    ]


def value_source(
    encoded: str | None, *, origin_source: str, origin_observed_on: date | None
) -> ValueSource:
    """Parse a `field_sources` entry; no entry means the vehicle's origin record."""

    if not encoded:
        return ValueSource(
            source=origin_source, ref=None, observed_on=origin_observed_on, origin=True
        )
    ref = parse_source_ref(encoded)
    return ValueSource(
        source=ref.source,
        ref=ref.ref,
        observed_on=ref.observed_on,
        origin=ref.source == origin_source and ref.observed_on == origin_observed_on,
    )


def field_values(vehicle: dict[str, Any]) -> list[VehicleFieldValue]:
    """Every field of the record, each with the source of its value and what lost."""

    sources: dict[str, str] = dict(vehicle.get("field_sources") or {})
    alternatives: dict[str, list[dict[str, Any]]] = dict(vehicle.get("field_alternatives") or {})
    origin_source = str(vehicle["origin_source"])
    origin_observed_on: date | None = vehicle.get("origin_observed_on")
    values = []
    for field in CORE_FIELDS:
        value = vehicle.get(field.name)
        if isinstance(value, tuple):
            value = list(value)
        values.append(
            VehicleFieldValue(
                field=field.name,
                label=field.label,
                group=field.group,
                value=value,
                source=(
                    None if value is None else value_source(
                        sources.get(field.name),
                        origin_source=origin_source,
                        origin_observed_on=origin_observed_on,
                    )
                ),
                alternatives=[
                    ValueAlternative(
                        value=entry.get("value"),
                        source=value_source(
                            str(entry.get("source") or ""),
                            origin_source=origin_source,
                            origin_observed_on=origin_observed_on,
                        ),
                    )
                    for entry in alternatives.get(field.name, [])
                ],
            )
        )
    return values


def rule_ids(fields: Sequence[VehicleFieldValue]) -> list[str]:
    """The learned rules any value or alternative on this vehicle came from."""

    found: set[str] = set()
    for field in fields:
        sources = [field.source] if field.source else []
        sources.extend(alternative.source for alternative in field.alternatives)
        found.update(
            source.ref for source in sources if source.source == SOURCE_RULE and source.ref
        )
    return sorted(found)


class VehicleService:
    def __init__(self, repository: VehicleRepository) -> None:
        self._repository = repository

    def search(
        self,
        conditions: Sequence[VehicleCondition],
        text: str,
        *,
        cursor: str | None,
        limit: int,
        with_total: bool = True,
    ) -> VehiclePage:
        """A keyset page; `matched_rows` only on the first, where it is worth its count.

        A page reads only its own rows, whatever the size of the register. The
        count reads every car of the filter, so a caller that shows the rows
        first asks without it (`with_total=False`) and for `count` beside it.
        """

        clauses = terms(conditions)
        if cursor is not None and not is_vehicle_id(cursor):
            raise ValueError("cursor must be a vehicle id from a previous page")
        rows = self._repository.page(clauses, text.strip(), after=cursor, limit=limit)
        counted = with_total and cursor is None
        matched = self._repository.count(clauses, text.strip()) if counted else None
        items = [VehicleRow(**row) for row in rows]
        has_more = len(items) == limit
        return VehiclePage(
            items=items,
            matched_rows=matched,
            next_cursor=items[-1].vehicle_id if items and has_more else None,
            has_more=has_more,
        )

    def count(self, conditions: Sequence[VehicleCondition], text: str) -> VehicleTotal:
        """How many vehicles the filter matches: what a first page leaves out on request."""

        return VehicleTotal(matched_rows=self._repository.count(terms(conditions), text.strip()))

    def facet(
        self, conditions: Sequence[VehicleCondition], text: str, *, field: str, limit: int
    ) -> VehicleFacet:
        values = self._repository.facet(terms(conditions), text.strip(), field=field, limit=limit)
        return VehicleFacet(
            field=field, values=[FacetValue(value=value, count=count) for value, count in values]
        )

    def record(self, vehicle_id: str) -> VehicleRecord:
        key = vehicle_id.strip().upper()
        if not is_vehicle_id(key):
            raise InvalidVehicleIdError(f"{vehicle_id!r} is not a NorthStar vehicle id (NOR-…).")
        rows = self._repository.record(key)
        if rows is None:
            raise VehicleNotFoundError(f"No vehicle {key}.")
        vehicle = rows.vehicle
        fields = field_values(vehicle)
        return VehicleRecord(
            vehicle_id=str(vehicle["vehicle_id"]),
            origin_source=str(vehicle["origin_source"]),
            origin_observed_on=vehicle.get("origin_observed_on"),
            ts_record_id=vehicle.get("ts_record_id"),
            registry_status=str(vehicle["registry_status"]),
            created_at=vehicle["created_at"],
            updated_at=vehicle["updated_at"],
            fields=fields,
            identifiers=[
                VehicleIdentifier(**identifier, current=identifier["valid_to"] is None)
                for identifier in rows.identifiers
            ],
            source_links=[VehicleSourceLink(**link) for link in rows.links],
            source_link_count=rows.link_count,
            rules=[EnrichmentRuleInfo(**rule) for rule in self._repository.rules(rule_ids(fields))],
        )
