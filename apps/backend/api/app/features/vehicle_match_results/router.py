"""HTTP surface for matching statistics read from stored results."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from api.app.core.db import get_postgres_connection
from api.app.core.settings import get_settings
from api.app.features.vehicle_match_results.repository import MatchResultRepository
from api.app.features.vehicle_match_results.schemas import (
    MatchResultCarPage,
    MatchResultCarsRequest,
    MatchResultOverview,
)
from api.app.features.vehicle_match_results.service import MatchResultService
from api.app.features.vehicles.schemas import VehicleFilter
from ingestion.vehicle_facts_query import UnknownFieldError

router = APIRouter(prefix="/v1/vehicles/match-results", tags=["vehicle-match-results"])


@lru_cache(maxsize=1)
def _repository() -> MatchResultRepository:
    settings = get_settings()
    return MatchResultRepository(lambda: get_postgres_connection(settings))


def get_service() -> MatchResultService:
    return MatchResultService(_repository())


ServiceDependency = Annotated[MatchResultService, Depends(get_service)]


def _unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Match results are temporarily unavailable.")


@router.post("/overview", response_model=MatchResultOverview)
def overview(vehicle_filter: VehicleFilter, service: ServiceDependency) -> MatchResultOverview:
    """Where every car of the Vehicles filter stands with matching, from stored results.

    Counts cars by state -- resolved, several possible KTypes, one unconfirmed,
    none, not matchable, decided by a person, not evaluated yet -- and names the
    fields behind the open ones. The matcher is not run.
    """

    try:
        return service.overview(vehicle_filter)
    except (UnknownFieldError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@router.post("/cars", response_model=MatchResultCarPage)
def cars(request: MatchResultCarsRequest, service: ServiceDependency) -> MatchResultCarPage:
    """The cars behind one number of the overview, a page at a time.

    Each car carries its accepted KType or its possible KTypes and the short
    reasons stored with them. For one car in full, ask the matching lookup.
    """

    try:
        return service.cars(request)
    except (UnknownFieldError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error
