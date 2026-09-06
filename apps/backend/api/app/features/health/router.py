from typing import Annotated

from fastapi import APIRouter, Depends

from api.app.core.settings import Settings, get_settings
from api.app.features.health.repository import build_datastore_health_clients
from api.app.features.health.schemas import HealthResponse
from api.app.features.health.service import HealthService

router = APIRouter(tags=["health"])


def get_health_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthService:
    return HealthService(settings=settings, clients=build_datastore_health_clients(settings))


@router.get("/health", response_model=HealthResponse)
def get_health(
    service: Annotated[HealthService, Depends(get_health_service)],
) -> HealthResponse:
    return service.get_health()
