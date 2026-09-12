from typing import Annotated, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from api.app.core.db import get_postgres_connection
from api.app.core.settings import Settings, get_settings
from api.app.features.tecdoc_review.gaps import TecDocResolveError
from api.app.features.tecdoc_review.predicate import UnknownTecDocFieldError
from api.app.features.tecdoc_review.repository import TecDocReviewRepository
from api.app.features.tecdoc_review.schemas import (
    TecDocEntityPage,
    TecDocGapValuesResponse,
    TecDocResolution,
    TecDocResolveRequest,
    TecDocReviewPage,
    TecDocUnresolvedSummary,
    TecDocVehicleCount,
    TecDocVehicleDetail,
    TecDocVehicleFacet,
    TecDocVehicleFilter,
)
from api.app.features.tecdoc_review.service import TecDocReviewService

router = APIRouter(prefix="/v1/normalization-review/tecdoc", tags=["tecdoc-review"])


def get_tecdoc_review_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TecDocReviewService:
    return TecDocReviewService(TecDocReviewRepository(lambda: get_postgres_connection(settings)))


@router.get("/vehicles", response_model=TecDocReviewPage)
def list_tecdoc_vehicles(
    service: Annotated[TecDocReviewService, Depends(get_tecdoc_review_service)],
    query: str = Query(default="", max_length=120),
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
) -> TecDocReviewPage:
    try:
        return service.list_vehicles(query=query, limit=limit, offset=offset)
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503, detail="TecDoc review data is temporarily unavailable."
        ) from error


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=503, detail="TecDoc review data is temporarily unavailable."
    )


def _bad_field(error: UnknownTecDocFieldError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


@router.post("/vehicles/page", response_model=TecDocReviewPage)
def page_tecdoc_vehicles(
    request: TecDocVehicleFilter,
    service: Annotated[TecDocReviewService, Depends(get_tecdoc_review_service)],
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
) -> TecDocReviewPage:
    """Filtered, paged KTypes -- the structured-condition sibling of `/vehicles`."""

    try:
        return service.list_vehicles(
            query=request.query,
            limit=limit,
            offset=offset,
            conditions=request.conditions,
            unresolved_field=request.unresolved_field,
        )
    except UnknownTecDocFieldError as error:
        raise _bad_field(error) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@router.post("/vehicles/count", response_model=TecDocVehicleCount)
def count_tecdoc_vehicles(
    request: TecDocVehicleFilter,
    service: Annotated[TecDocReviewService, Depends(get_tecdoc_review_service)],
) -> TecDocVehicleCount:
    try:
        return service.count_vehicles(
            query=request.query,
            conditions=request.conditions,
            unresolved_field=request.unresolved_field,
        )
    except UnknownTecDocFieldError as error:
        raise _bad_field(error) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@router.post("/vehicles/unresolved-summary", response_model=TecDocUnresolvedSummary)
def tecdoc_unresolved_summary(
    request: TecDocVehicleFilter,
    service: Annotated[TecDocReviewService, Depends(get_tecdoc_review_service)],
) -> TecDocUnresolvedSummary:
    """What the filtered KTypes still cannot say about themselves -- the worklist."""

    try:
        return service.unresolved_vehicle_summary(
            query=request.query, conditions=request.conditions
        )
    except UnknownTecDocFieldError as error:
        raise _bad_field(error) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@router.post("/vehicles/facets", response_model=TecDocVehicleFacet)
def facet_tecdoc_vehicles(
    request: TecDocVehicleFilter,
    service: Annotated[TecDocReviewService, Depends(get_tecdoc_review_service)],
    field: str = Query(max_length=60),
    limit: int = Query(default=12, ge=1, le=100),
) -> TecDocVehicleFacet:
    """Top values of one field inside the filter -- what still varies."""

    try:
        return service.facet_vehicles(
            query=request.query,
            conditions=request.conditions,
            unresolved_field=request.unresolved_field,
            field=field,
            limit=limit,
        )
    except UnknownTecDocFieldError as error:
        raise _bad_field(error) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@router.get("/vehicles/detail", response_model=TecDocVehicleDetail)
def tecdoc_vehicle_detail(
    service: Annotated[TecDocReviewService, Depends(get_tecdoc_review_service)],
    source_key: str = Query(...),
) -> TecDocVehicleDetail:
    """One KType's canonical fields, each with its outcome -- opened from a row
    the same way TS's record panel opens from a car, so the fields still
    missing a value are the ones with a Resolve action next to them."""

    detail = service.vehicle_detail(source_key=source_key)
    if detail is None:
        raise HTTPException(status_code=404, detail="No promoted KType with that source key.")
    return detail


def _bad_resolve(error: TecDocResolveError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


@router.get("/gaps", response_model=TecDocGapValuesResponse)
def tecdoc_gap_values(
    service: Annotated[TecDocReviewService, Depends(get_tecdoc_review_service)],
    field: Literal[
        "energy_sources", "bodywork_form", "drive_type", "transmission_type"
    ] = Query(...),
    limit: int = Query(default=100, ge=1, le=500),
) -> TecDocGapValuesResponse:
    """The distinct raw values behind one canonical field's gap -- the click
    target a reviewer resolves, one level under the row-level ktype count
    `/vehicles/unresolved-summary` reports."""

    try:
        return service.gap_values(canonical_field=field, limit=limit)
    except TecDocResolveError as error:
        raise _bad_resolve(error) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@router.post("/gaps/resolve", response_model=TecDocResolution)
def resolve_tecdoc_gap_value(
    request: TecDocResolveRequest,
    service: Annotated[TecDocReviewService, Depends(get_tecdoc_review_service)],
) -> TecDocResolution:
    """Write one reviewer's live ruling on one value.

    Not the same act as sealing a `tecdoc_rules` version: this is immediately
    visible in `/gaps` and `/vehicles/*`, mutable, and separate from the
    reviewed mappings `generate-tecdoc-rules` reads from
    `ingestion.tecdoc.reference_data` -- promoting a ruling made here into that
    file is a deliberate follow-up, not something this endpoint does itself.
    """

    try:
        return service.resolve(
            canonical_field=request.canonical_field,
            source_term=request.source_term,
            decision=request.decision,
            canonical_value=request.canonical_value,
            note=request.note,
            reviewed_by=request.reviewed_by,
            source_system=request.source_system,
            relation=request.relation,
            support=request.support,
        )
    except TecDocResolveError as error:
        raise _bad_resolve(error) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@router.get("/entities", response_model=TecDocEntityPage)
def list_tecdoc_entities(
    service: Annotated[TecDocReviewService, Depends(get_tecdoc_review_service)],
    kind: Literal[
        "manufacturer", "model_family", "engine", "fuel", "bodywork", "transmission", "drive"
    ] = "manufacturer",
    query: str = Query(default="", max_length=120),
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
) -> TecDocEntityPage:
    try:
        return service.list_entities(kind=kind, query=query, limit=limit, offset=offset)
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503, detail="TecDoc entity data is temporarily unavailable."
        ) from error
