"""Real PostgreSQL/Neo4j adapters for the write-free TS-to-TecDoc audit."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from neo4j import Driver
from psycopg import Connection

from ingestion.confidence_routing import ConfidenceRouter
from ingestion.fuzzy_matching import (
    FuzzyMatchConfig,
    FuzzyVehicleMatcher,
    ManufacturerCandidateIndex,
    VehicleCandidate,
    VehicleMatchQuery,
)
from ingestion.match_run_service import MatchSourceRecord, MatchTerminal
from ingestion.tecdoc.manufacturer_mapping import TecDocManufacturerIndex

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
       collect(DISTINCT body.canonical_name) AS bodyworks
ORDER BY ktype
"""


@dataclass(frozen=True)
class MatchEvaluation:
    """One terminal route plus sanitized, aggregate-safe reason codes."""

    terminal: MatchTerminal
    reason_codes: tuple[str, ...]

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


class TecDocDryRunEvaluator:
    """Classify normalized TS rows using the existing matcher and confidence router."""

    def __init__(
        self,
        candidates: Sequence[VehicleCandidate],
        manufacturer_rules: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        config = FuzzyMatchConfig()
        self._index = ManufacturerCandidateIndex(candidates)
        self._matcher = FuzzyVehicleMatcher(self._index, config)
        self._manufacturer_scope_threshold = config.manufacturer_scope_threshold
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
        self._cache: dict[
            tuple[
                str,
                str,
                int | None,
                frozenset[str],
                str | None,
                int | None,
                int | None,
                str | None,
                str | None,
                str | None,
            ],
            MatchEvaluation,
        ] = {}

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    def __call__(self, record: MatchSourceRecord) -> MatchTerminal:
        return self.evaluate(record).terminal

    def evaluate(self, record: MatchSourceRecord) -> MatchEvaluation:
        """Evaluate one row without retaining its plate, VIN, or raw payload."""

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
        model = normalized.get("model_family") or candidates.get("model_family")
        if not manufacturer:
            return MatchEvaluation("unmatched", ("manufacturer_missing",))
        recovery_reason: str | None = None
        source_evidence = _mapping(payload.get("source_evidence"))
        model_evidence = {
            field_name: str(value)
            for field_name in (
                "brand",
                "variant",
                "version",
                "model_no",
                "type_text",
                "eeg_type_approval",
            )
            if (value := source_evidence.get(field_name))
        }
        if not model and model_evidence:
            recovered = self._index.recover_model_from_evidence(
                str(manufacturer), model_evidence
            )
            if recovered is not None:
                model, source_field = recovered
                recovery_reason = f"model_recovered_from_{source_field}"
        if not model:
            return MatchEvaluation("review_required", ("model_evidence_missing",))
        energy = normalized.get("energy_sources")
        fuels = (
            frozenset(str(value) for value in energy) if isinstance(energy, list) else frozenset()
        )
        year = _integer(normalized.get("production_year"))
        engine_code = _text(normalized.get("engine_code"))
        displacement_cc = _integer(normalized.get("displacement_cc"))
        power_kw = _integer(normalized.get("power_kw"))
        drive_type = _text(normalized.get("drive_type"))
        bodywork = _text(normalized.get("bodywork_form"))
        # Map TS manufacturer spelling onto its TecDoc catalog name before
        # scoping. Only an unambiguous resolution is used; conflicts and
        # unmatched evidence keep the original text and the existing behaviour.
        scope_decision = self._manufacturer_scope.resolve(
            manufacturer=manufacturer,
            brand=source_evidence.get("brand"),
        )
        scope_manufacturer = (
            scope_decision.manufacturer
            if scope_decision.status == "resolved" and scope_decision.manufacturer
            else str(manufacturer)
        )
        cache_key = (
            scope_manufacturer,
            str(model),
            year,
            fuels,
            engine_code,
            displacement_cc,
            power_kw,
            recovery_reason,
            drive_type,
            bodywork,
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        terminal: MatchTerminal
        try:
            query = VehicleMatchQuery(
                manufacturer=scope_manufacturer,
                model=str(model),
                year=year,
                fuels=fuels,
                engine_code=engine_code,
                displacement_cc=displacement_cc,
                power_kw=power_kw,
                drive_type=drive_type,
                bodywork=bodywork,
            )
        except ValueError:
            # Dirty evidence such as punctuation-only model text is reviewable
            # source data and must not stop the full audit.
            terminal = "review_required"
            evaluation = MatchEvaluation(terminal, ("invalid_match_query_evidence",))
            self._cache[cache_key] = evaluation
            return evaluation
        _, scope = self._index.lookup(
            query.manufacturer,
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
        match_result = self._matcher.match(query)
        decision = self._router.route(match_result)
        if decision.hard_conflicts:
            terminal = "hard_conflict"
        else:
            terminal = decision.route
        match_reasons = {f"match:{match_result.reason}"}
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
        evaluation = MatchEvaluation(terminal, tuple(sorted(match_reasons)))
        self._cache[cache_key] = evaluation
        return evaluation


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _integer(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) and int(value) > 0 else None


def _text(value: object) -> str | None:
    return str(value) if value else None
