"""The Vehicles tab's reading of `core.vehicles`: sources, alternatives, paging, HTTP."""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.app.features.vehicles.repository import VehicleRecordRows
from api.app.features.vehicles.router import get_service
from api.app.features.vehicles.schemas import VehicleCondition
from api.app.features.vehicles.service import (
    InvalidVehicleIdError,
    VehicleNotFoundError,
    VehicleService,
    field_catalog,
    field_values,
    rule_ids,
    value_source,
)
from ingestion.vehicle_core_fields import CORE_FIELDS, FILTERABLE_FIELDS

VEHICLE_ID = "NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7G"
OBSERVED = date(2023, 12, 1)


def _vehicle(**values: Any) -> dict[str, Any]:
    row: dict[str, Any] = {field.name: None for field in CORE_FIELDS}
    row.update(
        vehicle_id=VEHICLE_ID,
        origin_source="transportstyrelsen",
        origin_observed_on=OBSERVED,
        ts_record_id=7,
        registry_status="registered",
        field_sources={},
        field_alternatives={},
        created_at=datetime(2026, 9, 26, tzinfo=UTC),
        updated_at=datetime(2026, 9, 26, tzinfo=UTC),
    )
    row.update(values)
    return row


def _row(vehicle_id: str) -> dict[str, Any]:
    columns = ("plate", "vin", "vehicle_scope", "manufacturer", "model_family", "production_year",
               "power_kw", "displacement_cc", "engine_code", "fuel", "transmission", "drive_type",
               "bodywork_form", "colour", "ktype")
    return {
        "vehicle_id": vehicle_id,
        "registry_status": "registered",
        **{column: None for column in columns},
        "review_fields": [],
        "rule_fields": [],
    }


class _Repository:
    def __init__(self, vehicle: dict[str, Any] | None = None) -> None:
        self.vehicle = vehicle
        self.pages: list[tuple[Any, ...]] = []
        self.counted = 0

    def page(self, terms: Sequence[Any], text: str, *, after: str | None, limit: int) -> list[
        dict[str, Any]
    ]:
        self.pages.append((list(terms), text, after, limit))
        return [_row(f"NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7{suffix}") for suffix in "GH"][:limit]

    def count(self, terms: Sequence[Any], text: str) -> int:
        self.counted += 1
        return 2

    def facet(self, terms: Sequence[Any], text: str, *, field: str, limit: int) -> list[
        tuple[str, int]
    ]:
        return [("VOLVO", 3)]

    def record(self, vehicle_id: str) -> VehicleRecordRows | None:
        if self.vehicle is None or vehicle_id != self.vehicle["vehicle_id"]:
            return None
        identifiers = [
            {"kind": "plate", "value": "ABC123", "valid_from": date(2016, 1, 1), "valid_to": None,
             "source": "transportstyrelsen", "source_ref": "7"},
            {"kind": "plate", "value": "TPD118", "valid_from": date(2015, 3, 1),
             "valid_to": date(2016, 1, 1), "source": "transportstyrelsen", "source_ref": "3"},
        ]
        links = [
            {"source_system": "transportstyrelsen", "source_record_key": "7",
             "observed_on": OBSERVED, "link_method": "minted",
             "linked_at": datetime(2026, 9, 26, tzinfo=UTC)},
        ]
        return VehicleRecordRows(self.vehicle, identifiers, links, 1)

    def rules(self, ids: Sequence[str]) -> list[dict[str, Any]]:
        return [
            {"rule_id": rule_id, "rule_family": "engine_code", "target_field": "engine_code",
             "key_fields": ["type_approval"], "key_values": ["e9*2001/116*0123"],
             "value": "D4204T14", "support": 41, "agreement": 0.98, "status": "active"}
            for rule_id in ids
        ]


# ----------------------------------------------------------------------------- sources


def test_a_field_without_a_recorded_source_came_from_the_origin_record() -> None:
    source = value_source(None, origin_source="transportstyrelsen", origin_observed_on=OBSERVED)

    assert source.source == "transportstyrelsen"
    assert source.observed_on == OBSERVED
    assert source.origin is True


def test_a_recorded_source_is_parsed_into_its_parts() -> None:
    source = value_source(
        "ais:2026-09-19T10:28:27@2026-09-19",
        origin_source="transportstyrelsen",
        origin_observed_on=OBSERVED,
    )

    assert (source.source, source.ref, source.observed_on) == (
        "ais", "2026-09-19T10:28:27", date(2026, 9, 19)
    )
    assert source.origin is False


