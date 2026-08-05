from datetime import UTC, datetime
from typing import Any

import pytest

from api.app.features.rule_review.schemas import ManufacturerEntityDraftRequest, RuleDraftRequest
from api.app.features.rule_review.service import RuleReviewError, RuleReviewService
from ingestion.normalization_repository import NormalizationSummary
from ingestion.translation_dictionaries import TranslationRuleSet


class FakeRuleRepository:
    def __init__(self) -> None:
        self.drafts: dict[str, dict[str, Any]] = {}
        self.entity_drafts: dict[str, dict[str, Any]] = {}
        self.active: dict[str, Any] | None = None
        self.activation: dict[str, Any] | None = None

    def ensure_schema(self) -> None:
        return None

    def fetch_drafts(self) -> dict[str, dict[str, Any]]:
        return self.drafts

    def fetch_active_version(self) -> dict[str, Any] | None:
        return self.active

    def fetch_manufacturer_entity_drafts(self) -> dict[str, dict[str, Any]]:
        return self.entity_drafts

    def fetch_discovered_manufacturer_entities(self) -> list[dict[str, Any]]:
        return [
            {
                "source_field": "brand",
                "source_term": "AUDI A4 2.0TS QUATTRO",
                "occurrences": 2,
                "base_manufacturers": [],
            }
        ]

    def fetch_review_reason_summary(self) -> dict[str, int]:
        return {"manufacturer_missing": 6}

    def save_draft(self, **values: Any) -> None:
        rule_id = str(values.pop("rule_id"))
        self.drafts[rule_id] = values

    def delete_draft(self, rule_id: str) -> bool:
        return self.drafts.pop(rule_id, None) is not None

    def save_manufacturer_entity_draft(self, **values: Any) -> None:
        entity_id = str(values.pop("entity_id"))
        self.entity_drafts[entity_id] = values

    def delete_manufacturer_entity_draft(self, entity_id: str) -> bool:
        return self.entity_drafts.pop(entity_id, None) is not None

    def activate_drafts(self, **values: Any) -> tuple[int, datetime]:
        self.activation = values
        count = len(self.drafts) + len(self.entity_drafts)
        self.drafts = {}
        self.entity_drafts = {}
        return count, datetime(2026, 8, 5, tzinfo=UTC)


class FakeReprocessingAdapter:
    def __init__(self) -> None:
        self.rule_set: TranslationRuleSet | None = None
        self.manufacturer_entity_rules: dict[str, Any] | None = None

    def reprocess(
        self,
        *,
        source_batch_id: str,
        new_batch_id: str,
        rule_set: TranslationRuleSet,
        manufacturer_entity_rules: dict[str, Any],
    ) -> tuple[NormalizationSummary, NormalizationSummary]:
        self.rule_set = rule_set
        self.manufacturer_entity_rules = manufacturer_entity_rules
        return (
            NormalizationSummary(source_batch_id, 10, 2, 3, 5, 0),
            NormalizationSummary(new_batch_id, 10, 6, 2, 2, 0),
        )


def service() -> tuple[RuleReviewService, FakeRuleRepository, FakeReprocessingAdapter]:
    repository = FakeRuleRepository()
    adapter = FakeReprocessingAdapter()
    return RuleReviewService(repository, adapter), repository, adapter  # type: ignore[arg-type]


def test_draft_is_visible_without_changing_the_active_rule() -> None:
    subject, repository, _ = service()
    repository.drafts["BDY-110"] = {
        "canonical_value": "sedan",
        "decision": "proposed",
        "display_value": "Saloon",
        "change_note": "Stakeholder correction",
    }

    page = subject.list_rules()
    rule = next(item for item in page.rules if item.rule_id == "BDY-110")

    assert rule.active_canonical_value == "estate"
    assert rule.effective_canonical_value == "sedan"
    assert rule.has_draft is True


def test_draft_value_must_come_from_the_reviewed_field_vocabulary() -> None:
    subject, _, _ = service()

    with pytest.raises(RuleReviewError, match="canonical_value_not_in_reviewed_vocabulary"):
        subject.save_draft(
            "BDY-110",
            RuleDraftRequest(
                canonical_value="spaceship",
                decision="accepted",
                change_note="Not an approved bodywork value",
            ),
        )


