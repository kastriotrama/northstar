from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from api.app.features.rule_review.repository import RuleReviewRepository
from api.app.features.rule_review.reprocessing import RuleReprocessingAdapter
from api.app.features.rule_review.schemas import (
    BatchSummaryView,
    ReprocessResponse,
    RuleActivationResponse,
    RuleDraftRequest,
    RuleListResponse,
    RuleView,
)
from ingestion.normalization_repository import NormalizationSummary
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
        active = self._repository.fetch_active_version()
        active_overrides = active["overrides"] if active is not None else {}
        options = self._canonical_options()
        rules = [
            self._view(rule, active_overrides.get(rule.rule_id), drafts.get(rule.rule_id), options)
            for rule in self._base.rules
        ]
        return RuleListResponse(
            base_version=self._base.version,
            active_version=str(active["version"]) if active is not None else self._base.version,
            active_at=active["activated_at"] if active is not None else None,
            draft_count=len(drafts),
            rules=rules,
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
        if self._repository.fetch_drafts():
            raise RuleReviewError("activate_or_discard_drafts_before_reprocessing")
        active = self._repository.fetch_active_version()
        rule_set = self._effective_rule_set(active)
        suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        new_batch_id = f"{source_batch_id[:120]}-rules-{suffix}"
        try:
            before, after = self._reprocessing.reprocess(
                source_batch_id=source_batch_id.strip(),
                new_batch_id=new_batch_id,
                rule_set=rule_set,
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
