"""HTTP surface for diagnosing TS-to-TecDoc matching from the Vehicles tab."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from api.app.core.db import get_postgres_connection
from api.app.core.settings import get_settings
from api.app.features.vehicle_matching.repository import VehicleMatchingRepository
from api.app.features.vehicle_matching.schemas import (
    MatchSummaryJob,
    MatchSummaryRequest,
    VehicleMatchLookup,
)
from api.app.features.vehicle_matching.service import (
    JobCapacityError,
    MatcherCache,
    NoCatalogError,
    SummaryJobNotFoundError,
    SummaryJobs,
    VehicleMatchingService,
    VehicleNotFoundError,
    build_matcher,
)
from ingestion.vehicle_facts_query import UnknownFieldError

router = APIRouter(prefix="/v1/vehicles/matching", tags=["vehicle-matching"])


@lru_cache(maxsize=1)
def _repository() -> VehicleMatchingRepository:
    settings = get_settings()
    return VehicleMatchingRepository(lambda: get_postgres_connection(settings))


@lru_cache(maxsize=1)
def _matcher_cache() -> MatcherCache:
    batch = get_settings().tecdoc_match_catalog_batch
    return MatcherCache(lambda: build_matcher(_repository(), batch))


@lru_cache(maxsize=1)
def _jobs() -> SummaryJobs:
    return SummaryJobs()


def get_service() -> VehicleMatchingService:
    return VehicleMatchingService(_repository(), _matcher_cache().get, _jobs())


ServiceDependency = Annotated[VehicleMatchingService, Depends(get_service)]


def _unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Matching data is temporarily unavailable.")


@router.get("/lookup", response_model=VehicleMatchLookup)
def lookup_vehicle(
    service: ServiceDependency,
    q: str = Query(min_length=1, max_length=40, description="A plate or a VIN."),
) -> VehicleMatchLookup:
    """Which KTypes one car could be, and why the matcher decided as it did."""

    try:
        return service.lookup(q)
    except VehicleNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except NoCatalogError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@router.post("/summary", response_model=MatchSummaryJob, status_code=202)
def start_summary(
    request: MatchSummaryRequest, service: ServiceDependency
) -> MatchSummaryJob:
    """Start running the matcher over the first `limit` cars of a filter.

    Returns at once with a job to poll: the matcher spends ~0.1s a car, so a
    summary of thousands takes minutes. Counts fill in as it runs.
    """

    try:
        return service.start_summary(request.conditions, request.text, request.limit)
    except (UnknownFieldError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except JobCapacityError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@router.get("/summary/{job_id}", response_model=MatchSummaryJob)
def get_summary(job_id: str, service: ServiceDependency) -> MatchSummaryJob:
    try:
        return service.summary_job(job_id)
    except SummaryJobNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.delete("/summary/{job_id}", response_model=MatchSummaryJob)
def cancel_summary(job_id: str, service: ServiceDependency) -> MatchSummaryJob:
    """Stop a running summary; what it has counted so far is kept."""

    try:
        return service.cancel_summary(job_id)
    except SummaryJobNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
