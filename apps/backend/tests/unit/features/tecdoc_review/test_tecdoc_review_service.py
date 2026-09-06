from api.app.features.tecdoc_review.service import TecDocReviewService


class Repository:
    def latest_batch(self):
        return {
            "batch_id": "tecdoc-local", "source_version": "0326", "source_rows": 72570,
            "promoted_ktypes": 1, "manufacturers": 1, "model_families": 1, "engines": 1,
        }

    def fetch_vehicles(self, **_kwargs):
        return 1, [{
            "source_key": "ktype:12345", "alias_id": "ALI-1", "variant_id": "VEH-1",
            "alias_attributes": {"alias_text": "12345", "target_source_key": "variant:12345"},
            "variant_attributes": {
                "source_name": "D4 AWD", "manufacturer_source_key": "manufacturer:5",
                "model_family_source_key": "model:50", "engine_source_key": "engine:8",
                "year_from": 2018,
                "hierarchy_link_status": "model_family_linked_platform_optional",
            },
            "manufacturer_attributes": {"canonical_name": "VOLVO"},
            "family_attributes": {"canonical_name": "XC60"},
            "engine_attributes": {"engine_code": "D4204T14", "displacement_cc": 1969,
                                  "displacement_source": "table_155_exact", "fuel_type": "diesel"},
            "source_row_refs": ["100:1", "110:1", "120:1"],
        }]

    def fetch_entities(self, **_kwargs):
        return 1, [{"source_key": "manufacturer:5", "name": "VOLVO",
                    "vehicle_count": 24, "sample_ktypes": ["12345"],
                    "details": {"canonical_name": "VOLVO"}}]


def test_exposes_promoted_vehicle_with_source_lineage() -> None:
    page = TecDocReviewService(Repository()).list_vehicles(query="volvo", limit=100, offset=0)

    assert page.summary.promoted_ktypes == 1
    assert page.items[0].ktype == "12345"
    assert page.items[0].manufacturer == "VOLVO"
    assert page.items[0].engine_code == "D4204T14"
    assert page.items[0].source_keys["model_family"] == "model:50"
    assert len(page.promotion_rules) == 4


def test_exposes_manufacturer_usage_and_example_ktypes() -> None:
    page = TecDocReviewService(Repository()).list_entities(
        kind="manufacturer", query="volvo", limit=100, offset=0
    )
    assert page.items[0].name == "VOLVO"
    assert page.items[0].vehicle_count == 24
    assert page.items[0].sample_ktypes == ["12345"]


class EmptyRepository:
    def latest_batch(self):
        return None

    def fetch_vehicles(self, **_kwargs):
        raise AssertionError("No query should run without a batch")

    def fetch_entities(self, **_kwargs):
        raise AssertionError("No query should run without a batch")


def test_returns_empty_page_before_a_promotion_exists() -> None:
    page = TecDocReviewService(EmptyRepository()).list_vehicles(query="", limit=100, offset=0)
    assert page.summary.batch_id is None
    assert page.items == []
