"""HTTP surface for field-level normalization coverage."""

from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from api.app.core.db import get_postgres_connection
from api.app.core.settings import Settings, get_settings
from api.app.features.coverage.repository import CoverageRepository
from api.app.features.coverage.schemas import (
    CoverageBatchList,
    TecDocCoverageReport,
    TsCoverageReport,
)
from api.app.features.coverage.service import CoverageError, CoverageService

router = APIRouter(prefix="/v1/coverage", tags=["coverage"])


def get_coverage_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> CoverageService:
    return CoverageService(CoverageRepository(lambda: get_postgres_connection(settings)))


@router.get("/batches", response_model=CoverageBatchList)
def list_coverage_batches(
    service: Annotated[CoverageService, Depends(get_coverage_service)],
) -> CoverageBatchList:
    try:
        return service.list_batches()
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503, detail="Coverage batch data is temporarily unavailable."
        ) from error


@router.get("/ts", response_model=TsCoverageReport)
def ts_coverage(
    service: Annotated[CoverageService, Depends(get_coverage_service)],
    batch_id: str | None = Query(default=None, max_length=200),
) -> TsCoverageReport:
    try:
        resolved = batch_id or service.latest_batch_id()
        if resolved is None:
            raise HTTPException(status_code=404, detail="No normalization batches found.")
        return service.ts_coverage(batch_id=resolved)
    except CoverageError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except psycopg.errors.QueryCanceled as error:
        raise HTTPException(
            status_code=504,
            detail="Coverage computation timed out for this batch.",
        ) from error
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503, detail="Coverage data is temporarily unavailable."
        ) from error


@router.get("/tecdoc", response_model=TecDocCoverageReport)
def tecdoc_coverage(
    service: Annotated[CoverageService, Depends(get_coverage_service)],
) -> TecDocCoverageReport:
    try:
        return service.tecdoc_coverage()
    except psycopg.errors.QueryCanceled as error:
        raise HTTPException(
            status_code=504, detail="TecDoc coverage computation timed out."
        ) from error
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503, detail="TecDoc coverage data is temporarily unavailable."
        ) from error
