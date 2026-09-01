"""Real PostgreSQL/Neo4j adapters for the write-free TS-to-TecDoc audit."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from neo4j import Driver
from psycopg import Connection
from psycopg.rows import dict_row

from ingestion.confidence_routing import ConfidenceRouter
from ingestion.context_comparison import SOURCE_CONTEXT_FIELDS, ContextComparisonPolicy
from ingestion.fuzzy_matching import (
    FuzzyMatchConfig,
    FuzzyVehicleMatcher,
    ManufacturerCandidateIndex,
    VehicleCandidate,
    VehicleMatchQuery,
)
from ingestion.match_run_service import MatchSourceRecord, MatchTerminal
from ingestion.tecdoc.engine_fingerprint_proposals import ReviewedEngineFingerprintIndex
from ingestion.tecdoc.manufacturer_mapping import TecDocManufacturerIndex
from ingestion.tecdoc.model_aliases import (
    ReviewedModelAliasIndex,
    prefer_non_degrading_alias_decision,
)
from ingestion.tecdoc.reference_data import (
    canonical_bodywork_by_kt086,
    canonical_drive_by_kt082,
)
from ingestion.tecdoc.source_model_rules import ReviewedSourceModelPolicy
from ingestion.vocabulary_alignment import FuelAlignment, align_catalog_fuels, canonical_fuels

_CATALOG_QUERY = """
MATCH (alias:Alias {source_system: 'tecdoc', alias_type: 'k_type'})-[:REFERS_TO]->
      (variant:VehicleVariant)-[:VARIANT_OF]->(family:ModelFamily)-[:MADE_BY]->
      (manufacturer:Manufacturer)
OPTIONAL MATCH (variant)-[:USES_ENGINE]->(engine:Engine)
OPTIONAL MATCH (variant)-[:HAS_BODY]->(body:BodyType)
RETURN alias.alias_text AS ktype,
       manufacturer.canonical_name AS manufacturer,
       family.canonical_name AS model,
       variant.year_from AS year_from,
       variant.year_to AS year_to,
       variant.fuel_type AS fuel_type,
       coalesce(variant.displacement_cc, engine.displacement_cc) AS displacement_cc,
       variant.power_kw AS power_kw,
       variant.drive_type AS drive_type,
       collect(DISTINCT engine.engine_code) AS engine_codes,
       collect(DISTINCT engine.fuel_components) AS engine_fuel_components,
       collect(DISTINCT body.canonical_name) AS bodyworks
ORDER BY ktype
"""

_POSTGRES_CATALOG_QUERY = """
SELECT ka.attributes->>'alias_text' AS ktype,
       manufacturer.attributes->>'canonical_name' AS manufacturer,
       family.attributes->>'canonical_name' AS model,
       variant.attributes->>'source_name' AS source_name,
       variant.attributes->>'year_from' AS year_from,
       variant.attributes->>'year_to' AS year_to,
       coalesce(variant.attributes->>'vehicle_fuel_type',
                variant.attributes->>'fuel_type') AS fuel_type,
       engine.attributes->>'engine_code' AS engine_code,
       variant.attributes->>'displacement_cc' AS displacement_cc,
       variant.attributes->>'power_kw' AS power_kw,
       variant.attributes->>'drive_type' AS drive_type,
       variant.attributes->>'tecdoc_drive_type_code' AS drive_type_code,
       bodywork.attributes->>'canonical_name' AS bodywork,
       variant.attributes->>'tecdoc_body_type_code' AS body_type_code,
       variant.attributes->>'promotion_status' AS promotion_status
FROM core.tecdoc_canonical_candidates variant
JOIN core.tecdoc_canonical_candidates ka
  ON ka.batch_id = variant.batch_id AND ka.entity_type = 'alias'
 AND ka.attributes->>'alias_type' = 'k_type'
 AND ka.attributes->>'target_source_key' = variant.source_key
JOIN core.tecdoc_canonical_candidates family
  ON family.batch_id = variant.batch_id AND family.entity_type = 'model_family'
 AND family.source_key = variant.attributes->>'model_family_source_key'
