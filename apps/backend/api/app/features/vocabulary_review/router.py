from contextlib import AbstractContextManager
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from api.app.core.db import get_postgres_connection
from api.app.core.settings import Settings, get_settings
from api.app.features.vocabulary_review.repository import (
    VocabularyReviewError,
    VocabularyReviewRepository,
)
from api.app.features.vocabulary_review.schemas import (
    ActivateDraftsRequest,
    ActivateDraftsResponse,
    ProposeDraftRequest,
    ReviewDraftRequest,
    VocabularyAlignmentCatalogResponse,
    VocabularyAlignmentDraft,
    VocabularyAlignmentDraftListResponse,
    VocabularyAlignmentVersion,
)

router = APIRouter(prefix="/v1/vocabulary-alignments", tags=["vocabulary-review"])


def get_vocabulary_review_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> VocabularyReviewRepository:
    def connection_factory() -> AbstractContextManager[psycopg.Connection[Any]]:
        return get_postgres_connection(settings)

    return VocabularyReviewRepository(connection_factory)


@router.get("", response_model=VocabularyAlignmentCatalogResponse)
def list_vocabulary_alignments(
    repository: Annotated[VocabularyReviewRepository, Depends(get_vocabulary_review_repository)],
) -> VocabularyAlignmentCatalogResponse:
    try:
        versions = repository.fetch_versions()
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503, detail="Vocabulary alignment data is unavailable."
        ) from error
    return VocabularyAlignmentCatalogResponse(
        versions=[VocabularyAlignmentVersion(**version) for version in versions]
    )


@router.get("/drafts", response_model=VocabularyAlignmentDraftListResponse)
def list_vocabulary_alignment_drafts(
    repository: Annotated[VocabularyReviewRepository, Depends(get_vocabulary_review_repository)],
) -> VocabularyAlignmentDraftListResponse:
    try:
        drafts = repository.fetch_drafts()
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503, detail="Vocabulary alignment drafts are unavailable."
        ) from error
    return VocabularyAlignmentDraftListResponse(
        drafts=[VocabularyAlignmentDraft(**draft) for draft in drafts]
    )


@router.post("/drafts", response_model=VocabularyAlignmentDraft)
def propose_vocabulary_alignment_draft(
    request: ProposeDraftRequest,
    repository: Annotated[VocabularyReviewRepository, Depends(get_vocabulary_review_repository)],
) -> VocabularyAlignmentDraft:
    try:
        draft = repository.propose_draft(**request.model_dump())
    except VocabularyReviewError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503, detail="Vocabulary alignment draft could not be proposed."
        ) from error
    return VocabularyAlignmentDraft(**draft)


@router.put("/drafts/{draft_id}", response_model=VocabularyAlignmentDraft)
def review_vocabulary_alignment_draft(
    draft_id: int,
    request: ReviewDraftRequest,
    repository: Annotated[VocabularyReviewRepository, Depends(get_vocabulary_review_repository)],
) -> VocabularyAlignmentDraft:
    try:
        draft = repository.review_draft(
            draft_id, status=request.status, reviewed_by=request.reviewed_by
        )
    except VocabularyReviewError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503, detail="Vocabulary alignment draft could not be reviewed."
        ) from error
    return VocabularyAlignmentDraft(**draft)


@router.post("/activate", response_model=ActivateDraftsResponse)
def activate_vocabulary_alignment_drafts(
    request: ActivateDraftsRequest,
    repository: Annotated[VocabularyReviewRepository, Depends(get_vocabulary_review_repository)],
) -> ActivateDraftsResponse:
    try:
        result = repository.activate_approved(
            vocabulary=request.vocabulary,
            alignment_version=request.alignment_version,
            activated_by=request.activated_by,
            note=request.note,
        )
    except VocabularyReviewError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503, detail="Vocabulary alignment drafts could not be activated."
        ) from error
    return ActivateDraftsResponse(**result)
