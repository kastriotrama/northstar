from fastapi import APIRouter, Depends

from api.app.features.resolve.schemas import ResolveRequest, ResolveResponse, ResolveStatusResponse
from api.app.features.resolve.service import ResolveService

router = APIRouter(prefix="/resolve", tags=["resolve"])


def get_resolve_service() -> ResolveService:
    return ResolveService()


@router.get("/status", response_model=ResolveStatusResponse)
def get_resolve_status(
    service: ResolveService = Depends(get_resolve_service),
) -> ResolveStatusResponse:
    return service.get_status()


@router.post("", response_model=ResolveResponse)
def resolve_vehicle(
    request: ResolveRequest,
    service: ResolveService = Depends(get_resolve_service),
) -> ResolveResponse:
    return service.resolve(query=request.query)