JOIN core.tecdoc_canonical_candidates manufacturer
  ON manufacturer.batch_id = variant.batch_id AND manufacturer.entity_type = 'manufacturer'
 AND manufacturer.source_key = variant.attributes->>'manufacturer_source_key'
LEFT JOIN core.tecdoc_canonical_candidates engine
  ON engine.batch_id = variant.batch_id AND engine.entity_type = 'engine'
 AND engine.source_key = variant.attributes->>'engine_source_key'
LEFT JOIN core.tecdoc_canonical_candidates bodywork
  ON bodywork.batch_id = variant.batch_id AND bodywork.entity_type = 'bodywork'
 AND bodywork.source_key = variant.attributes->>'bodywork_source_key'
WHERE variant.batch_id = %s AND variant.entity_type = 'vehicle_variant'
ORDER BY ka.attributes->>'alias_text'
"""


@dataclass(frozen=True)
class MatchEvaluation:
    """One terminal route plus sanitized, aggregate-safe reason codes."""

    terminal: MatchTerminal
    reason_codes: tuple[str, ...]
    top_candidate_reference: str | None = None
    candidate_matches: tuple[dict[str, Any], ...] = ()
    decision_trace: tuple[dict[str, Any], ...] = ()
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.reason_codes or any(not reason.strip() for reason in self.reason_codes):
            raise ValueError("match evaluation requires non-empty reason codes")


_CHASSIS_SUFFIX = re.compile(r"\s*\([^()]*\)")
# "II".."IX" and "I" are only ever generation markers in TecDoc model names.
_GENERATION_NUMERALS = frozenset({"I", "II", "III", "IV", "VI", "VII", "VIII", "IX"})
# A bare "V" or "X" can be the model name itself (Tesla "MODEL X"), so those are
# treated as decoration only when the rest of the name still carries a code.
_AMBIGUOUS_NUMERALS = frozenset({"V", "X"})


def tecdoc_model_aliases(model: str) -> tuple[str, ...]:
    """Return marketing-name aliases for one decorated TecDoc model name.

    TecDoc decorates its model names with a chassis code and a generation
    numeral -- "V60 I (155)", "QASHQAI I (J10, NJ10)" -- while Transportstyrelsen
    stores the bare marketing name ("V60", "Qashqai"). Scored against the
    decorated text those rows fall below the candidate threshold and are
    reviewed without a candidate, even though the model is in the catalog.

    Only decoration is removed: the chassis code in parentheses and standalone
    generation numerals. Body and trim words such as "Cross Country" or "SUV"
    are meaningful model distinctions and are deliberately preserved.

    A numeral is only decoration when the rest of the name still carries a
    model code, so "V60 I" yields "V60" while Tesla's "MODEL X" is left intact
    -- stripping its "X" would collide with Model S, 3 and Y.
    """
    without_chassis = _CHASSIS_SUFFIX.sub("", model).strip()
    aliases = {without_chassis}
    words = without_chassis.split()
    carries_code = any(character.isdigit() for word in words for character in word)
    decoration = _GENERATION_NUMERALS | (_AMBIGUOUS_NUMERALS if carries_code else frozenset())
    tokens = [word for word in words if word.upper() not in decoration]
    if tokens:
        aliases.add(" ".join(tokens))
    return tuple(sorted(alias for alias in aliases if alias and alias != model))


def postgres_tecdoc_model_aliases(model: str, source_name: object) -> tuple[str, ...]:
    """Keep PostgreSQL candidates aligned with the graph catalog's safe aliases."""

    source_alias = _text(source_name)
    return tuple(
        dict.fromkeys(
            (
                *tecdoc_model_aliases(model),
                *((source_alias,) if source_alias is not None and source_alias != model else ()),
            )
        )
    )


def reviewed_candidate_context(
    canonical_value: object,
    tecdoc_code: object,
    reviewed_by_code: Mapping[str, str],
) -> str | None:
    """Recover only reviewed code mappings when candidate-only links are absent."""

    return _text(canonical_value) or reviewed_by_code.get(str(tecdoc_code or ""))


