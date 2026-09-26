"""The Vehicles tab reads `core.vehicles`, and matching sees a vehicle's merged values.

Runs against a throwaway database: see `throwaway_database`. The vehicles are
built the way production builds them -- TS records normalized by the real
pipeline, projected, then backfilled into `core.vehicles`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, nullcontext
from datetime import date
from typing import Any

import pytest
from psycopg import Connection

from api.app.features.vehicle_matching.repository import VehicleMatchingRepository
from api.app.features.vehicles.repository import VehicleRepository
from api.app.features.vehicles.schemas import VehicleCondition
from api.app.features.vehicles.service import VehicleService, terms
from ingestion.vehicle_core_fields import SOURCE_REVIEW, SourceRef
from ingestion.vehicle_core_merge import Observation, merge
from ingestion.vehicle_core_store import load_vehicle, save_vehicles
from ingestion.vehicle_core_ts import backfill_vehicle_core
from ingestion.vehicle_facts_query import UnknownFieldError
from tests.integration.throwaway_database import throwaway_database
from tests.integration.vehicle_core_fixtures import (
    insert_ts_record,
    prepare_schema,
    project,
    volvo,
)


@pytest.fixture(scope="module")
def db() -> Iterator[Connection]:
    with throwaway_database("vehicles_api") as connection:
        prepare_schema(connection)
        # One Volvo first seen on a temporary plate, then on its permanent one.
        insert_ts_record(connection, volvo(plate="TPD118"), batch="old", ingested_at="2026-08-01")
        insert_ts_record(connection, volvo(plate="ABC123"), batch="new", ingested_at="2026-08-07")
        # A second, unrelated car.
        insert_ts_record(
            connection,
            volvo(vin="WVWZZZ1KZ8W123456", plate="GLF001", brand="VOLKSWAGEN", model="GOLF",
                  fab_code="VW", variant=None, version=None, kw="77", ccm="1390",
                  vehicle_year=2008, registration_date="20080415", build_month="200803"),
        )
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        connection.commit()
        yield connection


def _factory(connection: Connection) -> Any:
    def factory() -> AbstractContextManager[Connection]:
        return nullcontext(connection)

    return factory


@pytest.fixture
def service(db: Connection) -> VehicleService:
    return VehicleService(VehicleRepository(_factory(db)))


def _volvo_id(db: Connection) -> str:
    with db.cursor() as cursor:
        cursor.execute("SELECT vehicle_id FROM core.vehicles WHERE vin = 'YV1BW84S1F1234567'")
        return str(cursor.fetchone()[0])


def test_a_car_is_found_by_its_plate_its_previous_plate_and_its_nor_id(
    service: VehicleService, db: Connection
) -> None:
    volvo_id = _volvo_id(db)

    for text in ("ABC123", "abc 123", "TPD118", volvo_id, "volvo v70"):
        page = service.search([], text, cursor=None, limit=10)
        assert [row.vehicle_id for row in page.items] == [volvo_id], text
        assert page.matched_rows == 1

    assert service.search([], "", cursor=None, limit=10).matched_rows == 2


def test_conditions_read_the_merged_columns(service: VehicleService) -> None:
    newer = [VehicleCondition(field="production_year", operator="gte", values=["2010"])]
    page = service.search(newer, "", cursor=None, limit=10)

    assert [row.plate for row in page.items] == ["ABC123"]
    assert page.items[0].registry_status == "registered"
    assert page.items[0].manufacturer is not None

    with pytest.raises(UnknownFieldError):
        service.search([VehicleCondition(field="field_sources", values=["x"])], "", cursor=None,
                       limit=10)


def test_pages_follow_the_nor_id_keyset(service: VehicleService) -> None:
    first = service.search([], "", cursor=None, limit=1)
    second = service.search([], "", cursor=first.next_cursor, limit=1)

    assert first.next_cursor is not None
    assert second.items[0].vehicle_id > first.items[0].vehicle_id
    assert second.matched_rows is None


def test_a_facet_counts_as_if_its_own_filter_were_lifted(service: VehicleService) -> None:
    volvo_only = service.search([], "volvo", cursor=None, limit=1).items[0].manufacturer
    assert volvo_only is not None
    chosen = [VehicleCondition(field="manufacturer", values=[volvo_only])]

    facet = service.facet(chosen, "", field="manufacturer", limit=10)

    assert len(facet.values) == 2
    assert sum(value.count for value in facet.values) == 2


def test_a_record_shows_sources_plate_history_and_links(
    service: VehicleService, db: Connection
) -> None:
    record = service.record(_volvo_id(db))

    plates = [(item.value, item.current) for item in record.identifiers if item.kind == "plate"]
    assert plates == [("ABC123", True), ("TPD118", False)]
    assert record.origin_source == "transportstyrelsen"
    assert {link.source_system for link in record.source_links} == {"transportstyrelsen"}
    assert record.source_link_count == len(record.source_links) == 2
    manufacturer = next(field for field in record.fields if field.field == "manufacturer")
    assert manufacturer.source is not None and manufacturer.source.source == "transportstyrelsen"


def test_a_reviewed_value_is_marked_and_reaches_the_matcher(
    service: VehicleService, db: Connection
) -> None:
    volvo_id = _volvo_id(db)
    state = load_vehicle(db, volvo_id)
    assert state is not None
    derived = state.values.get("bodywork_form")
    merge(state, {"bodywork_form": Observation("suv", SourceRef(SOURCE_REVIEW, "rule-7"))})
    save_vehicles(db, [state])
    db.commit()

    row = service.search([], "ABC123", cursor=None, limit=1).items[0]
    assert row.bodywork_form == "suv"
    assert row.review_fields == ["bodywork_form"]

    field = next(item for item in service.record(volvo_id).fields if item.field == "bodywork_form")
    assert field.source is not None and field.source.source == "review"
    assert [alternative.value for alternative in field.alternatives] == (
        [derived] if derived else []
    )

    matching = VehicleMatchingRepository(_factory(db))
    (car,) = matching.vehicle_car_records([volvo_id])
    assert car.vehicle_id == volvo_id
    assert car.source_record_id is not None
    assert car.record.payload["normalized"]["bodywork_form"] == "suv"  # type: ignore[index]
    assert car.overlaid["bodywork_form"] == "review"
    assert car.rule_filled == ("bodywork_form",)


def test_matching_finds_a_vehicle_by_a_plate_it_used_to_carry(db: Connection) -> None:
    matching = VehicleMatchingRepository(_factory(db))
    volvo_id = _volvo_id(db)

    assert matching.vehicles_for_identifier("TPD118") == [volvo_id]
    assert matching.vehicles_for_identifier(volvo_id) == [volvo_id]
    assert matching.vehicles_for_identifier("NOPE") == []

    condition = VehicleCondition(field="first_registration_date", operator="lte",
                                 values=[date(2010, 1, 1).isoformat()])
    total, ids = matching.vehicle_population(terms([condition]), "", limit=5)
    assert total == 1
    assert ids != [volvo_id]
