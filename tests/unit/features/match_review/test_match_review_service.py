from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from api.app.features.match_review.adjudicator import HeuristicAdjudicator
from api.app.features.match_review.field_resolution import PredicateTerm
from api.app.features.match_review.schemas import (
    OemSampleRequest,
    ProposalReviewRequest,
    RefineRequest,
    RuleCondition,
    RulePreviewRequest,
)
from api.app.features.match_review.service import (
    MatchReviewConflictError,
    MatchReviewNotFoundError,
    MatchReviewService,
    MemberVinUnavailableError,
)

NOW = datetime.now(UTC)
CHUNK_ID = UUID("11111111-1111-1111-1111-111111111111")


class FakeRepository:
    def __init__(self) -> None:
        self.chunk_status = "open"
        self.brand_variants = 1
        self.model_no_variants = 1
        self.previewed_conditions: list[PredicateTerm] = []
        self.population_oem: list[dict[str, Any]] = []
        self.member_source: dict[str, Any] = {
            "plate": "ABC123",
            "manufacturer": "VOLVO",
            "model": "V70",
            "vehicle_year": 2014,
            "fuel1": "2",
        }
        self.vins: dict[int, str] = {10: "YV1SW6151E1234567"}
        self.evidence: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.samples: list[dict[str, Any]] = []
        self.proposals: dict[UUID, dict[str, Any]] = {}
        self.evidence_inserts = 0

    def ensure_schema(self) -> None:
        return None

    def fetch_builds(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return []

    def fetch_build(self, build_id: UUID) -> dict[str, Any] | None:
        return {
            "build_id": build_id,
            "source_batch_id": "batch-1",
            "signature_version": "1",
            "status": "completed",
            "row_count": 226_529,
            "chunk_count": 35_691,
            "started_at": NOW,
            "finished_at": NOW,
        }

    def fetch_latest_build(self) -> dict[str, Any] | None:
        return None

    def fetch_chunk_page(
        self,
        *,
        build_id: UUID,
        status: str | None,
        query: str,
        limit: int,
        offset: int,
    ) -> tuple[int, int, list[dict[str, Any]]]:
        return 0, 0, []

    def fetch_chunk(self, chunk_id: UUID) -> dict[str, Any] | None:
        if chunk_id != CHUNK_ID:
            return None
        return {
            "chunk_id": CHUNK_ID,
            "build_id": uuid4(),
            "signature": {"manufacturer": "Volvo", "model_family": "V70"},
            "member_count": 500,
            "reason_profile": {"model_evidence_missing": 500},
            "status": self.chunk_status,
        }

    def fetch_members(
        self, chunk_id: UUID, *, limit: int = 25
    ) -> list[dict[str, Any]]:
        return [
            {
                "source_record_id": 10,
                "source_batch_id": "batch-1",
                "normalization_status": "review_required",
                "review_reasons": ["model_evidence_missing"],
                "plate": "ABC123",
                "source_manufacturer": "VOLVO",
                "source_model": "V70",
                "source_year": "2014",
            }
        ]

    def fetch_field_profile(
        self,
        chunk_id: UUID,
        *,
        fields: tuple[str, ...],
        sample_limit: int = 5_000,
        top_values: int = 5,
    ) -> tuple[int, list[dict[str, Any]]]:
        return 500, [
            {
                "field": "brand",
                "distinct_count": self.brand_variants,
                "present_count": 500,
                "top_values": [{"value": "VOLVO 131341 M", "count": 500}],
            },
            {
                "field": "fuel1",
                "distinct_count": 1,
                "present_count": 500,
                "top_values": [{"value": "1", "count": 500}],
            },
        ]

    def fetch_unresolved_populations(
        self,
        build_id: UUID,
        *,
        source_field: str,
        signature_field: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if source_field != "is_4wd":
            return []
        return [{"source_value": "0", "row_count": 191_921}]

    def fetch_discriminators(
        self,
        build_id: UUID,
        *,
        source_field: str,
        source_value: str,
        signature_field: str,
        candidate_fields: tuple[str, ...],
        top_values: int = 8,
    ) -> tuple[int, list[dict[str, Any]]]:
        return 191_921, [
            {
                "field": "eu_category",
                "distinct_count": 2,
                "present_count": 191_921,
                "top_values": [
                    {"value": "M1", "count": 191_884},
                    {"value": "M1G", "count": 37},
                ],
            },
            {
                "field": "fab_code",
                "distinct_count": 151,
                "present_count": 191_921,
                "top_values": [{"value": "VO", "count": 44_253}],
            },
        ]

    def preview_rule(
        self,
        build_id: UUID,
        *,
        conditions: list[PredicateTerm],
        signature_field: str,
        sample_limit: int = 5,
    ) -> dict[str, Any]:
        self.previewed_conditions = conditions
        return {
            "matched_rows": 44_253,
            "would_resolve": 44_253,
            "already_resolved": 0,
            "sample_plates": ["ABS229"],
        }

    def fetch_population_attributes(
        self,
        build_id: UUID,
        *,
        source_field: str,
        source_value: str,
        signature_field: str,
        top_values: int = 6,
        sample_limit: int = 20_000,
    ) -> tuple[int, list[dict[str, Any]]]:
        return 191_000, [
            {
                "field": "odometer",
                "distinct_count": 900,
                "present_count": 191_000,
                "top_values": [{"value": "120000", "count": 12}],
            }
        ]

    def fetch_refined_discriminators(
        self,
        build_id: UUID,
        *,
        signature_field: str,
        conditions: list[PredicateTerm],
        candidate_fields: tuple[str, ...],
        top_values: int = 8,
    ) -> tuple[int, list[dict[str, Any]]]:
        return 938, [
            {
                "field": "brand",
                "distinct_count": 2,
                "present_count": 938,
                "top_values": [{"value": "MERCEDES-BENZ 204 K", "count": 493}],
            },
            {
                "field": "model_no",
                "distinct_count": self.model_no_variants,
                "present_count": 938,
                "top_values": [{"value": "000731", "count": 938}],
            },
        ]

    def fetch_narrowing_trail(
        self,
        build_id: UUID,
        *,
        signature_field: str,
        conditions: list[PredicateTerm],
    ) -> list[int]:
        return [191_921, 938][: len(conditions)]

    def fetch_signature_values(
        self, build_id: UUID, *, signature_field: str, limit: int = 60
    ) -> list[dict[str, Any]]:
        return [{"value": "Clio", "count": 2_494}, {"value": "Octavia", "count": 1_899}]

    def fetch_population_oem_samples(
        self,
        build_id: UUID,
        *,
        source_field: str,
        source_value: str,
        signature_field: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        return self.population_oem

    def fetch_member_evidence(
        self, chunk_id: UUID, source_record_id: int
    ) -> dict[str, Any] | None:
        if source_record_id != 10:
            return None
        return {
            "source_record": self.member_source,
            "normalized_payload": {
                "normalized": {
                    "manufacturer": "Volvo",
                    "model_family": "V70",
                    "production_year": 2014,
                    "energy_sources": ["petrol"],
                },
                "candidates": {},
            },
        }

    def fetch_member_vin(
        self, chunk_id: UUID, source_record_id: int
    ) -> str | None:
        return self.vins.get(source_record_id)

    def fetch_oem_evidence(
        self, *, provider: str, vin: str, dataset_version: str
    ) -> dict[str, Any] | None:
        return self.evidence.get((provider, vin, dataset_version))

    def insert_oem_evidence(
        self,
        *,
        request_id: UUID,
        provider: str,
        vin: str,
        dataset_version: str,
        response_payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.evidence_inserts += 1
        stored = {
            "id": self.evidence_inserts,
            "vin": vin,
            "response_payload": response_payload,
            "fetched_at": NOW,
        }
        self.evidence[(provider, vin, dataset_version)] = stored
        return stored

    def link_sample(
        self, *, chunk_id: UUID, evidence_id: int, source_record_id: int
    ) -> None:
        self.samples.append(
            {
                "sample_id": evidence_id,
                "source_record_id": source_record_id,
                "provider": "test-provider",
                "vin": self.vins[source_record_id],
                "dataset_version": "2026-08",
                "response_payload": {"manufacturer": "Volvo"},
                "fetched_at": NOW,
            }
        )

    def fetch_samples(self, chunk_id: UUID) -> list[dict[str, Any]]:
        return list(self.samples)

    def insert_proposal(self, **kwargs: Any) -> dict[str, Any]:
        stored = {
            **kwargs,
            "status": "proposed",
            "reviewed_by": None,
            "review_note": None,
            "reviewed_at": None,
            "created_at": NOW,
        }
        self.proposals[kwargs["proposal_id"]] = stored
        self.chunk_status = "proposed"
        return stored

    def fetch_proposal(self, proposal_id: UUID) -> dict[str, Any] | None:
        return self.proposals.get(proposal_id)

    def fetch_proposals(self, chunk_id: UUID) -> list[dict[str, Any]]:
        return list(self.proposals.values())

    def review_proposal(
        self,
        *,
        proposal_id: UUID,
        status: str,
        chunk_status: str,
        reviewer: str,
        note: str | None,
    ) -> dict[str, Any] | None:
        proposal = self.proposals.get(proposal_id)
        if proposal is None or proposal["status"] != "proposed":
            return None
        proposal.update(
            status=status,
            reviewed_by=reviewer,
            review_note=note,
            reviewed_at=NOW,
        )
        self.chunk_status = chunk_status
        return proposal


class FakeProvider:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "test-provider"

    @property
    def dataset_version(self) -> str:
        return "2026-08"

    def fetch_vehicle(self, vin: str) -> dict[str, Any]:
        self.calls += 1
        return {"manufacturer": "Volvo", "vin": vin}


def _service(
    repository: FakeRepository | None = None,
    provider: FakeProvider | None = None,
) -> tuple[MatchReviewService, FakeRepository, FakeProvider]:
    repository = repository or FakeRepository()
    provider = provider or FakeProvider()
    service = MatchReviewService(
        repository,
        oem_provider=provider,
        adjudicator=HeuristicAdjudicator(),
    )
    return service, repository, provider


def test_oem_sample_masks_vin_and_bills_provider_once() -> None:
    service, _repository, provider = _service()
    request = OemSampleRequest(source_record_id=10, request_id=uuid4())

    first = service.fetch_oem_sample(CHUNK_ID, request)
    second = service.fetch_oem_sample(
        CHUNK_ID, OemSampleRequest(source_record_id=10, request_id=uuid4())
    )

    assert provider.calls == 1
    assert first.reused_cached_evidence is False
    assert second.reused_cached_evidence is True
    assert "YV1SW6151E1234567" not in first.masked_vin
    assert first.masked_vin.startswith("YV1")
    assert first.masked_vin.endswith("567")
    assert first.response_payload["vin"] != "YV1SW6151E1234567"


def test_oem_sample_without_vin_fails_without_provider_call() -> None:
    service, _, provider = _service()

    with pytest.raises(MemberVinUnavailableError):
        service.fetch_oem_sample(
            CHUNK_ID, OemSampleRequest(source_record_id=99, request_id=uuid4())
        )
    assert provider.calls == 0


def test_unknown_chunk_is_reported_as_missing() -> None:
    service, _, _ = _service()

    with pytest.raises(MatchReviewNotFoundError):
        service.get_chunk(uuid4())


def test_members_carry_readable_labels() -> None:
    service, _, _ = _service()

    detail = service.get_chunk(CHUNK_ID)

    assert detail.members[0].label == "ABC123 · VOLVO · V70 · 2014"


def test_member_comparison_pairs_sources_without_oem() -> None:
    service, _, _ = _service()

    comparison = service.get_member_comparison(CHUNK_ID, 10)

    assert comparison.plate == "ABC123"
    assert comparison.has_oem_evidence is False
    by_field = {row.field: row for row in comparison.rows}
    assert by_field["Manufacturer"].source_value == "VOLVO"
    assert by_field["Manufacturer"].normalized_value == "Volvo"
    assert by_field["Manufacturer"].conflict is None
    assert by_field["Fuel"].source_value == "2"
    assert by_field["Fuel"].normalized_value == "petrol"


def test_member_comparison_flags_oem_conflicts() -> None:
    repository = FakeRepository()
    service, _, _ = _service(repository)
    service.fetch_oem_sample(
        CHUNK_ID, OemSampleRequest(source_record_id=10, request_id=uuid4())
    )
    repository.samples[0]["response_payload"] = {
        "manufacturer": "Volvo",
        "model": "V60",
        "model_year": 2014,
    }

    comparison = service.get_member_comparison(CHUNK_ID, 10)

    assert comparison.has_oem_evidence is True
    by_field = {row.field: row for row in comparison.rows}
    assert by_field["Manufacturer"].conflict is False
    assert by_field["Model"].conflict is True
    assert by_field["Year"].conflict is False


def test_field_profile_reports_uniform_chunk() -> None:
    service, _, _ = _service()

    profile = service.get_field_profile(CHUNK_ID)

    assert profile.varying_fields == []
    assert profile.truncated is False
    assert all(field.uniform for field in profile.fields)


def test_field_profile_flags_varying_identity_field() -> None:
    repository = FakeRepository()
    repository.brand_variants = 26
    service, _, _ = _service(repository)

    profile = service.get_field_profile(CHUNK_ID)

    assert profile.varying_fields == ["brand"]
    assert profile.member_count == 500
    assert profile.truncated is False


def test_identity_spread_drives_split_proposal() -> None:
    repository = FakeRepository()
    repository.brand_variants = 26
    service, _, _ = _service(repository)

    proposal = service.create_proposal(CHUNK_ID)

    assert proposal.recommendation == "split_chunk"
    assert "brand" in proposal.reasoning


def test_unresolved_overview_ranks_populations() -> None:
    service, _, _ = _service()

    overview = service.get_unresolved_overview(uuid4())

    assert overview.populations[0].source_field == "is_4wd"
    assert overview.populations[0].signature_field == "drive_type"
    assert overview.populations[0].row_count == 191_921


def test_discriminators_demote_near_constant_fields() -> None:
    service, _, _ = _service()

    report = service.get_discriminators(
        uuid4(), source_field="is_4wd", source_value="0"
    )

    assert report.population == 191_921
    assert report.fields[0].field == "fab_code"
    assert report.fields[-1].field == "eu_category"
    assert report.fields[-1].usable is False


def test_rule_preview_reports_coverage_without_writing() -> None:
    repository = FakeRepository()
    service, _, _ = _service(repository)

    preview = service.preview_rule(
        RulePreviewRequest(
            build_id=uuid4(),
            conditions=[
                RuleCondition(field="is_4wd", value="0"),
                RuleCondition(field="fab_code", value="VO"),
            ],
            target_field="drive_type",
            target_value="fwd",
        )
    )

    assert preview.would_resolve == 44_253
    assert repository.previewed_conditions == [
        PredicateTerm("source", "is_4wd", "equals", ("0",)),
        PredicateTerm("source", "fab_code", "equals", ("VO",)),
    ]
    assert not repository.proposals


def test_rule_preview_rejects_non_canonical_target_value() -> None:
    service, _, _ = _service()

    with pytest.raises(MatchReviewConflictError):
        service.preview_rule(
            RulePreviewRequest(
                build_id=uuid4(),
                conditions=[RuleCondition(field="is_4wd", value="0")],
                target_field="drive_type",
                target_value="front-wheel",
            )
        )


def test_rule_preview_rejects_unknown_source_field() -> None:
    service, _, _ = _service()

    with pytest.raises(MatchReviewConflictError):
        service.preview_rule(
            RulePreviewRequest(
                build_id=uuid4(),
                conditions=[RuleCondition(field="odometer_secret", value="1")],
                target_field="drive_type",
                target_value="fwd",
            )
        )


def test_comparison_marks_unresolved_versus_missing() -> None:
    service, _, _ = _service()

    comparison = service.get_member_comparison(CHUNK_ID, 10)
    by_field = {row.field: row for row in comparison.rows}

    assert by_field["Manufacturer"].status == "resolved"
    assert by_field["Drive"].status == "missing"
    assert by_field["Variant"].status == "missing"


def test_open_vocabulary_offers_observed_values_as_suggestions() -> None:
    """`model_family` has no canonical list, so free text must be allowed."""

    service, _, _ = _service()

    vocabulary = service.get_target_vocabulary(uuid4(), target_field="model_family")

    assert vocabulary.closed is False
    assert vocabulary.source == "observed"
    assert vocabulary.values[0].value == "Clio"


def test_closed_vocabulary_comes_from_the_reviewed_rules() -> None:
    service, _, _ = _service()

    vocabulary = service.get_target_vocabulary(uuid4(), target_field="drive_type")

    assert vocabulary.closed is True
    assert vocabulary.source == "reviewed_rules"
    assert [entry.value for entry in vocabulary.values] == ["fwd", "rwd", "awd"]


def test_bodywork_vocabulary_is_derived_not_restated() -> None:
    service, _, _ = _service()

    vocabulary = service.get_target_vocabulary(uuid4(), target_field="bodywork_form")

    # Derived from the active reviewed rule set, which uses `estate` — not
    # the `wagon` spelling that appears in older rule tuples in the module.
    values = {entry.value for entry in vocabulary.values}
    assert vocabulary.closed is True
    assert {"sedan", "hatchback", "estate", "convertible"} <= values


def test_unknown_target_field_is_rejected() -> None:
    service, _, _ = _service()

    with pytest.raises(MatchReviewNotFoundError):
        service.get_target_vocabulary(uuid4(), target_field="odometer")


def test_free_text_is_accepted_for_an_open_target() -> None:
    service, _, _ = _service()

    preview = service.preview_rule(
        RulePreviewRequest(
            build_id=uuid4(),
            conditions=[RuleCondition(field="type_text", value="YS3F")],
            target_field="model_family",
            target_value="9-3",
        )
    )

    assert preview.target_value == "9-3"


def test_comparison_names_the_key_that_actually_supplied_the_value() -> None:
    """Manufacturer reads `manufacturer` then falls back to `brand`."""

    service, _, _ = _service()

    by_field = {row.field: row for row in service.get_member_comparison(CHUNK_ID, 10).rows}

    assert by_field["Manufacturer"].source_field == "manufacturer"
    assert by_field["Fuel"].source_field == "fuel1"
    # Nothing supplied a value, so the primary key is named as where we looked.
    assert by_field["Drive"].source_field == "is_4wd"


def test_comparison_falls_back_to_the_secondary_key_when_primary_is_empty() -> None:
    repository = FakeRepository()
    repository.member_source = {
        "plate": "ABC123",
        "brand": "VOLVO 21134 E",
        "model_year": "1965",
    }
    service, _, _ = _service(repository)

    by_field = {row.field: row for row in service.get_member_comparison(CHUNK_ID, 10).rows}

    assert by_field["Manufacturer"].source_value == "VOLVO 21134 E"
    assert by_field["Manufacturer"].source_field == "brand"
    assert by_field["Year"].source_field == "model_year"


def _refine(repository: FakeRepository) -> Any:
    service, _, _ = _service(repository)
    return service.refine(
        RefineRequest(
            build_id=uuid4(),
            source_field="is_4wd",
            source_value="0",
            conditions=[
                RuleCondition(field="is_4wd", value="0"),
                RuleCondition(
                    field="brand",
                    operator="starts_with",
                    values=["MERCEDES-BENZ 204"],
                ),
            ],
        )
    )


def test_refine_ignores_fields_the_reviewer_already_constrained() -> None:
    """Grouping `204` with `204 K` is a deliberate statement, not a conflict."""

    result = _refine(FakeRepository())

    assert "brand" not in result.varying_identity_fields
    assert result.homogeneous is True
    assert all(field.field != "brand" for field in result.fields)


def test_refine_blocks_while_an_unconstrained_identity_field_varies() -> None:
    repository = FakeRepository()
    repository.model_no_variants = 4

    result = _refine(repository)

    assert result.varying_identity_fields == ["model_no"]
    assert result.homogeneous is False


def test_refine_reports_the_narrowing_trail() -> None:
    result = _refine(FakeRepository())

    assert [step.matched_rows for step in result.trail] == [191_921, 938]
    assert result.trail[1].label == "brand starts with MERCEDES-BENZ 204"


def test_member_comparison_missing_member_raises() -> None:
    service, _, _ = _service()

    with pytest.raises(MatchReviewNotFoundError):
        service.get_member_comparison(CHUNK_ID, 999)


def test_proposal_lifecycle_approve_updates_chunk() -> None:
    service, repository, _ = _service()

    proposal = service.create_proposal(CHUNK_ID)
    assert proposal.recommendation == "needs_more_evidence"
    assert repository.chunk_status == "proposed"

    reviewed = service.review_proposal(
        proposal.proposal_id,
        ProposalReviewRequest(action="approve", reviewer="Valon", note="ok"),
    )
    assert reviewed.status == "approved"
    assert repository.chunk_status == "approved"

    with pytest.raises(MatchReviewConflictError):
        service.review_proposal(
            proposal.proposal_id,
            ProposalReviewRequest(action="reject", reviewer="Valon", note=None),
        )


def test_rejected_proposal_reopens_chunk() -> None:
    service, repository, _ = _service()
    proposal = service.create_proposal(CHUNK_ID)

    reviewed = service.review_proposal(
        proposal.proposal_id,
        ProposalReviewRequest(action="reject", reviewer="Ada", note=None),
    )

    assert reviewed.status == "rejected"
    assert repository.chunk_status == "open"


def test_closed_chunk_refuses_new_proposals() -> None:
    repository = FakeRepository()
    repository.chunk_status = "approved"
    service, _, _ = _service(repository)

    with pytest.raises(MatchReviewConflictError):
        service.create_proposal(CHUNK_ID)
