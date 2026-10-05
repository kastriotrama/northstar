"""HTTP surface for matching statistics read from stored results."""

from __future__ import annotations

from contextlib import AbstractContextManager
from functools import lru_cache
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg import Connection

from api.app.core.db import get_postgres_connection
from api.app.core.settings import get_settings
from api.app.features.vehicle_match_results.refresh import MatchResultRefresher
from api.app.features.vehicle_match_results.repository import MatchResultRepository
from api.app.features.vehicle_match_results.schemas import (
    MatchResultCarPage,
    MatchResultCarsRequest,
    MatchResultCounts,
    MatchResultOverview,
    MatchResultRefresh,
    ReviewerRuleChanges,
)
from api.app.features.vehicle_match_results.service import MatchResultService
from api.app.features.vehicle_match_results.sync import MatchResultSync
from api.app.features.vehicle_matching.router import _matcher_cache
from api.app.features.vehicles.schemas import VehicleFilter
from ingestion.vehicle_facts_query import UnknownFieldError

router = APIRouter(prefix="/v1/vehicles/match-results", tags=["vehicle-match-results"])


@lru_cache(maxsize=1)
def _repository() -> MatchResultRepository:
    settings = get_settings()
    return MatchResultRepository(lambda: get_postgres_connection(settings))


@lru_cache(maxsize=1)
def match_result_sync() -> MatchResultSync:
    """What a saved correction or choice calls so the car's stored result follows."""

    settings = get_settings()

    def connect() -> AbstractContextManager[Connection[Any]]:
        return get_postgres_connection(settings)

    refresher = MatchResultRefresher(connect, _matcher_cache().get, settings.build_version)
    return MatchResultSync(lambda: refresher, connect)


def get_service() -> MatchResultService:
    return MatchResultService(_repository())


ServiceDependency = Annotated[MatchResultService, Depends(get_service)]


def _unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Match results are temporarily unavailable.")


@router.post("/counts", response_model=MatchResultCounts)
def counts(vehicle_filter: VehicleFilter, service: ServiceDependency) -> MatchResultCounts:
    """Cars per matching state under the Vehicles filter: one grouped query, no matcher."""

    try:
        counted = service.counts(vehicle_filter)
        counted.refreshing = match_result_sync().refreshing
        return counted
    except (UnknownFieldError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


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


@router.post("/refresh", response_model=MatchResultRefresh, status_code=202)
def refresh_changed_cars() -> MatchResultRefresh:
    """Match again every car that is new or changed since its result was stored.

    Runs in the background on this server and returns at once; the counts say
    `refreshing` while it works and `changed_since_matched` falls as it goes.
    Asking while one runs is safe: it runs once more when it ends.
    """

    sync = match_result_sync()
    started = sync.population_changed()
    return MatchResultRefresh(started=started, refreshing=True)


@router.get("/reviewer-rules", response_model=ReviewerRuleChanges)
def reviewer_rules(
    service: ServiceDependency, limit: int = Query(default=50, ge=1, le=200)
) -> ReviewerRuleChanges:
    """The latest reviewer rules from the TS data screen, as changes to many cars.

    Who made each, what it sets, how many vehicles it reached and how many of
    those have a match result older than the change.
    """

    try:
        return service.reviewer_rules(limit)
    except psycopg.Error as error:
        raise _unavailable() from error

