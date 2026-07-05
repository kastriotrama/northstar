from api.app.core.settings import Settings
from api.app.features.health.service import HealthService


def test_get_health_returns_expected_payload() -> None:
    settings = Settings(ENVIRONMENT="test")
    service = HealthService(settings=settings)

    result = service.get_health()

    assert result == {
        "status": "ok",
        "service": "Vehicle Intelligence API",
        "environment": "test",
    }
