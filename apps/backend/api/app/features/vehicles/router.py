"""HTTP surface for NorthStar vehicles (`core.vehicles`), keyed by NOR ID.

The TS screen's per-record endpoints live under `/v1/ts-records`; these read
the provider-independent vehicle every source enriches.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from api.app.core.db import get_postgres_connection
from api.app.core.settings import get_settings
from api.app.features.vehicles.repository import VehicleRepository
from api.app.features.vehicles.schemas import (
    VehicleFacet,
    VehicleFieldInfo,
    VehicleFilter,
    VehiclePage,
    VehicleRecord,
)
from api.app.features.vehicles.service import (
    InvalidVehicleIdError,
    VehicleNotFoundError,
    VehicleService,
    field_catalog,
)
from ingestion.vehicle_facts_query import UnknownFieldError

router = APIRouter(prefix="/v1/vehicles", tags=["vehicles"])


@lru_cache(maxsize=1)
def _repository() -> VehicleRepository:
    settings = get_settings()
    return VehicleRepository(lambda: get_postgres_connection(settings))


def get_service() -> VehicleService:
    return VehicleService(_repository())


ServiceDependency = Annotated[VehicleService, Depends(get_service)]


def _unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Vehicle data is temporarily unavailable.")


@router.get("/fields", response_model=list[VehicleFieldInfo])
def list_fields() -> list[VehicleFieldInfo]:
    """Every column of the vehicle record: label, group, and whether it can be filtered."""

    return field_catalog()


@router.post("/search", response_model=VehiclePage)
def search_vehicles(
    request: VehicleFilter,
    service: ServiceDependency,
    cursor: str | None = Query(default=None, max_length=30),
    limit: int = Query(default=50, ge=1, le=200),
) -> VehiclePage:
    """Find vehicles by their merged values, identifiers (current or past) or NOR ID."""

    try:
        return service.search(request.conditions, request.text, cursor=cursor, limit=limit)
    except (UnknownFieldError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@router.post("/facets", response_model=VehicleFacet)
def facet_vehicles(
    request: VehicleFilter,
    service: ServiceDependency,
    field: str = Query(max_length=60),
    limit: int = Query(default=12, ge=1, le=200),
) -> VehicleFacet:
    """Top values of one field inside the filter, counted as if it were not filtered on."""

    try:
        return service.facet(request.conditions, request.text, field=field, limit=limit)
    except (UnknownFieldError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@router.get("/{vehicle_id}", response_model=VehicleRecord)
def get_vehicle(vehicle_id: str, service: ServiceDependency) -> VehicleRecord:
    """One vehicle: each value with its source, the values that lost, plates over time."""

    try:
        return service.record(vehicle_id)
    except InvalidVehicleIdError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except VehicleNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error