def load_ktype_catalog(driver: Driver) -> tuple[VehicleCandidate, ...]:
    """Load one deterministic candidate per immutable TecDoc KType alias."""

    with driver.session() as session:
        rows = tuple(session.run(_CATALOG_QUERY))
    candidates = tuple(
        VehicleCandidate(
            candidate_reference=str(row["ktype"]),
            candidate_type="TecDocKType",
            manufacturer=str(row["manufacturer"]),
            model=str(row["model"]),
            model_aliases=tecdoc_model_aliases(str(row["model"])),
            year_from=_integer(row["year_from"]),
            year_to=_integer(row["year_to"]),
            fuels=frozenset({str(row["fuel_type"])}) if row["fuel_type"] else frozenset(),
            fuel_components=_flatten_strings(row["engine_fuel_components"]),
            engine_codes=frozenset(str(value) for value in row["engine_codes"] if value),
            displacement_cc=_integer(row["displacement_cc"]),
            power_kw=_integer(row["power_kw"]),
            drive_type=_text(row["drive_type"]),
            bodyworks=frozenset(str(value) for value in row["bodyworks"] if value),
        )
        for row in rows
    )
    if not candidates:
        raise ValueError("TecDoc KType catalog is empty")
    return candidates


def load_postgres_ktype_catalog(
    connection: Connection,
    *,
    batch_id: str,
) -> tuple[VehicleCandidate, ...]:
    """Load graph-safe and explicitly candidate-only KTypes from one pinned batch."""

    if not batch_id.strip():
        raise ValueError("candidate catalog batch_id must not be empty")
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(_POSTGRES_CATALOG_QUERY, (batch_id,))
        rows = tuple(cursor.fetchall())
        cursor.execute(
            "SELECT from_source_key, attributes->>'engine_code' AS engine_code, "
            "attributes->'engine_fuel_evidence'->'components' AS fuel_components "
            "FROM core.tecdoc_candidate_relationships "
            "WHERE batch_id=%s AND relationship_type='USES_ENGINE' "
            "AND status='candidate'",
            (batch_id,),
        )
        relationship_engine_codes: dict[str, set[str]] = {}
        relationship_fuel_components: dict[str, set[str]] = {}
        for row in cursor.fetchall():
            ktype = str(row["from_source_key"]).removeprefix("variant:")
            if engine_code := _text(row["engine_code"]):
                relationship_engine_codes.setdefault(ktype, set()).add(engine_code)
            relationship_fuel_components.setdefault(ktype, set()).update(
                _flatten_strings(row["fuel_components"])
            )
    drive_by_code = canonical_drive_by_kt082()
    bodywork_by_code = canonical_bodywork_by_kt086()
    candidates = tuple(
        VehicleCandidate(
            candidate_reference=str(row["ktype"]),
            candidate_type=(
                "TecDocKTypeCandidateOnly"
                if row["promotion_status"] == "candidate_only"
                else "TecDocKType"
            ),
            manufacturer=str(row["manufacturer"]),
            model=str(row["model"]),
            model_aliases=postgres_tecdoc_model_aliases(
                str(row["model"]), row["source_name"]
            ),
            year_from=_integer(row["year_from"]),
            year_to=_integer(row["year_to"]),
            fuels=frozenset({str(row["fuel_type"])}) if row["fuel_type"] else frozenset(),
            fuel_components=frozenset(
                relationship_fuel_components.get(str(row["ktype"]), set())
            ),
            engine_codes=(
                frozenset(relationship_engine_codes.get(str(row["ktype"]), set()))
                | (
                    frozenset({str(row["engine_code"])})
                    if row["engine_code"]
                    else frozenset()
                )
            ),
            displacement_cc=_integer(row["displacement_cc"]),
            power_kw=_integer(row["power_kw"]),
            drive_type=reviewed_candidate_context(
                row["drive_type"], row["drive_type_code"], drive_by_code
            ),
            bodyworks=(
                frozenset({bodywork})
                if (
                    bodywork := reviewed_candidate_context(
                        row["bodywork"], row["body_type_code"], bodywork_by_code
                    )
                )
                else frozenset()
            ),
        )
        for row in rows
    )
    if not candidates:
        raise ValueError(f"TecDoc candidate catalog batch is empty: {batch_id}")
    return candidates


