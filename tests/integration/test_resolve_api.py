from fastapi.testclient import TestClient


def test_resolve_status_endpoint_returns_stub_status(client: TestClient) -> None:
    response = client.get("/v1/resolve/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "stubbed",
        "feature": "vehicle-resolution",
    }


def test_resolve_endpoint_returns_empty_stub_candidates(client: TestClient) -> None:
    response = client.post("/v1/resolve", json={"query": "volvo xc90"})

    assert response.status_code == 200
    assert response.json() == {
        "query": "volvo xc90",
        "status": "stubbed",
        "candidates": [],
    }


def test_resolve_endpoint_rejects_empty_query(client: TestClient) -> None:
    response = client.post("/v1/resolve", json={"query": ""})

    assert response.status_code == 422


def test_openapi_includes_resolve_routes(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/v1/resolve/status" in paths
    assert "/v1/resolve" in paths

