"""Real PostgreSQL/Neo4j adapters for the write-free TS-to-TecDoc audit."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from neo4j import Driver
from psycopg import Connection
from psycopg.rows import dict_row

from ingestion.confidence_routing import (
    ConfidenceRouter,
    ConfidenceRoutingDecision,
    ConfidenceTraceEntry,
)
from ingestion.context_comparison import SOURCE_CONTEXT_FIELDS, ContextComparisonPolicy
from ingestion.fuzzy_matching import (
    PLUG_IN_ELECTRIFICATION,
    PLUG_IN_POWER_GUARD,
    FuzzyCandidateMatch,
    FuzzyMatchConfig,
    FuzzyMatchResult,
    FuzzyVehicleMatcher,
    ManufacturerCandidateIndex,
    VehicleCandidate,
    VehicleMatchQuery,
    registry_spelling,
    same_model_family,
    same_model_text,
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
       variant.tecdoc_engine_type_code AS engine_type_code,
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
       variant.attributes->>'month_from' AS month_from,
       variant.attributes->>'month_to' AS month_to,
       coalesce(variant.attributes->>'vehicle_fuel_type',
                variant.attributes->>'fuel_type') AS fuel_type,
       engine.attributes->>'engine_code' AS engine_code,
       variant.attributes->>'displacement_cc' AS displacement_cc,
       variant.attributes->>'power_kw' AS power_kw,
       variant.attributes->>'drive_type' AS drive_type,
       variant.attributes->>'tecdoc_drive_type_code' AS drive_type_code,
       bodywork.attributes->>'canonical_name' AS bodywork,
       variant.attributes->>'tecdoc_body_type_code' AS body_type_code,
       variant.attributes->>'tecdoc_engine_type_code' AS engine_type_code,
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

    Each alias is also given in the registry's spelling (`registry_spelling`):
    "CEE'D (JD)" yields "CEE'D" and "CEED", "SANTA FÉ III (DM, DMA)" yields
    "SANTA FÉ III", "SANTA FÉ", "SANTA FE III" and "SANTA FE".
    """
    without_chassis = _CHASSIS_SUFFIX.sub("", model).strip()
    aliases = {without_chassis}
    words = without_chassis.split()
    carries_code = any(character.isdigit() for word in words for character in word)
    decoration = _GENERATION_NUMERALS | (_AMBIGUOUS_NUMERALS if carries_code else frozenset())
    tokens = [word for word in words if word.upper() not in decoration]
    if tokens:
        aliases.add(" ".join(tokens))
    aliases |= {registry_spelling(alias) for alias in aliases}
    return tuple(sorted(alias for alias in aliases if alias and alias != model))


#: TecDoc families the registry knows by another name, reviewed one by one: by
#: (TecDoc manufacturer, family name without its chassis code, both upper case),
#: the names to add. Each is an alias on that family's own KTypes and nothing
#: else, so the family's years, fuel, power and body decide as for any other name;
#: the registry's own text and its normalization are not touched.
REVIEWED_EXPORT_NAMES: dict[tuple[str, str], tuple[str, ...]] = {
    # BYD sells the Yuan Plus (TecDoc "YUAN PLUS": from 2022, electric, 150 kW, FWD)
    # in Europe as the Atto 3; TecDoc has no "ATTO 3".
    ("BYD", "YUAN PLUS"): ("ATTO 3",),
    # TecDoc names the Golf Plus "GOLF PLUS V (5M1, 521)", and a bare "V" is not
    # stripped as a numeral (see `_AMBIGUOUS_NUMERALS`), so no alias read "GOLF
    # PLUS" as the registry writes it.
    ("VW", "GOLF PLUS V"): ("GOLF PLUS",),
    # The registry files the 1998-2010 New Beetle as "BEETLE" too. TecDoc's own
    # "BEETLE (5C1, 5C2)" starts 04/2011, so the years tell the two apart.
    ("VW", "NEW BEETLE"): ("BEETLE",),
    ("VW", "NEW BEETLE CONVERTIBLE"): ("BEETLE",),
    # "ID. Buzz Bus (EBB, EBJ)" is the passenger ID. Buzz; the "Cargo" is the van.
    ("VW", "ID. BUZZ BUS"): ("ID. BUZZ",),
    # The registry's model field reads "CC" for both; TecDoc names the 2008-2012
    # car "PASSAT CC B6 (357)" and its 11/2011-2016 successor "CC B7 (358)".
    ("VW", "PASSAT CC B6"): ("CC",),
    ("VW", "CC B7"): ("CC",),
    # The electric Citigo (09/2019-2021) is registered as "CITIGO"; the fuel sets it
    # apart from the petrol and CNG "CITIGO (NF1)".
    ("SKODA", "E-CITIGO"): ("CITIGO",),
    # The 1963-1971 230/250/280 SL is TecDoc's "PAGODE (W113)", by its nickname.
    # Only that family gets the name, so only cars of those years reach it.
    ("MERCEDES-BENZ", "PAGODE"): ("SL",),
    # The Sovereign is a trim of the XJ saloon (Series III to X350) that the
    # registry files as a model of its own. The "XJ Coupe" is not one.
    ("JAGUAR", "XJ"): ("SOVEREIGN",),
    # The registry's "Duett" of 1960-1969 is "P 210 DUETT (P211, P212)". Its
    # predecessor "PV 445 DUETT (P445)" gets the name too, or the name alone would
    # choose the P 210 for a 1960 car, a year TecDoc lists both with 44 kW: that
    # car is a tie for a person, and a 1957 "Duett" is the PV 445 its year names.
    ("VOLVO", "P 210 DUETT"): ("DUETT",),
    ("VOLVO", "PV 445 DUETT"): ("DUETT",),
    # "PHASE I" is TecDoc's facelift decoration, on the only Scenic E-Tech there is.
    ("RENAULT", "SCENIC E-TECH PHASE I"): ("SCENIC E-TECH",),
}