def test_discovered_brand_entity_can_be_classified_as_a_reviewed_draft() -> None:
    subject, repository, _ = service()
    page = subject.list_rules()
    entity = next(item for item in page.manufacturer_entities if item.source_field == "brand")

    updated = subject.save_manufacturer_entity_draft(
        entity.entity_id,
        ManufacturerEntityDraftRequest(
            canonical_name="Audi",
            entity_role="vehicle_manufacturer",
            base_behavior="use_entity",
            change_note="Exact reviewed Brand value contains Audi model evidence",
        ),
    )

    reviewed = next(
        item for item in updated.manufacturer_entities if item.entity_id == entity.entity_id
    )
    assert reviewed.effective_canonical_name == "Audi"
    assert reviewed.effective_entity_role == "vehicle_manufacturer"
    assert reviewed.has_draft is True
    assert repository.entity_drafts[entity.entity_id]["source_term"] == "AUDI A4 2 0TS QUATTRO"


def test_activation_inherits_prior_overrides_and_adds_current_drafts() -> None:
    subject, repository, _ = service()
    repository.active = {
        "version": "ts-review-previous",
        "base_rule_version": "ts-translation-v4",
        "overrides": {"BDY-101": {"canonical_value": "hatchback"}},
        "activated_at": datetime(2026, 8, 4, tzinfo=UTC),
    }
    repository.drafts["BDY-110"] = {
        "canonical_value": "sedan",
        "decision": "accepted",
        "display_value": None,
        "change_note": "Approved correction",
    }

    result = subject.activate("Reviewed in stakeholder meeting")

    assert result.activated_rules == 1
    assert repository.activation is not None
    assert repository.activation["inherited_overrides"] == repository.active["overrides"]


def test_reprocess_uses_active_version_and_returns_before_after_comparison() -> None:
    subject, repository, adapter = service()
    repository.active = {
        "version": "ts-review-approved",
        "base_rule_version": "ts-translation-v4",
        "overrides": {
            "BDY-110": {
                "canonical_value": "sedan",
                "decision": "accepted",
                "display_value": None,
                "change_note": "Approved correction",
            },
            "MFE-TEST": {
                "kind": "manufacturer_entity",
                "source_field": "brand",
                "source_term": "AUDI A4 2.0TS QUATTRO",
                "canonical_name": "Audi",
                "entity_role": "vehicle_manufacturer",
                "base_behavior": "use_entity",
                "change_note": "Reviewed exact Brand entity",
            },
            "MFR-MODEL-VARIANT-FALLBACK": {
                "kind": "manufacturer_match_policy",
                "rule_id": "MFR-MODEL-VARIANT-FALLBACK",
                "allowed_fields": ["model", "variant"],
                "match_type": "whole_token_prefix",
                "change_note": "Approved missing Brand fallback",
            },
            "MFR-BRAND-PREFIX-FALLBACK": {
                "kind": "manufacturer_match_policy",
                "rule_id": "MFR-BRAND-PREFIX-FALLBACK",
                "match_type": "whole_token_prefix",
                "review_terms": ["ADRIA", "DETHLEFFS", "DAIMLER"],
                "change_note": "Approved reviewed Brand prefix fallback",
            },
        },
        "activated_at": datetime(2026, 8, 5, tzinfo=UTC),
    }

    result = subject.reprocess("meeting-sample")

    assert result.before.review_required == 5
    assert result.after.review_required == 2
    assert adapter.rule_set is not None
    assert adapter.rule_set.version == "ts-review-approved"
    assert adapter.rule_set.get("BDY-110").canonical_value == "sedan"
    assert adapter.manufacturer_entity_rules is not None
    assert (
        adapter.manufacturer_entity_rules["brand:AUDI A4 2 0TS QUATTRO"]["canonical_name"] == "Audi"
    )
    assert adapter.manufacturer_entity_rules["policy:MFR-MODEL-VARIANT-FALLBACK"][
        "allowed_fields"
    ] == ["model", "variant"]
    assert adapter.manufacturer_entity_rules["policy:MFR-BRAND-PREFIX-FALLBACK"][
        "review_terms"
    ] == ["ADRIA", "DETHLEFFS", "DAIMLER"]


def test_reprocess_is_blocked_while_unapproved_drafts_exist() -> None:
    subject, repository, adapter = service()
    repository.drafts["BDY-110"] = {"canonical_value": "sedan"}

    with pytest.raises(RuleReviewError, match="activate_or_discard_drafts"):
        subject.reprocess("meeting-sample")

    assert adapter.rule_set is None
