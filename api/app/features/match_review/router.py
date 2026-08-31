from typing import Annotated, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from api.app.core.db import get_postgres_connection
from api.app.core.settings import Settings, get_settings
from api.app.features.match_review.repository import MatchReviewRepository
from api.app.features.match_review.schemas import (
    MatchReviewDecisionRequest,
    MatchReviewItemView,
    MatchReviewPage,
    MatchReviewPatternDecision,
    MatchReviewPatternDecisionRequest,
    MatchReviewPatternMemberPage,
    MatchReviewPatternPage,
    MatchReviewPatternTechnicalEvidence,
    MatchRunReviewSummary,
)
from api.app.features.match_review.service import MatchReviewService

router = APIRouter(prefix="/v1/match-review", tags=["match-review"])


def get_match_review_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> MatchReviewService:
    return MatchReviewService(
        MatchReviewRepository(lambda: get_postgres_connection(settings))
    )


@router.get("/summary", response_model=MatchRunReviewSummary)
def get_match_review_summary(
    service: Annotated[MatchReviewService, Depends(get_match_review_service)],
    operation_id: str | None = Query(default=None, max_length=80),
) -> MatchRunReviewSummary:
    try:
        return service.summary(operation_id)
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="Match audit status is unavailable.") from error


@router.get("/items", response_model=MatchReviewPage)
def list_match_review_items(
    service: Annotated[MatchReviewService, Depends(get_match_review_service)],
    operation_id: str = Query(max_length=80),
    category: str | None = Query(default=None, max_length=80),
    status: Literal["pending", "in_review", "resolved", "rejected"] | None = None,
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
) -> MatchReviewPage:
    try:
        return service.items(
            operation_id=operation_id,
            category=category,
            status=status,
            limit=limit,
            offset=offset,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="Match review items are unavailable.") from error


@router.post("/items/{item_id}/decision", response_model=MatchReviewItemView)
def decide_match_review_item(
    item_id: int,
    request: MatchReviewDecisionRequest,
    service: Annotated[MatchReviewService, Depends(get_match_review_service)],
    operation_id: str = Query(max_length=80),
) -> MatchReviewItemView:
    try:
        return service.decide(operation_id, item_id, request)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="Match review decision was not saved.") from error


@router.get("/patterns", response_model=MatchReviewPatternPage)
def list_match_review_patterns(
    service: Annotated[MatchReviewService, Depends(get_match_review_service)],
    operation_id: str = Query(max_length=80),
    category: str | None = Query(default=None, max_length=80),
) -> MatchReviewPatternPage:
    try:
        return service.patterns(operation_id=operation_id, category=category)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="Match review patterns are unavailable.") from error


@router.post("/patterns/{pattern_key}/decision", response_model=MatchReviewPatternDecision)
def decide_match_review_pattern(
    pattern_key: str,
    request: MatchReviewPatternDecisionRequest,
    service: Annotated[MatchReviewService, Depends(get_match_review_service)],
    operation_id: str = Query(max_length=80),
) -> MatchReviewPatternDecision:
    try:
        return service.decide_pattern(operation_id, pattern_key, request)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="Pattern decision was not saved.") from error


@router.get("/patterns/{pattern_key}/members", response_model=MatchReviewPatternMemberPage)
def list_match_review_pattern_members(
    pattern_key: str,
    service: Annotated[MatchReviewService, Depends(get_match_review_service)],
    operation_id: str = Query(max_length=80),
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
) -> MatchReviewPatternMemberPage:
    try:
        return service.pattern_members(
            operation_id=operation_id, pattern_key=pattern_key, limit=limit, offset=offset,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="Pattern members are unavailable.") from error


@router.get("/patterns/{pattern_key}/technical-evidence", response_model=MatchReviewPatternTechnicalEvidence)
def get_match_review_pattern_technical_evidence(
    pattern_key: str,
    service: Annotated[MatchReviewService, Depends(get_match_review_service)],
    operation_id: str = Query(max_length=80),
) -> MatchReviewPatternTechnicalEvidence:
    try:
        return service.pattern_technical_evidence(operation_id=operation_id, pattern_key=pattern_key)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="Pattern technical evidence is unavailable.") from error
