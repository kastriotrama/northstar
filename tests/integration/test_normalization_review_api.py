from datetime import UTC, datetime

from fastapi.testclient import TestClient

from api.app.features.match_review.router import get_match_review_service
from api.app.features.match_review.schemas import (
    MatchBlockerCategoryView,
    MatchReviewDecisionRequest,
    MatchReviewItemView,
    MatchReviewPatternDecision,
    MatchReviewPatternDecisionRequest,
    MatchReviewPatternPage,
    MatchReviewPatternView,
    MatchReviewPage,
    MatchRunReviewSummary,
)
from api.app.features.normalization_review.router import get_normalization_review_service
from api.app.features.normalization_review.schemas import (
    NormalizationReviewFacets,
    NormalizationReviewFilters,
    NormalizationReviewPage,
    NormalizationReviewVehicle,
    NormalizationStatusSummary,
)
from api.app.features.review_queue.router import get_review_queue_service
from api.app.features.review_queue.schemas import (
    ReviewQueueItemView,
    ReviewQueuePage,
    ReviewTransitionRequest,
    RuleActivityView,
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
                    source_batch_id="screen-demo-250",
                    registration_plate="ABC123",
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


class FakeReviewQueueService:
    def list_items(
        self, *, status: str | None, batch_id: str | None, limit: int
    ) -> ReviewQueuePage:
        item = self._item(status or "pending")
        return ReviewQueuePage(
            items=[item],
            counts={"pending": 1, "in_review": 0, "resolved": 0, "rejected": 0},
            rule_activity=[
                RuleActivityView(
                    rule_id="BDY-110",
                    rule_kind="translation_rule",
                    action="draft",
                    previous_value="estate",
                    new_value="sedan",
                    change_note="Stakeholder correction",
                    changed_at=datetime.now(UTC),
                )
            ],
        )

    def transition(self, item_id: int, request: ReviewTransitionRequest) -> ReviewQueueItemView:
        item = self._item(request.status)
        if request.status == "resolved":
            item.resolution = (
                {"verdict": request.verdict, "reason": request.reason}
                if request.verdict
                else {
                    "field": request.field,
                    "canonical_value": request.canonical_value,
                    "decision_scope": request.decision_scope,
                    "reason": request.reason,
                }
            )
            item.resolved_by = request.reviewer
        return item

    @staticmethod
    def _item(status: str) -> ReviewQueueItemView:
        now = datetime.now(UTC)
        return ReviewQueueItemView(
            id=7,
            review_id="00000000-0000-0000-0000-000000000007",
            source_batch_id="batch-1",
            source_record_id=42,
            reason_code="normalization_review_required",
            reason_detail="manufacturer_missing",
            confidence=0.55,
            status=status,
            created_at=now,
            updated_at=now,
            source_evidence={"brand": "TOYOTA COROLLA", "model": "COROLLA"},
        )


class FakeMatchReviewService:
    def summary(self, operation_id: str | None) -> MatchRunReviewSummary:
        return MatchRunReviewSummary(
            operation_id=operation_id or "op-1",
            status="running",
            processed=25_000,
            expected_source_rows=6_515_471,
            progress_percent=0.384,
            blockers=[
                MatchBlockerCategoryView(
                    code="bodywork_conflict",
                    title="Bodywork conflict",
                    guidance="Review bodywork evidence",
                    count=7_282,
                    pending=10,
                )
            ],
        )

    def items(
        self, *, operation_id: str, category: str | None, status: str | None,
        limit: int, offset: int,
    ) -> MatchReviewPage:
        return MatchReviewPage(
            operation_id=operation_id,
            category=category,
            total=1,
            limit=limit,
            offset=offset,
            items=[self._item(operation_id, status or "pending")],
        )

    def decide(
        self, operation_id: str, item_id: int, request: MatchReviewDecisionRequest
    ) -> MatchReviewItemView:
        item = self._item(operation_id, "resolved")
        item.id = item_id
        item.resolution = {
            "action": request.action,
            "selected_candidate_reference": "0001",
        }
        return item

    def patterns(self, *, operation_id: str, category: str | None) -> MatchReviewPatternPage:
        return MatchReviewPatternPage(
            operation_id=operation_id,
            category=category,
            patterns=[MatchReviewPatternView(
                pattern_key="bodywork_conflict:abc",
                category="bodywork_conflict",
                title="TS body code AC → TecDoc SUV",
                summary="Choose a scoped compatibility policy.",
                source_values={"body_code": "AC"},
                candidate_values={"bodywork": ["SUV"]},
                sample_occurrences=4,
                category_occurrences=7282,
                examples=[{"manufacturer": "KIA", "model": "NIRO"}],
            )],
        )

    def decide_pattern(
        self, operation_id: str, pattern_key: str, request: MatchReviewPatternDecisionRequest,
    ) -> MatchReviewPatternDecision:
        return MatchReviewPatternDecision(
            decision_id="00000000-0000-0000-0000-000000000008",
            action=request.action,
            selected_values=request.selected_values,
            reviewer=request.reviewer,
            reason=request.reason,
            created_at=datetime.now(UTC),
        )

    @staticmethod
    def _item(operation_id: str, status: str) -> MatchReviewItemView:
        return MatchReviewItemView(
            id=9,
            operation_id=operation_id,
            category="bodywork_conflict",
            category_title="Bodywork conflict",
            category_guidance="Review bodywork evidence",
            source_record_id=42,
            source_evidence={"plate": "ABC123", "model": "V60"},
            reason_codes=["context_conflict:bodywork"],
            candidate_matches=[{"candidate_reference": "0001", "confidence": 0.91}],
            status=status,
            updated_at=datetime.now(UTC),
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
    assert "Brand exactly matches a reviewed example" in javascript.text
    assert '.normalize("NFD")' in javascript.text
    assert "Manufacturer entities" in screen.text
    assert stylesheet.status_code == 200
    assert ".workspace" in stylesheet.text
    assert javascript.status_code == 200
    assert "/v1/normalization-review/vehicles" in javascript.text
    assert "/v1/normalization-review/rules/reprocess" in javascript.text
    assert "/v1/normalization-review/tecdoc/vehicles" in javascript.text
    assert "/v1/normalization-review/tecdoc/entities" in javascript.text
    assert 'data-rule-id="${escapeHtml(rule)}"' in javascript.text
    assert "showRuleInVehicle(button.dataset.ruleId)" in javascript.text
    assert 'id="vehicle-rule-detail"' in screen.text
    assert 'id="queue-view"' in screen.text
    assert 'id="match-review-view"' in screen.text
    assert "/v1/normalization-review/queue" in javascript.text
    assert "/v1/match-review/summary" in javascript.text
    assert "If Tillverkare is missing, Brand may become a manufacturer candidate" in javascript.text


def test_match_review_api_lists_blockers_and_records_decision(client: TestClient) -> None:
    client.app.dependency_overrides[get_match_review_service] = FakeMatchReviewService
    try:
        summary = client.get("/v1/match-review/summary", params={"operation_id": "op-1"})
        listed = client.get(
            "/v1/match-review/items",
            params={"operation_id": "op-1", "category": "bodywork_conflict"},
        )
        decided = client.post(
            "/v1/match-review/items/9/decision",
            params={"operation_id": "op-1"},
            json={
                "action": "accept_top_candidate",
                "reviewer": "Stakeholder",
                "reason": "Independent evidence confirms this KType",
                "scope": "vehicle_only",
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert summary.status_code == 200
    assert summary.json()["blockers"][0]["count"] == 7_282
    assert listed.status_code == 200
    assert listed.json()["items"][0]["source_evidence"]["plate"] == "ABC123"
    assert decided.status_code == 200
    assert decided.json()["resolution"]["selected_candidate_reference"] == "0001"


def test_match_review_api_exposes_plate_free_patterns_and_rule_decision(client: TestClient) -> None:
    client.app.dependency_overrides[get_match_review_service] = FakeMatchReviewService
    try:
        patterns = client.get(
            "/v1/match-review/patterns",
            params={"operation_id": "op-1", "category": "bodywork_conflict"},
        )
        decision = client.post(
            "/v1/match-review/patterns/bodywork_conflict%3Aabc/decision",
            params={"operation_id": "op-1"},
            json={
                "action": "accept_pattern",
                "reviewer": "Stakeholder",
                "reason": "Repeated examples confirm the ontology mapping",
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert patterns.status_code == 200
    assert patterns.json()["patterns"][0]["title"] == "TS body code AC → TecDoc SUV"
    assert patterns.json()["patterns"][0]["category_occurrences"] == 7282
    assert decision.status_code == 200
    assert decision.json()["action"] == "accept_pattern"


def test_review_queue_api_lists_and_resolves_items(client: TestClient) -> None:
    client.app.dependency_overrides[get_review_queue_service] = FakeReviewQueueService
    try:
        listed = client.get("/v1/normalization-review/queue", params={"status": "pending"})
        resolved = client.post(
            "/v1/normalization-review/queue/7/transition",
            json={
                "status": "resolved",
                "reviewer": "Ada",
                "field": "manufacturer",
                "canonical_value": "Toyota",
                "decision_scope": "vehicle_only",
                "reason": "Reviewed TS Brand and model evidence",
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert listed.status_code == 200
    assert listed.json()["items"][0]["source_record_id"] == 42
    assert listed.json()["rule_activity"][0]["rule_id"] == "BDY-110"
    assert resolved.status_code == 200
    assert resolved.json()["resolution"]["canonical_value"] == "Toyota"


def test_review_queue_api_records_margin_calibration_verdict(client: TestClient) -> None:
    client.app.dependency_overrides[get_review_queue_service] = FakeReviewQueueService
    try:
        response = client.post(
            "/v1/normalization-review/queue/7/transition",
            json={
                "status": "resolved",
                "reviewer": "Ada",
                "verdict": "unsure",
                "reason": "Evidence does not distinguish the candidates",
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["resolution"]["verdict"] == "unsure"


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
