"""HTTP surface for filtering the whole vehicle population.

These endpoints and the match-review rule endpoints speak the same condition
shape on purpose: whatever narrowed a population here can be handed to a rule
without a reviewer rebuilding it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from api.app.core.db import get_postgres_connection
from api.app.core.settings import get_settings
from api.app.features.match_review.chunk_schemas import FieldValueCount, RuleAdvice
from api.app.features.match_review.field_resolution import CANDIDATE_DISCRIMINATORS
from api.app.features.match_review.integrations import GeminiJsonLlm
from api.app.features.match_review.rule_advisor import (
    LlmRuleAdvisor,
    PatternRuleAdvisor,
    RuleAdvisor,
)
from api.app.features.vehicle_filter.repository import VehicleFilterRepository
from api.app.features.vehicle_filter.schemas import (
    AdviseRequest,
    UnresolvedField,
    UnresolvedSummary,
    VehicleCount,
    VehicleDetail,
    VehicleFacet,
    VehicleFilter,
    VehiclePage,
    VehicleRow,
)
from ingestion.vehicle_facts_query import UnknownFieldError

router = APIRouter(prefix="/v1/vehicles", tags=["vehicles"])


@lru_cache(maxsize=1)
def _cached_advisor() -> RuleAdvisor:
    """The model when one is configured, the statistical advisor otherwise."""

    settings = get_settings()
    fallback = PatternRuleAdvisor()
    if not settings.gemini_api_key:
        return fallback
    return LlmRuleAdvisor(
        llm=GeminiJsonLlm(
            api_key=settings.gemini_api_key,
            base_url=settings.gemini_base_url,
            model=settings.rule_advisor_model,
            timeout_seconds=settings.rule_advisor_timeout_seconds,
        ),
        fallback=fallback,
    )


def get_advisor() -> RuleAdvisor:
    return _cached_advisor()


AdvisorDependency = Annotated[RuleAdvisor, Depends(get_advisor)]


@lru_cache(maxsize=1)
def _cached_repository() -> VehicleFilterRepository:
    settings = get_settings()
    return VehicleFilterRepository(lambda: get_postgres_connection(settings))


def get_repository() -> VehicleFilterRepository:
    return _cached_repository()


RepositoryDependency = Annotated[VehicleFilterRepository, Depends(get_repository)]


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=503, detail="Vehicle data is temporarily unavailable."
    )


def _bad_field(error: UnknownFieldError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


@router.post("/count", response_model=VehicleCount)
def count_vehicles(
    request: VehicleFilter, repository: RepositoryDependency
) -> VehicleCount:
    try:
        return VehicleCount(
            matched_rows=repository.count(request.conditions, request.unresolved_field),
            total_rows=repository.total_rows(),
        )
    except UnknownFieldError as error:
        raise _bad_field(error) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@router.post("/unresolved-summary", response_model=UnresolvedSummary)
def unresolved_summary(
    request: VehicleFilter, repository: RepositoryDependency
) -> UnresolvedSummary:
    """What the filtered set still cannot say about itself -- the worklist."""

    try:
        matched, counts = repository.unresolved_summary(request.conditions)
    except UnknownFieldError as error:
        raise _bad_field(error) from error
    except psycopg.Error as error:
        raise _unavailable() from error

    fields = [
        UnresolvedField(
            field=field,
            unresolved=unresolved,
            share=round(unresolved / matched, 4) if matched else 0.0,
        )
        for field, unresolved in counts
        if unresolved > 0
    ]
    fields.sort(key=lambda entry: entry.unresolved, reverse=True)
    return UnresolvedSummary(matched_rows=matched, fields=fields)


@router.post("/facets", response_model=VehicleFacet)
def facet_vehicles(
    request: VehicleFilter,
    repository: RepositoryDependency,
    field: str = Query(max_length=60),
    limit: int = Query(default=12, ge=1, le=100),
) -> VehicleFacet:
    """Top values of one field inside the filter -- what still varies."""

    try:
        values = repository.facet(
            request.conditions, request.unresolved_field, field=field, limit=limit
        )
        matched = repository.count(request.conditions, request.unresolved_field)
    except UnknownFieldError as error:
        raise _bad_field(error) from error
    except psycopg.Error as error:
        raise _unavailable() from error

    return VehicleFacet(
        field=field,
        matched_rows=matched,
        values=[
            FieldValueCount(value=value, count=count, meaning=None)
            for value, count in values
        ],
    )


@router.post("/page", response_model=VehiclePage)
def page_vehicles(
    request: VehicleFilter,
    repository: RepositoryDependency,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> VehiclePage:
    """Keyset page: pass the previous page's `next_cursor`, never an offset."""

    try:
        rows = repository.page(
            request.conditions,
            request.unresolved_field,
            cursor_id=cursor,
            limit=limit,
        )
    except UnknownFieldError as error:
        raise _bad_field(error) from error
    except psycopg.Error as error:
        raise _unavailable() from error

    items = [VehicleRow(**row) for row in rows]
    has_more = len(items) == limit
    return VehiclePage(
        items=items,
        next_cursor=items[-1].source_record_id if items and has_more else None,
        has_more=has_more,
    )


@router.get("/{source_record_id}", response_model=VehicleDetail)
def get_vehicle(
    source_record_id: int, repository: RepositoryDependency
) -> VehicleDetail:
    try:
        detail = repository.detail(source_record_id)
    except psycopg.Error as error:
        raise _unavailable() from error
    if detail is None:
        raise HTTPException(status_code=404, detail="Vehicle not found.")
    return VehicleDetail(**detail)


@router.post("/advise", response_model=RuleAdvice)
def advise_for_filter(
    request: AdviseRequest,
    repository: RepositoryDependency,
    advisor: AdvisorDependency,
) -> RuleAdvice:
    """Suggest a rule for the filtered population. Writes nothing."""

    try:
        population, discriminators, field_values = repository.advisor_evidence(
            request.conditions,
            target_field=request.target_field,
            candidate_fields=CANDIDATE_DISCRIMINATORS,
        )
    except UnknownFieldError as error:
        raise _bad_field(error) from error
    except psycopg.Error as error:
        raise _unavailable() from error

    if population == 0:
        raise HTTPException(
            status_code=422,
            detail="Nothing in this filter still lacks that field.",
        )

    first = request.conditions[0] if request.conditions else None
    return advisor.advise(
        source_field=first.field if first else request.target_field,
        source_value=first.terms[0] if first and first.terms else "",
        target_field=request.target_field,
        population=population,
        discriminators=discriminators,
        field_values=field_values,
        # OEM evidence is bought per VIN through the chunk workspace; the filter
        # path has none, and the advisor treats its absence as "cannot justify a
        # value" rather than inventing one.
        oem_samples=[],
    )
