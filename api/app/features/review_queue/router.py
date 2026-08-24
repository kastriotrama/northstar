from typing import Annotated, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from api.app.core.db import get_postgres_connection
from api.app.core.settings import Settings, get_settings
from api.app.features.review_queue.repository import ReviewQueueRepository
from api.app.features.review_queue.schemas import (
    ReviewQueueItemView,
    ReviewQueuePage,
    ReviewTransitionRequest,
)
from api.app.features.review_queue.service import ReviewQueueService

router = APIRouter(prefix="/v1/normalization-review/queue", tags=["review-queue"])


def get_review_queue_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReviewQueueService:
    return ReviewQueueService(ReviewQueueRepository(lambda: get_postgres_connection(settings)))


@router.get("", response_model=ReviewQueuePage)
def list_review_queue(
    service: Annotated[ReviewQueueService, Depends(get_review_queue_service)],
    status: Literal["pending", "in_review", "resolved", "rejected"] | None = None,
    batch_id: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=300, ge=1, le=1000),
) -> ReviewQueuePage:
    try:
        return service.list_items(status=status, batch_id=batch_id, limit=limit)
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503, detail="Review queue is temporarily unavailable."
        ) from error


@router.post("/{item_id}/transition", response_model=ReviewQueueItemView)
def transition_review_queue_item(
    item_id: int,
    request: ReviewTransitionRequest,
    service: Annotated[ReviewQueueService, Depends(get_review_queue_service)],
) -> ReviewQueueItemView:
    try:
        return service.transition(item_id, request)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503, detail="Review decision could not be saved."
        ) from error
