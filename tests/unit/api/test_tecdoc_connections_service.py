from api.app.features.tecdoc_connections.service import ResolvedConnectionService


class Repository:
    def load(self):
        return (
            {"vehicle_id": "TS-ONE", "plate": "ABC123", "manufacturer": "VW", "ts_model": "Golf", "tecdoc_model": "GOLF", "ktype": "123", "engine_codes": ["CAYC"], "confidence_route": "resolved"},
            {"vehicle_id": "TS-TWO", "plate": "XYZ789", "manufacturer": "HYUNDAI", "ts_model": "i30", "tecdoc_model": "i30", "ktype": "456", "engine_codes": ["G4FU"], "confidence_route": "resolved"},
        )


def test_lists_and_searches_privacy_safe_resolved_connections() -> None:
    page = ResolvedConnectionService(Repository()).list(query="abc123", limit=25, offset=0)  # type: ignore[arg-type]
    assert page.total == 2
    assert page.filtered_total == 1
    assert page.items[0].ktype == "123"
    assert page.items[0].plate == "ABC123"
    assert "registration plate is shown" in page.privacy_note
