from fastapi import APIRouter, Depends

from api.app.core.settings import Settings, get_settings
from api.app.features.health.schemas import HealthResponse
from api.app.features.health.service import HealthService

router = APIRouter(tags=["health"])


def get_health_service(settings: Settings = Depends(get_settings)) -> HealthService:
    return HealthService(settings=settings)


@router.get("/health", response_model=HealthResponse)
def get_health(service: HealthService = Depends(get_health_service)) -> HealthResponse:
    return HealthResponse(**service.get_health())
