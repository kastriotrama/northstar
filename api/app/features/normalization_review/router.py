from pathlib import Path
from typing import Annotated, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from api.app.core.db import get_postgres_connection
from api.app.core.settings import Settings, get_settings
from api.app.features.normalization_review.repository import NormalizationReviewRepository
from api.app.features.normalization_review.schemas import (
    NormalizationReviewFilters,
    NormalizationReviewPage,
)
from api.app.features.normalization_review.service import NormalizationReviewService

STATIC_DIRECTORY = Path(__file__).with_name("static")

api_router = APIRouter(prefix="/v1/normalization-review", tags=["normalization-review"])
screen_router = APIRouter(tags=["normalization-review"])


def get_normalization_review_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> NormalizationReviewService:
    repository = NormalizationReviewRepository(
        connection_factory=lambda: get_postgres_connection(settings)
    )
    return NormalizationReviewService(repository)


@api_router.get("/vehicles", response_model=NormalizationReviewPage)
def list_normalized_vehicles(
    service: Annotated[NormalizationReviewService, Depends(get_normalization_review_service)],
    query: str = Query(default="", max_length=120),
    status: Literal["resolved", "provisional", "review_required", "failed"] | None = None,
    manufacturer: str | None = Query(default=None, max_length=120),
    bodywork: str | None = Query(default=None, max_length=80),
    fuel: str | None = Query(default=None, max_length=80),
    transmission: str | None = Query(default=None, max_length=80),
    batch_id: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=250, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
) -> NormalizationReviewPage:
    filters = NormalizationReviewFilters(
        query=query,
        status=status,
        manufacturer=manufacturer,
        bodywork=bodywork,
        fuel=fuel,
        transmission=transmission,
        batch_id=batch_id,
        limit=limit,
        offset=offset,
    )
    try:
        return service.list_vehicles(filters)
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Normalization review data is temporarily unavailable.",
        ) from error


@screen_router.get("/normalization-review", include_in_schema=False)
def normalization_review_screen() -> FileResponse:
    return FileResponse(STATIC_DIRECTORY / "index.html", media_type="text/html")
