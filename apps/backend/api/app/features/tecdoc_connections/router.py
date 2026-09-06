from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from api.app.core.settings import Settings, get_settings
from api.app.features.tecdoc_connections.repository import ResolvedConnectionRepository
from api.app.features.tecdoc_connections.schemas import ResolvedConnectionPage
from api.app.features.tecdoc_connections.service import ResolvedConnectionService

router = APIRouter(prefix="/v1/normalization-review/connections", tags=["tecdoc-connections"])


def get_service(settings: Annotated[Settings, Depends(get_settings)]) -> ResolvedConnectionService:
    return ResolvedConnectionService(ResolvedConnectionRepository(settings.resolved_match_showcase_path))


@router.get("/resolved", response_model=ResolvedConnectionPage)
def list_resolved(
    service: Annotated[ResolvedConnectionService, Depends(get_service)],
    query: str = Query(default="", max_length=120),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ResolvedConnectionPage:
    try:
        return service.list(query=query, limit=limit, offset=offset)
    except (OSError, TypeError, ValueError) as error:
        raise HTTPException(status_code=503, detail="Resolved connection showcase is unavailable.") from error
