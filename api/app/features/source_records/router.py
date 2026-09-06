"""HTTP surface for browsing raw Transportstyrelsen staging rows."""

from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from api.app.core.db import get_postgres_connection
from api.app.core.settings import Settings, get_settings
from api.app.features.source_records.repository import SourceRecordRepository
from api.app.features.source_records.schemas import (
    SourceBatchList,
    SourceFieldInventory,
    SourceRecordDetail,
    SourceRecordFilters,
    SourceRecordPage,
)
from api.app.features.source_records.service import SourceRecordService

router = APIRouter(prefix="/v1/source-records/ts", tags=["source-records"])


def get_source_record_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> SourceRecordService:
    return SourceRecordService(
        SourceRecordRepository(lambda: get_postgres_connection(settings))
    )


@router.get("", response_model=SourceRecordPage)
def list_source_records(
    service: Annotated[SourceRecordService, Depends(get_source_record_service)],
    query: str = Query(default="", max_length=120),
    field: str | None = Query(default=None, max_length=80),
    value: str | None = Query(default=None, max_length=200),
    batch_id: str | None = Query(default=None, max_length=200),
    cursor: int | None = Query(
        default=None,
        ge=0,
        description="Keyset cursor: the last id of the previous page.",
    ),
    limit: int = Query(default=100, ge=1, le=200),
) -> SourceRecordPage:
    filters = SourceRecordFilters(
        query=query,
        field=field,
        value=value,
        batch_id=batch_id,
        cursor=cursor,
        limit=limit,
    )
    try:
        return service.list_records(filters)
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Source record data is temporarily unavailable.",
        ) from error


@router.get("/batches", response_model=SourceBatchList)
def list_source_batches(
    service: Annotated[SourceRecordService, Depends(get_source_record_service)],
) -> SourceBatchList:
    try:
        return service.list_batches()
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Source batch data is temporarily unavailable.",
        ) from error


@router.get("/fields", response_model=SourceFieldInventory)
def source_field_inventory(
    service: Annotated[SourceRecordService, Depends(get_source_record_service)],
) -> SourceFieldInventory:
    try:
        return service.field_inventory()
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Source field inventory is temporarily unavailable.",
        ) from error


@router.get("/{record_id}", response_model=SourceRecordDetail)
def get_source_record(
    record_id: int,
    service: Annotated[SourceRecordService, Depends(get_source_record_service)],
) -> SourceRecordDetail:
    try:
        detail = service.get_record(record_id)
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Source record data is temporarily unavailable.",
        ) from error
    if detail is None:
        raise HTTPException(status_code=404, detail="Source record not found.")
    return detail