def test_every_field_is_listed_with_its_source_and_what_lost() -> None:
    vehicle = _vehicle(
        engine_code="D4204T14",
        colour="SVART",
        bodywork_form="suv",
        field_sources={"engine_code": "rule:ENG-1", "bodywork_form": "review:r-1"},
        field_alternatives={
            "bodywork_form": [{"source": "transportstyrelsen:7@2023-12-01", "value": "estate"}]
        },
    )

    fields = {field.field: field for field in field_values(vehicle)}

    assert list(fields) == [field.name for field in CORE_FIELDS]
    assert fields["colour"].source is not None and fields["colour"].source.origin
    assert fields["engine_code"].source is not None
    assert fields["engine_code"].source.source == "rule"
    assert fields["bodywork_form"].alternatives[0].value == "estate"
    assert fields["bodywork_form"].alternatives[0].source.origin is True
    # An empty field has no source to show.
    assert fields["kerb_weight_kg"].source is None
    assert rule_ids(list(fields.values())) == ["ENG-1"]


def test_the_field_catalog_marks_what_can_be_filtered() -> None:
    catalog = {info.field: info for info in field_catalog()}

    assert catalog["manufacturer"].filterable and catalog["manufacturer"].reviewable
    assert not catalog["vehicle_variant_id"].filterable
    assert {name for name, info in catalog.items() if info.filterable} == set(FILTERABLE_FIELDS)


# ------------------------------------------------------------------------------ service


def test_the_first_page_carries_the_count_and_a_keyset_cursor() -> None:
    repository = _Repository()
    service = VehicleService(repository)  # type: ignore[arg-type]
    condition = VehicleCondition(field="fuel", values=["diesel"])

    page = service.search([condition], " volvo ", cursor=None, limit=2)

    assert page.matched_rows == 2
    assert page.has_more is True
    assert page.next_cursor == page.items[-1].vehicle_id
    assert repository.pages[0] == ([("fuel", "equals", ("diesel",))], "volvo", None, 2)

    later = service.search([], "", cursor=page.next_cursor, limit=50)
    assert later.matched_rows is None
    assert later.next_cursor is None
    assert repository.counted == 1


def test_a_cursor_that_is_not_a_vehicle_id_is_refused() -> None:
    with pytest.raises(ValueError):
        VehicleService(_Repository()).search([], "", cursor="42", limit=5)  # type: ignore[arg-type]


def test_a_record_lists_plate_history_links_and_the_rules_behind_its_values() -> None:
    vehicle = _vehicle(plate="ABC123", engine_code="D4204T14", field_sources={"engine_code": "rule:ENG-1"})
    service = VehicleService(_Repository(vehicle))  # type: ignore[arg-type]

    record = service.record(VEHICLE_ID.lower())

    assert record.vehicle_id == VEHICLE_ID
    assert [(item.value, item.current) for item in record.identifiers] == [
        ("ABC123", True), ("TPD118", False)
    ]
    assert record.source_links[0].link_method == "minted"
    assert [rule.rule_id for rule in record.rules] == ["ENG-1"]


def test_a_record_that_is_not_there_or_not_a_nor_id_says_which() -> None:
    service = VehicleService(_Repository(_vehicle()))  # type: ignore[arg-type]

    with pytest.raises(VehicleNotFoundError):
        service.record("NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7Z")
    with pytest.raises(InvalidVehicleIdError):
        service.record("ABC123")


# --------------------------------------------------------------------------------- HTTP


def test_http_maps_the_services_answers(client: TestClient) -> None:
    client.app.dependency_overrides[get_service] = lambda: VehicleService(  # type: ignore[attr-defined]
        _Repository(_vehicle())  # type: ignore[arg-type]
    )
    try:
        assert client.get(f"/v1/vehicles/{VEHICLE_ID}").status_code == 200
        assert client.get("/v1/vehicles/ABC123").status_code == 422
        assert client.get("/v1/vehicles/NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7Z").status_code == 404
        searched = client.post("/v1/vehicles/search", json={"text": "volvo"}, params={"limit": 1})
        assert searched.status_code == 200
        assert searched.json()["next_cursor"] == searched.json()["items"][0]["vehicle_id"]
        assert client.post("/v1/vehicles/search", json={}, params={"cursor": "1"}).status_code == 422
        ranged = {"conditions": [{"field": "power_kw", "operator": "gte", "values": ["1", "2"]}]}
        assert client.post("/v1/vehicles/search", json=ranged).status_code == 422
        facet = client.post("/v1/vehicles/facets", json={}, params={"field": "manufacturer"})
        assert facet.json() == {"field": "manufacturer", "values": [{"value": "VOLVO", "count": 3}]}
        fields = client.get("/v1/vehicles/fields").json()
        assert fields[0]["field"] == CORE_FIELDS[0].name
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def test_ts_record_endpoints_moved_off_the_vehicles_prefix(client: TestClient) -> None:
    """`/v1/vehicles/{id}` is a NOR ID now; TS records are served from `/v1/ts-records`."""

    paths = set(client.app.openapi()["paths"])  # type: ignore[attr-defined]

    assert "/v1/ts-records/search" in paths
    assert "/v1/ts-records/{source_record_id}/full" in paths
    assert "/v1/vehicles/{vehicle_id}" in paths
    assert "/v1/vehicles/count" not in paths
