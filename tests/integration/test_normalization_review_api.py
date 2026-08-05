from fastapi.testclient import TestClient

from api.app.features.normalization_review.router import get_normalization_review_service
from api.app.features.normalization_review.schemas import (
    NormalizationReviewFacets,
    NormalizationReviewFilters,
    NormalizationReviewPage,
    NormalizationReviewVehicle,
    NormalizationStatusSummary,
)
from api.app.features.rule_review.router import get_rule_review_service
from api.app.features.rule_review.schemas import (
    BatchSummaryView,
    ManufacturerEntityDraftRequest,
    ManufacturerEntityView,
    ReprocessResponse,
    RuleActivationResponse,
    RuleDraftRequest,
    RuleListResponse,
    RuleView,
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
                    source_brand="VOLVO V60",
                    status="provisional",
                    confidence=0.8,
                    manufacturer="Volvo",
                    model_family="V60",
                    bodywork="estate",
                    energy_sources=["petrol"],
                    candidate_rule_ids=["MFR-BRAND-PREFIX-FALLBACK"],
                )
            ],
        )


class FakeRuleReviewService:
    def list_rules(self) -> RuleListResponse:
        return RuleListResponse(
            base_version="ts-translation-v4",
            active_version="ts-review-approved",
            draft_count=0,
            rules=[
                RuleView(
                    rule_id="BDY-110",
                    area="bodywork_code",
                    source_fields=["body_code"],
                    source_terms=["AC"],
                    canonical_field="bodywork_form",
                    base_canonical_value="estate",
                    active_canonical_value="estate",
                    effective_canonical_value="estate",
                    canonical_options=["estate", "sedan"],
                    active_decision="accepted",
                    effective_decision="accepted",
                    vehicle_scopes=["passenger"],
                    manufacturers=[],
                    has_draft=False,
                )
            ],
            manufacturer_entities=[
                ManufacturerEntityView(
                    entity_id="MFE-AUDI",
                    source_field="brand",
                    source_term="AUDI A4 2 0TS QUATTRO",
                    effective_canonical_name=None,
                    active_entity_role="unknown",
                    effective_entity_role="unknown",
                    active_base_behavior="require_evidence_review",
                    effective_base_behavior="require_evidence_review",
                    occurrences=1,
                    is_discovered=True,
                    match_type="whole_token_prefix",
                    reviewed_examples=["AUDI A4 2 0TS QUATTRO"],
                )
            ],
            review_reason_summary={"manufacturer_missing": 106},
        )

    def save_draft(self, rule_id: str, request: RuleDraftRequest) -> RuleListResponse:
        result = self.list_rules()
        result.rules[0].has_draft = True
        result.rules[0].effective_canonical_value = request.canonical_value
        result.draft_count = 1
        return result

    def save_manufacturer_entity_draft(
        self, entity_id: str, request: ManufacturerEntityDraftRequest
    ) -> RuleListResponse:
        result = self.list_rules()
        result.manufacturer_entities[0].has_draft = True
        result.manufacturer_entities[0].effective_canonical_name = request.canonical_name
        result.manufacturer_entities[0].effective_entity_role = request.entity_role
        result.manufacturer_entities[0].effective_base_behavior = request.base_behavior
        result.draft_count = 1
        return result

    def activate(self, note: str) -> RuleActivationResponse:
        return RuleActivationResponse(
            version="ts-review-new",
            activated_rules=1,
            activated_at="2026-08-05T12:00:00Z",
        )

    def reprocess(self, source_batch_id: str) -> ReprocessResponse:
        return ReprocessResponse(
            source_batch_id=source_batch_id,
            new_batch_id=f"{source_batch_id}-rules",
            rule_version="ts-review-new",
            before=BatchSummaryView(
                total=10, resolved=1, provisional=2, review_required=7, failed=0
            ),
            after=BatchSummaryView(
                total=10, resolved=5, provisional=2, review_required=3, failed=0
            ),
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
    assert payload["items"][0]["source_brand"] == "VOLVO V60"
    assert payload["items"][0]["candidate_rule_ids"] == ["MFR-BRAND-PREFIX-FALLBACK"]


def test_review_screen_and_assets_are_served_by_application(client: TestClient) -> None:
    screen = client.get("/normalization-review")
    stylesheet = client.get("/normalization-review/assets/styles.css")
    javascript = client.get("/normalization-review/assets/app.js")

    assert screen.status_code == 200
    assert "Normalization review" in screen.text
    assert 'id="filter-manufacturer"' in screen.text
    assert 'id="decision-trace"' in screen.text
    assert 'id="rules-view"' in screen.text
    assert 'id="rule-form"' in screen.text
    assert 'id="manufacturer-form"' in screen.text
    assert 'id="manufacturer-created-at"' in screen.text
    assert 'id="manufacturer-examples-section"' in screen.text
    assert 'id="source-evidence"' in screen.text
    assert "Brand is an exact reviewed example beneath its Manufacturer entity" in javascript.text
    assert '.normalize("NFD")' in javascript.text
    assert "Manufacturer entities" in screen.text
    assert stylesheet.status_code == 200
    assert ".workspace" in stylesheet.text
    assert javascript.status_code == 200
    assert "/v1/normalization-review/vehicles" in javascript.text
    assert "/v1/normalization-review/rules/reprocess" in javascript.text


def test_rule_review_api_supports_drafts_activation_and_safe_reprocess(
    client: TestClient,
) -> None:
    client.app.dependency_overrides[get_rule_review_service] = FakeRuleReviewService
    try:
        rules = client.get("/v1/normalization-review/rules")
        draft = client.put(
            "/v1/normalization-review/rules/BDY-110/draft",
            json={
                "canonical_value": "sedan",
                "decision": "accepted",
                "change_note": "Stakeholder correction",
            },
        )
        entity_draft = client.put(
            "/v1/normalization-review/rules/entities/MFE-AUDI/draft",
            json={
                "canonical_name": "Audi",
                "entity_role": "vehicle_manufacturer",
                "base_behavior": "use_entity",
                "change_note": "Reviewed exact Brand entity",
            },
        )
        activated = client.post(
            "/v1/normalization-review/rules/activate",
            json={"note": "Reviewed and approved"},
        )
        reprocessed = client.post(
            "/v1/normalization-review/rules/reprocess",
            json={"source_batch_id": "meeting-sample"},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert rules.status_code == 200
    assert rules.json()["rules"][0]["rule_id"] == "BDY-110"
    assert draft.json()["draft_count"] == 1
    assert entity_draft.json()["manufacturer_entities"][0]["effective_canonical_name"] == "Audi"
    assert activated.json()["version"] == "ts-review-new"
    assert reprocessed.json()["before"]["review_required"] == 7
    assert reprocessed.json()["after"]["review_required"] == 3


def test_review_api_is_documented_but_screen_is_not(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/v1/normalization-review/vehicles" in paths
    assert "/v1/normalization-review/rules" in paths
    assert "/v1/normalization-review/rules/reprocess" in paths
    assert "/v1/normalization-review/rules/entities/{entity_id}/draft" in paths
    assert "/normalization-review" not in paths
