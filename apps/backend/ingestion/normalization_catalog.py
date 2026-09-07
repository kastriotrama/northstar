"""Every transform the normalization pipeline applies, in one inspectable list.

The Rules page reads ``translation_dictionaries``, so only the rules defined
there were ever visible. The pipeline also carries lookup tables, regex
grammars and unit conversions written directly in Python, and those change a
normalized value with nothing in the catalog to show for it -- a hidden
transformer. This module renders each of them in the same shape as a
translation rule, and describes the pipeline stage that owns it, so one list
can hold all of them.

Nothing here re-implements a decision. Every entry reads the same object the
pipeline reads, so the listing cannot drift from behavior, and
``tests/unit/ingestion/test_normalization_catalog.py`` fails when a table,
grammar or transformer is added without being registered.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

from ingestion import normalization_rules as rules
from ingestion import text_canonicalization as text
from ingestion.translation_dictionaries import TranslationRuleSet

RuleOrigin = Literal["catalog", "code"]

#: Code rules are keyed by table prefix and source term, which is unique within
#: a table. The prefix keeps them from colliding with catalog IDs like MOD-001.
CODE_RULE_PREFIX = "CODE"

#: Reviewer-authored projection rules. They are not produced here -- they live in the
#: database -- but the prefix belongs with the other one so the ID namespaces of the
#: rule list are declared in a single place and cannot collide.
RESOLUTION_RULE_PREFIX = "RES"


@dataclass(frozen=True)
class EmbeddedRule:
    """One hardcoded mapping, shaped like a translation rule so it can be listed."""

    rule_id: str
    area: str
    transformer_id: str
    source_fields: tuple[str, ...]
    source_terms: tuple[str, ...]
    canonical_field: str
    canonical_value: str | None
    display_value: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class TransformerStage:
    """One pipeline stage, with everything it reads and everything it writes."""

    transformer_id: str
    order: int
    default_rule_id: str
    summary: str
    source_fields: tuple[str, ...]
    writes: tuple[str, ...]
    rule_areas: tuple[str, ...]
    code_areas: tuple[str, ...]
    catalog_rule_count: int
    code_rule_count: int
    review_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class _TableSpec:
    """How to render one hardcoded lookup as rules."""

    name: str
    area: str
    transformer_id: str
    prefix: str
    source_fields: tuple[str, ...]
    canonical_field: str
    notes: str
    render: Callable[[Any], tuple[str, str | None, str | None]] = field(
        default=lambda value: (str(value), None, None)
    )


def _plain(value: Any) -> tuple[str, str | None, str | None]:
    """A term that maps straight to one canonical value."""

    return (str(value), None, None)


def _identity(_: Any) -> tuple[str, str | None, str | None]:
    """A membership set: the term is the whole rule, the value is fixed."""

    return ("", None, None)


_TABLES: tuple[_TableSpec, ...] = (
    _TableSpec(
        name="_MANUFACTURER_ALIASES",
        area="manufacturer_alias",
        transformer_id="ts.manufacturer",
        prefix="MFA",
        source_fields=("manufacturer", "brand", "base_manufacturer"),
        canonical_field="manufacturer",
        notes="Registry spelling of a manufacturer resolved to one canonical name.",
    ),
    _TableSpec(
        name="_EVIDENCE_ONLY_MANUFACTURER_ALIASES",
        area="manufacturer_alias_evidence_only",
        transformer_id="ts.manufacturer",
        prefix="EVA",
        source_fields=("manufacturer", "brand"),
        canonical_field="manufacturer_evidence",
        notes=(
            "Resolved for evidence comparison only. It never sets manufacturer on its own."
        ),
    ),
    _TableSpec(
        name="_CONVERTER_ALIASES",
        area="converter_alias",
        transformer_id="ts.manufacturer",
        prefix="CVA",
        source_fields=("manufacturer", "brand"),
        canonical_field="builder_converter_names",
        notes="Bodybuilder or converter recognized by name; the base manufacturer is kept.",
        render=lambda value: (str(value[0]), None, f"cites catalog rule {value[1]}"),
    ),
    _TableSpec(
        name="_REVIEWED_LEGACY_BRAND_ENTITIES",
        area="legacy_brand_entity",
        transformer_id="ts.manufacturer",
        prefix="LBE",
        source_fields=("brand",),
        canonical_field="manufacturer",
        notes=(
            "Exact legacy Brand text reviewed by hand. These rows carry no Tillverkare and "
            "Brand mixes make with model, so only the whole string may match."
        ),
    ),
    _TableSpec(
        name="_REVIEWED_EXACT_BRAND_REPAIRS",
        area="brand_repair",
        transformer_id="ts.manufacturer",
        prefix="BRP",
        source_fields=("brand",),
        canonical_field="manufacturer",
        notes="Exact brand string corrected to the manufacturer it names.",
    ),
    _TableSpec(
        name="_CORPORATE_GROUP_MARKERS",
        area="corporate_group_marker",
        transformer_id="ts.manufacturer",
        prefix="CGM",
        source_fields=("manufacturer", "brand"),
        canonical_field="manufacturer_role",
        notes=(
            "A name containing this marker is a corporate group, not a marque. The record "
            "goes to review rather than resolving to a manufacturer."
        ),
        render=_identity,
    ),
    _TableSpec(
        name="_MODEL_MANUFACTURERS",
        area="model_manufacturer",
        transformer_id="ts.manufacturer",
        prefix="MDM",
        source_fields=("model",),
        canonical_field="manufacturer",
        notes="Model name that identifies its manufacturer when the brand field cannot.",
    ),
    _TableSpec(
        name="_VIN_WMI_MANUFACTURERS",
        area="vin_wmi",
        transformer_id="ts.manufacturer",
        prefix="WMI",
        source_fields=("vin",),
        canonical_field="vin_manufacturing_entity",
        notes="World manufacturer identifier, the first three VIN characters.",
    ),
    _TableSpec(
        name="_FAB_CODE_MANUFACTURERS",
        area="fab_code",
        transformer_id="ts.manufacturer",
        prefix="FAB",
        source_fields=("fab_code",),
        canonical_field="manufacturer",
        notes="Registry fabrication code resolved to a manufacturer.",
    ),
    _TableSpec(
        name="_MARKETED_PARENT_CHILDREN",
        area="marketed_parent",
        transformer_id="ts.manufacturer",
        prefix="MPC",
        source_fields=("manufacturer", "brand"),
        canonical_field="sub_brand",
        notes="Marques a parent group may resolve to. Anything else stays unresolved.",
        render=lambda value: (", ".join(sorted(value)), None, None),
    ),
    _TableSpec(
        name="_PRIMARY_SPECIAL_PURPOSE_CODES",
        area="special_purpose_primary",
        transformer_id="ts.bodywork",
        prefix="SPP",
        source_fields=("body_code",),
        canonical_field="special_purpose_type",
        notes="Primary body code that states a special purpose rather than a body shape.",
        render=lambda value: (str(value[0]), str(value[1]), None),
    ),
    _TableSpec(
        name="_SECONDARY_PURPOSE_CODES",
        area="special_purpose_secondary",
        transformer_id="ts.bodywork",
        prefix="SPS",
        source_fields=("body_code2", "body_code_extra"),
        canonical_field="special_purpose_type",
        notes="Secondary body code. The field it writes is stated per code.",
        render=lambda value: (str(value[1]), str(value[2]), f"writes {value[0]}"),
    ),
    _TableSpec(
        name="_TEXT_CODE_DEFINITIONS",
        area="text_code",
        transformer_id="ts.special-vehicle",
        prefix="TXC",
        source_fields=("text_code", "text_codes"),
        canonical_field="modification_types",
        notes="Registry text code and the modification it records.",
        render=lambda value: (str(value[2]), str(value[1]), f"Swedish: {value[0]}"),
    ),
    _TableSpec(
        name="_TEXT_CODE_FLAGS",
        area="text_code_flag",
        transformer_id="ts.special-vehicle",
        prefix="TXF",
        source_fields=("text_code", "text_codes"),
        canonical_field="special_vehicle_flags",
        notes="Text code that raises a special-vehicle flag.",
    ),
    _TableSpec(
        name="_SPECIAL_MODIFIED_TEXT_CODES",
        area="special_modified_code",
        transformer_id="ts.special-vehicle",
        prefix="SMC",
        source_fields=("text_code", "text_codes"),
        canonical_field="vehicle_classification",
        notes=(
            "Marks the vehicle special_modified, which excludes it from parts matching "
            "and from TecDoc."
        ),
        render=_identity,
    ),
    _TableSpec(
        name="_SPECIAL_BODY_CODE_FLAGS",
        area="body_code_flag",
        transformer_id="ts.special-vehicle",
        prefix="SBF",
        source_fields=("body_code", "body_code2", "body_code_extra"),
        canonical_field="special_vehicle_flags",
        notes="Body code that raises a special-vehicle flag.",
    ),
    _TableSpec(
        name="_MOTORHOME_MARQUE_FAB_CODES",
        area="motorhome_fab_code",
        transformer_id="ts.special-vehicle",
        prefix="MHF",
        source_fields=("fab_code",),
        canonical_field="record_route",
        notes="Corroborates a motorhome marque, which routes the record out of passenger cars.",
        render=_identity,
    ),
    _TableSpec(
        name="_ENGINE_QUALIFIER_PHRASES",
        area="engine_qualifier",
        transformer_id="ts.model-family",
        prefix="EQP",
        source_fields=("model", "variant", "version", "type"),
        canonical_field="engine_badge",
        notes="Multi-word phrase that belongs to the engine badge rather than the model.",
        render=_identity,
    ),
    _TableSpec(
        name="_ENGINE_QUALIFIER_WORDS",
        area="engine_qualifier",
        transformer_id="ts.model-family",
        prefix="EQW",
        source_fields=("model", "variant", "version", "type"),
        canonical_field="engine_badge",
        notes="Single token that continues an engine badge instead of ending it.",
        render=_identity,
    ),
    _TableSpec(
        name="_TYRE_CONSTRUCTION",
        area="tyre_construction",
        transformer_id="ts.tyres",
        prefix="TYC",
        source_fields=("tyre_front", "tyre_rear"),
        canonical_field="construction",
        notes="Construction letter inside a tyre size.",
    ),
    _TableSpec(
        name="_HYBRID_COMBINATION_TOKENS",
        area="fuel_match_token",
        transformer_id="ts.fuel",
        prefix="HCT",
        source_fields=("fuel1", "fuel2", "fuel3", "fuel_combo"),
        canonical_field="fuel_match_tokens",
        notes=(
            "The registry records a hybrid as separate carriers. This publishes the combined "
            "token a TecDoc KType can be compared against."
        ),
        render=lambda value: (str(value[1]), None, f"when {value[0]} appears with electricity"),
    ),
)

#: ``_MEASUREMENTS`` is rendered on its own because a scale factor is not a
#: canonical value: the rule is the arithmetic, not a mapping.
_MEASUREMENT_TRANSFORMER = "ts.measurements"

#: Regex grammars. A pattern is the rule, so the pattern itself is shown.
@dataclass(frozen=True)
class _GrammarSpec:
    rule_id: str
    transformer_id: str
    constant: str | None
    source_fields: tuple[str, ...]
    writes: str
    summary: str


_GRAMMARS: tuple[_GrammarSpec, ...] = (
    _GrammarSpec(
        rule_id="TYRE-METRIC",
        transformer_id="ts.tyres",
        constant="_TYRE_METRIC",
        source_fields=("tyre_front", "tyre_rear"),
        writes="tyre_front, tyre_rear",
        summary=(
            "Modern metric size. Yields width, aspect, construction, rim, load index and "
            "speed symbol. C is a commercial casing, not part of the rim diameter."
        ),
    ),
    _GrammarSpec(
        rule_id="TYRE-ALPHA",
        transformer_id="ts.tyres",
        constant="_TYRE_ALPHA",
        source_fields=("tyre_front", "tyre_rear"),
        writes="tyre_front, tyre_rear",
        summary=(
            "Pre-1980 alpha size such as 175SR14: the speed symbol sits inside and there is "
            "no aspect ratio. A missing aspect ratio stays missing."
        ),
    ),
    _GrammarSpec(
        rule_id="TYRE-IMPERIAL",
        transformer_id="ts.tyres",
        constant="_TYRE_IMPERIAL",
        source_fields=("tyre_front", "tyre_rear"),
        writes="tyre_front, tyre_rear",
        summary="Imperial size such as 5.60-15, where the section width is in inches.",
    ),
    _GrammarSpec(
        rule_id="TYRE-DASH",
        transformer_id="ts.tyres",
        constant="_TYRE_DASH",
        source_fields=("tyre_front", "tyre_rear"),
        writes="tyre_front, tyre_rear",
        summary="Dashed metric size such as 175-15, read as bias construction.",
    ),
    _GrammarSpec(
        rule_id="TYPE-APPROVAL",
        transformer_id="ts.type-approval",
        constant="_TYPE_APPROVAL",
        source_fields=("eeg_type_approval",),
        writes=(
            "type_approval_base, type_approval_directive, type_approval_number, "
            "type_approval_extension"
        ),
        summary=(
            "Splits the EC type approval into country, directive, number and extension. Text "
            "that does not match is kept as a candidate with "
            "type_approval_format_unrecognized."
        ),
    ),
    _GrammarSpec(
        rule_id="ENGINE-BADGE",
        transformer_id="ts.model-family",
        constant="_BADGE_CODE",
        source_fields=("model", "variant", "version", "type"),
        writes="engine_badge",
        summary=(
            "A token joins the badge when it carries a number. A purely alphabetic word such "
            "as AVANT or CROSS ends it."
        ),
    ),
    _GrammarSpec(
        rule_id="ENGINE-BADGE-GLUED-DRIVE",
        transformer_id="ts.model-family",
        constant="_GLUED_DRIVE_BADGE",
        source_fields=("model", "variant", "version", "type"),
        writes="engine_badge",
        summary=(
            "BMW glues the drivetrain to the badge, as in xDrive30d. The badge is recovered "
            "from inside the glued token."
        ),
    ),
    _GrammarSpec(
        rule_id="MOTORHOME-MARQUE",
        transformer_id="ts.special-vehicle",
        constant="_MOTORHOME_REGISTERED_MARQUE",
        source_fields=("brand",),
        writes="record_route",
        summary=(
            "Registered motorhome marque. With corroborating evidence the record is routed "
            "out of the passenger car dataset."
        ),
    ),
    _GrammarSpec(
        rule_id="SPECIAL-MODIFIED-TEXT",
        transformer_id="ts.special-vehicle",
        constant=None,
        source_fields=("brand", "model"),
        writes="vehicle_classification",
        summary=(
            "Brand or model text naming a home-built, amateur-built or replica vehicle. "
            "Written inline in _apply_special_vehicle_classification."
        ),
    ),
    _GrammarSpec(
        rule_id="TERM-NON-WORD",
        transformer_id="ts.manufacturer",
        constant="_NON_WORD",
        source_fields=("manufacturer", "brand", "model"),
        writes="manufacturer",
        summary=(
            "Reduces a term to comparable tokens before any manufacturer lookup. Every alias "
            "table above is matched against this form, not against the raw text."
        ),
    ),
)

#: Text canonicalization runs first and rewrites the working copy every later
#: stage reads, so its four rules belong in the same list.
@dataclass(frozen=True)
class _TextRuleSpec:
    rule_id: str
    summary: str
    source_fields: tuple[str, ...]


_TEXT_RULES: tuple[_TextRuleSpec, ...] = (
    _TextRuleSpec(
        rule_id="TXT-NFKC-V1",
        summary="Unicode NFKC normalization.",
        source_fields=tuple(sorted(text.TEXT_FIELDS)),
    ),
    _TextRuleSpec(
        rule_id="TXT-WHITESPACE-V1",
        summary="Runs of whitespace collapsed to one space, then trimmed.",
        source_fields=tuple(sorted(text.TEXT_FIELDS)),
    ),
    _TextRuleSpec(
        rule_id="TXT-PUNCT-SAFE-V1",
        summary="Typographic dashes and quotes replaced with ASCII equivalents.",
        source_fields=tuple(sorted(text.NAME_FIELDS)),
    ),
    _TextRuleSpec(
        rule_id="TXT-CASE-CODE-V1",
        summary="Code fields upper-cased.",
        source_fields=tuple(sorted(text.CODE_FIELDS)),
    ),
)

#: Every text rule rewrites the working copy rather than producing a normalized
#: field, so they share one output name.
_TEXT_RULE_OUTPUT = "canonical text"


@dataclass(frozen=True)
class _StageSpec:
    """What a pipeline stage does, beyond what the transformer object states."""

    summary: str
    rule_areas: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    review_reasons: tuple[str, ...] = ()


_STAGES: Mapping[str, _StageSpec] = {
    "ts.text-canonicalization": _StageSpec(
        summary=(
            "Rewrites the working copy of allow-listed text fields. Every later stage matches "
            "against this form, never against the raw record, which is kept intact."
        ),
        writes=(_TEXT_RULE_OUTPUT,),
    ),
    "ts.initialize": _StageSpec(
        summary="Records the market and which alias types the row carries.",
        writes=("alias_types_present", "market"),
    ),
    "ts.special-vehicle": _StageSpec(
        summary=(
            "Classifies amateur-built, rebuilt, test, police, ambulance and motorhome records, "
            "and decides whether the vehicle may be matched to parts at all."
        ),
        writes=(
            "classification_source",
            "manufacturer_group",
            "modification_types",
            "parts_matching_eligible",
            "parts_matching_exclusion_reason",
            "parts_matching_policy",
            "record_route",
            "registry_body_codes",
            "special_vehicle_flags",
            "tecdoc_match_policy",
            "text_codes",
            "vehicle_classification",
        ),
    ),
    "ts.manufacturer": _StageSpec(
        summary=(
            "Resolves the manufacturer from brand, Tillverkare, model, VIN and fab code, and "
            "separates a bodybuilder or corporate group from the marque that built the vehicle."
        ),
        writes=(
            "base_manufacturer",
            "base_model",
            "base_vehicle_manufacturer",
            "builder_converter_names",
            "legal_manufacturer",
            "manufacturer",
            "manufacturer_evidence",
            "manufacturer_group",
            "manufacturer_role",
            "manufacturer_source_repair",
            "model",
            "model_family",
            "parts_matching_eligible",
            "parts_matching_policy",
            "registered_make",
            "special_purpose_type",
            "sub_brand",
            "vin_manufacturing_entity",
        ),
        review_reasons=(
            "manufacturer_unknown",
            "manufacturer_missing",
            "manufacturer_corporate_group_unresolved",
        ),
    ),
    "ts.model-family": _StageSpec(
        summary=(
            "Matches the registry model text to a reviewed model family and splits the engine "
            "badge off the model name."
        ),
        rule_areas=("model_family",),
        writes=("engine_badge", "model_family_candidate"),
    ),
    "ts.type-approval": _StageSpec(
        summary="Decomposes the EC type approval and resolves its issuing country.",
        rule_areas=("type_approval_country",),
        writes=(
            "type_approval",
            "type_approval_base",
            "type_approval_directive",
            "type_approval_extension",
            "type_approval_number",
        ),
        review_reasons=("type_approval_format_unrecognized", "type_approval_country_unknown"),
    ),
    "ts.tyres": _StageSpec(
        summary=(
            "Decomposes the per-axle tyre size into width, aspect, construction, rim diameter, "
            "load index and speed symbol, and detects a staggered fitment."
        ),
        writes=("rim_diameter_in", "tyre_front", "tyre_rear", "tyre_staggered"),
        review_reasons=("tyre_size_unrecognized",),
    ),
    "ts.measurements": _StageSpec(
        summary=(
            "Converts the registry's scaled integers into stated units. Scaling differs per "
            "field, so each one is declared rather than inferred."
        ),
    ),
    "ts.emission-class": _StageSpec(
        summary="Resolves the registry emission class to a Euro standard.",
        rule_areas=("emission_class",),
    ),
    "ts.dates": _StageSpec(
        summary=(
            "Parses registration, build and production dates, keeping the precision each one "
            "was written with."
        ),
        writes=(
            "production_date",
            "production_date_precision",
            "production_from",
            "production_from_precision",
            "production_to",
            "production_to_precision",
            "production_year",
            "production_year_from",
            "production_year_to",
            "registration_date",
        ),
    ),
    "ts.engine-measurements": _StageSpec(
        summary="Extracts engine codes, power and displacement, recording the source unit.",
        writes=(
            "displacement_cc",
            "displacement_source_unit",
            "engine_code",
            "engine_family_code",
            "engine_family_name",
            "power_kw",
            "power_source_unit",
        ),
    ),
    "ts.transmission": _StageSpec(
        summary="Resolves the gearbox code, and marketing transmission names as candidates.",
        rule_areas=("transmission_code", "transmission_marketing"),
        writes=("transmission_display", "transmission_name"),
    ),
    "ts.bodywork": _StageSpec(
        summary=(
            "Resolves the registry body code to a bodywork form, and separates a special "
            "purpose code from a body shape."
        ),
        rule_areas=("bodywork_code", "bodywork_marketing"),
        writes=(
            "bodywork_registry_code",
            "bodywork_registry_label_sv",
            "bodywork_source",
            "marketing_body_style",
            "secondary_registry_body_code",
            "secondary_registry_body_label_sv",
            "special_purpose_type",
        ),
    ),
    "ts.drive": _StageSpec(
        summary=(
            "Reads the registry all-wheel-drive flag and compares it with marketing drive "
            "names, sending a contradiction to review rather than picking one."
        ),
        rule_areas=("drive_flag", "drive_marketing"),
        review_reasons=("drive_registry_marketing_conflict", "is_4wd_malformed"),
    ),
    "ts.fuel": _StageSpec(
        summary=(
            "Resolves fuel carriers and electrification, and publishes the tokens a TecDoc "
            "KType is compared against."
        ),
        rule_areas=(
            "electrification",
            "electrification_marketing",
            "fuel_carrier",
            "fuel_combination",
        ),
        writes=("energy_sources", "fuel_match_tokens"),
    ),
    "ts.reviewed-record-policy": _StageSpec(
        summary=(
            "Applies reviewed record corrections activated from the review workbench. These "
            "are stored decisions, not code, and run last so they can override any stage."
        ),
        writes=("fuel_match_tokens",),
    ),
}


def _table(name: str) -> Any:
    return getattr(rules, name)


def _iter_terms(table: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(table, Mapping):
        yield from sorted(table.items(), key=lambda item: str(item[0]))
        return
    for term in sorted(table, key=str):
        yield (term if isinstance(term, str) else term[0], term)


def _pattern_text(name: str | None) -> str:
    if name is None:
        return "inline regular expression"
    pattern = getattr(rules, name)
    return pattern.pattern if isinstance(pattern, re.Pattern) else str(pattern)


@lru_cache(maxsize=1)
def code_rules() -> tuple[EmbeddedRule, ...]:
    """Every mapping, grammar and conversion the pipeline holds in Python."""

    entries: list[EmbeddedRule] = []
    for spec in _TABLES:
        for term, value in _iter_terms(_table(spec.name)):
            canonical, display, note = spec.render(value)
            entries.append(
                EmbeddedRule(
                    rule_id=f"{CODE_RULE_PREFIX}:{spec.prefix}:{term}",
                    area=spec.area,
                    transformer_id=spec.transformer_id,
                    source_fields=spec.source_fields,
                    source_terms=(str(term),),
                    canonical_field=spec.canonical_field,
                    canonical_value=canonical or None,
                    display_value=display,
                    notes=" ".join(part for part in (spec.notes, note) if part),
                )
            )
    for source_field, target_field, multiplier, rounded in rules._MEASUREMENTS:
        entries.append(
            EmbeddedRule(
                rule_id=f"{CODE_RULE_PREFIX}:MSR:{source_field}",
                area="measurement_scale",
                transformer_id=_MEASUREMENT_TRANSFORMER,
                source_fields=(source_field,),
                source_terms=(source_field,),
                canonical_field=target_field,
                canonical_value=f"x {multiplier:g}",
                display_value=f"{source_field} x {multiplier:g} -> {target_field}",
                notes=(
                    "Rounded to a whole number." if rounded else "Kept to one decimal."
                )
                + " Reading this field with another field's scale is a tenfold error.",
            )
        )
    for grammar in _GRAMMARS:
        entries.append(
            EmbeddedRule(
                rule_id=f"{CODE_RULE_PREFIX}:GRM:{grammar.rule_id}",
                area="grammar",
                transformer_id=grammar.transformer_id,
                source_fields=grammar.source_fields,
                source_terms=(_pattern_text(grammar.constant),),
                canonical_field=grammar.writes,
                canonical_value=None,
                notes=grammar.summary,
            )
        )
    for text_rule in _TEXT_RULES:
        entries.append(
            EmbeddedRule(
                rule_id=f"{CODE_RULE_PREFIX}:TXT:{text_rule.rule_id}",
                area="text_canonicalization",
                transformer_id="ts.text-canonicalization",
                source_fields=text_rule.source_fields,
                source_terms=(),
                canonical_field=_TEXT_RULE_OUTPUT,
                canonical_value=None,
                notes=text_rule.summary,
            )
        )
    return tuple(entries)


@lru_cache(maxsize=1)
def _code_rules_by_transformer() -> Mapping[str, tuple[EmbeddedRule, ...]]:
    grouped: dict[str, list[EmbeddedRule]] = {}
    for entry in code_rules():
        grouped.setdefault(entry.transformer_id, []).append(entry)
    return {key: tuple(value) for key, value in grouped.items()}


def transformer_stages(rule_set: TranslationRuleSet) -> tuple[TransformerStage, ...]:
    """Every pipeline stage in execution order, with what it reads and writes."""

    grouped = _code_rules_by_transformer()
    stages: list[TransformerStage] = []
    for transformer in rules.DEFAULT_PIPELINE.transformers:
        transformer_id = transformer.transformer_id
        spec = _STAGES.get(transformer_id)
        if spec is None:
            raise KeyError(
                f"pipeline transformer {transformer_id!r} has no catalog entry; "
                "add one to _STAGES so it cannot change records invisibly"
            )
        owned = grouped.get(transformer_id, ())
        catalog_rules = [rule for rule in rule_set.rules if rule.area in spec.rule_areas]
        writes = set(spec.writes)
        writes.update(rule.canonical_field for rule in catalog_rules)
        writes.update(entry.canonical_field for entry in owned if entry.canonical_value)
        stages.append(
            TransformerStage(
                transformer_id=transformer_id,
                order=transformer.order,
                default_rule_id=getattr(transformer, "default_rule_id", transformer_id),
                summary=spec.summary,
                source_fields=tuple(getattr(transformer, "source_fields", ()) or ()),
                writes=tuple(sorted(writes)),
                rule_areas=spec.rule_areas,
                code_areas=tuple(sorted({entry.area for entry in owned})),
                catalog_rule_count=len(catalog_rules),
                code_rule_count=len(owned),
                review_reasons=spec.review_reasons,
            )
        )
    return tuple(stages)
