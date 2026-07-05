from api.app.core.settings import Settings


class HealthService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def get_health(self) -> dict[str, str]:
        return {
            "status": "ok",
            "service": self._settings.app_name,
            "environment": self._settings.environment,
        }
