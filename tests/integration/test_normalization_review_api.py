from fastapi.testclient import TestClient

from api.app.features.normalization_review.router import get_normalization_review_service
from api.app.features.normalization_review.schemas import (
    NormalizationReviewFacets,
    NormalizationReviewFilters,
    NormalizationReviewPage,
    NormalizationReviewVehicle,
    NormalizationStatusSummary,
)


class FakeReviewService:
    def list_vehicles(self, filters: NormalizationReviewFilters) -> NormalizationReviewPage:
        return NormalizationReviewPage(
            batch_id="screen-demo-250",
            total=250,
            filtered_total=1,
            limit=filters.limit,
            offset=filters.offset,
            summary=NormalizationStatusSummary(total=250, provisional=250),
            facets=NormalizationReviewFacets(
                manufacturers=["Volvo"],
                bodywork_forms=["estate"],
                fuels=["petrol"],
                transmissions=["automatic"],
            ),
            items=[
                NormalizationReviewVehicle(
                    source_record_id=1,
                    status="provisional",
                    confidence=0.8,
                    manufacturer="Volvo",
                    model_family="V60",
                    bodywork="estate",
                    energy_sources=["petrol"],
                )
            ],
        )


def test_review_api_accepts_vehicle_search_and_filter_parameters(client: TestClient) -> None:
    client.app.dependency_overrides[get_normalization_review_service] = FakeReviewService
    try:
        response = client.get(
            "/v1/normalization-review/vehicles",
            params={"query": "V60", "bodywork": "estate", "limit": 250},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["batch_id"] == "screen-demo-250"
    assert payload["total"] == 250
    assert payload["items"][0]["model_family"] == "V60"


def test_review_screen_and_assets_are_served_by_application(client: TestClient) -> None:
    screen = client.get("/normalization-review")
    stylesheet = client.get("/normalization-review/assets/styles.css")
    javascript = client.get("/normalization-review/assets/app.js")

    assert screen.status_code == 200
    assert "Normalization review" in screen.text
    assert 'id="filter-manufacturer"' in screen.text
    assert 'id="decision-trace"' in screen.text
    assert stylesheet.status_code == 200
    assert ".workspace" in stylesheet.text
    assert javascript.status_code == 200
    assert "/v1/normalization-review/vehicles" in javascript.text


def test_review_api_is_documented_but_screen_is_not(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/v1/normalization-review/vehicles" in paths
    assert "/normalization-review" not in paths
