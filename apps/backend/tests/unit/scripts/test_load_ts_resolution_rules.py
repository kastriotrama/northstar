from datetime import UTC, datetime
from uuid import UUID, uuid4

from api.app.features.match_review.adjudicator import HeuristicAdjudicator
from api.app.features.match_review.chunk_service import MatchReviewService
from api.app.features.match_review.integrations import UnconfiguredOemVinProvider
from scripts.load_ts_resolution_rules import load_rules

BUILD_ID = UUID("00000000-0000-0000-0000-000000000001")

RULE = {
    "source_field": "brand", "source_value": "BMW",
    "target_field": "drive_type", "target_value": "fwd",
    "conditions": [{"field": "brand", "value": "BMW", "layer": "source", "operator": "equals"}],
    "author": "pytest", "note": None,
}


class FakeRepository:
    """Only the surface `save_resolution_rule`/`load_rules` actually touch."""

    def __init__(self, existing: list[dict] | None = None) -> None:
        self.existing = existing or []
        self.inserted: list[dict] = []

    def ensure_schema(self) -> None:
        pass

    def fetch_build(self, build_id: UUID) -> dict | None:
        return {"build_id": build_id} if build_id == BUILD_ID else None

    def preview_rule(self, build_id: UUID, *, conditions, signature_field: str) -> dict:
        return {"matched_rows": 10, "would_resolve": 10, "already_resolved": 0, "sample_plates": []}

    def insert_resolution_rule(self, **kwargs) -> dict:
        row = {
            "rule_id": uuid4(), "status": "saved", "resolved_rows": 0,
            "created_at": datetime.now(UTC), "applied_at": None, "applied_by": None,
            "retired_at": None, "retired_by": None, **kwargs,
        }
        self.inserted.append(row)
        return row

    def fetch_resolution_rules(
        self, build_id: UUID, *, source_field=None, source_value=None, limit: int = 100
    ) -> list[dict]:
        return [
            item for item in self.existing
            if item["source_field"] == source_field and item["source_value"] == source_value
        ]


def _service(repository: FakeRepository) -> MatchReviewService:
    return MatchReviewService(
        repository,  # type: ignore[arg-type]
        oem_provider=UnconfiguredOemVinProvider(),
        adjudicator=HeuristicAdjudicator(),
    )


def test_dry_run_reports_what_would_be_created_without_writing() -> None:
    repository = FakeRepository()
    service = _service(repository)

    counts = load_rules(service, repository, [RULE], build_id=BUILD_ID, commit=False)  # type: ignore[arg-type]

    assert counts == {"created": 1, "already_present": 0, "skipped_invalid": 0}
    assert repository.inserted == []


def test_commit_creates_the_rule_against_this_databases_own_build() -> None:
    repository = FakeRepository()
    service = _service(repository)

    counts = load_rules(service, repository, [RULE], build_id=BUILD_ID, commit=True)  # type: ignore[arg-type]

    assert counts == {"created": 1, "already_present": 0, "skipped_invalid": 0}
    assert len(repository.inserted) == 1
    assert repository.inserted[0]["build_id"] == BUILD_ID
    assert repository.inserted[0]["target_value"] == "fwd"


def test_an_equivalent_existing_rule_is_not_duplicated() -> None:
    repository = FakeRepository(existing=[
        {
            **RULE, "rule_id": uuid4(), "build_id": BUILD_ID, "status": "saved",
            "matched_rows": 10, "would_resolve": 10, "already_resolved": 0,
        }
    ])
    service = _service(repository)

    counts = load_rules(service, repository, [RULE], build_id=BUILD_ID, commit=True)  # type: ignore[arg-type]

    assert counts == {"created": 0, "already_present": 1, "skipped_invalid": 0}
    assert repository.inserted == []


def test_a_rule_with_a_different_target_value_is_not_treated_as_a_duplicate() -> None:
    repository = FakeRepository(existing=[
        {
            **RULE, "target_value": "rwd", "rule_id": uuid4(), "build_id": BUILD_ID,
            "status": "saved", "matched_rows": 10, "would_resolve": 10, "already_resolved": 0,
        }
    ])
    service = _service(repository)

    counts = load_rules(service, repository, [RULE], build_id=BUILD_ID, commit=True)  # type: ignore[arg-type]

    assert counts == {"created": 1, "already_present": 0, "skipped_invalid": 0}
