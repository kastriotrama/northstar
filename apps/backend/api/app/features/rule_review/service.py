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
    RuleCatalogEntry,
    RuleCatalogResponse,
    RuleDraftRequest,
    RuleListResponse,
    RuleView,
    TransformerStageView,
)
from ingestion.normalization_catalog import (
    CODE_RULE_PREFIX,
    RESOLUTION_RULE_PREFIX,
    EmbeddedRule,
    code_rules,
    transformer_stages,
)
from ingestion.normalization_repository import NormalizationSummary
from ingestion.normalization_rules import (
    PIPELINE_VERSION,
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
        entity_lifecycle = self._repository.fetch_manufacturer_entity_lifecycle()
        options = self._canonical_options()
        rules = [
            self._view(rule, active_overrides.get(rule.rule_id), drafts.get(rule.rule_id), options)
            for rule in self._base.rules
        ]
        entities = self._manufacturer_entities(active_overrides, entity_drafts, entity_lifecycle)
        return RuleListResponse(
            base_version=self._base.version,
            active_version=str(active["version"]) if active is not None else self._base.version,
            active_at=active["activated_at"] if active is not None else None,
            draft_count=len(drafts) + len(entity_drafts),
            rules=rules,
            manufacturer_entities=entities,
            review_reason_summary=self._repository.fetch_review_reason_summary(),
        )

    def list_rule_catalog(
        self,
        *,
        query: str = "",
        area: str | None = None,
        canonical_field: str | None = None,
        decision: str | None = None,
        origin: str | None = None,
        transformer_id: str | None = None,
        source: str | None = None,
        include_inventory: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> RuleCatalogResponse:
        """Paginated browsing of everything that transforms a record.

        Two kinds of rule share this list. Catalog rules come from the reviewed
        translation dictionaries and can be redrafted here. Code rules are the lookup
        tables, grammars and unit conversions compiled into the pipeline; they are
        listed so no transform is invisible, and they are not editable from this screen.

        Deliberately avoids the two aggregates that make ``list_rules`` expensive --
        ``fetch_review_reason_summary`` and ``fetch_discovered_manufacturer_entities``
        both scan the newest normalization batch, which measured at ~25s end to end for
        an 11.9MB response. This path touches only the draft and version tables plus the
        in-process rule set, and pages the result.
        """
        self._repository.ensure_schema()
        drafts = self._repository.fetch_drafts()
        active = self._repository.fetch_active_version()
        active_overrides = active["overrides"] if active is not None else {}

        entries: list[RuleCatalogEntry] = []
        for rule in self._base.rules:
            override = active_overrides.get(rule.rule_id)
            draft = drafts.get(rule.rule_id)
            active_value = (
                override.get("canonical_value")
                if override is not None
                else rule.canonical_value
            )
            active_decision = (
                override.get("decision") if override is not None else rule.decision
            )
            active_display = (
                override.get("display_value")
                if override is not None
                else rule.display_value
            )
            entries.append(
                RuleCatalogEntry(
                    rule_id=rule.rule_id,
                    area=rule.area,
                    source_fields=list(rule.source_fields),
                    source_terms=list(rule.source_terms),
                    canonical_field=rule.canonical_field,
                    base_canonical_value=rule.canonical_value,
                    effective_canonical_value=(
                        draft.get("canonical_value") if draft is not None else active_value
                    ),
                    effective_decision=str(
                        draft.get("decision") if draft is not None else active_decision
                    ),
                    effective_display_value=(
                        draft.get("display_value") if draft is not None else active_display
                    ),
                    vehicle_scopes=list(rule.vehicle_scopes),
                    manufacturers=list(rule.manufacturers),
                    has_draft=draft is not None,
                    change_note=str(draft["change_note"]) if draft is not None else None,
                )
            )

        catalog_total = len(entries)
        embedded = [self._code_entry(entry) for entry in code_rules()]
        entries.extend(embedded)
        resolution = [
            self._resolution_entry(rule)
            for rule in self._repository.fetch_resolution_rules()
        ]
        entries.extend(resolution)

        tecdoc_version, tecdoc_rows = self._repository.fetch_tecdoc_rules()
        tecdoc = [self._tecdoc_entry(rule) for rule in tecdoc_rows]
        tecdoc_inventory_total = sum(1 for entry in tecdoc if entry.inventory_only)
        # Inventory rows are the model names and manufacturers TecDoc holds. They
        # outnumber every other rule roughly forty to one and none of them can ever
        # gain a target, so including them by default would bury the rules a reviewer
        # can actually act on. They stay one filter click away.
        if not include_inventory:
            tecdoc = [entry for entry in tecdoc if not entry.inventory_only]
        entries.extend(tecdoc)

        tecdoc_resolution = [
            self._tecdoc_resolution_entry(rule)
            for rule in self._repository.fetch_tecdoc_resolution_rules()
        ]
        entries.extend(tecdoc_resolution)

        total = len(entries)
        term = query.strip().lower()
        if term:
            entries = [
                entry
                for entry in entries
                if term in entry.rule_id.lower()
                or term in entry.canonical_field.lower()
                or (entry.effective_canonical_value or "").lower().find(term) >= 0
                or (entry.notes or "").lower().find(term) >= 0
                or any(term in value.lower() for value in entry.source_terms)
                or any(term in value.lower() for value in entry.source_fields)
                or any(term in value.lower() for value in entry.manufacturers)
            ]
        if area:
            entries = [entry for entry in entries if entry.area == area]
        if canonical_field:
            entries = [
                entry for entry in entries if entry.canonical_field == canonical_field
            ]
        if decision:
            entries = [entry for entry in entries if entry.effective_decision == decision]
        if origin:
            entries = [entry for entry in entries if entry.origin == origin]
        if transformer_id:
            entries = [entry for entry in entries if entry.transformer_id == transformer_id]
        if source:
            entries = [entry for entry in entries if entry.source == source]

        filtered_total = len(entries)
        page = entries[offset : offset + limit]

        return RuleCatalogResponse(
            base_version=self._base.version,
            active_version=(
                str(active["version"]) if active is not None else self._base.version
            ),
            draft_count=len(drafts),
            total=total,
            filtered_total=filtered_total,
            catalog_total=catalog_total,
            code_total=len(embedded),
            resolution_total=len(resolution),
            tecdoc_total=len(tecdoc),
            tecdoc_inventory_total=tecdoc_inventory_total,
            tecdoc_resolution_total=len(tecdoc_resolution),
            tecdoc_rule_version=tecdoc_version,
            pipeline_version=PIPELINE_VERSION,
            limit=limit,
            offset=offset,
            areas=sorted(
                {rule.area for rule in self._base.rules}
                | {entry.area for entry in embedded}
                | {entry.area for entry in resolution}
                | {entry.area for entry in tecdoc}
                | {entry.area for entry in tecdoc_resolution}
            ),
            canonical_fields=sorted(
                {rule.canonical_field for rule in self._base.rules}
                | {entry.canonical_field for entry in embedded}
                | {entry.canonical_field for entry in resolution}
                | {entry.canonical_field for entry in tecdoc}
                | {entry.canonical_field for entry in tecdoc_resolution}
            ),
            canonical_options_by_field=self._canonical_options(),
            transformers=[
                TransformerStageView(
                    transformer_id=stage.transformer_id,
                    order=stage.order,
                    default_rule_id=stage.default_rule_id,
                    summary=stage.summary,
                    source_fields=list(stage.source_fields),
                    writes=list(stage.writes),
                    rule_areas=list(stage.rule_areas),
                    code_areas=list(stage.code_areas),
                    catalog_rule_count=stage.catalog_rule_count,
                    code_rule_count=stage.code_rule_count,
                    review_reasons=list(stage.review_reasons),
                )
                for stage in transformer_stages(self._base)
            ],
            items=page,
        )

    @staticmethod
    def _condition_text(condition: dict[str, Any]) -> str:
        """One predicate, read the way the reviewer wrote it in the filter."""

        values = [str(value) for value in (condition.get("values") or []) if value is not None]
        if not values and condition.get("value") is not None:
            values = [str(condition["value"])]
        layer = condition.get("layer")
        field = f"{condition.get('field')}" + (" (normalized)" if layer == "normalized" else "")
        return f"{field} {condition.get('operator', 'equals')} {', '.join(values) or '—'}"

    @staticmethod
    def _resolution_entry(rule: dict[str, Any]) -> RuleCatalogEntry:
        """Render one reviewer-authored projection rule as a catalog row.

        These do not run in the normalization pipeline. They are applied afterwards to
        the vehicle projection, which is why they carry no transformer and why their
        state is applied/saved/retired rather than accepted/proposed. They are listed
        because they change a field's value just as much as a dictionary rule does.
        """

        conditions = [
            condition for condition in rule["conditions"] if isinstance(condition, dict)
        ]
        fields = [
            str(condition["field"]) for condition in conditions if condition.get("field")
        ]
        terms = [RuleReviewService._condition_text(condition) for condition in conditions]
        counts = (
            f"Matched {rule['matched_rows']:,} rows, resolved {rule['resolved_rows']:,}."
        )
        note = f" {rule['note']}" if rule["note"] else ""
        return RuleCatalogEntry(
            rule_id=f"{RESOLUTION_RULE_PREFIX}:{rule['rule_id']}",
            area="resolution_rule",
            source_fields=sorted(dict.fromkeys(fields)) or [rule["source_field"]],
            source_terms=terms,
            canonical_field=rule["target_field"],
            base_canonical_value=rule["target_value"],
            effective_canonical_value=rule["target_value"],
            effective_decision=rule["status"],
            origin="resolution",
            editable=False,
            notes=f"Authored by {rule['author']} on the projection. {counts}{note}",
        )

    @staticmethod
    def _tecdoc_resolution_entry(rule: dict[str, Any]) -> RuleCatalogEntry:
        """Render one reviewer-authored TecDoc value ruling as a catalog row.

        TecDoc's sibling of `_resolution_entry`: not run by any pipeline, not part
        of a sealed `tecdoc_rules` version, effective the moment it is written.
        Sharing `origin="resolution"` with the TS rows is deliberate -- both are
        the same act, a human ruling on one value outside the reviewed batch
        process -- and `source="tecdoc"` is what tells the two apart.
        """

        if rule["decision"] == "excluded":
            decision = "excluded"
            note = f"Excluded: {rule['note']}" if rule["note"] else "Excluded."
        else:
            decision = "accepted"
            note = rule["note"] or ""
        key_table = f" TecDoc key table {rule['key_table']}." if rule["key_table"] else ""
        return RuleCatalogEntry(
            rule_id=f"TDRES:{rule['canonical_field']}:{rule['comparison_key']}",
            area="tecdoc_resolution_rule",
            source_fields=[rule["canonical_field"]],
            source_terms=[rule["source_term"]],
            canonical_field=rule["canonical_field"],
            base_canonical_value=rule["canonical_value"],
            effective_canonical_value=rule["canonical_value"],
            effective_decision=decision,
            origin="resolution",
            source="tecdoc",
            editable=False,
            notes=f"Ruled by {rule['reviewed_by']} on the live gap browser.{key_table} {note}".strip(),
        )

    @staticmethod
    def _tecdoc_entry(rule: dict[str, Any]) -> RuleCatalogEntry:
        """Render one generated TecDoc rule as a catalog row.

        TecDoc and Transportstyrelsen are peers: each is normalized into the same
        canonical vocabulary and written to the same graph, so their rules belong in
        one list. The shapes differ in what each source actually has -- a TecDoc rule
        carries a key table and a support count, a TS rule carries vehicle scopes --
        and the fields with no counterpart stay empty rather than being invented.

        Not editable here. These rules live in a sealed, immutable version; a
        correction is a new generation, not an edit, exactly as it is for the TS
        rule definitions.
        """

        reason = str(rule["evidence"].get("reason") or "")
        support = int(rule["support"])
        notes = [f"{support:,} rows in the scanned release carry this value."]
        if rule["key_table"]:
            notes.append(f"TecDoc key table {rule['key_table']}.")
        if reason == "unmapped":
            notes.append(
                "No reviewed mapping and no canonical token covers it, so it reaches "
                "the graph unnormalized until a reviewer rules on it."
            )
        elif reason == "mixed_descriptor":
            components = ", ".join(str(c) for c in rule["evidence"].get("components", []))
            notes.append(
                f"A mixed descriptor naming {components}. It states a capability, not "
                "the fuel this vehicle uses, so it resolves to nothing on purpose."
            )
        elif reason == "exact_canonical_spelling":
            notes.append(
                "The catalog value already spells a canonical token. Still a proposal: "
                "matching spellings is not evidence of matching meaning."
            )
        elif reason == "open_vocabulary":
            notes.append(
                "Listed for completeness only. This field has no closed vocabulary, so "
                "generation never assigns it a target."
            )

        return RuleCatalogEntry(
            rule_id=rule["rule_id"],
            area=rule["area"],
            source_fields=[f"{rule['entity_type']}.{rule['source_field']}"],
            source_terms=[rule["source_term"]],
            canonical_field=rule["canonical_field"],
            base_canonical_value=rule["canonical_value"],
            effective_canonical_value=rule["canonical_value"],
            effective_decision=rule["decision"],
            origin=(
                "reviewed_mapping"
                if rule["derivation"] == "reviewed_mapping"
                else "generated"
            ),
            source="tecdoc",
            support=support,
            inventory_only=reason == "open_vocabulary",
            editable=False,
            notes=" ".join(notes),
        )

    @staticmethod
    def _code_entry(rule: EmbeddedRule) -> RuleCatalogEntry:
        """Render one compiled-in transform in the same shape as a catalog rule."""

        return RuleCatalogEntry(
            rule_id=rule.rule_id,
            area=rule.area,
            source_fields=list(rule.source_fields),
            source_terms=list(rule.source_terms),
            canonical_field=rule.canonical_field,
            base_canonical_value=rule.canonical_value,
            effective_canonical_value=rule.canonical_value,
            effective_decision="accepted",
            effective_display_value=rule.display_value,
            origin="code",
            transformer_id=rule.transformer_id,
            editable=False,
            notes=rule.notes,
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
        entity_lifecycle = self._repository.fetch_manufacturer_entity_lifecycle()
        entity = next(
            (
                item
                for item in self._manufacturer_entities(active_overrides, drafts, entity_lifecycle)
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
            if override.get("kind") in {
                "manufacturer_match_policy",
                "special_vehicle_policy",
            }:
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
                "reviewed_examples": list(override.get("reviewed_examples") or []),
                "aliases": list(override.get("aliases") or []),
                "marketed_brand_overrides": dict(override.get("marketed_brand_overrides") or {}),
                "fallback_manufacturer": override.get("fallback_manufacturer"),
            }
        return rules

    def _manufacturer_entities(
        self,
        active_overrides: dict[str, Any],
        drafts: dict[str, dict[str, Any]],
        lifecycle: dict[str, dict[str, datetime]],
    ) -> list[ManufacturerEntityView]:
        sources: dict[str, dict[str, Any]] = {}
        reviewed_children = {
            (str(override.get("source_field")), normalized)
            for override in active_overrides.values()
            if override.get("kind") == "manufacturer_entity"
            for example in override.get("reviewed_examples", [])
            if (normalized := normalize_manufacturer_entity(example)) is not None
        }
        for item in manufacturer_entity_catalog():
            if (
                str(item["source_field"]),
                normalize_manufacturer_entity(item["source_term"]),
            ) in reviewed_children:
                continue
            entity_id = self._entity_id(str(item["source_field"]), str(item["source_term"]))
            sources[entity_id] = {**item, "occurrences": 0, "base_manufacturers": []}
        for item in self._repository.fetch_discovered_manufacturer_entities():
            normalized = normalize_manufacturer_entity(item["source_term"])
            if normalized is None:
                continue
            if (str(item["source_field"]), normalized) in reviewed_children:
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
            timestamps = lifecycle.get(entity_id, {})
            active_name = active.get("canonical_name") if active else base.get("canonical_name")
            active_role = (
                active.get("entity_role") if active else base.get("entity_role", "unknown")
            )
            active_behavior = (
                active.get("base_behavior")
                if active
                else base.get("base_behavior", "require_evidence_review")
            )
            reviewed_examples = list(
                active.get("reviewed_examples", []) if active else base.get("reviewed_examples", [])
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
                    created_at=timestamps.get("created_at")
                    or (draft.get("created_at") if draft else None),
                    updated_at=timestamps.get("updated_at")
                    or (draft.get("updated_at") if draft else None),
                    match_type=str(
                        active.get("match_type", "exact")
                        if active
                        else base.get("match_type", "exact")
                    ),
                    reviewed_examples=[str(value) for value in reviewed_examples],
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
        if rule_id.startswith(f"{CODE_RULE_PREFIX}:"):
            # Listed so the transform is visible, but it lives in the pipeline, not the
            # catalog. Overriding it here would claim an authority this screen lacks.
            raise RuleReviewError("rule_is_compiled_into_the_pipeline")
        if rule_id.startswith(f"{RESOLUTION_RULE_PREFIX}:"):
            # Authored against the projection and applied by its own job. It is edited
            # where it was written, not by drafting a translation override over it.
            raise RuleReviewError("rule_is_a_projection_resolution_rule")
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
