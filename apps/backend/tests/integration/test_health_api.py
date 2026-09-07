from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.app.core.health import StoreHealth
from api.app.core.settings import get_settings
from api.app.features.health.router import get_health_service
from api.app.features.health.service import HealthService


class FakeClient:
    def __init__(self, name: str, result: StoreHealth) -> None:
        self.name = name
        self._result = result

    def check(self) -> StoreHealth:
        return self._result


def override_health_service(app: FastAPI, clients: list[FakeClient]) -> None:
    app.dependency_overrides[get_health_service] = lambda: HealthService(
        settings=get_settings(),
        clients=clients,
    )


@pytest.fixture
def app_client(client: TestClient) -> Iterator[TestClient]:
    yield client
    client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def test_health_endpoint_returns_ok_with_per_store_status(app_client: TestClient) -> None:
    override_health_service(
        app_client.app,  # type: ignore[arg-type]
        [
            FakeClient("postgres", StoreHealth(name="postgres", status="ok")),
            FakeClient("redis", StoreHealth(name="redis", status="ok")),
        ],
    )

    response = app_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "Vehicle Intelligence API"
    assert body["datastores"] == [
        {"name": "postgres", "status": "ok", "detail": None},
        {"name": "redis", "status": "ok", "detail": None},
    ]


def test_health_endpoint_reports_degraded_when_a_store_fails(app_client: TestClient) -> None:
    override_health_service(
        app_client.app,  # type: ignore[arg-type]
        [
            FakeClient("postgres", StoreHealth(name="postgres", status="ok")),
            FakeClient("neo4j", StoreHealth(name="neo4j", status="error", detail="Timeout")),
        ],
    )

    response = app_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert {"name": "neo4j", "status": "error", "detail": "Timeout"} in body["datastores"]
