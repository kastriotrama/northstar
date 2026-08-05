from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from api.app.features.rule_review.repository import RuleReviewRepository
from api.app.features.rule_review.reprocessing import RuleReprocessingAdapter
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
from ingestion.normalization_repository import NormalizationSummary
from ingestion.normalization_rules import (
    ManufacturerEntityRules,
    manufacturer_entity_catalog,
    normalize_manufacturer_entity,
)
from ingestion.translation_dictionaries import (
    REVIEWED_RULE_SET_VERSION,
    TranslationRule,
    TranslationRuleSet,
    load_translation_rule_set,
)


class RuleReviewError(ValueError):
    pass


class RuleReviewService:
    def __init__(
        self,
        repository: RuleReviewRepository,
        reprocessing: RuleReprocessingAdapter,
    ) -> None:
        self._repository = repository
        self._reprocessing = reprocessing
        self._base = load_translation_rule_set(REVIEWED_RULE_SET_VERSION)

    def list_rules(self) -> RuleListResponse:
        self._repository.ensure_schema()
        drafts = self._repository.fetch_drafts()
        entity_drafts = self._repository.fetch_manufacturer_entity_drafts()
        active = self._repository.fetch_active_version()
        active_overrides = active["overrides"] if active is not None else {}
        options = self._canonical_options()
        rules = [
            self._view(rule, active_overrides.get(rule.rule_id), drafts.get(rule.rule_id), options)
            for rule in self._base.rules
        ]
        entities = self._manufacturer_entities(active_overrides, entity_drafts)
        return RuleListResponse(
            base_version=self._base.version,
            active_version=str(active["version"]) if active is not None else self._base.version,
            active_at=active["activated_at"] if active is not None else None,
            draft_count=len(drafts) + len(entity_drafts),
            rules=rules,
            manufacturer_entities=entities,
            review_reason_summary=self._repository.fetch_review_reason_summary(),
        )

    def save_draft(self, rule_id: str, request: RuleDraftRequest) -> RuleListResponse:
        self._repository.ensure_schema()
        rule = self._get_rule(rule_id)
        allowed = self._canonical_options()[rule.canonical_field]
        if request.canonical_value is not None and request.canonical_value not in allowed:
            raise RuleReviewError("canonical_value_not_in_reviewed_vocabulary")
        self._repository.save_draft(
            rule_id=rule.rule_id,
            canonical_value=request.canonical_value,
            decision=request.decision,
            display_value=request.display_value,
            change_note=request.change_note,
        )
        return self.list_rules()

    def discard_draft(self, rule_id: str) -> RuleListResponse:
        self._repository.ensure_schema()
        self._get_rule(rule_id)
        self._repository.delete_draft(rule_id)
        return self.list_rules()

    def save_manufacturer_entity_draft(
        self,
        entity_id: str,
        request: ManufacturerEntityDraftRequest,
    ) -> RuleListResponse:
        self._repository.ensure_schema()
        active = self._repository.fetch_active_version()
        active_overrides = active["overrides"] if active is not None else {}
        drafts = self._repository.fetch_manufacturer_entity_drafts()
        entity = next(
            (
                item
                for item in self._manufacturer_entities(active_overrides, drafts)
                if item.entity_id == entity_id
            ),
            None,
        )
        if entity is None:
            raise RuleReviewError("manufacturer_entity_not_found")
        expected_behavior = {
            "vehicle_manufacturer": "use_entity",
            "bodybuilder_converter": "use_base_manufacturer",
            "corporate_group": "require_evidence_review",
            "unknown": "require_evidence_review",
        }[request.entity_role]
        if request.base_behavior != expected_behavior:
            raise RuleReviewError("manufacturer_entity_role_behavior_conflict")
        if request.entity_role in {"vehicle_manufacturer", "bodybuilder_converter"} and not (
            request.canonical_name and request.canonical_name.strip()
        ):
            raise RuleReviewError("manufacturer_entity_canonical_name_required")
        self._repository.save_manufacturer_entity_draft(
            entity_id=entity.entity_id,
            source_field=entity.source_field,
            source_term=entity.source_term,
            canonical_name=(request.canonical_name.strip() if request.canonical_name else None),
            entity_role=request.entity_role,
            base_behavior=request.base_behavior,
            change_note=request.change_note,
        )
        return self.list_rules()

    def discard_manufacturer_entity_draft(self, entity_id: str) -> RuleListResponse:
        self._repository.ensure_schema()
        self._repository.delete_manufacturer_entity_draft(entity_id)
        return self.list_rules()

    def activate(self, note: str) -> RuleActivationResponse:
        self._repository.ensure_schema()
        active = self._repository.fetch_active_version()
        inherited_overrides = dict(active["overrides"]) if active is not None else {}
        version = datetime.now(UTC).strftime("ts-review-%Y%m%dT%H%M%S%fZ")
        try:
            count, activated_at = self._repository.activate_drafts(
                version=version,
                base_rule_version=self._base.version,
                inherited_overrides=inherited_overrides,
                note=note,
            )
        except ValueError as error:
            raise RuleReviewError(str(error)) from error
        return RuleActivationResponse(
            version=version,
            activated_rules=count,
            activated_at=activated_at,
        )

    def reprocess(self, source_batch_id: str) -> ReprocessResponse:
        self._repository.ensure_schema()
        if self._repository.fetch_drafts() or self._repository.fetch_manufacturer_entity_drafts():
            raise RuleReviewError("activate_or_discard_drafts_before_reprocessing")
        active = self._repository.fetch_active_version()
        rule_set = self._effective_rule_set(active)
        manufacturer_entity_rules = self._effective_manufacturer_entity_rules(active)
        suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        new_batch_id = f"{source_batch_id[:120]}-rules-{suffix}"
        try:
            before, after = self._reprocessing.reprocess(
                source_batch_id=source_batch_id.strip(),
                new_batch_id=new_batch_id,
                rule_set=rule_set,
                manufacturer_entity_rules=manufacturer_entity_rules,
            )
        except ValueError as error:
            raise RuleReviewError(str(error)) from error
        return ReprocessResponse(
            source_batch_id=source_batch_id,
            new_batch_id=new_batch_id,
            rule_version=rule_set.version,
            before=self._summary(before),
            after=self._summary(after),
        )

    def _effective_rule_set(self, active: dict[str, Any] | None) -> TranslationRuleSet:
        if active is None:
            return self._base
        overrides = active["overrides"]
        rules = tuple(
            self._apply_override(rule, overrides.get(rule.rule_id)) for rule in self._base.rules
        )
        return TranslationRuleSet(version=str(active["version"]), rules=rules)

    def _effective_manufacturer_entity_rules(
        self, active: dict[str, Any] | None
    ) -> ManufacturerEntityRules:
        if active is None:
            return {}
        rules: dict[str, dict[str, Any]] = {}
        for entity_id, override in active["overrides"].items():
            if override.get("kind") == "manufacturer_match_policy":
                rules[f"policy:{entity_id}"] = dict(override)
                continue
            if override.get("kind") != "manufacturer_entity":
                continue
            source_term = normalize_manufacturer_entity(override.get("source_term"))
            source_field = override.get("source_field")
            if source_term is None or not isinstance(source_field, str):
                continue
            rules[f"{source_field}:{source_term}"] = {
                "entity_id": str(entity_id),
                "kind": "manufacturer_entity",
                "source_field": source_field,
                "source_term": source_term,
                "canonical_name": override.get("canonical_name"),
                "entity_role": override.get("entity_role"),
                "base_behavior": override.get("base_behavior"),
                "match_type": override.get("match_type"),
            }
        return rules

    def _manufacturer_entities(
        self,
        active_overrides: dict[str, Any],
        drafts: dict[str, dict[str, Any]],
    ) -> list[ManufacturerEntityView]:
        sources: dict[str, dict[str, Any]] = {}
        for item in manufacturer_entity_catalog():
            entity_id = self._entity_id(str(item["source_field"]), str(item["source_term"]))
            sources[entity_id] = {**item, "occurrences": 0, "base_manufacturers": []}
        for item in self._repository.fetch_discovered_manufacturer_entities():
            normalized = normalize_manufacturer_entity(item["source_term"])
            if normalized is None:
                continue
            entity_id = self._entity_id(str(item["source_field"]), normalized)
            existing = sources.get(entity_id, {})
            sources[entity_id] = {
                "source_field": item["source_field"],
                "source_term": normalized,
                "canonical_name": existing.get("canonical_name"),
                "entity_role": existing.get("entity_role", "unknown"),
                "base_behavior": existing.get("base_behavior", "require_evidence_review"),
                "occurrences": item["occurrences"],
                "base_manufacturers": item["base_manufacturers"],
                "is_discovered": True,
            }
        for entity_id, override in active_overrides.items():
            if override.get("kind") == "manufacturer_entity" and entity_id not in sources:
                sources[entity_id] = {**override, "occurrences": 0, "base_manufacturers": []}
        views: list[ManufacturerEntityView] = []
        for entity_id, base in sources.items():
            active = (
                active_overrides.get(entity_id)
                if active_overrides.get(entity_id, {}).get("kind") == "manufacturer_entity"
                else None
            )
            draft = drafts.get(entity_id)
            active_name = active.get("canonical_name") if active else base.get("canonical_name")
            active_role = (
                active.get("entity_role") if active else base.get("entity_role", "unknown")
            )
            active_behavior = (
                active.get("base_behavior")
                if active
                else base.get("base_behavior", "require_evidence_review")
            )
            views.append(
                ManufacturerEntityView(
                    entity_id=entity_id,
                    source_field=str(base["source_field"]),
                    source_term=str(base["source_term"]),
                    active_canonical_name=active_name,
                    effective_canonical_name=(
                        draft.get("canonical_name") if draft else active_name
                    ),
                    active_entity_role=str(active_role),
                    effective_entity_role=str(draft.get("entity_role") if draft else active_role),
                    active_base_behavior=str(active_behavior),
                    effective_base_behavior=str(
                        draft.get("base_behavior") if draft else active_behavior
                    ),
                    occurrences=int(base.get("occurrences", 0)),
                    base_manufacturers=list(base.get("base_manufacturers", [])),
                    has_draft=draft is not None,
                    is_discovered=bool(base.get("is_discovered", False)),
                    change_note=str(draft["change_note"]) if draft else None,
                )
            )
        return sorted(
            views,
            key=lambda item: (-item.occurrences, item.source_term, item.source_field),
        )

    @staticmethod
    def _entity_id(source_field: str, source_term: str) -> str:
        digest = sha256(f"{source_field}:{source_term}".encode()).hexdigest()[:14].upper()
        return f"MFE-{digest}"

    @staticmethod
    def _apply_override(rule: TranslationRule, override: dict[str, Any] | None) -> TranslationRule:
        if override is None:
            return rule
        return replace(
            rule,
            canonical_value=override.get("canonical_value"),
            decision=override["decision"],
            display_value=override.get("display_value"),
        )

    def _canonical_options(self) -> dict[str, list[str]]:
        fields = {rule.canonical_field for rule in self._base.rules}
        return {
            field: sorted(
                {
                    rule.canonical_value
                    for rule in self._base.rules
                    if rule.canonical_field == field and rule.canonical_value is not None
                }
            )
            for field in fields
        }

    def _get_rule(self, rule_id: str) -> TranslationRule:
        try:
            return self._base.get(rule_id)
        except KeyError as error:
            raise RuleReviewError("translation_rule_not_found") from error

    @staticmethod
    def _view(
        rule: TranslationRule,
        active: dict[str, Any] | None,
        draft: dict[str, Any] | None,
        options: dict[str, list[str]],
    ) -> RuleView:
        active_value = active.get("canonical_value") if active is not None else rule.canonical_value
        active_decision = active.get("decision") if active is not None else rule.decision
        active_display = active.get("display_value") if active is not None else rule.display_value
        return RuleView(
            rule_id=rule.rule_id,
            area=rule.area,
            source_fields=list(rule.source_fields),
            source_terms=list(rule.source_terms),
            canonical_field=rule.canonical_field,
            base_canonical_value=rule.canonical_value,
            active_canonical_value=active_value,
            effective_canonical_value=(
                draft.get("canonical_value") if draft is not None else active_value
            ),
            canonical_options=options[rule.canonical_field],
            active_decision=str(active_decision),
            effective_decision=str(draft.get("decision") if draft is not None else active_decision),
            active_display_value=active_display,
            effective_display_value=(
                draft.get("display_value") if draft is not None else active_display
            ),
            vehicle_scopes=list(rule.vehicle_scopes),
            manufacturers=list(rule.manufacturers),
            has_draft=draft is not None,
            change_note=str(draft["change_note"]) if draft is not None else None,
        )

    @staticmethod
    def _summary(summary: NormalizationSummary) -> BatchSummaryView:
        return BatchSummaryView(
            total=summary.processed,
            resolved=summary.resolved,
            provisional=summary.provisional,
            review_required=summary.review_required,
            failed=summary.failed,
        )
