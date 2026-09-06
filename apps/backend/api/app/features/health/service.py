from collections.abc import Sequence
from typing import Literal

from api.app.core.health import DatastoreHealthClient, run_health_checks
from api.app.core.settings import Settings
from api.app.features.health.schemas import DatastoreHealth, HealthResponse


class HealthService:
    def __init__(self, settings: Settings, clients: Sequence[DatastoreHealthClient]) -> None:
        self._settings = settings
        self._clients = clients

    def get_health(self) -> HealthResponse:
        results = run_health_checks(self._clients)
        datastores = [
            DatastoreHealth(name=result.name, status=result.status, detail=result.detail)
            for result in results
        ]
        all_ok = all(store.status == "ok" for store in datastores)
        status: Literal["ok", "degraded"] = "ok" if all_ok else "degraded"

        return HealthResponse(
            status=status,
            service=self._settings.app_name,
            environment=self._settings.environment,
            datastores=datastores,
        )
