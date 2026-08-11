import psycopg
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query

from api.app.core.db import get_postgres_connection
from api.app.core.settings import Settings, get_settings
from api.app.features.tecdoc_review.repository import TecDocReviewRepository
from api.app.features.tecdoc_review.schemas import TecDocEntityPage, TecDocReviewPage
from api.app.features.tecdoc_review.service import TecDocReviewService

router = APIRouter(prefix="/v1/normalization-review/tecdoc", tags=["tecdoc-review"])


def get_tecdoc_review_service(settings: Settings = Depends(get_settings)) -> TecDocReviewService:
    return TecDocReviewService(TecDocReviewRepository(lambda: get_postgres_connection(settings)))


@router.get("/vehicles", response_model=TecDocReviewPage)
def list_tecdoc_vehicles(
    service: TecDocReviewService = Depends(get_tecdoc_review_service),
    query: str = Query(default="", max_length=120),
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
) -> TecDocReviewPage:
    try:
        return service.list_vehicles(query=query, limit=limit, offset=offset)
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="TecDoc review data is temporarily unavailable.") from error


@router.get("/entities", response_model=TecDocEntityPage)
def list_tecdoc_entities(
    service: TecDocReviewService = Depends(get_tecdoc_review_service),
    kind: Literal["manufacturer", "model_family", "engine", "fuel"] = "manufacturer",
    query: str = Query(default="", max_length=120),
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
) -> TecDocEntityPage:
    try:
        return service.list_entities(kind=kind, query=query, limit=limit, offset=offset)
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="TecDoc entity data is temporarily unavailable.") from error