#: Makers whose numbered families the registry writes with the make glued to the
#: number: "MAZDA3", "MAZDA2" and "MAZDA6" are TecDoc's "3 (BM, BN)", "2 (DY)" and
#: "6 Estate (GH)". The glued form replaces the number alone and the rest of the
#: name stays ("MAZDA3 SALOON"), so a body word tells siblings apart as it does
#: for "3". Catalog side only: the reviewed rule's own value ("Mazda3") is unchanged.
REVIEWED_GLUED_MAKE_NUMBERS: dict[str, frozenset[str]] = {
    "MAZDA": frozenset({"2", "3", "6"}),
}


#: TecDoc makers that carry a registry make's models from another market, in order
#: of preference. A car goes there only when its model is no family under the make
#: itself.
REVIEWED_SISTER_MAKERS: dict[str, tuple[str, ...]] = {
    # TecDoc files every Mustang (and the Mach-E, Bronco, Crown Victoria...) under
    # "FORD USA", none under "FORD".
    "FORD": ("FORD USA", "FORD AUSTRALIA"),
    # Cars the registry still calls SEAT ("SEAT BORN", "SEAT FORMENTOR") are TecDoc
    # "CUPRA" families; a SEAT Leon or Ateca stays a SEAT, which has both.
    "SEAT": ("CUPRA",),
    # ORA is not listed: TecDoc has the same cars under "ORA" ("07 EV (EC24)",
    # "ES11 EV / GT") and under "GREAT WALL" ("Ora 07 EV", "ORA 03 (ES11)"). Which
    # maker's KTypes are canonical is the data owner's decision; until then an ORA
    # stays under ORA, where a person sees it.
}


def reviewed_export_names(manufacturer: str, model: str) -> tuple[str, ...]:
    """The other names a TecDoc family is known by, for its own manufacturer only."""

    maker = manufacturer.strip().upper()
    family = _CHASSIS_SUFFIX.sub("", model).strip().upper()
    names = REVIEWED_EXPORT_NAMES.get((maker, family), ())
    if family.split(" ", 1)[0] in REVIEWED_GLUED_MAKE_NUMBERS.get(maker, ()):
        names = (*names, f"{maker}{family}")
    return names


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


#: TecDoc engine types (KT 080) by what each says of electrification. The other
#: types in the catalog are engines alone: 001 spark ignition (petrol, ethanol,
#: gas), 002 diesel, 003 two-stroke, 004 Wankel.
_ELECTRIFICATION_BY_ENGINE_TYPE: dict[str, str] = {
    "040": "battery_electric",
    "046": "plug_in_hybrid",
    "047": "range_extender",
    "048": "full_hybrid",
    "049": "mild_hybrid",
    **dict.fromkeys(("001", "002", "003", "004"), "combustion"),
}


def ktype_electrification(engine_type_code: object) -> str | None:
    """A KType's electrification from its TecDoc engine type; None when unknown.

    A missing code or one outside the table above is unknown, not "combustion":
    the electrification checks skip such a KType rather than read it as no plug-in.
    """

    code = _text(engine_type_code)
    return _ELECTRIFICATION_BY_ENGINE_TYPE.get(code.strip()) if code else None


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
            model_aliases=tuple(dict.fromkeys((
                *tecdoc_model_aliases(str(row["model"])),
                *reviewed_export_names(str(row["manufacturer"]), str(row["model"])),
            ))),
            year_from=_integer(row["year_from"]),
            year_to=_integer(row["year_to"]),
            fuels=frozenset({str(row["fuel_type"])}) if row["fuel_type"] else frozenset(),
            fuel_components=_flatten_strings(row["engine_fuel_components"]),
            engine_codes=frozenset(str(value) for value in row["engine_codes"] if value),
            displacement_cc=_integer(row["displacement_cc"]),
            power_kw=_integer(row["power_kw"]),
            drive_type=_text(row["drive_type"]),
            bodyworks=frozenset(str(value) for value in row["bodyworks"] if value),
            electrification=ktype_electrification(row.get("engine_type_code")),
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
    drive_by_code = canonical_drive_by_kt082(connection)
    bodywork_by_code = canonical_bodywork_by_kt086(connection)
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
            model_aliases=tuple(dict.fromkeys((
                *postgres_tecdoc_model_aliases(str(row["model"]), row["source_name"]),
                *reviewed_export_names(str(row["manufacturer"]), str(row["model"])),
            ))),
            year_from=_integer(row["year_from"]),
            year_to=_integer(row["year_to"]),
            month_from=_integer(row["month_from"]),
            month_to=_integer(row["month_to"]),
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
            electrification=ktype_electrification(row.get("engine_type_code")),
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
    #: The engine code is the car's own (registry or AIS), not a reviewed
    #: fingerprint's inference. Only an observed code may confirm a KType.
    engine_code_observed: bool = False
    #: The car's build month as YYYYMM, when the registry gives one.
    build_month: int | None = None
    #: The registry's electrification type ("hybrid", "plug_in_hybrid", ...).
    electrification: str | None = None
    #: The car's model family as normalization states it, whichever model values
    #: the matcher read: the family the reading checks of `evaluate` measure against.
    registry_family: str | None = None


