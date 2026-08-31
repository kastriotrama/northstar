"""HTTP boundary for the chunk review workspace."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from api.app.core.db import get_postgres_connection
from api.app.core.settings import Settings, get_settings
from api.app.features.match_review.adjudicator import HeuristicAdjudicator
from api.app.features.match_review.integrations import (
    HttpOemVinProvider,
    OemProviderError,
    OemProviderNotConfiguredError,
    OemVinProvider,
    UnconfiguredOemVinProvider,
)
from api.app.features.match_review.repository import MatchReviewRepository
from api.app.features.match_review.rule_advisor import (
    LlmRuleAdvisor,
    PatternRuleAdvisor,
    RuleAdvisor,
)
from api.app.features.match_review.schemas import (
    BuildSummary,
    ChunkDetail,
    ChunkFieldProfile,
    ChunkPage,
    DiscriminatorReport,
    MemberComparison,
    OemSampleRequest,
    OemSampleSummary,
    PatternReport,
    PopulationAttributes,
    ProposalReviewRequest,
    ProposalSummary,
    RefineRequest,
    RefineResult,
    RuleAdvice,
    RuleAdviceRequest,
    RulePreview,
    RulePreviewRequest,
    TargetVocabulary,
    UnresolvedOverview,
)
from api.app.features.match_review.service import (
    MatchReviewConflictError,
    MatchReviewNotFoundError,
    MatchReviewService,
    MemberVinUnavailableError,
)

STATIC_DIRECTORY = Path(__file__).with_name("static")

api_router = APIRouter(prefix="/v1/match-review", tags=["match-review"])
screen_router = APIRouter(tags=["match-review"])


def _build_oem_provider(settings: Settings) -> OemVinProvider:
    if (
        settings.oem_vin_provider_name
        and settings.oem_vin_provider_base_url
        and settings.oem_vin_provider_api_key
    ):
        return HttpOemVinProvider(
            provider_name=settings.oem_vin_provider_name,
            base_url=settings.oem_vin_provider_base_url,
            api_key=settings.oem_vin_provider_api_key,
            dataset_version=settings.oem_vin_provider_dataset_version,
            timeout_seconds=settings.oem_vin_provider_timeout_seconds,
        )
    return UnconfiguredOemVinProvider()


def _build_rule_advisor(settings: Settings) -> RuleAdvisor:
    """Use the LLM advisor only when a key is configured; heuristics otherwise."""

    if settings.openai_api_key:
        return LlmRuleAdvisor(
            api_key=settings.openai_api_key,
            model=settings.rule_advisor_model,
            base_url=settings.rule_advisor_base_url,
            timeout_seconds=settings.rule_advisor_timeout_seconds,
            fallback=PatternRuleAdvisor(),
        )
    return PatternRuleAdvisor()


@lru_cache(maxsize=1)
def _cached_service() -> MatchReviewService:
    settings = get_settings()
    repository = MatchReviewRepository(
        connection_factory=lambda: get_postgres_connection(settings)
    )
    return MatchReviewService(
        repository,
        oem_provider=_build_oem_provider(settings),
        adjudicator=HeuristicAdjudicator(),
        rule_advisor=_build_rule_advisor(settings),
    )


def get_match_review_service() -> MatchReviewService:
    return _cached_service()


ServiceDependency = Annotated[
    MatchReviewService, Depends(get_match_review_service)
]


@api_router.get("/builds", response_model=list[BuildSummary])
def list_builds(service: ServiceDependency) -> list[BuildSummary]:
    try:
        return service.list_builds()
    except psycopg.Error as error:
        raise _unavailable() from error


@api_router.get("/chunks", response_model=ChunkPage)
def list_chunks(
    service: ServiceDependency,
    build_id: UUID | None = None,
    status: Literal["open", "proposed", "approved", "rejected", "split"]
    | None = None,
    query: str = Query(default="", max_length=120),
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
) -> ChunkPage:
    try:
        return service.list_chunks(
            build_id=build_id,
            status=status,
            query=query,
            limit=limit,
            offset=offset,
        )
    except MatchReviewNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@api_router.get("/chunks/{chunk_id}", response_model=ChunkDetail)
def get_chunk(chunk_id: UUID, service: ServiceDependency) -> ChunkDetail:
    try:
        return service.get_chunk(chunk_id)
    except MatchReviewNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@api_router.get("/unresolved", response_model=UnresolvedOverview)
def get_unresolved_overview(
    build_id: UUID, service: ServiceDependency
) -> UnresolvedOverview:
    try:
        return service.get_unresolved_overview(build_id)
    except MatchReviewNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@api_router.get("/unresolved/discriminators", response_model=DiscriminatorReport)
def get_discriminators(
    build_id: UUID,
    source_field: str,
    source_value: str,
    service: ServiceDependency,
) -> DiscriminatorReport:
    try:
        return service.get_discriminators(
            build_id, source_field=source_field, source_value=source_value
        )
    except MatchReviewNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@api_router.get("/unresolved/attributes", response_model=PopulationAttributes)
def get_population_attributes(
    build_id: UUID,
    source_field: str,
    source_value: str,
    service: ServiceDependency,
) -> PopulationAttributes:
    """Every source key in the population, for the all-attributes picker."""

    try:
        return service.get_population_attributes(
            build_id, source_field=source_field, source_value=source_value
        )
    except MatchReviewNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@api_router.get("/unresolved/patterns", response_model=PatternReport)
def get_value_patterns(
    build_id: UUID,
    source_field: str,
    source_value: str,
    field_name: str,
    service: ServiceDependency,
) -> PatternReport:
    try:
        return service.get_value_patterns(
            build_id,
            source_field=source_field,
            source_value=source_value,
            field_name=field_name,
        )
    except MatchReviewNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@api_router.post("/unresolved/advise", response_model=RuleAdvice)
def advise_rule(
    request: RuleAdviceRequest, service: ServiceDependency
) -> RuleAdvice:
    """Suggest a rule. Writes nothing; the proposal still needs preview."""

    try:
        return service.advise_rule(request)
    except MatchReviewNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except MatchReviewConflictError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@api_router.get("/target-vocabulary", response_model=TargetVocabulary)
def get_target_vocabulary(
    build_id: UUID, target_field: str, service: ServiceDependency
) -> TargetVocabulary:
    try:
        return service.get_target_vocabulary(build_id, target_field=target_field)
    except MatchReviewNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@api_router.post("/unresolved/refine", response_model=RefineResult)
def refine_rule(request: RefineRequest, service: ServiceDependency) -> RefineResult:
    """Live counts and facets for the predicate as it stands. Writes nothing."""

    try:
        return service.refine(request)
    except MatchReviewNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@api_router.post("/rule-preview", response_model=RulePreview)
def preview_rule(
    request: RulePreviewRequest, service: ServiceDependency
) -> RulePreview:
    """Dry-run only: counts what a rule would resolve. Writes nothing."""

    try:
        return service.preview_rule(request)
    except MatchReviewNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except MatchReviewConflictError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@api_router.get(
    "/chunks/{chunk_id}/field-profile", response_model=ChunkFieldProfile
)
def get_field_profile(
    chunk_id: UUID, service: ServiceDependency
) -> ChunkFieldProfile:
    try:
        return service.get_field_profile(chunk_id)
    except MatchReviewNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@api_router.get(
    "/chunks/{chunk_id}/members/{source_record_id}",
    response_model=MemberComparison,
)
def get_member_comparison(
    chunk_id: UUID,
    source_record_id: int,
    service: ServiceDependency,
) -> MemberComparison:
    try:
        return service.get_member_comparison(chunk_id, source_record_id)
    except MatchReviewNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@api_router.post(
    "/chunks/{chunk_id}/oem-samples",
    response_model=OemSampleSummary,
    status_code=201,
)
def fetch_oem_sample(
    chunk_id: UUID,
    request: OemSampleRequest,
    service: ServiceDependency,
) -> OemSampleSummary:
    try:
        return service.fetch_oem_sample(chunk_id, request)
    except (MatchReviewNotFoundError, MemberVinUnavailableError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except OemProviderNotConfiguredError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except OemProviderError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@api_router.post(
    "/chunks/{chunk_id}/proposals",
    response_model=ProposalSummary,
    status_code=201,
)
def create_proposal(
    chunk_id: UUID, service: ServiceDependency
) -> ProposalSummary:
    try:
        return service.create_proposal(chunk_id)
    except MatchReviewNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except MatchReviewConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@api_router.post(
    "/proposals/{proposal_id}/review", response_model=ProposalSummary
)
def review_proposal(
    proposal_id: UUID,
    request: ProposalReviewRequest,
    service: ServiceDependency,
) -> ProposalSummary:
    try:
        return service.review_proposal(proposal_id, request)
    except MatchReviewNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except MatchReviewConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="Match review data is temporarily unavailable.",
    )


@screen_router.get("/match-review", include_in_schema=False)
def match_review_screen() -> FileResponse:
    return FileResponse(STATIC_DIRECTORY / "index.html", media_type="text/html")
