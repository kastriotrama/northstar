from api.app.core.health import StoreHealth
from api.app.core.settings import Settings
from api.app.features.health.service import HealthService


class FakeClient:
    def __init__(self, name: str, result: StoreHealth) -> None:
        self.name = name
        self._result = result

    def check(self) -> StoreHealth:
        return self._result


def test_get_health_returns_ok_when_all_stores_are_healthy() -> None:
    settings = Settings(ENVIRONMENT="test")
    clients = [
        FakeClient("postgres", StoreHealth(name="postgres", status="ok")),
        FakeClient("redis", StoreHealth(name="redis", status="ok")),
    ]

    result = HealthService(settings=settings, clients=clients).get_health()

    assert result.status == "ok"
    assert result.service == "Vehicle Intelligence API"
    assert result.environment == "test"
    assert [store.name for store in result.datastores] == ["postgres", "redis"]
    assert all(store.status == "ok" for store in result.datastores)


def test_get_health_returns_degraded_when_any_store_fails() -> None:
    settings = Settings(ENVIRONMENT="test")
    clients = [
        FakeClient("postgres", StoreHealth(name="postgres", status="ok")),
        FakeClient("neo4j", StoreHealth(name="neo4j", status="error", detail="AuthError")),
    ]

    result = HealthService(settings=settings, clients=clients).get_health()

    assert result.status == "degraded"
    failing = next(store for store in result.datastores if store.name == "neo4j")
    assert failing.status == "error"
    assert failing.detail == "AuthError"


def test_get_health_with_no_clients_reports_ok_without_datastores() -> None:
    settings = Settings(ENVIRONMENT="test")

    result = HealthService(settings=settings, clients=[]).get_health()

    assert result.status == "ok"
    assert result.datastores == []
