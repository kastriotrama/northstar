"""`TecDocReviewService.vehicle_detail` -- one KType's fields, each with its outcome.

Mirrors `vehicle_filter`'s TS-side `detail()` test intent: a field promotion
already resolved reads as resolved, a field with no data at all is not shown,
a field with data but no canonical target is unresolved, and a field a
reviewer ruled on live (but promotion never baked in) reads as rule_resolved.
"""

from api.app.features.tecdoc_review.service import TecDocReviewService

BATCH = {"batch_id": "tecdoc-local"}


class FakeRepository:
    def __init__(self, raw: dict | None, resolutions: dict[str, dict]) -> None:
        self._raw = raw
        self._resolutions = resolutions

    def latest_batch(self):
        return BATCH

    def vehicle_detail(self, *, batch_id: str, source_key: str):
        assert batch_id == "tecdoc-local"
        return self._raw

    def fetch_resolutions(self, *, canonical_field: str):
        return self._resolutions.get(canonical_field, {})


RAW = {
    "manufacturer": "VOLVO",
    "model_family": "XC60",
    "fields": {
        # Promotion already computed a canonical fuel -- resolved regardless
        # of whether a rule also exists.
        "energy_sources": {"source_term": "Diesel", "label": None, "canonical_value": "diesel"},
        # A real code, no promotion, no rule -- open gap.
        "bodywork_form": {"source_term": "34", "label": "SUV", "canonical_value": None},
        # A real code, no promotion, but a reviewer ruled on it live.
        "drive_type": {"source_term": "4", "label": "AWD", "canonical_value": None},
        # No raw data on this row at all -- not a gap, must not appear.
        "transmission_type": {"source_term": None, "label": None, "canonical_value": None},
    },
}


def test_a_field_promotion_already_resolved_reads_as_resolved() -> None:
    detail = TecDocReviewService(FakeRepository(RAW, {})).vehicle_detail(source_key="ktype:1")

    assert detail is not None
    energy = next(f for f in detail.fields if f.canonical_field == "energy_sources")
    assert energy.status == "resolved"
    assert energy.canonical_value == "diesel"
    assert energy.source_term == "Diesel"


def test_a_field_with_data_and_no_ruling_is_unresolved() -> None:
    detail = TecDocReviewService(FakeRepository(RAW, {})).vehicle_detail(source_key="ktype:1")

    assert detail is not None
    bodywork = next(f for f in detail.fields if f.canonical_field == "bodywork_form")
    assert bodywork.status == "unresolved"
    assert bodywork.canonical_value is None
    assert bodywork.source_term == "34"


def test_a_field_a_reviewer_ruled_on_live_reads_as_rule_resolved() -> None:
    resolutions = {
        "drive_type": {
            "4": {"decision": "accepted", "canonical_value": "awd", "note": "", "reviewed_by": "x", "updated_at": ""}
        }
    }
    detail = TecDocReviewService(FakeRepository(RAW, resolutions)).vehicle_detail(source_key="ktype:1")

    assert detail is not None
    drive = next(f for f in detail.fields if f.canonical_field == "drive_type")
    assert drive.status == "rule_resolved"
    assert drive.canonical_value == "awd"


def test_a_field_with_no_raw_data_at_all_is_omitted() -> None:
    detail = TecDocReviewService(FakeRepository(RAW, {})).vehicle_detail(source_key="ktype:1")

    assert detail is not None
    fields = {f.canonical_field for f in detail.fields}
    assert "transmission_type" not in fields


def test_no_promoted_batch_yields_no_detail() -> None:
    class NoBatchRepository(FakeRepository):
        def latest_batch(self):
            return None

    detail = TecDocReviewService(NoBatchRepository(RAW, {})).vehicle_detail(source_key="ktype:1")
    assert detail is None


def test_unknown_source_key_yields_no_detail() -> None:
    detail = TecDocReviewService(FakeRepository(None, {})).vehicle_detail(source_key="ktype:missing")
    assert detail is None
