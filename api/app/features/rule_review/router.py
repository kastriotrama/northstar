from contextlib import AbstractContextManager
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from api.app.core.db import get_postgres_connection
from api.app.core.settings import Settings, get_settings
from api.app.features.rule_review.repository import RuleReviewRepository
from api.app.features.rule_review.reprocessing import RuleReprocessingAdapter
from api.app.features.rule_review.schemas import (
    ManufacturerEntityDraftRequest,
    ReprocessRequest,
    ReprocessResponse,
    RuleActivationRequest,
    RuleActivationResponse,
    RuleDraftRequest,
    RuleListResponse,
)
from api.app.features.rule_review.service import RuleReviewError, RuleReviewService

router = APIRouter(prefix="/v1/normalization-review/rules", tags=["rule-review"])


def get_rule_review_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> RuleReviewService:
    def connection_factory() -> AbstractContextManager[psycopg.Connection[Any]]:
        return get_postgres_connection(settings)

    return RuleReviewService(
        RuleReviewRepository(connection_factory),
        RuleReprocessingAdapter(connection_factory),
    )


@router.get("", response_model=RuleListResponse)
def list_rules(
    service: Annotated[RuleReviewService, Depends(get_rule_review_service)],
) -> RuleListResponse:
    try:
        return service.list_rules()
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="Rule review data is unavailable.") from error


@router.put("/{rule_id}/draft", response_model=RuleListResponse)
def save_rule_draft(
    rule_id: str,
    request: RuleDraftRequest,
    service: Annotated[RuleReviewService, Depends(get_rule_review_service)],
) -> RuleListResponse:
    try:
        return service.save_draft(rule_id, request)
    except RuleReviewError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="Rule draft could not be saved.") from error


@router.delete("/{rule_id}/draft", response_model=RuleListResponse)
def discard_rule_draft(
    rule_id: str,
    service: Annotated[RuleReviewService, Depends(get_rule_review_service)],
) -> RuleListResponse:
    try:
        return service.discard_draft(rule_id)
    except RuleReviewError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="Rule draft could not be discarded.") from error


@router.put("/entities/{entity_id}/draft", response_model=RuleListResponse)
def save_manufacturer_entity_draft(
    entity_id: str,
    request: ManufacturerEntityDraftRequest,
    service: Annotated[RuleReviewService, Depends(get_rule_review_service)],
) -> RuleListResponse:
    try:
        return service.save_manufacturer_entity_draft(entity_id, request)
    except RuleReviewError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503, detail="Manufacturer entity draft could not be saved."
        ) from error


@router.delete("/entities/{entity_id}/draft", response_model=RuleListResponse)
def discard_manufacturer_entity_draft(
    entity_id: str,
    service: Annotated[RuleReviewService, Depends(get_rule_review_service)],
) -> RuleListResponse:
    try:
        return service.discard_manufacturer_entity_draft(entity_id)
    except RuleReviewError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503, detail="Manufacturer entity draft could not be discarded."
        ) from error


@router.post("/activate", response_model=RuleActivationResponse)
def activate_rule_drafts(
    request: RuleActivationRequest,
    service: Annotated[RuleReviewService, Depends(get_rule_review_service)],
) -> RuleActivationResponse:
    try:
        return service.activate(request.note)
    except RuleReviewError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="Rules could not be activated.") from error


@router.post("/reprocess", response_model=ReprocessResponse)
def reprocess_batch(
    request: ReprocessRequest,
    service: Annotated[RuleReviewService, Depends(get_rule_review_service)],
) -> ReprocessResponse:
    try:
        return service.reprocess(request.source_batch_id)
    except RuleReviewError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except psycopg.Error as error:
        raise HTTPException(status_code=503, detail="Batch reprocessing failed safely.") from error