def fetch_normalized_ts_page(
    connection: Connection,
    *,
    source_batch_prefix: str,
    normalization_rule_version: str,
    after_source_record_id: int,
    limit: int,
) -> tuple[MatchSourceRecord, ...]:
    """Read a globally ordered, version-pinned page without exposing identifiers."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT source_record_id, status, normalized_payload "
            "FROM core.normalization_results "
            "WHERE source_batch_id LIKE %s AND rule_version = %s "
            "AND source_record_id > %s ORDER BY source_record_id LIMIT %s",
            (
                f"{source_batch_prefix}%",
                normalization_rule_version,
                after_source_record_id,
                limit,
            ),
        )
        rows = cursor.fetchall()
    return tuple(
        MatchSourceRecord(
            int(row[0]),
            {"normalization_status": str(row[1]), **dict(row[2])},
        )
        for row in rows
    )


@dataclass(frozen=True)
class ResolvedMatchQuery:
    """Everything the matcher keys on, resolved from one normalized row.

    Exposed so that consumers which need to know whether two rows evaluate
    identically -- signature chunking, for one -- can ask the evaluator instead
    of reimplementing its key. Two definitions of "equivalent rows" drift the
    moment either side gains a derivation the other cannot see.
    """

    key: tuple[object, ...]
    scope_manufacturer: str
    model_values: tuple[str, ...]
    year: int | None
    fuels: frozenset[str]
    engine_code: str | None
    displacement_cc: int | None
    power_kw: int | None
    drive_type: str | None
    bodywork: str | None
    recovery_reason: str | None
    source_context: tuple[tuple[str, str], ...]
    source_model_resolution: Any


class TecDocDryRunEvaluator:
    """Classify normalized TS rows using the existing matcher and confidence router."""

    def __init__(
        self,
        candidates: Sequence[VehicleCandidate],
        manufacturer_rules: Mapping[str, Mapping[str, Any]] | None = None,
        reviewed_model_aliases: ReviewedModelAliasIndex | None = None,
        reviewed_engine_fingerprints: ReviewedEngineFingerprintIndex | None = None,
        *,
        fuel_alignment: FuelAlignment | None = None,
        context_policy: ContextComparisonPolicy | None = None,
        source_model_policy: ReviewedSourceModelPolicy | None = None,
    ) -> None:
        config = FuzzyMatchConfig()
        self._fuel_alignment = fuel_alignment
        self._context_policy = context_policy or ContextComparisonPolicy()
        self._source_model_policy = source_model_policy or ReviewedSourceModelPolicy()
        self._source_model_policy.validate_catalog(candidates)
        if fuel_alignment is not None:
            candidates = align_catalog_fuels(candidates, fuel_alignment.tecdoc_equivalences)
        compatible_pairs = fuel_alignment.compatible_pairs if fuel_alignment else frozenset()
        self._index = ManufacturerCandidateIndex(candidates)
        self._matcher = FuzzyVehicleMatcher(
            self._index, config, fuel_compatible_pairs=compatible_pairs,
            context_policy=self._context_policy,
        )
        expanded_candidates = (
            tuple(reviewed_model_aliases.expand(candidate) for candidate in candidates)
            if reviewed_model_aliases is not None
            else tuple(candidates)
        )
        self._alias_index = ManufacturerCandidateIndex(expanded_candidates)
        self._alias_matcher = FuzzyVehicleMatcher(
            self._alias_index, config, fuel_compatible_pairs=compatible_pairs,
            context_policy=self._context_policy,
        )
        self._candidate_only_references = frozenset(
            candidate.candidate_reference
            for candidate in candidates
            if candidate.candidate_type == "TecDocKTypeCandidateOnly"
        )
        self._manufacturer_scope_threshold = config.manufacturer_scope_threshold
        self._engine_fingerprints = reviewed_engine_fingerprints or ReviewedEngineFingerprintIndex()
        self._router = ConfidenceRouter()
        # TS spells manufacturers differently from TecDoc ("CITROEN" vs
        # "CITROËN", "LYNK&CO" vs "LYNK & CO"). Without this accent- and
        # punctuation-tolerant mapping those rows resolve to global scope and
        # are reviewed without ever being scored. Reviewed manufacturer rules
        # additionally bridge alias spellings onto their catalog target.
        self._manufacturer_scope = TecDocManufacturerIndex(
            sorted({candidate.manufacturer for candidate in candidates}),
            manufacturer_rules or {},
        )
        self._cache: dict[tuple[object, ...], MatchEvaluation] = {}

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    def __call__(self, record: MatchSourceRecord) -> MatchTerminal:
        return self.evaluate(record).terminal

    def _resolve_query(
        self, record: MatchSourceRecord
    ) -> MatchEvaluation | ResolvedMatchQuery:
        """Resolve the matcher inputs, or terminate the row before matching."""

        payload = record.payload
        status = str(payload.get("normalization_status") or "")
        if status == "failed":
            return MatchEvaluation("failed", ("normalization_failed",))
        if status == "review_required":
            review_reasons = payload.get("review_reasons")
            reasons = (
                tuple(f"normalization:{reason!s}" for reason in review_reasons)
                if isinstance(review_reasons, list) and review_reasons
                else ("normalization_review_required",)
            )
            return MatchEvaluation("normalization_review", reasons)
        normalized = _mapping(payload.get("normalized"))
        if normalized.get("record_route") in {
            "exclude_from_passenger_car_dataset",
            "quarantine_test_record",
        }:
            route = str(normalized["record_route"])
            return MatchEvaluation("policy_excluded", (f"policy:{route}",))
        candidates = _mapping(payload.get("candidates"))
        manufacturer = normalized.get("manufacturer") or candidates.get("manufacturer")
        if not manufacturer:
            return MatchEvaluation("unmatched", ("manufacturer_missing",))
        recovery_reason: str | None = None
        source_evidence = _mapping(payload.get("source_evidence"))
        model_values = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in (
                    normalized.get("model_family"),
                    candidates.get("model_family"),
                    source_evidence.get("model"),
                )
                if str(value or "").strip()
            )
        )
        model_evidence = {
            field_name: str(value)
            for field_name in (
                # The registry's own model text is consulted first. Normalization
                # only sets model_family when a reviewed rule covers the term, so
                # a car registered as "DUSTER" or "GRAND C-MAX" reaches matching
                # with the model named plainly in the row and nothing reading it.
                "model",
                "brand",
                "variant",
                "version",
                "model_no",
                "type_text",
                "eeg_type_approval",
            )
            if (value := source_evidence.get(field_name))
        }
        # Map TS manufacturer spelling onto its TecDoc catalog name before
        # scoping. Only an unambiguous resolution is used; conflicts and
        # unmatched evidence keep the original text and the existing behaviour.
        # This must precede model recovery: recovery looks up the catalog's
        # models by manufacturer, so running it on the unbridged spelling finds
        # no labels at all for any manufacturer whose registry name differs from
        # the catalog's, and silently recovers nothing.
        scope_decision = self._manufacturer_scope.resolve(
            manufacturer=manufacturer,
            brand=source_evidence.get("brand"),
        )
        scope_manufacturer = (
            scope_decision.manufacturer
            if scope_decision.status == "resolved" and scope_decision.manufacturer
            else str(manufacturer)
        )
        explicit_model = source_evidence.get("model")
        if explicit_model:
            explicit = self._alias_index.recover_model_from_evidence(
                scope_manufacturer, {"model": str(explicit_model)}
            )
            brand_model = self._alias_index.recover_model_from_evidence(
                scope_manufacturer, {"brand": str(source_evidence.get("brand") or "")}
            )
            if explicit is not None and brand_model is not None and explicit[0] != brand_model[0]:
                return MatchEvaluation("review_required", ("model_source_evidence_conflict",))
            if explicit is not None:
                # Catalog-recognized raw evidence must not compete with a
                # broader normalization on whichever happens to score highest.
                model_values = (explicit[0], str(explicit_model))
                recovery_reason = "model_recovered_from_model"
        if not model_values and model_evidence:
            recovered = self._alias_index.recover_model_from_evidence(
                scope_manufacturer, model_evidence
            )
            if recovered is not None:
                recovered_model, source_field = recovered
                model_values = (recovered_model,)
                recovery_reason = f"model_recovered_from_{source_field}"
        if not model_values:
            return MatchEvaluation("review_required", ("model_evidence_missing",))
        source_model_resolution = self._source_model_policy.resolve(
            manufacturer=scope_manufacturer,
            source_model=str(explicit_model or "") if scope_decision.status == "resolved" else "",
            source_evidence=source_evidence,
        )
        if source_model_resolution.conflict:
            return MatchEvaluation("review_required", ("source_model_rules_conflict",))
        if source_model_resolution.target_model is not None:
            # A reviewed source assertion supplies the family query, not a
            # candidate ID. Every catalog KType still competes through the
            # unchanged matcher; never fall back to a broader source model.
            model_values = (source_model_resolution.target_model,)
        # Prefer the comparison vocabulary: it carries the combined hybrid
        # token TecDoc uses, which the raw carrier list cannot express. Older
        # normalization payloads predate the field and fall back to carriers.
        energy = normalized.get("fuel_match_tokens") or normalized.get("energy_sources")
        fuels = (
            frozenset(str(value) for value in energy)
            if isinstance(energy, list | tuple | set | frozenset)
            else frozenset()
        )
        if self._fuel_alignment is not None:
            fuels = canonical_fuels(fuels, self._fuel_alignment.ts_equivalences)
        year = _integer(normalized.get("production_year"))
        engine_code = _text(normalized.get("engine_code"))
        displacement_cc = _integer(normalized.get("displacement_cc"))
        power_kw = _integer(normalized.get("power_kw"))
        drive_type = _text(normalized.get("drive_type"))
        bodywork = _text(normalized.get("bodywork_form"))
        source_context = tuple(sorted(
            (key, str(value)) for key in SOURCE_CONTEXT_FIELDS
            if (value := source_evidence.get(key)) is not None
        )) if self._context_policy.rules else ()
        if engine_code is None:
            engine_code = self._engine_fingerprints.resolve(
                manufacturer=scope_manufacturer,
                type_approval=source_evidence.get("eeg_type_approval"),
                variant=source_evidence.get("variant"),
                version=source_evidence.get("version"),
            )
        cache_key = (
            scope_manufacturer,
            "\x1f".join(model_values),
            year,
            fuels,
            engine_code,
            displacement_cc,
            power_kw,
            recovery_reason,
            drive_type,
            bodywork,
            source_context,
            source_model_resolution.rule_ids,
        )
        return ResolvedMatchQuery(
            key=cache_key,
            scope_manufacturer=scope_manufacturer,
            model_values=tuple(model_values),
            year=year,
            fuels=fuels,
            engine_code=engine_code,
            displacement_cc=displacement_cc,
            power_kw=power_kw,
            drive_type=drive_type,
            bodywork=bodywork,
            recovery_reason=recovery_reason,
            source_context=source_context,
            source_model_resolution=source_model_resolution,
        )

    def evaluation_key(self, record: MatchSourceRecord) -> tuple[object, ...] | None:
        """The key two rows must share to be guaranteed the same evaluation.

        None when the row terminates before matching -- a normalization failure,
        a policy route, or missing model evidence -- because such rows are not
        grouped by anything the matcher computed.
        """

        resolved = self._resolve_query(record)
        return resolved.key if isinstance(resolved, ResolvedMatchQuery) else None

    def evaluate(self, record: MatchSourceRecord) -> MatchEvaluation:
        """Evaluate one row without retaining its plate, VIN, or raw payload."""

        resolved = self._resolve_query(record)
        if isinstance(resolved, MatchEvaluation):
            return resolved
        scope_manufacturer = resolved.scope_manufacturer
        model_values = resolved.model_values
        year = resolved.year
        fuels = resolved.fuels
        engine_code = resolved.engine_code
        displacement_cc = resolved.displacement_cc
        power_kw = resolved.power_kw
        drive_type = resolved.drive_type
        bodywork = resolved.bodywork
        source_context = resolved.source_context
        recovery_reason = resolved.recovery_reason
        source_model_resolution = resolved.source_model_resolution
        cache_key = resolved.key
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        terminal: MatchTerminal
        _, scope = self._index.lookup(
            scope_manufacturer,
            similarity_threshold=self._manufacturer_scope_threshold,
        )
        if scope == "global":
            # This audit persists terminal counts only. Global candidates can
            # never be promoted, so scoring the entire catalog adds no useful
            # evidence and can turn one dirty manufacturer into hours of work.
            terminal = "review_required"
            evaluation = MatchEvaluation(terminal, ("manufacturer_global_scope",))
            self._cache[cache_key] = evaluation
            return evaluation
        routed = []
        for model_position, model in enumerate(model_values):
            try:
                query = VehicleMatchQuery(
                    manufacturer=scope_manufacturer,
                    model=model,
                    year=year,
                    fuels=fuels,
                    engine_code=engine_code,
                    displacement_cc=displacement_cc,
                    power_kw=power_kw,
                    drive_type=drive_type,
                    bodywork=bodywork,
                    source_context=source_context,
                )
            except ValueError:
                continue
            match_result = self._matcher.match(query)
            alias_match_result = self._alias_matcher.match(query)
            base_decision = self._router.route(match_result)
            alias_decision = self._router.route(alias_match_result)
            decision = prefer_non_degrading_alias_decision(base_decision, alias_decision)
            routed.append(
                (
                    decision,
                    alias_match_result if decision is alias_decision else match_result,
                    model_position,
                )
            )
        if not routed:
            evaluation = MatchEvaluation(
                "review_required", ("invalid_match_query_evidence",)
            )
            self._cache[cache_key] = evaluation
            return evaluation
        rank = {"review_required": 0, "provisional": 1, "resolved": 2}
        decision, match_result, model_position = max(
            routed,
            key=lambda item: (
                rank[item[0].route],
                item[0].confidence,
                -item[2],
            ),
        )
        if decision.hard_conflicts:
            terminal = "hard_conflict"
        else:
            terminal = decision.route
        match_reasons = {f"match:{match_result.reason}"}
        if source_model_resolution.rule_ids:
            match_reasons.add(f"source_model_policy:{self._source_model_policy.content_digest}")
            match_reasons.update(f"source_model_rule:{rule_id}" for rule_id in source_model_resolution.rule_ids)
        match_reasons.add(
            "model_evidence:alternative"
            if model_position > 0
            else "model_evidence:primary"
        )
        if recovery_reason is not None:
            match_reasons.add(recovery_reason)
            match_reasons.add(f"{recovery_reason}:{terminal}")
        match_reasons.update(f"route:{reason}" for reason in decision.reason_codes)
        match_reasons.update(f"conflict:{field}" for field in decision.hard_conflicts)
        if match_result.candidates:
            match_reasons.update(
                f"context_conflict:{field}"
                for field in match_result.candidates[0].conflicting_fields
                if field not in decision.hard_conflicts
            )
        if decision.selected_candidate_reference in self._candidate_only_references:
            match_reasons.add("candidate_only_not_graph_safe")
            if terminal == "resolved":
                terminal = "provisional"
        evaluation = MatchEvaluation(
            terminal,
            tuple(sorted(match_reasons)),
            top_candidate_reference=decision.top_candidate_reference,
            candidate_matches=decision.alternative_candidates,
            decision_trace=tuple(entry.to_payload() for entry in decision.decision_trace),
            confidence=decision.confidence,
        )
        self._cache[cache_key] = evaluation
        return evaluation


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _flatten_strings(value: object) -> frozenset[str]:
    """Flatten JSON/Neo4j component arrays without coercing unknown objects."""

    if isinstance(value, str):
        return frozenset({value}) if value.strip() else frozenset()
    if isinstance(value, list | tuple | set | frozenset):
        return frozenset(
            item
            for nested in value
            for item in _flatten_strings(nested)
        )
    return frozenset()


def _integer(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(float(str(value)))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _text(value: object) -> str | None:
    return str(value) if value else None