#: Registry fields a model can be read from. The registry's own model text is
#: consulted first. Normalization only sets model_family when a reviewed rule
#: covers the term, so a car registered as "DUSTER" or "GRAND C-MAX" reaches
#: matching with the model named plainly in the row and nothing reading it.
MODEL_EVIDENCE_FIELDS: tuple[str, ...] = (
    "model",
    "brand",
    "variant",
    "version",
    "model_no",
    "type_text",
    "eeg_type_approval",
)


_ROUTE_RANK = {"review_required": 0, "provisional": 1, "resolved": 2}


@dataclass(frozen=True)
class _Reading:
    """One matcher's routed decision on one of the car's model values."""

    decision: ConfidenceRoutingDecision
    result: FuzzyMatchResult
    position: int
    alias: bool

    @property
    def top(self) -> FuzzyCandidateMatch | None:
        return self.result.candidates[0] if self.result.candidates else None


def _same_family(left: str, right: str, manufacturer: str) -> bool:
    """Two catalog models of one family, whichever is read as the family name."""

    return same_model_family(left, right, manufacturer) or same_model_family(right, left, manufacturer)


#: Routing-gate entries (rule id, explanation) for `FuzzyMatchResult.guards`.
_MATCH_GUARDS: dict[str, tuple[str, str]] = {
    PLUG_IN_POWER_GUARD: (
        "ROUTE-REVIEW-PLUG-IN-POWER-V1",
        (
            "Only exact power set the top KType apart from a plug-in sibling, and the registry "
            "gives a hybrid's engine power, not the system power TecDoc gives a plug-in."
        ),
    ),
    # `DISPLACEMENT_ROUNDING_SIBLING_GUARD` and `TOLERATED_EVIDENCE_GUARD` of fuzzy_matching.
    "displacement_rounding_exact_sibling": (
        "ROUTE-REVIEW-CC-ROUNDING-EXACT-SIBLING-V1",
        (
            "The top KType's displacement is 1-3 cc off the car's, while a sibling with the "
            "car's exact displacement is held back only by its engine code."
        ),
    ),
    "no_exact_technical_field": (
        "ROUTE-REVIEW-NO-EXACT-TECHNICAL-FIELD-V1",
        (
            "The top KType's displacement or power is only close to the car's, and none of "
            "power, displacement and engine code matches exactly."
        ),
    ),
}
_HARD_CONFLICT_REPLACED = (
    "ROUTE-REVIEW-HARD-CONFLICT-REPLACED-V1",
    (
        "A reading that contradicts nothing replaced a suggestion with a hard conflict; "
        "the car stays in review."
    ),
)
#: The checks `_reading_guards` runs, by the reason each adds, with the
#: routing-gate entry (rule id, explanation) it writes into the trace.
_READING_GUARDS: dict[str, tuple[str, str]] = {
    "reading_disagreement:outside_registry_family": (
        "ROUTE-REVIEW-OUTSIDE-REGISTRY-FAMILY-V1",
        (
            "The KType came from an alternative model value outside the car's registry model "
            "family, while another reading lies inside that family."
        ),
    ),
    "reading_disagreement:other_matcher_family": (
        "ROUTE-REVIEW-MATCHERS-DISAGREE-V1",
        "On the same model value the other matcher reaches a KType of another model family.",
    ),
    "reading_disagreement:other_value_ktype": (
        "ROUTE-REVIEW-MODEL-VALUES-DISAGREE-V1",
        "Another of the car's model values also resolves, to another KType.",
    ),
    "electrification_conflict": (
        "ROUTE-REVIEW-ELECTRIFICATION-CONFLICT-V1",
        "The registry's electrification type contradicts the KType's TecDoc engine type.",
    ),
    "export_name_without_engine_evidence": (
        "ROUTE-REVIEW-EXPORT-NAME-WITHOUT-ENGINE-EVIDENCE-V1",
        (
            "A rule-inferred model reached the KType only through a reviewed export name, and "
            "neither the car's power nor its engine code matched it."
        ),
    ),
}


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
        drive_alignment: FuelAlignment | None = None,
        bodywork_alignment: FuelAlignment | None = None,
        context_policy: ContextComparisonPolicy | None = None,
        source_model_policy: ReviewedSourceModelPolicy | None = None,
    ) -> None:
        config = FuzzyMatchConfig()
        self._fuel_alignment = fuel_alignment
        self._drive_alignment = drive_alignment
        self._context_policy = context_policy or ContextComparisonPolicy()
        self._source_model_policy = source_model_policy or ReviewedSourceModelPolicy()
        self._source_model_policy.validate_catalog(candidates)
        if fuel_alignment is not None:
            candidates = align_catalog_fuels(candidates, fuel_alignment.tecdoc_equivalences)
        compatible_pairs = fuel_alignment.compatible_pairs if fuel_alignment else frozenset()
        drive_compatible_pairs = (
            drive_alignment.compatible_pairs if drive_alignment else frozenset()
        )
        bodywork_compatible_pairs = (
            bodywork_alignment.compatible_pairs if bodywork_alignment else frozenset()
        )
        self._index = ManufacturerCandidateIndex(candidates)
        self._matcher = FuzzyVehicleMatcher(
            self._index, config, fuel_compatible_pairs=compatible_pairs,
            drive_compatible_pairs=drive_compatible_pairs,
            bodywork_compatible_pairs=bodywork_compatible_pairs,
            context_policy=self._context_policy,
        )
        # TS spells manufacturers differently from TecDoc ("CITROEN" vs
        # "CITROËN", "LYNK&CO" vs "LYNK & CO"). Without this accent- and
        # punctuation-tolerant mapping those rows resolve to global scope and
        # are reviewed without ever being scored. Reviewed manufacturer rules
        # additionally bridge alias spellings onto their catalog target.
        self._manufacturer_scope = TecDocManufacturerIndex(
            sorted({candidate.manufacturer for candidate in candidates}),
            manufacturer_rules or {},
        )
        if reviewed_model_aliases is not None:
            # Reviewed model rules name the manufacturer as TS does ("Volkswagen");
            # the catalog as TecDoc does ("VW"). Without this no VW rule attaches.
            reviewed_model_aliases = reviewed_model_aliases.scoped_to_catalog(
                lambda name: self._manufacturer_scope.resolve(manufacturer=name).manufacturer
            )
        expanded_candidates = (
            tuple(reviewed_model_aliases.expand(candidate) for candidate in candidates)
            if reviewed_model_aliases is not None
            else tuple(candidates)
        )
        self._alias_index = ManufacturerCandidateIndex(expanded_candidates)
        self._alias_matcher = FuzzyVehicleMatcher(
            self._alias_index, config, fuel_compatible_pairs=compatible_pairs,
            drive_compatible_pairs=drive_compatible_pairs,
            bodywork_compatible_pairs=bodywork_compatible_pairs,
            context_policy=self._context_policy,
        )
        self._candidate_only_references = frozenset(
            candidate.candidate_reference
            for candidate in candidates
            if candidate.candidate_type == "TecDocKTypeCandidateOnly"
        )
        self._electrification = {
            candidate.candidate_reference: candidate.electrification for candidate in candidates
        }
        # Catalog families (maker, TecDoc model) with a full or mild hybrid KType:
        # a car registered as a non-plug-in hybrid could be one of them.
        self._hybrid_families = frozenset(
            (candidate.manufacturer, candidate.model)
            for candidate in candidates
            if candidate.electrification in {"full_hybrid", "mild_hybrid"}
        )
        # Catalog model names: a recovered model outside them is a family label.
        self._catalog_models = frozenset(candidate.model for candidate in candidates)
        self._manufacturer_scope_threshold = config.manufacturer_scope_threshold
        self._engine_fingerprints = reviewed_engine_fingerprints or ReviewedEngineFingerprintIndex()
        self._router = ConfidenceRouter()
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
        # A model a learned rule filled is inferred from sibling vehicles, so the
        # car's own registry text is read first: "VW BORA 1,6" is a Bora even
        # where its VIN prefix taught the rule "Golf". The inferred model is the
        # fallback when the text names nothing the catalog recognizes.
        inferred_fields = payload.get("inferred_fields")
        inferred_model = (
            str(normalized.get("model_family") or "").strip()
            if isinstance(inferred_fields, list | tuple) and "model_family" in inferred_fields
            else ""
        )
        # A model a person set for this car is the car's model: the registry's own
        # text does not overrule it, and it is not weighed against that text. Only
        # the read seam sets this, and only for a car a person corrected.
        asserted_fields = payload.get("asserted_fields")
        asserted_model = (
            str(normalized.get("model_family") or "").strip()
            if isinstance(asserted_fields, list | tuple) and "model_family" in asserted_fields
            else ""
        )
        model_values = (
            (asserted_model,)
            if asserted_model
            else tuple(
                dict.fromkeys(
                    str(value).strip()
                    for value in (
                        None if inferred_model else normalized.get("model_family"),
                        candidates.get("model_family"),
                        source_evidence.get("model"),
                    )
                    if str(value or "").strip()
                )
            )
        )
        model_evidence = {
            field_name: str(value)
            for field_name in MODEL_EVIDENCE_FIELDS
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
        scope_manufacturer = self._sister_maker_scope(
            scope_decision.manufacturer
            if scope_decision.status == "resolved" and scope_decision.manufacturer
            else str(manufacturer),
            model_values,
            source_evidence,
            inferred_model,
        )
        explicit_model = source_evidence.get("model")
        if asserted_model:
            # Read the way a registry model text is: when it names a catalog model,
            # that model is queried, with the person's own wording beside it.
            named = self._alias_index.recover_model_from_evidence(
                scope_manufacturer, {"model": asserted_model}
            )
            if named is not None:
                model_values = tuple(dict.fromkeys((named[0], asserted_model)))
            recovery_reason = "model_asserted_by_person"
        elif explicit_model:
            explicit = self._alias_index.recover_model_from_evidence(
                scope_manufacturer, {"model": str(explicit_model)}
            )
            # Model field and brand text naming different models fail closed. The
            # check reads both the way it always has: reading model words only
            # decides which model to use, never adds a disagreement.
            stated = self._alias_index.recover_model_from_evidence(
                scope_manufacturer, {"model": str(explicit_model)}, reading="legacy"
            )
            brand_model = self._alias_index.recover_model_from_evidence(
                scope_manufacturer, {"brand": str(source_evidence.get("brand") or "")}, reading="legacy"
            )
            # A label shared by generations ("CEED" on CEE'D (JD) and CEED (CD)) is a
            # family, not one catalog model, so it is compared by family: with brand
            # text "KIA CEED SW" it names one family twice, with "KIA PRO_CEE'D GT"
            # two. Two catalog models are compared as before: a "GOLF" whose brand
            # text names the "GOLF VARIANT" still disagrees.
            if (
                stated is not None
                and brand_model is not None
                and stated[0] != brand_model[0]
                and not (
                    not {stated[0], brand_model[0]} <= self._catalog_models
                    and _same_family(stated[0], brand_model[0], scope_manufacturer)
                )
            ):
                return MatchEvaluation("review_required", ("model_source_evidence_conflict",))
            if explicit is not None:
                # Catalog-recognized raw evidence must not compete with a
                # broader normalization on whichever happens to score highest.
                model_values = (explicit[0], str(explicit_model))
                recovery_reason = "model_recovered_from_model"
        if not model_values and model_evidence:
            # Only the car's model word overrules an inferred model; a trim word
            # ("200 T", "1 6 FSI") is weaker evidence than the rule.
            recovered = self._alias_index.recover_model_from_evidence(
                scope_manufacturer, model_evidence, reading="strict" if inferred_model else "model_word"
            )
            if recovered is None and inferred_model:
                # The text may still name the inferred family more precisely
                # ("BMW 630 CS" is the 6 (E24) of a "6 Series"); never another one.
                specific = self._alias_index.recover_model_from_evidence(scope_manufacturer, model_evidence)
                if specific is not None and same_model_family(inferred_model, specific[0], scope_manufacturer):
                    recovered = specific
            if recovered is not None:
                recovered_model, source_field = recovered
                model_values = (recovered_model,)
                recovery_reason = f"model_recovered_from_{source_field}"
        if inferred_model and recovery_reason is None:
            model_values = tuple(dict.fromkeys((*model_values, inferred_model)))
            recovery_reason = "model_inferred_by_rule"
        if not model_values:
            return MatchEvaluation("review_required", ("model_evidence_missing",))
        source_model_resolution = self._source_model_policy.resolve(
            manufacturer=scope_manufacturer,
            source_model=str(explicit_model or "") if scope_decision.status == "resolved" else "",
            source_evidence=source_evidence,
        )
        if source_model_resolution.conflict and not asserted_model:
            return MatchEvaluation("review_required", ("source_model_rules_conflict",))
        if source_model_resolution.target_model is not None and not asserted_model:
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
        build_month = _build_month(normalized, year)
        engine_code = _text(normalized.get("engine_code"))
        displacement_cc = _integer(normalized.get("displacement_cc"))
        power_kw = _integer(normalized.get("power_kw"))
        drive_type = _text(normalized.get("drive_type"))
        bodywork = _text(normalized.get("bodywork_form"))
        source_context = tuple(sorted(
            (key, str(value)) for key in SOURCE_CONTEXT_FIELDS
            if (value := source_evidence.get(key)) is not None
        )) if self._context_policy.rules else ()
        engine_code_observed = engine_code is not None
        if engine_code is None:
            engine_code = self._engine_fingerprints.resolve(
                manufacturer=scope_manufacturer,
                type_approval=source_evidence.get("eeg_type_approval"),
                variant=source_evidence.get("variant"),
                version=source_evidence.get("version"),
            )
        electrification = _text(normalized.get("electrification_type"))
        registry_family = str(normalized.get("model_family") or "").strip() or None
        cache_key: tuple[object, ...] = (
            scope_manufacturer,
            "\x1f".join(model_values),
            year,
            build_month,
            fuels,
            engine_code,
            displacement_cc,
            power_kw,
            recovery_reason,
            drive_type,
            bodywork,
            source_context,
            source_model_resolution.rule_ids,
            engine_code_observed,
        )
        # The electrification check reads the registry's electrification type on
        # every car, and the reading checks the registry family, so cars differing
        # in either must not share an answer. Appended only when set: a car with
        # neither keeps its key, and with it its chunk signature.
        cache_key += tuple(
            (name, value)
            for name, value in (("electrification", electrification), ("registry_family", registry_family))
            if value is not None
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
            engine_code_observed=engine_code_observed,
            build_month=build_month,
            electrification=electrification,
            registry_family=registry_family,
        )

    def _sister_maker_scope(
        self,
        scope: str,
        model_values: Sequence[str],
        source_evidence: Mapping[str, Any],
        inferred_model: str = "",
    ) -> str:
        """The sister maker (REVIEWED_SISTER_MAKERS) whose catalog has the car's
        model, when the make's own catalog has no such family; else the make.

        The car's own text is read first, as everywhere: its model values, else its
        brand text. Only when that names no family of the make or of a sister is a
        rule-inferred model read the same way: a 2011 Mustang whose brand text is
        just "FORD" has nothing but the inferred "Mustang" to say it is a FORD USA.
        """

        sisters = REVIEWED_SISTER_MAKERS.get(scope.strip().upper(), ())
        if not sisters:
            return scope
        own = [{"model": value} for value in model_values]
        if not own and (brand := _text(source_evidence.get("brand"))):
            own = [{"brand": brand}]

        def names_a_family(maker: str, texts: Sequence[Mapping[str, str]]) -> bool:
            return any(
                self._alias_index.recover_model_from_evidence(maker, text, reading="strict") is not None
                for text in texts
            )

        for texts in (own, [{"model": inferred_model}] if inferred_model else []):
            if names_a_family(scope, texts):
                return scope
            sister = next((sister for sister in sisters if names_a_family(sister, texts)), None)
            if sister is not None:
                return sister
        return scope

    def catalog_manufacturer(self, manufacturer: str, source_evidence: Mapping[str, Any]) -> str:
        """The catalog's name for a registry make, scoped as `evaluate` scopes it."""

        decision = self._manufacturer_scope.resolve(
            manufacturer=manufacturer, brand=source_evidence.get("brand"),
        )
        if decision.status == "resolved" and decision.manufacturer:
            return str(decision.manufacturer)
        return str(manufacturer)

    def source_text_model(
        self, manufacturer: str, source_evidence: Mapping[str, Any]
    ) -> tuple[str, str] | None:
        """The catalog model a car's registry text names by its model word.

        Read as `evaluate` reads text against a rule-inferred model: the registry
        model field, else the word each field names the model by. Returns the
        catalog manufacturer and the model, or None when the text names no catalog
        model that way or its model and brand text name different ones.
        """

        scope = self.catalog_manufacturer(manufacturer, source_evidence)
        explicit_model = source_evidence.get("model")
        if explicit_model:
            explicit = self._alias_index.recover_model_from_evidence(
                scope, {"model": str(explicit_model)}, reading="strict"
            )
            brand_model = self._alias_index.recover_model_from_evidence(
                scope, {"brand": str(source_evidence.get("brand") or "")}, reading="strict"
            )
            if explicit is not None and brand_model is not None and explicit[0] != brand_model[0]:
                return None
            if explicit is not None:
                return scope, explicit[0]
        evidence = {
            field: str(value) for field in MODEL_EVIDENCE_FIELDS if (value := source_evidence.get(field))
        }
        recovered = (
            self._alias_index.recover_model_from_evidence(scope, evidence, reading="strict")
            if evidence else None
        )
        return (scope, recovered[0]) if recovered is not None else None

    def resolved_query(self, record: MatchSourceRecord) -> ResolvedMatchQuery | None:
        """What the matcher keys on for this row, or None if it terminates first.

        For diagnostics that must show *what the matcher saw* -- including values
        it derived itself, such as an engine code from a reviewed fingerprint --
        rather than re-deriving them and drifting from the real evaluation.
        """

        resolved = self._resolve_query(record)
        return resolved if isinstance(resolved, ResolvedMatchQuery) else None

    def evaluation_key(self, record: MatchSourceRecord) -> tuple[object, ...] | None:
        """The key two rows must share to be guaranteed the same evaluation.

        None when the row terminates before matching -- a normalization failure,
        a policy route, or missing model evidence -- because such rows are not
        grouped by anything the matcher computed.
        """

        resolved = self._resolve_query(record)
        return resolved.key if isinstance(resolved, ResolvedMatchQuery) else None

    def evaluate(self, record: MatchSourceRecord, *, remember: bool = True) -> MatchEvaluation:
        """Evaluate one row without retaining its plate, VIN, or raw payload.

        `remember=False` reads the memo but adds nothing to it, so a what-if
        check over many cars cannot grow it. The evaluation is the same either way.
        """

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
            if remember:
                self._cache[cache_key] = evaluation
            return evaluation
        preferred: list[_Reading] = []
        readings: list[_Reading] = []
        for model_position, model in enumerate(model_values):
            try:
                query = VehicleMatchQuery(
                    manufacturer=scope_manufacturer,
                    model=model,
                    year=year,
                    build_month=resolved.build_month,
                    fuels=fuels,
                    engine_code=engine_code,
                    displacement_cc=displacement_cc,
                    power_kw=power_kw,
                    drive_type=drive_type,
                    bodywork=bodywork,
                    source_context=source_context,
                    electrification=resolved.electrification,
                )
            except ValueError:
                continue
            match_result = self._matcher.match(query)
            alias_match_result = self._alias_matcher.match(query)
            base = _Reading(self._router.route(match_result), match_result, model_position, alias=False)
            alias = _Reading(
                self._router.route(alias_match_result), alias_match_result, model_position, alias=True
            )
            decision = prefer_non_degrading_alias_decision(base.decision, alias.decision)
            preferred.append(alias if decision is alias.decision else base)
            readings += (base, alias)
        if not preferred:
            evaluation = MatchEvaluation(
                "review_required", ("invalid_match_query_evidence",)
            )
            if remember:
                self._cache[cache_key] = evaluation
            return evaluation
        winner = max(
            preferred,
            key=lambda reading: (
                _ROUTE_RANK[reading.decision.route],
                reading.decision.confidence,
                -reading.position,
            ),
        )
        replaced: tuple[str, ...] = ()
        clean = (
            self._conflict_free_reading(resolved, winner, readings)
            if winner.decision.hard_conflicts
            else None
        )
        if clean is not None:
            replaced, winner = winner.decision.hard_conflicts, clean
        # A match guard holds back one reading inside the matcher; no other reading of
        # the same car may then resolve around it. A plug-in Range Rover read through
        # the reviewed alias "RANGE ROVER" was held back on the Evoque P300 MHEV, and
        # the base reading resolved the L405 instead. The car is shown the held-back
        # reading, in review, with the plug-in sibling as its runner-up.
        guarded = [reading for reading in readings if reading.result.guards]
        if guarded and not winner.result.guards and winner.decision.route != "review_required":
            winner = max(
                guarded,
                key=lambda reading: (reading.decision.confidence, -reading.position, reading.alias),
            )
        decision, match_result, model_position = winner.decision, winner.result, winner.position
        if decision.hard_conflicts:
            terminal = "hard_conflict"
        else:
            terminal = decision.route
        guards = (
            self._reading_guards(resolved, winner, readings)
            if terminal in {"resolved", "provisional"}
            else ()
        )
        if guards:
            terminal = "review_required"
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
            if terminal == "resolved" and _engine_confirms(resolved, match_result):
                # TecDoc could not settle which engine this KType has; the
                # car's own engine code is one of its engines, which settles it.
                match_reasons.add("candidate_only_engine_confirmed")
            else:
                match_reasons.add("candidate_only_not_graph_safe")
                if terminal == "resolved":
                    terminal = "provisional"
        # What held the car back after routing, as reasons and as routing-gate
        # entries, so the trace's last gate explains where the car ended.
        notes = [
            *(_MATCH_GUARDS[guard] for guard in match_result.guards),
            *((_HARD_CONFLICT_REPLACED,) if replaced else ()),
            *(_READING_GUARDS[reason] for reason in guards),
        ]
        match_reasons.update(f"match_guard:{guard}" for guard in match_result.guards)
        match_reasons.update(f"hard_conflict_replaced:{field}" for field in replaced)
        match_reasons.update(guards)
        trace = [entry.to_payload() for entry in decision.decision_trace]
        for rule_id, explanation in notes:
            trace.append(
                ConfidenceTraceEntry(
                    sequence=len(trace) + 1, rule_id=rule_id, signal="routing_gate",
                    value="review_required", weight=0.0, contribution=0.0, explanation=explanation,
                ).to_payload()
            )
        evaluation = MatchEvaluation(
            terminal,
            tuple(sorted(match_reasons)),
            top_candidate_reference=decision.top_candidate_reference,
            candidate_matches=decision.alternative_candidates,
            decision_trace=tuple(trace),
            confidence=decision.confidence,
        )
        if remember:
            self._cache[cache_key] = evaluation
        return evaluation

    def _conflict_free_reading(
        self, resolved: ResolvedMatchQuery, winner: _Reading, readings: Sequence[_Reading]
    ) -> _Reading | None:
        """The best reading that contradicts nothing, to suggest instead of a hard conflict.

        A winner with a hard conflict means every reading is review_required (the
        winner has the highest route), so this changes what a reviewer is shown,
        never the route: a Q8 50 e-tron is shown the Q8 e-tron that fits, not a KType
        TecDoc contradicts. Any conflicting field disqualifies a reading, a body too,
        or a Berlingo would be shown a Multispace. It stays in the car's registry
        model family, or the conflicting suggestion's family when the registry names
        none: a GLE 53 Hybrid is never shown the electric EQE SUV. Ordered by
        confidence, then the earlier model value, then the alias matcher's reading.
        """

        maker = resolved.scope_manufacturer
        family, conflicted = resolved.registry_family, winner.top

        def inside(model: str) -> bool:
            if family is not None:
                return same_model_family(family, model, maker)
            return conflicted is not None and _same_family(conflicted.model, model, maker)

        clean = [
            reading
            for reading in readings
            if reading.decision.route == "review_required"
            and (top := reading.top) is not None
            and not top.conflicting_fields
            and inside(top.model)
        ]
        return max(
            clean,
            key=lambda reading: (reading.decision.confidence, -reading.position, reading.alias),
            default=None,
        )

    def _reading_guards(
        self, resolved: ResolvedMatchQuery, winner: _Reading, readings: Sequence[_Reading]
    ) -> tuple[str, ...]:
        """Why a resolved or provisional winner must still be reviewed: the reasons.

        Each check catches a pattern measured wrong on the 20k and 30k samples, and
        none raises a route or picks another KType:

        - `outside_registry_family`: the winner came from an alternative model value
          and lies outside the car's registry model family, while some reading lies
          inside it. An "Ioniq 5" whose own IONIQ 5 conflicts on power must not
          resolve to the IONIQ 6 its alternative value reaches, nor an E-Class
          All-Terrain to the C-Class estate. A primary-value winner is not checked,
          nor a family no reading reaches: an iX1 the registry calls X1 stays.
        - `other_matcher_family`: on the winning model value the other matcher (base
          or reviewed aliases) itself reaches provisional or resolved in another
          family, so an alias has swapped the family (Multivan against Transporter).
        - `other_value_ktype`: the winner resolves and another model value, as text,
          resolves to another KType. "V60" resolves the plain V60 and wins on order
          over "V60 CROSS COUNTRY" resolving the V60 Cross Country; repeated values
          ("IBIZA", "IBIZA") are one value, and a value that only ties or reaches
          provisional (C 400 4MATIC) does not count. A 2024 V60 CC B5 that the plain
          "V60" got right goes to review too: the accepted cost.
        - `electrification_conflict`: see `_electrification_conflict`.
        - `export_name_without_engine_evidence`: the model is rule-inferred and names
          the KType's family only through a reviewed export name ("Duett" for the
          P 210, "SL" for the Pagode), and neither power nor an engine code matched.
          The rule and the name are both inferences, so name and year alone do not
          match: a plain "230" saloon a rule filled "SL" is no Pagode by its year.
        """

        top = winner.top
        if top is None:
            return ()
        maker, family, values = resolved.scope_manufacturer, resolved.registry_family, resolved.model_values
        checks = {
            "reading_disagreement:outside_registry_family": (
                winner.position > 0
                and family is not None
                and not same_model_family(family, top.model, maker)
                and any(
                    reading.top is not None and same_model_family(family, reading.top.model, maker)
                    for reading in readings
                )
            ),
            "reading_disagreement:other_matcher_family": any(
                reading.position == winner.position
                and reading.alias != winner.alias
                and reading.decision.route != "review_required"
                and reading.top is not None
                and not _same_family(reading.top.model, top.model, maker)
                for reading in readings
            ),
            "reading_disagreement:other_value_ktype": winner.decision.route == "resolved" and any(
                reading.decision.route == "resolved"
                and reading.decision.top_candidate_reference != top.candidate_reference
                and not same_model_text(values[reading.position], values[winner.position])
                for reading in readings
            ),
            "electrification_conflict": self._electrification_conflict(resolved.electrification, top),
            "export_name_without_engine_evidence": (
                resolved.recovery_reason == "model_inferred_by_rule"
                and _only_by_export_name(values[winner.position], top)
                and not any(
                    field == "power_kw" or field.startswith("engine_code") for field in top.matched_fields
                )
            ),
        }
        return tuple(reason for reason, fires in checks.items() if fires)

    def _electrification_conflict(self, registered: str | None, top: FuzzyCandidateMatch) -> bool:
        """True when the registry's electrification type contradicts the KType's engine type.

        A car registered as a plug-in (LADDHYBRID) is no KType TecDoc knows is not
        one: a 530e resolved to the 520i, a RAV4 PHEV to the full hybrid. Nor is a
        car registered as a non-plug-in hybrid (ELHYBRID, or a word such as eTSI) a
        plug-in KType: a Terramar with 13 kW of electric power, a mild hybrid,
        resolved to the eHybrid. That direction counts only where the KType's own
        family has a full or mild hybrid the car could be instead: every i8 KType
        is a plug-in, and TecDoc files the 2011 Panamera S Hybrid, no plug-in, as
        one. The plug-in direction is not narrowed, since 330e cars resolved to the
        330i of the 3 Touring (F31), a family with no plug-in KType. A KType whose
        electrification is unknown (no engine type loaded) never conflicts. Known
        cost: a car the registry wrongly calls a plug-in (a Lexus CT200h) goes to
        review.
        """

        electrification = self._electrification.get(top.candidate_reference)
        if electrification is None:
            return False
        if registered == "plug_in_hybrid":
            return electrification not in PLUG_IN_ELECTRIFICATION
        return (
            registered == "hybrid"
            and electrification == "plug_in_hybrid"
            and (top.manufacturer, top.model) in self._hybrid_families
        )


def _only_by_export_name(value: str, top: FuzzyCandidateMatch) -> bool:
    """A model value that names the KType's family by a reviewed export name alone.

    True for "Duett" on "P 210 DUETT (P211, P212)" and "SL" on "PAGODE (W113)";
    false when the value is also the family's own name or one of its TecDoc aliases.
    """

    maker = top.manufacturer.strip().upper()
    family = _CHASSIS_SUFFIX.sub("", top.model).strip().upper()
    return any(
        same_model_text(value, name) for name in REVIEWED_EXPORT_NAMES.get((maker, family), ())
    ) and not any(same_model_text(value, name) for name in (top.model, *tecdoc_model_aliases(top.model)))


def _engine_confirms(query: ResolvedMatchQuery, match_result: Any) -> bool:
    """The car's own engine code matched the selected KType's engine exactly."""

    if not query.engine_code_observed or not match_result.candidates:
        return False
    return "engine_code" in match_result.candidates[0].matched_fields


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


def _build_month(normalized: Mapping[str, Any], year: int | None) -> int | None:
    """The car's build month as YYYYMM: the vehicle's own month, else normalization's.

    Only a month the registry states counts; a year alone gives no month.
    """

    month = _integer(normalized.get("production_month"))
    if year is not None and month is not None and 1 <= month <= 12:
        return year * 100 + month
    if str(normalized.get("production_date_precision") or "") not in {"month", "day"}:
        return None
    text = str(normalized.get("production_date") or "")
    if len(text) < 7 or text[4] != "-" or not (text[:4] + text[5:7]).isdigit():
        return None
    value = int(text[:4]) * 100 + int(text[5:7])
    return value if 1 <= value % 100 <= 12 else None


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
