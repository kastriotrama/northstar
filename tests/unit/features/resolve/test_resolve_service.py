from api.app.features.resolve.service import ResolveService


def test_get_status_returns_stubbed_resolve_feature() -> None:
    service = ResolveService()

    result = service.get_status()

    assert result.model_dump() == {
        "status": "stubbed",
        "feature": "vehicle-resolution",
    }


def test_resolve_returns_empty_stub_candidate_list() -> None:
    service = ResolveService()

    result = service.resolve(query="volvo xc90")

    assert result.model_dump() == {
        "query": "volvo xc90",
        "status": "stubbed",
        "candidates": [],
    }
