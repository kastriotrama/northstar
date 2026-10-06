"""Learned enrichment rules: "every vehicle with this key has this value".

Two kinds, both learned from `core.vehicles` itself and stored as rows of
`core.vehicle_enrichment_rules`:

- *Enrichment* rules learn a value TS never had from the vehicles a provider
  stated it for, and fill it in where no provider did. The engine code is the
  main one: within one make + variant + version the AIS engine code agrees on
  98.1% of cars, so a TS car the AIS export does not cover -- a future TS import,
  a car AIS left blank -- can still get one.
- *Completion* rules learn TS-only fields from TS vehicles, keyed by make + group
  code, for the vehicles the AIS export adds that TS never had: their records
  lack an EU category, a displacement, a variant, and the normalizer needs them.
- *Model* rules fill the model family normalization left empty -- 2.37M passenger
  cars whose registry text names only the make ("VOLVO") or a manufacturer code.
  Learned from the TS vehicles that have one, keyed by variant + version, by the
  VIN's manufacturer and descriptor section, by type code, by variant, by the
  registry brand text or by its word after the make. On a 10% holdout of vehicles
  with a known model these keys predict it 99.77-99.99% right.

A rule is kept only when it is common and unanimous enough (by default at least 5
vehicles and 95% agreement). A rule never overrides a value a source stated; it
fills gaps, marks what it filled (`rule:<rule_id>`), and retiring it takes back
exactly what it filled.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from typing import Literal, Protocol

from psycopg import Connection

from ingestion.vehicle_core_fields import (
    FIELDS_BY_NAME,
    REGISTRY_EVIDENCE_COLUMNS,
    SOURCE_AIS,
    SOURCE_RULE,
    SOURCE_TS,
    SourceRef,
)
from ingestion.vehicle_core_merge import retract
from ingestion.vehicle_core_migrations import VEHICLE_ENRICHMENT_RULES_TABLE, VEHICLES_TABLE
from ingestion.vehicle_core_store import (
    LedgerRow,
    ledger_event_id,
    load_vehicles,
    record_ledger_rows,
    save_vehicles,
)
from ingestion.vehicle_drive_layouts import drive_layout, drive_variant
from ingestion.vehicle_model_patterns import (
    MISREAD_STATED_FAMILIES,
    REVIEWED_MODEL_NAMES,
    REVIEWED_NON_FAMILIES,
    model_text_family,
    pattern_model,
    preferred_spellings,
)

Purpose = Literal["enrichment", "completion"]
#: What a pattern-proposed rule was learned from: the reviewed patterns, not a source.
PATTERN_SOURCE = "reviewed-pattern"
#: The statistical family keyed like the patterns; a key it learns needs no pattern.
STATISTICS_FAMILY_OF_PATTERNS = "MOD-BT"
#: The family that learns which brand-text words name which make.
MAKE_WORD_FAMILY = "MFR-BW"

#: The family that lists the brand texts the registry writes for a make code.
BRAND_TEXT_FAMILY = "TSC-BT"

#: The family that states a two-wheel-drive car's driven axle from the reviewed table.
DRIVE_LAYOUT_FAMILY = "DRV-MY"
#: The family that states the drive type of a variant: a car the table and the cars
#: alike have left open, told apart by its power, fuel, body or registry text.
DRIVE_VARIANT_FAMILY = "DRV-CAR"

#: What the drive evidence families learn from: cars with a real drive type.
DRIVE_EVIDENCE = "cars-with-a-drive-type"
#: A fill never contradicts the registry's own four-wheel-drive statement: no
#: four-wheel drive on a car it marks as not four-wheel drive, and the reverse.
_DRIVE_GUARD = (
    "(v.registry_all_wheel_drive IS NULL OR v.registry_all_wheel_drive = (r.value = 'awd'))"
)
#: The evidence: a drive type that names the driven wheels, from any source except
#: these families' own fills, which would otherwise confirm themselves.
_DRIVE_EVIDENCE_FILTER = (
    "drive_type::text IN ('fwd', 'rwd', 'awd') "
    "AND left(coalesce(field_sources ->> 'drive_type', ''), 10) <> 'rule:DRV-E'"
)

DEFAULT_MIN_SUPPORT = 5
DEFAULT_MIN_AGREEMENT = 0.95


@dataclass(frozen=True)
class RuleFamily:
    family: str
    target_field: str
    key_fields: tuple[str, ...]
    learned_from: str
    purpose: Purpose
    description: str
    #: The family's own evidence bar; a caller's explicit threshold overrides it.
    min_support: int = DEFAULT_MIN_SUPPORT
    min_agreement: float = DEFAULT_MIN_AGREEMENT
    #: "statistics": learned from agreeing vehicles. "patterns": proposed by the
    #: reviewed patterns in `vehicle_model_patterns`, then checked against them.
    #: "reviewed": stated by a reviewed table (`vehicle_drive_layouts`), key by key.
    #: "drive_evidence": learned from the cars alike that carry a real drive type,
    #: whatever source gave it.
    #: "electrification": learned from the registry's own statement of whether a
    #: combustion car is a hybrid, a plug-in hybrid or neither.
    learner: Literal[
        "statistics", "patterns", "reviewed", "drive_evidence", "electrification"
    ] = "statistics"
    #: Values of the target that count as empty for this family: a generic value
    #: the family's own, more specific one replaces.
    replaces: tuple[str, ...] = ()
    #: A condition a fill must also meet, in SQL over the vehicle `v` and the rule `r`.
    guard: str = ""


#: Keys computed from a vehicle's columns rather than read from one. A VIN's first
#: eight characters are the manufacturer (WMI) and the descriptor section (VDS),
#: which names the model; only a full 17-character VIN has them in those places.
#
#: Registry brand text ("TOYOTA RAV4", "BMW 320I TOURING") names the model after
#: the make. Text that is only the make ("POLESTAR", "VOLVO") names no model: a
#: rule learned from it would give every later model of a one-model make that
#: make's first one, so such text yields no key. The token is the first word after
#: the make, unless it repeats the make ("TOYOTA TOYOTA RAV4"); a two-word make's
#: second word ("MERCEDES BENZ 200 D", "ALFA ROMEO 156") is part of the make. Volvo registered its
#: 1990s cars as "<model-year letter> + <model>" ("VOLVO S + V70", "VOLVO 9 + 940"):
#: there the token is the word after the plus.
_BRAND = "upper(btrim({alias}registry_brand_text))"
_AFTER_MAKE = (
    f"CASE WHEN strpos({_BRAND}, ' + ') > 0 "
    f"THEN split_part(btrim(split_part({_BRAND}, ' + ', 2)), ' ', 1) "
    f"ELSE split_part(regexp_replace({_BRAND}, '^[^ ]+ +((BENZ|ROMEO|ROVER|MARTIN|ROYCE) +)?', ''), "
    "' ', 1) END"
)
KEY_EXPRESSIONS: dict[str, str] = {
    "vin_descriptor": "CASE WHEN length({alias}vin) = 17 THEN left({alias}vin, 8) END",
    # The VIN's tenth character, its model year: a descriptor a model kept through a
    # rename ("XC40" became "EX40") names one model per year.
    "vin_year": "CASE WHEN length({alias}vin) = 17 THEN substr({alias}vin, 10, 1) END",
    "brand_text": f"CASE WHEN strpos({_BRAND}, ' ') > 0 THEN {_BRAND} END",
    "brand_token": (
        f"CASE WHEN strpos({_BRAND}, ' ') > 0 AND {_AFTER_MAKE} <> '' "
        f"AND {_AFTER_MAKE} <> split_part({_BRAND}, ' ', 1) THEN {_AFTER_MAKE} END"
    ),
    # The brand text's first word, the make as the registry writes it ("POLESTAR" in
    # "POLESTAR POLESTAR 4", "VOLKSWAGEN" in "VOLKSWAGEN, VW").
    "brand_make_word": f"NULLIF(substring({_BRAND} from '^[^ ,&/+]+'), '')",
    # A brand text the registry writes beside a model text: the make's own name
    # ("VOLVO", "VOLKSWAGEN, VW"). Older records put make and model in the brand
    # text ("VOLVO V70") and have no model text; those are not this key.
    "brand_beside_model": (
        "CASE WHEN btrim({alias}registry_model_text) <> '' "
        f"THEN {_BRAND} END"
    ),
    # The registry's own model text ("EX40", "FIAT TIPO"), as the model families read it.
    "model_text": (
        "CASE WHEN btrim({alias}registry_model_text) <> '' "
        "THEN upper(btrim({alias}registry_model_text)) END"
    ),
    # The drive layout's key. A missing model, year or fuel is a value of its own
    # ("-"), so a rule can state "this make, whatever the model". The key exists
    # only for a car the registry marks as not four-wheel drive.
    "drive_model": "coalesce({alias}model_family, '-')",
    "drive_year": "coalesce({alias}production_year::text, '-')",
    "drive_fuel": "coalesce({alias}fuel, '-')",
    "two_wheel_drive": "CASE WHEN {alias}registry_all_wheel_drive IS FALSE THEN 'yes' END",
    # The variant's key: everything a variant can be told apart by. The registry's
    # statement is part of it -- "no" (not four-wheel drive) or "unknown" -- and a
    # car it marks as four-wheel drive has no key.
    "drive_second_fuel": "coalesce({alias}fuel_secondary, '-')",
    "drive_power": "coalesce({alias}power_kw::text, '-')",
    "drive_body": "coalesce({alias}bodywork_form, '-')",
    "drive_text": (
        "coalesce(nullif(upper(btrim(coalesce(nullif(btrim({alias}registry_model_text), ''), "
        "{alias}registry_brand_text))), ''), '-')"
    ),
    "drive_statement": (
        "CASE WHEN {alias}registry_all_wheel_drive IS FALSE THEN 'no' "
        "WHEN {alias}registry_all_wheel_drive IS NULL THEN 'unknown' END"
    ),
}


def key_sql(field: str, alias: str = "") -> str:
    """The SQL for one key field, qualified by `alias` when given."""

    prefix = f"{alias}." if alias else ""
    expression = KEY_EXPRESSIONS.get(field)
    return f"({expression.format(alias=prefix)})" if expression else f"{prefix}{field}"


#: What the hybrid-type families learn from: the registry's petrol and diesel cars,
#: each a hybrid, a plug-in hybrid, or neither ("none": no hybrid type and no
#: second fuel). A car with electricity as second fuel and no stated type is
#: left out: the registry did not say which it is.
NOT_A_HYBRID = "none"
_ELECTRIFICATION_VALUE = f"coalesce(electrification_type, '{NOT_A_HYBRID}')"
_ELECTRIFICATION_TRAINING = (
    "fuel IN ('petrol', 'diesel') AND ("
    "electrification_type IN ('hybrid', 'plug_in_hybrid') "
    "OR (electrification_type IS NULL AND fuel_secondary IS NULL))"
)
#: Which cars a hybrid-type rule may fill. Only where the registry is silent: a car
#: AIS added, or a registry car with electricity as second fuel and no stated type
#: -- a registry car with neither is one the registry says is no hybrid. And a
#: plug-in only where the car's own fuels already include electricity.
_ELECTRIFICATION_GUARD = (
    "v.fuel IN ('petrol', 'diesel') "
    "AND (v.fuel_secondary IS NULL OR v.fuel_secondary = 'electricity') "
    f"AND (v.origin_source <> '{SOURCE_TS}' OR v.fuel_secondary = 'electricity') "
    "AND (r.value = 'hybrid' OR v.fuel_secondary = 'electricity')"
)
#: A battery electric car has no displacement, whatever its cars alike say.
_HAS_AN_ENGINE = "NOT (v.fuel = 'electricity' AND v.fuel_secondary IS NULL)"
#: What a hybrid type implies on a car that carries no second fuel: electricity as
#: that fuel, and the tokens a hybrid KType is compared on. Filled, and taken
#: back, together with the type.
IMPLIED_BY_HYBRID_TYPE: tuple[str, ...] = ("fuel_secondary", "fuel_match_tokens")

# Order matters within a target: a more specific key is tried first, so the engine
# code comes from variant + version before falling back to the group code.
RULE_FAMILIES: tuple[RuleFamily, ...] = (
    RuleFamily("ENG-VV", "engine_code", ("registry_make_code", "variant_code", "version_code"),
               SOURCE_AIS, "enrichment", "Engine code by make, variant and version"),
    RuleFamily("ENG-GC", "engine_code", ("registry_make_code", "group_code"),
               SOURCE_AIS, "enrichment", "Engine code by make and group code"),
    RuleFamily("ENG-TP", "engine_code",
               ("registry_make_code", "registry_type_code", "displacement_cc", "power_kw", "fuel"),
               SOURCE_AIS, "enrichment", "Engine code by make, type, displacement, power and fuel"),
    # Where the three above are silent, the cars alike by VIN or by model. An
    # engine code confirms a KType, so these are held to the higher bar.
    RuleFamily("ENG-VINP", "engine_code", ("manufacturer", "vin_descriptor", "power_kw", "fuel"),
               SOURCE_AIS, "enrichment",
               "Engine code by manufacturer, VIN characters 1-8, power and fuel",
               min_agreement=0.98),
    RuleFamily("ENG-MP", "engine_code",
               ("manufacturer", "model_family", "fuel", "power_kw", "displacement_cc",
                "production_year"),
               SOURCE_AIS, "enrichment",
               "Engine code by make, model, fuel, power, displacement and build year",
               min_agreement=0.98),
    # Displacement: 1.15M registry records of combustion cars state none, and a
    # car AIS added has one only where its group is known. It sets KTypes of one
    # model apart, so a car without it stays tied between them.
    RuleFamily("CCM-VV", "displacement_cc", ("registry_make_code", "variant_code", "version_code"),
               SOURCE_TS, "enrichment", "Displacement by make, variant and version",
               min_agreement=0.98, guard=_HAS_AN_ENGINE),
    RuleFamily("CCM-ENG", "displacement_cc", ("manufacturer", "engine_code", "power_kw"),
               SOURCE_TS, "enrichment", "Displacement by manufacturer, engine code and power",
               min_agreement=0.98, guard=_HAS_AN_ENGINE),
    RuleFamily("CCM-VINP", "displacement_cc", ("manufacturer", "vin_descriptor", "power_kw", "fuel"),
               SOURCE_TS, "enrichment",
               "Displacement by manufacturer, VIN characters 1-8, power and fuel",
               min_agreement=0.98, guard=_HAS_AN_ENGINE),
    RuleFamily("CCM-MP", "displacement_cc",
               ("manufacturer", "model_family", "fuel", "power_kw", "production_year"),
               SOURCE_TS, "enrichment", "Displacement by make, model, fuel, power and build year",
               min_agreement=0.98, guard=_HAS_AN_ENGINE),
    RuleFamily("MY-VB", "model_year",
               ("registry_make_code", "registry_vehicle_year", "production_year", "production_month"),
               SOURCE_AIS, "enrichment", "Model year by make, vehicle year and build month"),
    RuleFamily("MW-VV", "max_weight_kg", ("registry_make_code", "variant_code", "version_code"),
               SOURCE_AIS, "enrichment", "Max weight by make, variant and version"),
    RuleFamily("LEN-VV", "length_mm", ("registry_make_code", "variant_code", "version_code"),
               SOURCE_AIS, "enrichment", "Length by make, variant and version"),
    # The brand text's first word names the make where the make code does not: a
    # make the reviewed rules do not know by its code, or a code read wrongly (the
    # AIS import once cut "POL", Polestar, to "PO", Pontiac).
    RuleFamily("MFR-BW", "manufacturer", ("brand_make_word",),
               SOURCE_TS, "enrichment", "Manufacturer by the brand text's first word",
               min_support=10, min_agreement=0.98),
    # A model is identity, not a detail: the bar is higher than for the fields above.
    RuleFamily("MOD-VV", "model_family", ("registry_make_code", "variant_code", "version_code"),
               SOURCE_TS, "enrichment", "Model family by make, variant and version",
               min_support=10, min_agreement=0.98),
    RuleFamily("MOD-VIN", "model_family", ("manufacturer", "vin_descriptor"),
               SOURCE_TS, "enrichment", "Model family by manufacturer and VIN descriptor",
               min_support=10, min_agreement=0.98),
    # Sister models can share a VIN descriptor (Peugeot 3008 and 5008 are both
    # VR3KAHPY); their registered length still tells them apart.
    RuleFamily("MOD-VINL", "model_family", ("manufacturer", "vin_descriptor", "length_mm"),
               SOURCE_TS, "enrichment", "Model family by manufacturer, VIN descriptor and length",
               min_support=10, min_agreement=0.98),
    RuleFamily("MOD-VINY", "model_family", ("manufacturer", "vin_descriptor", "vin_year"),
               SOURCE_TS, "enrichment", "Model family by manufacturer, VIN descriptor and model year",
               min_support=10, min_agreement=0.98),
    RuleFamily("MOD-TP", "model_family", ("registry_make_code", "registry_type_code"),
               SOURCE_TS, "enrichment", "Model family by make and type code",
               min_support=10, min_agreement=0.98),
    RuleFamily("MOD-VAR", "model_family", ("registry_make_code", "variant_code"),
               SOURCE_TS, "enrichment", "Model family by make and variant",
               min_support=10, min_agreement=0.98),
    RuleFamily("MOD-BR", "model_family", ("registry_make_code", "brand_text"),
               SOURCE_TS, "enrichment", "Model family by make and registry brand text",
               min_support=10, min_agreement=0.98),
    RuleFamily("MOD-BT", "model_family", ("registry_make_code", "brand_token"),
               SOURCE_TS, "enrichment", "Model family by make and the brand text's word after the make",
               min_support=10, min_agreement=0.98),
    # Last: only for words no sibling taught, such as an old Volvo's type code.
    # The car's own model text, where it names a family TS uses or one reviewed
    # (`REVIEWED_MODEL_NAMES`) but no normalization rule maps it yet.
    RuleFamily("MOD-MT", "model_family", ("manufacturer", "model_text"),
               PATTERN_SOURCE, "enrichment", "Model family read from the registry model text",
               min_support=1, min_agreement=0.98, learner="patterns"),
    # The whole brand text read the same way, for AIS texts that repeat the make
    # ("TOYOTA TOYOTA YARIS CROSS") and so give no model word.
    RuleFamily("MOD-BRT", "model_family", ("manufacturer", "brand_text"),
               PATTERN_SOURCE, "enrichment", "Model family read from the registry brand text",
               min_support=1, min_agreement=0.98, learner="patterns"),
    RuleFamily("MOD-PAT", "model_family", ("registry_make_code", "brand_token"),
               PATTERN_SOURCE, "enrichment", "Model family read from the brand text by reviewed patterns",
               min_support=1, min_agreement=0.98, learner="patterns"),
    # Which axle a two-wheel-drive car drives is a property of its model. Stated by
    # the reviewed table, never learned; it also replaces the generic "2wd".
    RuleFamily(DRIVE_LAYOUT_FAMILY, "drive_type",
               ("manufacturer", "drive_model", "drive_year", "drive_fuel", "two_wheel_drive"),
               "reviewed-drive-layouts", "enrichment",
               "Drive type by make, model, build year and fuel, for cars the registry marks "
               "as not four-wheel drive",
               min_support=1, min_agreement=1.0, learner="reviewed", replaces=("2wd",)),
    # Next, the variant: a car the table leaves open, or one the registry makes no
    # four-wheel-drive statement about, named by what the car itself carries.
    RuleFamily(DRIVE_VARIANT_FAMILY, "drive_type",
               ("manufacturer", "drive_model", "drive_year", "drive_fuel", "drive_second_fuel",
                "drive_power", "drive_body", "drive_text", "drive_statement"),
               "reviewed-drive-variants", "enrichment",
               "Drive type of a variant, by power, fuel, body or registry text",
               min_support=1, min_agreement=1.0, learner="reviewed",
               replaces=("2wd",), guard=_DRIVE_GUARD),
    # Last, what is still open takes the drive type of the cars alike: the same
    # descriptor section of the VIN and the same power is the same variant. Most
    # specific key first; a key whose cars disagree states nothing and the next one
    # is asked. Reviewed knowledge goes first because the cars alike can share one
    # wrong registry statement.
    RuleFamily("DRV-EVP", "drive_type", ("manufacturer", "vin_descriptor", "power_kw"),
               DRIVE_EVIDENCE, "enrichment",
               "Drive type by manufacturer, VIN characters 1-8 and power",
               min_support=5, min_agreement=0.98, learner="drive_evidence",
               replaces=("2wd",), guard=_DRIVE_GUARD),
    RuleFamily("DRV-EME", "drive_type",
               ("manufacturer", "model_family", "fuel", "power_kw", "engine_code"),
               DRIVE_EVIDENCE, "enrichment",
               "Drive type by make, model, fuel, power and engine code",
               min_support=5, min_agreement=0.98, learner="drive_evidence",
               replaces=("2wd",), guard=_DRIVE_GUARD),
    # With the build year: a later generation reuses a model name and a power figure
    # for another drivetrain (a 132 kW V60 was a front-driven T4, then a plug-in hybrid).
    RuleFamily("DRV-EMP", "drive_type",
               ("manufacturer", "model_family", "fuel", "power_kw", "production_year"),
               DRIVE_EVIDENCE, "enrichment",
               "Drive type by make, model, fuel, power and build year",
               min_support=8, min_agreement=0.98, learner="drive_evidence",
               replaces=("2wd",), guard=_DRIVE_GUARD),
    RuleFamily("DRV-EV", "drive_type", ("manufacturer", "vin_descriptor"),
               DRIVE_EVIDENCE, "enrichment",
               "Drive type by manufacturer and VIN characters 1-8",
               min_support=8, min_agreement=0.98, learner="drive_evidence",
               replaces=("2wd",), guard=_DRIVE_GUARD),
    # Whether a petrol or diesel car is a hybrid. The registry says so in a field
    # of its own (ELHYBRID, LADDHYBRID); the AIS export has no such field, and it
    # gives a hybrid that does not charge from the grid no second fuel either, so a
    # RAV4 Hybrid AIS added reads as a plain petrol car. The registry's cars alike
    # say what it is. Most specific key first; a key whose cars disagree states
    # nothing, and a key whose cars are no hybrids has no rule.
    RuleFamily("ELT-GC", "electrification_type", ("registry_make_code", "group_code"),
               SOURCE_TS, "enrichment", "Hybrid type by make and group code",
               min_agreement=0.98, learner="electrification", guard=_ELECTRIFICATION_GUARD),
    RuleFamily("ELT-VINP", "electrification_type", ("manufacturer", "vin_descriptor", "power_kw"),
               SOURCE_TS, "enrichment", "Hybrid type by manufacturer, VIN characters 1-8 and power",
               min_agreement=0.98, learner="electrification", guard=_ELECTRIFICATION_GUARD),
    RuleFamily("ELT-ENG", "electrification_type",
               ("manufacturer", "model_family", "engine_code", "power_kw"),
               SOURCE_TS, "enrichment", "Hybrid type by make, model, engine code and power",
               min_agreement=0.98, learner="electrification", guard=_ELECTRIFICATION_GUARD),
    RuleFamily("ELT-VAR", "electrification_type", ("registry_make_code", "variant_code"),
               SOURCE_TS, "enrichment", "Hybrid type by make and variant",
               min_agreement=0.98, learner="electrification", guard=_ELECTRIFICATION_GUARD),
    # The AIS export names a car by the registry's brand text and model text in a
    # row ("VOLVO" + "EX30"). Where the car's group is new to us, the brand texts the
    # registry writes for the make say where the name divides. One rule per text.
    RuleFamily(BRAND_TEXT_FAMILY, "registry_brand_text",
               ("registry_make_code", "brand_beside_model"),
               SOURCE_TS, "completion",
               "Brand texts the registry writes beside a model text, by make code",
               min_support=20),
    RuleFamily("TSC-EU", "eu_category", ("registry_make_code", "group_code"),
               SOURCE_TS, "completion", "EU category by make and group code"),
    RuleFamily("TSC-BRAND", "registry_brand_text", ("registry_make_code", "group_code"),
               SOURCE_TS, "completion", "Registry brand text by make and group code"),
    RuleFamily("TSC-MODEL", "registry_model_text", ("registry_make_code", "group_code"),
               SOURCE_TS, "completion", "Registry model text by make and group code"),
    RuleFamily("TSC-TYPE", "registry_type_code", ("registry_make_code", "group_code"),
               SOURCE_TS, "completion", "Registry type code by make and group code"),
    RuleFamily("TSC-VAR", "variant_code", ("registry_make_code", "group_code"),
               SOURCE_TS, "completion", "Variant by make and group code"),
    RuleFamily("TSC-CCM", "displacement_cc", ("registry_make_code", "group_code"),
               SOURCE_TS, "completion", "Displacement by make and group code"),
    RuleFamily("TSC-4WD", "registry_all_wheel_drive", ("registry_make_code", "group_code"),
               SOURCE_TS, "completion", "Registry 4WD flag by make and group code"),
)
FAMILIES_BY_ID: dict[str, RuleFamily] = {family.family: family for family in RULE_FAMILIES}
COMPLETION_FAMILIES: tuple[RuleFamily, ...] = tuple(
    family for family in RULE_FAMILIES if family.purpose == "completion"
)

_SOURCE_OF = (
    "substring(coalesce(field_sources ->> '{field}', origin_source) from '^[a-z_]+')"
)


def rule_id_for(family: str, key_values: Sequence[str], value: str) -> str:
    """Derived from the rule's whole content, so an id always means one assertion."""

    digest = hashlib.sha256("\x1f".join((family, *key_values, "=", value)).encode()).hexdigest()
    return f"{family}-{digest[:16]}"


@dataclass(frozen=True)
class LearnedRule:
    rule_id: str
    family: str
    target_field: str
    key_fields: tuple[str, ...]
    key_values: tuple[str, ...]
    value: str
    support: int
    agreement: float


def learn_statement(family: RuleFamily) -> str:
    keys = ", ".join(
        f"{key_sql(field)}::text AS k{index}" for index, field in enumerate(family.key_fields)
    )
    key_names = ", ".join(f"k{index}" for index in range(len(family.key_fields)))
    not_null = " AND ".join(
        f"{key_sql(field)} IS NOT NULL" for field in (*family.key_fields, family.target_field)
    )
    source = _SOURCE_OF.format(field=family.target_field)
    # The drive evidence families take a value from whoever gave it; the others
    # learn from one source (the statement's first parameter).
    evidence = _DRIVE_EVIDENCE_FILTER if family.learner == "drive_evidence" else f"{source} = %s"
    value, stated = f"{family.target_field}::text", ""
    if family.learner == "electrification":
        # A car that is no hybrid is evidence too: it has no type to count, so
        # "none" stands for it here, and a key that comes out as "none" is no rule.
        not_null = " AND ".join(f"{key_sql(field)} IS NOT NULL" for field in family.key_fields)
        evidence = f"{evidence} AND {_ELECTRIFICATION_TRAINING}"
        value, stated = _ELECTRIFICATION_VALUE, f" AND value <> '{NOT_A_HYBRID}'"
    return f"""
        WITH training AS (
            SELECT {keys}, {value} AS value
            FROM {VEHICLES_TABLE}
            WHERE {not_null} AND {evidence}
        ),
        grouped AS (
            SELECT {key_names}, value, count(*) AS n FROM training GROUP BY {key_names}, value
        ),
        ranked AS (
            SELECT {key_names}, value, n,
                   sum(n) OVER (PARTITION BY {key_names}) AS total,
                   row_number() OVER (PARTITION BY {key_names} ORDER BY n DESC, value) AS rank
            FROM grouped
        )
        SELECT ARRAY[{key_names}], value, n::int, total::int
        FROM ranked
        WHERE rank = 1 AND total >= %s AND n >= %s * total{stated}
    """


def learn_rules(
    connection: Connection,
    family: RuleFamily,
    *,
    min_support: int | None = None,
    min_agreement: float | None = None,
) -> list[LearnedRule]:
    """Every key the family can state a value for, with the evidence behind it.

    Thresholds default to the family's own bar.
    """

    support = family.min_support if min_support is None else min_support
    agreement = family.min_agreement if min_agreement is None else min_agreement
    if family.learner == "patterns":
        return pattern_rules(connection, family, min_support=support, min_agreement=agreement)
    if family.learner == "reviewed":
        if family.family == DRIVE_VARIANT_FAMILY:
            return drive_variant_rules(connection, family)
        return drive_layout_rules(connection, family)
    parameters: tuple[object, ...] = (
        (support, agreement)
        if family.learner == "drive_evidence"
        else (family.learned_from, support, agreement)
    )
    with connection.cursor() as cursor:
        cursor.execute(learn_statement(family), parameters)
        rows = cursor.fetchall()
    return [
        LearnedRule(
            rule_id=rule_id_for(family.family, tuple(str(k) for k in keys), str(value)),
            family=family.family,
            target_field=family.target_field,
            key_fields=family.key_fields,
            key_values=tuple(str(value) for value in keys),
            value=str(value),
            support=int(total),
            agreement=round(int(n) / int(total), 4),
        )
        for keys, value, n, total in rows
    ]


def drive_layout_rules(connection: Connection, family: RuleFamily) -> list[LearnedRule]:
    """One rule per make, model, build year and fuel the reviewed table states a layout for.

    The keys are those present among the cars the registry marks as not
    four-wheel drive, whether or not they have a drive type yet, so the family's
    rules stay the same from run to run. Support is the number of such cars.
    """

    keys = ", ".join(f"{key_sql(field)}::text" for field in family.key_fields)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT manufacturer, model_family, production_year, fuel, ARRAY[{keys}], count(*) "
            f"FROM {VEHICLES_TABLE} "
            "WHERE registry_all_wheel_drive IS FALSE AND manufacturer IS NOT NULL "
            "GROUP BY 1, 2, 3, 4, 5"
        )
        rows = cursor.fetchall()
    rules = []
    for manufacturer, model, year, fuel, key_values, cars in rows:
        layout = drive_layout(manufacturer, model, year, fuel)
        if layout is None:
            continue
        values = tuple(str(value) for value in key_values)
        rules.append(
            LearnedRule(
                rule_id=rule_id_for(family.family, values, layout),
                family=family.family,
                target_field=family.target_field,
                key_fields=family.key_fields,
                key_values=values,
                value=layout,
                support=int(cars),
                agreement=1.0,
            )
        )
    return rules


def drive_variant_rules(connection: Connection, family: RuleFamily) -> list[LearnedRule]:
    """One rule per variant the reviewed knowledge states a drive type for.

    The keys are those of the cars still without a real drive type, and of the cars
    this family filled before, so its rules stay the same from run to run.
    """

    keys = ", ".join(f"{key_sql(field)}::text" for field in family.key_fields)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT manufacturer, model_family, production_year, fuel, fuel_secondary, power_kw, "
            f"bodywork_form, {key_sql('drive_text')}, registry_all_wheel_drive, ARRAY[{keys}], "
            f"count(*) FROM {VEHICLES_TABLE} "
            "WHERE registry_all_wheel_drive IS NOT TRUE AND manufacturer IS NOT NULL "
            "AND (drive_type IS NULL OR drive_type::text = ANY(%s) "
            "     OR left(coalesce(field_sources ->> 'drive_type', ''), %s) = %s) "
            "GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10",
            (list(family.replaces), len(f"rule:{family.family}-"), f"rule:{family.family}-"),
        )
        rows = cursor.fetchall()
    rules = []
    for make, model, year, fuel, second, power, body, text, four_wheel, key_values, cars in rows:
        drive = drive_variant(
            make, model, year, fuel, second, power, body, None if text == "-" else text, four_wheel
        )
        if drive is None:
            continue
        values = tuple(str(value) for value in key_values)
        rules.append(
            LearnedRule(
                rule_id=rule_id_for(family.family, values, drive),
                family=family.family,
                target_field=family.target_field,
                key_fields=family.key_fields,
                key_values=values,
                value=drive,
                support=int(cars),
                agreement=1.0,
            )
        )
    return rules


#: A make's model families as TS spells them: what a pattern may answer with. A
#: name on fewer vehicles than this is too rare to be trusted as vocabulary.
VOCABULARY_MIN_VEHICLES = 3
#: Known vehicles under a pattern key that may disagree with it: one stray car
#: ("307 Cc" among 307s), or the family's tolerance once there are many.
PATTERN_TOLERATED_DISAGREEMENTS = 1


def model_vocabulary(
    connection: Connection, target_field: str = "model_family", *, reviewed: bool = True
) -> dict[str, set[str]]:
    """Each make's model families as TS spells them, with the reviewed names
    (`REVIEWED_MODEL_NAMES`): what a pattern may answer, and what the model guard
    reads a car's own text by. `reviewed=False`: only the names TS itself states.
    A reviewed non-family (`REVIEWED_NON_FAMILIES`, Renault "B") is never one."""

    source = _SOURCE_OF.format(field=target_field)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT manufacturer, {target_field}, count(*)
            FROM {VEHICLES_TABLE}
            WHERE {target_field} IS NOT NULL AND manufacturer IS NOT NULL AND {source} = %s
            GROUP BY 1, 2 HAVING count(*) >= %s
            """,
            (SOURCE_TS, VOCABULARY_MIN_VEHICLES),
        )
        stated = [(str(make), str(model), int(count)) for make, model, count in cursor.fetchall()]
    return vocabulary_of(stated, reviewed=reviewed)


def vocabulary_of(
    stated: Iterable[tuple[str, str, int]], *, reviewed: bool = True
) -> dict[str, set[str]]:
    """The vocabulary from the (make, family, cars) TS states: one spelling per name,
    the reviewed names added when `reviewed`, no reviewed non-family."""

    # A reviewed name counts as used once, so TS's own spelling of it wins.
    reviewed_names = REVIEWED_MODEL_NAMES if reviewed else {}
    counts: dict[str, dict[str, int]] = {
        manufacturer: dict.fromkeys(names, 1) for manufacturer, names in reviewed_names.items()
    }
    for manufacturer, model, count in stated:
        if model not in REVIEWED_NON_FAMILIES.get(manufacturer, ()):
            counts.setdefault(manufacturer, {})[model] = count
    return {manufacturer: preferred_spellings(names) for manufacturer, names in counts.items()}


#: The registry text fields a stated sibling is matched by, as the guard names them.
_STATED_TEXT_COLUMNS = {"model": "registry_model_text", "brand": "registry_brand_text"}


def stated_text_families(connection: Connection) -> dict[tuple[str, str, str], dict[str, int]]:
    """The families TS states for the cars with each registry text, by make, field
    ("model", "brand") and the text in upper case: TS states Clio for all 60 cars
    with the brand text "RENAULT B". What the pattern families check a reading
    against (`pattern_rules`), for the model guard."""

    source = _SOURCE_OF.format(field="model_family")
    families: dict[tuple[str, str, str], dict[str, int]] = {}
    with connection.cursor() as cursor:
        for field_name, column in _STATED_TEXT_COLUMNS.items():
            cursor.execute(
                f"""
                SELECT manufacturer, upper(btrim({column})), model_family, count(*)
                FROM {VEHICLES_TABLE}
                WHERE model_family IS NOT NULL AND manufacturer IS NOT NULL
                  AND btrim({column}) <> '' AND {source} = %s
                GROUP BY 1, 2, 3
                """,
                (SOURCE_TS,),
            )
            for manufacturer, text, family, count in cursor.fetchall():
                key = (str(manufacturer), field_name, str(text))
                families.setdefault(key, {})[str(family)] = int(count)
    return families


def tolerated_disagreements(stated_total: int, min_agreement: float) -> int:
    """Known vehicles under a key that may disagree with a reading of it."""

    return max(PATTERN_TOLERATED_DISAGREEMENTS, int((1 - min_agreement) * stated_total))


#: The rule families keyed on the VIN's descriptor section, which the maker gives
#: each model: the key that best tells two sibling models apart.
VIN_KEY_FAMILIES = ("MOD-VIN", "MOD-VINL", "MOD-VINY")


def unanimous_vin_rule_eras(connection: Connection) -> dict[str, tuple[int, int]]:
    """The VIN-descriptor model rules every TS-named car under the key agreed with,
    and the build years of those cars.

    Such a key tells a car apart from a sibling better than one car's own text in
    TS's words: of 446 TS-named cars with the descriptor of a "LEXUS LS250" (a
    misspelt IS 250), all are IS. A maker reuses a descriptor decades later ("BMW
    735 IA" of 1984 under the X6's), hence the years. Retired rules count, since
    their fills stay until a check takes them back.
    """

    source = _SOURCE_OF.format(field="model_family")
    descriptor = key_sql("vin_descriptor")
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH known AS MATERIALIZED (
                SELECT manufacturer, {descriptor} AS descriptor, model_family AS value,
                       min(production_year) AS first_year, max(production_year) AS last_year
                FROM {VEHICLES_TABLE}
                WHERE {descriptor} IS NOT NULL AND model_family IS NOT NULL
                  AND manufacturer IS NOT NULL AND production_year IS NOT NULL AND {source} = %s
                GROUP BY 1, 2, 3
            )
            SELECT r.rule_id, known.first_year, known.last_year
            FROM {VEHICLE_ENRICHMENT_RULES_TABLE} AS r
            JOIN known
              ON known.manufacturer = r.key_values[1] AND known.descriptor = r.key_values[2]
             AND known.value = r.value
            WHERE r.rule_family = ANY(%s) AND r.agreement >= 1
            """,
            (SOURCE_TS, list(VIN_KEY_FAMILIES)),
        )
        return {str(rule_id): (int(first), int(last)) for rule_id, first, last in cursor.fetchall()}


def make_words(connection: Connection) -> dict[str, set[str]]:
    """The words the registry writes each make by ("VW", "VOLKSWAGEN"), as the
    manufacturer rules learned them: a text reader skips them in front."""

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT value, key_values[1] FROM {VEHICLE_ENRICHMENT_RULES_TABLE} "
            "WHERE rule_family = %s AND status = 'active'",
            (MAKE_WORD_FAMILY,),
        )
        words: dict[str, set[str]] = {}
        for manufacturer, word in cursor.fetchall():
            words.setdefault(str(manufacturer), set()).add(str(word))
    return words


def pattern_rules(
    connection: Connection,
    family: RuleFamily,
    *,
    min_support: int,
    min_agreement: float,
) -> list[LearnedRule]:
    """Rules the reviewed patterns propose for every make + model word in the data.

    Each proposal is checked against the vehicles whose TS-stated model is known
    under the same key. More than one of them disagreeing (more than
    `1 - min_agreement` of them, when there are many) means the word is shared by
    models -- "C4" is also the C4 Grand Picasso -- and no rule is made. A key the
    statistics already learn (`statistics_family`) needs no pattern, and a key
    shared by two makes is skipped.
    """

    statistics_family = FAMILIES_BY_ID[STATISTICS_FAMILY_OF_PATTERNS]

    make_field, token_field = family.key_fields
    token = key_sql(token_field)
    source = _SOURCE_OF.format(field=family.target_field)
    vocabulary = model_vocabulary(connection, family.target_field)
    with connection.cursor() as cursor:
        # A car this family already filled still needs its rule: counting only the
        # empty ones would drop every rule that filled all its cars on the next learn.
        cursor.execute(
            f"""
            SELECT {make_field}::text, {token}, array_agg(DISTINCT manufacturer), count(*),
                   count(*) FILTER (
                       WHERE {family.target_field} IS NULL
                          OR starts_with(coalesce(field_sources ->> %s, ''), %s)
                   )
            FROM {VEHICLES_TABLE}
            WHERE {make_field} IS NOT NULL AND {token} IS NOT NULL AND manufacturer IS NOT NULL
            GROUP BY 1, 2
            """,
            (family.target_field, f"{SOURCE_RULE}:{family.family}-"),
        )
        keys = cursor.fetchall()
        cursor.execute(
            f"""
            SELECT {make_field}::text, {token}, {family.target_field}, count(*)
            FROM {VEHICLES_TABLE}
            WHERE {make_field} IS NOT NULL AND {token} IS NOT NULL
              AND {family.target_field} IS NOT NULL AND {source} = %s
            GROUP BY 1, 2, 3
            """,
            (SOURCE_TS,),
        )
        known: dict[tuple[str, str], dict[str, int]] = {}
        for make, word, model, count in cursor.fetchall():
            known.setdefault((str(make), str(word)), {})[str(model)] = int(count)
    words_by_make = make_words(connection)
    # Only the brand-word patterns share their key with a statistical family; a
    # text family is keyed on the manufacturer, which AIS cars reach by other codes.
    shares_statistics_key = family.key_fields == statistics_family.key_fields
    reads_text = token_field in {"model_text", "brand_text"}
    rules: list[LearnedRule] = []
    for make, word, manufacturers, total, missing in keys:
        if len(manufacturers) != 1 or not missing or int(total) < min_support:
            continue
        stated = known.get((str(make), str(word)), {})
        stated_total = sum(stated.values())
        if (
            shares_statistics_key
            and stated_total >= statistics_family.min_support
            and max(stated.values()) >= statistics_family.min_agreement * stated_total
        ):
            continue
        manufacturer = str(manufacturers[0])
        if reads_text:
            value = model_text_family(
                manufacturer, str(word), vocabulary.get(manufacturer, ()),
                make_words=words_by_make.get(manufacturer, ()),
            )
        else:
            value = pattern_model(manufacturer, str(word), vocabulary.get(manufacturer, ()))
        if value is None:
            continue
        # A sibling TS misread is no evidence against the answer (MISREAD_STATED_FAMILIES).
        misread = MISREAD_STATED_FAMILIES.get(manufacturer, {}).get(value, ())
        disagreeing = stated_total - stated.get(value, 0) - sum(stated.get(other, 0) for other in misread)
        if disagreeing > tolerated_disagreements(stated_total, min_agreement):
            continue
        key_values = (str(make), str(word))
        rules.append(
            LearnedRule(
                rule_id=rule_id_for(family.family, key_values, value),
                family=family.family,
                target_field=family.target_field,
                key_fields=family.key_fields,
                key_values=key_values,
                value=value,
                support=int(total),
                agreement=round(stated.get(value, 0) / stated_total, 4) if stated_total else 1.0,
            )
        )
    return rules


@dataclass
class StoreSummary:
    family: str
    added: int = 0
    kept: int = 0
    retired: int = 0


def store_rules(
    connection: Connection, family: RuleFamily, rules: Sequence[LearnedRule], *, learned_from: str
) -> StoreSummary:
    """Make the family's active rules exactly `rules`.

    A rule whose key and value are unchanged stays as it is -- its id and what it
    filled remain valid. A key whose value changed, or that no longer qualifies,
    is retired, and the new rule gets a new id: a rule's content never changes
    under its id. Callers commit.
    """

    summary = StoreSummary(family.family)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT rule_id, key_values, value FROM {VEHICLE_ENRICHMENT_RULES_TABLE} "
            "WHERE rule_family = %s AND status = 'active'",
            (family.family,),
        )
        active = {tuple(keys): (str(rule_id), str(value)) for rule_id, keys, value in cursor.fetchall()}
        wanted = {rule.key_values: rule for rule in rules}
        retire = [
            rule_id
            for keys, (rule_id, value) in active.items()
            if keys not in wanted or wanted[keys].value != value
        ]
        if retire:
            cursor.execute(
                f"UPDATE {VEHICLE_ENRICHMENT_RULES_TABLE} SET status = 'retired', retired_at = now() "
                "WHERE rule_id = ANY(%s)",
                (retire,),
            )
            summary.retired = len(retire)
        fresh = [
            rule
            for rule in rules
            if rule.key_values not in active or active[rule.key_values][1] != rule.value
        ]
        summary.kept = len(rules) - len(fresh)
        if fresh:
            cursor.execute(
                "CREATE TEMP TABLE IF NOT EXISTS vehicle_rule_page "
                f"(LIKE {VEHICLE_ENRICHMENT_RULES_TABLE} INCLUDING DEFAULTS) ON COMMIT DELETE ROWS"
            )
            cursor.execute("TRUNCATE vehicle_rule_page")
            with cursor.copy(
                "COPY vehicle_rule_page (rule_id, rule_family, target_field, key_fields, "
                "key_values, value, support, agreement, learned_from) FROM STDIN"
            ) as copy:
                for rule in fresh:
                    copy.write_row(
                        (rule.rule_id, rule.family, rule.target_field, list(rule.key_fields),
                         list(rule.key_values), rule.value, rule.support, rule.agreement,
                         learned_from)
                    )
            # A rule learned again after it was retired comes back under its own id:
            # same content, same id, so what it filled before stays attributable.
            cursor.execute(
                f"""
                INSERT INTO {VEHICLE_ENRICHMENT_RULES_TABLE} (rule_id, rule_family, target_field,
                    key_fields, key_values, value, support, agreement, learned_from)
                SELECT rule_id, rule_family, target_field, key_fields, key_values, value,
                       support, agreement, learned_from
                FROM vehicle_rule_page
                ON CONFLICT (rule_id) DO UPDATE
                    SET status = 'active', retired_at = NULL, support = EXCLUDED.support,
                        agreement = EXCLUDED.agreement, learned_from = EXCLUDED.learned_from
                """
            )
            summary.added = len(fresh)
    return summary


@dataclass
class ApplySummary:
    family: str
    filled: int = 0
    #: Fills the model guard refused, by reason.
    refused: dict[str, int] = field(default_factory=dict)
    #: Fills taken back because the reviewed statement behind them was withdrawn.
    retracted: int = 0


class ModelChecker(Protocol):
    """What judges a learned model against the car (`vehicle_model_guard.ModelGuard`):
    the car's texts, fuel, engine and build year, and the rule that would fill it."""

    def verdict(
        self,
        *,
        manufacturer: str | None,
        model_family: str,
        evidence: Mapping[str, str | None],
        fuel: str | None = None,
        engine_code: str | None = None,
        year: int | None = None,
        rule_id: str | None = None,
    ) -> object | None: ...


_EVIDENCE_SELECT = ", ".join(f"v.{column}" for column in REGISTRY_EVIDENCE_COLUMNS.values())


def apply_rules(
    connection: Connection,
    family: RuleFamily,
    *,
    source_batch_id: str | None = None,
    guard: ModelChecker | None = None,
) -> ApplySummary:
    """Fill the family's target on every vehicle that lacks it and matches a key.

    Set-based: one UPDATE joins the family's active rules to the vehicles. Each
    fill is marked `rule:<rule_id>` and recorded in the ledger with the rule's
    agreement as its confidence -- a learned value is evidence, not a fact.

    A model family needs `guard`: every fill is first checked against the model
    word in the car's own registry text, and a contradicted fill is not made.
    """

    if family.purpose != "enrichment":
        raise ValueError(f"{family.family} completes AIS records before normalization")
    if family.target_field == "model_family":
        if guard is None:
            raise ValueError(f"{family.family} fills models: pass a guard (the catalog batch)")
        return _apply_guarded(connection, family, guard, source_batch_id)
    target = family.target_field
    # A reviewed statement that was corrected, or evidence that no longer holds, must
    # not stay on the cars it filled.
    retracted = (
        retract_retired_fills(connection, family)
        if family.learner in ("reviewed", "drive_evidence", "electrification")
        else 0
    )
    if family.learner == "electrification":
        return _apply_hybrid_type(connection, family, source_batch_id, retracted)
    sql_type = FIELDS_BY_NAME[target].sql_type
    cast = {"integer": "::integer", "smallint": "::smallint", "boolean": "::boolean"}.get(sql_type, "")
    key_match = " AND ".join(
        f"{key_sql(field, 'v')}::text = r.key_values[{index + 1}]"
        for index, field in enumerate(family.key_fields)
    )
    condition = f" AND {family.guard}" if family.guard else ""
    with connection.cursor() as cursor:
        # Fresh statistics first. Learning adds tens of thousands of rules at once;
        # planned against the old statistics the join looked like a handful of rules
        # and became a nested loop that rescanned 738k vehicles once per rule -- ten
        # minutes of CPU for 31k fills that take seconds as the merge join it is.
        cursor.execute(f"ANALYZE {VEHICLE_ENRICHMENT_RULES_TABLE}")
        cursor.execute(
            f"""
            WITH filled AS (
                UPDATE {VEHICLES_TABLE} AS v
                SET {target} = r.value{cast},
                    field_sources = v.field_sources
                        || jsonb_build_object('{target}', 'rule:' || r.rule_id),
                    updated_at = now()
                FROM {VEHICLE_ENRICHMENT_RULES_TABLE} AS r
                WHERE r.rule_family = %s AND r.status = 'active'
                  AND (v.{target} IS NULL OR v.{target}::text = ANY(%s))
                  AND {key_match}{condition}
                RETURNING v.vehicle_id, r.rule_id, r.value, r.agreement
            )
            SELECT vehicle_id, rule_id, value, agreement FROM filled
            """,
            (family.family, list(family.replaces)),
        )
        rows = cursor.fetchall()
    record_ledger_rows(
        connection,
        [
            LedgerRow(
                event_id=ledger_event_id("rule", str(rule_id), str(vehicle_id)),
                source=SOURCE_RULE,
                target_node_id=str(vehicle_id),
                attributes_added=(target,),
                confidence=float(agreement),
                evidence={target: {"to": value, "rule_id": rule_id}},
                source_batch_id=source_batch_id or str(rule_id),
            )
            for vehicle_id, rule_id, value, agreement in rows
        ],
    )
    return ApplySummary(family.family, filled=len(rows), retracted=retracted)


def _apply_hybrid_type(
    connection: Connection, family: RuleFamily, source_batch_id: str | None, retracted: int
) -> ApplySummary:
    """Fill the hybrid type, and on a car without a second fuel what the type implies.

    One UPDATE, like `apply_rules`. A hybrid AIS added carries its combustion fuel
    alone; with the type it gets electricity as second fuel and the hybrid's fuel
    tokens, each marked with the same rule. The tokens it had stay behind the new
    ones, so retiring the rule puts them back.
    """

    key_match = " AND ".join(
        f"{key_sql(field, 'v')}::text = r.key_values[{index + 1}]"
        for index, field in enumerate(family.key_fields)
    )
    implied = "v.fuel_secondary IS NULL"
    rule = "'rule:' || r.rule_id"
    # The source a value has now, written as `merge` writes one it puts behind another.
    present_source = (
        "coalesce(v.field_sources ->> 'fuel_match_tokens', v.origin_source "
        "|| coalesce('@' || to_char(v.origin_observed_on, 'YYYY-MM-DD'), ''))"
    )
    with connection.cursor() as cursor:
        cursor.execute(f"ANALYZE {VEHICLE_ENRICHMENT_RULES_TABLE}")
        cursor.execute(
            f"""
            WITH filled AS (
                UPDATE {VEHICLES_TABLE} AS v
                SET electrification_type = r.value,
                    fuel_secondary = CASE WHEN {implied} THEN 'electricity' ELSE v.fuel_secondary END,
                    fuel_match_tokens = CASE WHEN {implied}
                        THEN ARRAY[v.fuel, 'electricity', 'hybrid_' || v.fuel]
                        ELSE v.fuel_match_tokens END,
                    field_sources = v.field_sources
                        || jsonb_build_object('electrification_type', {rule})
                        || CASE WHEN {implied}
                            THEN jsonb_build_object('fuel_secondary', {rule},
                                                    'fuel_match_tokens', {rule})
                            ELSE '{{}}'::jsonb END,
                    field_alternatives = CASE WHEN {implied} AND v.fuel_match_tokens IS NOT NULL
                        THEN v.field_alternatives || jsonb_build_object(
                            'fuel_match_tokens',
                            coalesce(v.field_alternatives -> 'fuel_match_tokens', '[]'::jsonb)
                            || jsonb_build_array(jsonb_build_object(
                                'source', {present_source},
                                'value', to_jsonb(v.fuel_match_tokens))))
                        ELSE v.field_alternatives END,
                    updated_at = now()
                FROM {VEHICLE_ENRICHMENT_RULES_TABLE} AS r
                WHERE r.rule_family = %s AND r.status = 'active'
                  AND v.electrification_type IS NULL
                  AND {key_match} AND {family.guard}
                RETURNING v.vehicle_id, r.rule_id, r.value, r.agreement,
                          v.fuel_secondary = 'electricity' AND v.field_sources ->> 'fuel_secondary' = {rule}
            )
            SELECT * FROM filled
            """,
            (family.family,),
        )
        rows = cursor.fetchall()
    record_ledger_rows(
        connection,
        [
            LedgerRow(
                event_id=ledger_event_id("rule", str(rule_id), str(vehicle_id)),
                source=SOURCE_RULE,
                target_node_id=str(vehicle_id),
                attributes_added=(
                    ("electrification_type", *IMPLIED_BY_HYBRID_TYPE)
                    if with_implied
                    else ("electrification_type",)
                ),
                confidence=float(agreement),
                evidence={"electrification_type": {"to": value, "rule_id": rule_id}},
                source_batch_id=source_batch_id or str(rule_id),
            )
            for vehicle_id, rule_id, value, agreement, with_implied in rows
        ],
    )
    return ApplySummary(family.family, filled=len(rows), retracted=retracted)


def _fields_of(target: str) -> tuple[str, ...]:
    """The fields a rule's fill sits on: its target, and what that target implied."""

    return (target, *IMPLIED_BY_HYBRID_TYPE) if target == "electrification_type" else (target,)


def retract_retired_fills(connection: Connection, family: RuleFamily) -> int:
    """Take back what the family's retired rules filled; a rule still active keeps its fills.

    For a reviewed family: when a statement in the table is corrected, its rule is
    retired by `store_rules`, and the value it put on cars goes with it. A car the
    corrected table still covers is filled again by the apply that follows.
    """

    target = family.target_field
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT v.vehicle_id, r.rule_id
            FROM {VEHICLE_ENRICHMENT_RULES_TABLE} AS r
            JOIN {VEHICLES_TABLE} AS v ON v.field_sources ->> %s = 'rule:' || r.rule_id
            WHERE r.rule_family = %s AND r.status = 'retired'
            """,
            (target, family.family),
        )
        fills = [(str(vehicle_id), str(rule_id)) for vehicle_id, rule_id in cursor.fetchall()]
    for start in range(0, len(fills), _RETRACT_PAGE):
        page = dict(fills[start : start + _RETRACT_PAGE])
        states = load_vehicles(connection, list(page))
        for vehicle_id, state in states.items():
            for name in _fields_of(target):
                retract(state, name, SOURCE_RULE, page[vehicle_id])
        save_vehicles(connection, states.values())
    return len(fills)


#: Years either side of a number rule's learned era it still fills.
ERA_SLACK = 2


def number_rule_eras(connection: Connection) -> dict[str, tuple[int, int]]:
    """The build years each model rule keyed on a number was learned from.

    A number in brand text is a chassis code as often as an engine size: "220"
    names the W220 S-Class the rule was learned from (1998-2005), but also the
    1970s "220 D" saloons that share no model with it. Such a rule only fills a
    car built in the era of the known vehicles it was learned from. A word key
    ("GOLF") has no era, and neither has a number that is itself one of the
    make's model names: a Mazda 6 or a Fiat 500 is that model in any decade.
    """

    family = FAMILIES_BY_ID[STATISTICS_FAMILY_OF_PATTERNS]
    make_field, token_field = family.key_fields
    target = family.target_field
    token = key_sql(token_field)
    source = _SOURCE_OF.format(field=target)
    # Each side reads the vehicles once and is joined only to the few number
    # rules. Joined to the vehicles directly, the planner misjudged the computed
    # key and read the whole table again for every car the join matched.
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH known AS MATERIALIZED (
                SELECT {make_field}::text AS make, {token} AS word, {target} AS value,
                       min(production_year) AS first_year, max(production_year) AS last_year
                FROM {VEHICLES_TABLE}
                WHERE {token} ~ '^[0-9]+$' AND {target} IS NOT NULL
                  AND production_year IS NOT NULL AND {source} = %s
                GROUP BY 1, 2, 3
            ),
            names AS MATERIALIZED (
                SELECT DISTINCT {make_field}::text AS make, upper({target}) AS name
                FROM {VEHICLES_TABLE}
                WHERE {target} ~ '^[0-9]+$' AND {make_field} IS NOT NULL AND {source} = %s
            )
            SELECT r.rule_id, known.first_year, known.last_year
            FROM {VEHICLE_ENRICHMENT_RULES_TABLE} AS r
            JOIN known
              ON known.make = r.key_values[1] AND known.word = r.key_values[2] AND known.value = r.value
            WHERE r.rule_family = %s AND r.status = 'active' AND r.key_values[2] ~ '^[0-9]+$'
              AND NOT EXISTS (SELECT 1 FROM names WHERE names.make = r.key_values[1]
                              AND names.name = r.key_values[2])
            """,
            (SOURCE_TS, SOURCE_TS, family.family),
        )
        return {str(rule_id): (int(first), int(last)) for rule_id, first, last in cursor.fetchall()}


def _outside_era(eras: Mapping[str, tuple[int, int]], rule_id: str, year: int | None) -> bool:
    era = eras.get(rule_id)
    return era is not None and year is not None and not era[0] - ERA_SLACK <= year <= era[1] + ERA_SLACK


Eras = tuple[tuple[int, int | None], ...]

#: The build years a model rule's answer is right for, reviewed one by one for
#: rules keyed on a number, a letter or a name that meant another car in another
#: era (2026-10-01). The learned era (`number_rule_eras`) cannot see these: it
#: covers only brand-word numbers, and the years it learns from are biased to cars
#: TS named, which are mostly recent. Keyed by the rule's content -- family, key
#: values and answer -- of which its id is the hash, so an era follows its rule
#: through any re-learn. A range is inclusive; None leaves it open.
REVIEWED_RULE_ERAS: dict[tuple[str, tuple[str, ...], str], Eras] = {
    # "MERCEDES-BENZ 230" is the R230 SL from 2001; before it the 230 saloon (W110/
    # W111, W114/W115, W123): 503 of 557 fills were. The 1963-67 230 SL (W113) is no
    # era of its own: about 70 of the 81 fills from those years are 4.75-5.25 m
    # saloons and estates, and only some 7 the 4.3 m SL, which goes unfilled.
    # Its sister key without the hyphen filled 293 cars, 255 of them W123s.
    ("MOD-BR", ("MB", "MERCEDES-BENZ 230"), "SL"): ((2001, None),),
    ("MOD-BR", ("MB", "MERCEDES BENZ 230"), "SL"): ((2001, None),),
    # The S4 of 1992-94 is the Audi 100 (C4); the A4's S4 starts in 1997.
    ("MOD-BT", ("AU", "S4"), "A4"): ((1995, None),),
    # "CITROEN B 11", "B 425", "B 602": Traction Avants, 2CVs and Ami 6s; the C4 is from 2004.
    ("MOD-BT", ("CI", "B"), "C4"): ((2004, None),),
}
REVIEWED_ERAS_BY_RULE_ID: dict[str, Eras] = {
    rule_id_for(family, key_values, value): eras
    for (family, key_values, value), eras in REVIEWED_RULE_ERAS.items()
}

#: The build years a family's name means that car, by make, for names an older or a
#: newer car's text reads the same (2026-10-01). Whatever rule answers with one --
#: and whichever key a re-learn gives it -- fills only cars of those years, and the
#: guard reads a text naming it as no family for a car built outside them.
REVIEWED_FAMILY_ERAS: dict[tuple[str, str], Eras] = {
    # The 1955-60 Saab 93 ("SAAB 93 B DE LUXE"); "SAAB 93 AERO" and the other 1998+
    # cars registered "SAAB 93" are 9-3s.
    ("Saab", "93"): ((1955, 1960),),
    # The Renault 4 from 1961; "RENAULT 4 CV R 1062" before it is the 4CV.
    ("Renault", "4"): ((1961, None),),
    # The Clio from 1990: "RENAULT B 40805" of 1989 is no Clio II.
    ("Renault", "Clio"): ((1990, None),),
    # The A-Class from 1997, the B-Class from 2005: "MERCEDES A CABR 170 S" is a 1950s
    # 170 S Cabriolet A, "MERCEDES B 170 S" of 1950 and "M B 250 CE" of 1972 abbreviate
    # Mercedes-Benz.
    ("Mercedes-Benz", "A-Class"): ((1997, None),),
    ("Mercedes-Benz", "B-Class"): ((2005, None),),
    # TecDoc "CABRIOLET B3 (8G7)" of 1991-2000 and "COUPE (81, 85)", "COUPE (8B3)" of
    # 1980-96: the "AUDI CABRIO 2,4" of 2002-04 is an A4 Cabriolet.
    ("Audi", "Cabriolet"): ((1991, 2000),),
    ("Audi", "Coupe"): ((1980, 1996),),
    # The Fiat Coupé (type 175), built until late 2000, the registry's last ones 2001;
    # "FIAT 130 COUPÉ" of 1973 and "FIAT COUPE 124" of 1968 are no Coupe.
    ("Fiat", "Coupe"): ((1993, 2001),),
    # The Galaxie of model years 1959-74, built from 1958; a 2009 "FORD GALAXIE"
    # misspells the Galaxy.
    ("Ford", "Galaxie"): ((1958, 1974),),
    ("Ford", "Galaxie 500"): ((1958, 1974),),
}


def _outside(eras: Eras | None, year: int | None) -> bool:
    return (
        eras is not None
        and year is not None
        and not any(first <= year and (last is None or year <= last) for first, last in eras)
    )


def outside_reviewed_era(rule_id: str, year: int | None) -> bool:
    """True when the rule has a reviewed era (`REVIEWED_RULE_ERAS`) and the car's
    build year is outside it. A car without a build year is not judged."""

    return _outside(REVIEWED_ERAS_BY_RULE_ID.get(rule_id), year)


def outside_family_era(manufacturer: str | None, family: str, year: int | None) -> bool:
    """True when the family has a reviewed era (`REVIEWED_FAMILY_ERAS`) and the car's
    build year is outside it. A car without a build year is not judged."""

    eras = REVIEWED_FAMILY_ERAS.get((manufacturer, family)) if manufacturer else None
    return _outside(eras, year)


def _era_refusal(
    eras: Mapping[str, tuple[int, int]],
    rule_id: str,
    year: int | None,
    manufacturer: str | None = None,
    value: str | None = None,
) -> str | None:
    if _outside_era(eras, rule_id, year):
        return "outside_learned_era"
    if outside_reviewed_era(rule_id, year) or (
        value is not None and outside_family_era(manufacturer, value, year)
    ):
        return "outside_reviewed_era"
    return None


def _apply_guarded(
    connection: Connection, family: RuleFamily, guard: ModelChecker, source_batch_id: str | None
) -> ApplySummary:
    target = family.target_field
    key_match = " AND ".join(
        f"{key_sql(key, 'v')}::text = r.key_values[{index + 1}]"
        for index, key in enumerate(family.key_fields)
    )
    eras = number_rule_eras(connection) if family.family == STATISTICS_FAMILY_OF_PATTERNS else {}
    with connection.cursor() as cursor:
        cursor.execute(f"ANALYZE {VEHICLE_ENRICHMENT_RULES_TABLE}")
        cursor.execute(
            f"""
            SELECT v.vehicle_id, r.rule_id, r.value, r.agreement, v.manufacturer, v.production_year,
                   v.fuel, v.engine_code, {_EVIDENCE_SELECT}
            FROM {VEHICLES_TABLE} AS v
            JOIN {VEHICLE_ENRICHMENT_RULES_TABLE} AS r
              ON r.rule_family = %s AND r.status = 'active' AND {key_match}
            WHERE v.{target} IS NULL
            """,
            (family.family,),
        )
        proposed = cursor.fetchall()
        refused: Counter[str] = Counter()
        accepted = []
        for vehicle_id, rule_id, value, agreement, manufacturer, year, fuel, engine_code, *texts in proposed:
            if era_refusal := _era_refusal(eras, str(rule_id), year, manufacturer, str(value)):
                refused[era_refusal] += 1
                continue
            verdict = guard.verdict(
                manufacturer=manufacturer, model_family=str(value),
                evidence=dict(zip(REGISTRY_EVIDENCE_COLUMNS, texts, strict=True)), fuel=fuel,
                engine_code=engine_code, year=year, rule_id=str(rule_id),
            )
            if verdict is None:
                accepted.append((vehicle_id, rule_id, value, agreement))
            else:
                refused[str(getattr(verdict, "reason", verdict))] += 1
        cursor.execute(
            "CREATE TEMP TABLE IF NOT EXISTS guarded_fill "
            "(vehicle_id TEXT, rule_id TEXT, value TEXT, agreement REAL) ON COMMIT DROP"
        )
        cursor.execute("TRUNCATE guarded_fill")
        with cursor.copy("COPY guarded_fill FROM STDIN") as copy:
            for row in accepted:
                copy.write_row(row)
        cursor.execute(
            f"""
            UPDATE {VEHICLES_TABLE} AS v
            SET {target} = f.value,
                field_sources = v.field_sources || jsonb_build_object('{target}', 'rule:' || f.rule_id),
                updated_at = now()
            FROM guarded_fill AS f
            WHERE v.vehicle_id = f.vehicle_id AND v.{target} IS NULL
            RETURNING v.vehicle_id, f.rule_id, f.value, f.agreement
            """
        )
        rows = cursor.fetchall()
    record_ledger_rows(
        connection,
        [
            LedgerRow(
                event_id=ledger_event_id("rule", str(rule_id), str(vehicle_id)),
                source=SOURCE_RULE,
                target_node_id=str(vehicle_id),
                attributes_added=(target,),
                confidence=float(agreement),
                evidence={target: {"to": value, "rule_id": rule_id}},
                source_batch_id=source_batch_id or str(rule_id),
            )
            for vehicle_id, rule_id, value, agreement in rows
        ],
    )
    return ApplySummary(family.family, filled=len(rows), refused=dict(refused))


@dataclass
class CheckSummary:
    checked: int = 0
    contradicted: dict[str, int] = field(default_factory=dict)
    retracted: int = 0
    examples: list[dict[str, object]] = field(default_factory=list)


_EXAMPLES = 25
_RETRACT_PAGE = 5000


def check_model_fills(
    connection: Connection, guard: ModelChecker, *, retract_contradicted: bool = False
) -> CheckSummary:
    """Check every model a rule filled against its car, and take back contradicted ones.

    Fills made before the guard existed, or before a catalog or text changed, are
    judged the same way new fills are. Without `retract_contradicted` it only counts.
    """

    summary = CheckSummary()
    contradicted: Counter[str] = Counter()
    retract_ids: list[tuple[str, str]] = []
    eras = number_rule_eras(connection)
    with connection.cursor(name="model_fill_check") as cursor:
        cursor.itersize = 20000
        cursor.execute(
            f"""
            SELECT v.vehicle_id, substring(v.field_sources ->> 'model_family' from 6), v.model_family,
                   v.manufacturer, v.production_year, v.fuel, v.engine_code, {_EVIDENCE_SELECT}
            FROM {VEHICLES_TABLE} AS v
            WHERE v.field_sources ->> 'model_family' LIKE 'rule:MOD-%'
            """
        )
        for vehicle_id, rule_id, value, manufacturer, year, fuel, engine_code, *texts in cursor:
            summary.checked += 1
            evidence = dict(zip(REGISTRY_EVIDENCE_COLUMNS, texts, strict=True))
            verdict: object | None
            if era_refusal := _era_refusal(eras, str(rule_id), year, manufacturer, str(value)):
                verdict, reason = era_refusal, era_refusal
            else:
                verdict = guard.verdict(
                    manufacturer=manufacturer, model_family=str(value), evidence=evidence, fuel=fuel,
                    engine_code=engine_code, year=year, rule_id=str(rule_id),
                )
                if verdict is None:
                    continue
                reason = str(getattr(verdict, "reason", verdict))
            contradicted[reason] += 1
            retract_ids.append((str(vehicle_id), str(rule_id)))
            if len(summary.examples) < _EXAMPLES:
                summary.examples.append({
                    "manufacturer": manufacturer, "brand_text": evidence.get("brand"),
                    "model_text": evidence.get("model"), "filled": value,
                    "reason": reason, "detail": getattr(verdict, "detail", None),
                })
    summary.contradicted = dict(contradicted)
    if retract_contradicted:
        for start in range(0, len(retract_ids), _RETRACT_PAGE):
            page = dict(retract_ids[start : start + _RETRACT_PAGE])
            states = load_vehicles(connection, list(page))
            for vehicle_id, state in states.items():
                retract(state, "model_family", SOURCE_RULE, page[vehicle_id])
            save_vehicles(connection, states.values())
            summary.retracted += len(states)
    return summary


def retire_rule(connection: Connection, rule_id: str) -> int:
    """Retire one rule and take back exactly what it filled."""

    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {VEHICLE_ENRICHMENT_RULES_TABLE} SET status = 'retired', retired_at = now() "
            "WHERE rule_id = %s AND status = 'active' RETURNING target_field",
            (rule_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return 0
        target = str(row[0])
        cursor.execute(
            f"SELECT vehicle_id FROM {VEHICLES_TABLE} WHERE field_sources ->> %s = %s",
            (target, f"rule:{rule_id}"),
        )
        vehicle_ids = [str(r[0]) for r in cursor.fetchall()]
    states = load_vehicles(connection, vehicle_ids)
    for state in states.values():
        for name in _fields_of(target):
            retract(state, name, SOURCE_RULE, rule_id)
    save_vehicles(connection, states.values())
    return len(states)


# --- completion rules, read by the AIS import ---------------------------------------


@dataclass(frozen=True)
class CompletionRules:
    """Active completion rules by family and key, each as (value, rule id)."""

    rules: Mapping[str, Mapping[tuple[str, ...], tuple[str, str]]]

    def lookup(self, family: str, key: tuple[str, ...]) -> tuple[str, str] | None:
        return self.rules.get(family, {}).get(key)

    @cached_property
    def _brand_texts(self) -> dict[str, list[tuple[str, str, str]]]:
        by_make: dict[str, list[tuple[str, str, str]]] = {}
        for (make_code, text), (value, rule_id) in self.rules.get(BRAND_TEXT_FAMILY, {}).items():
            by_make.setdefault(make_code, []).append((text.casefold(), value, rule_id))
        for texts in by_make.values():
            texts.sort(key=lambda entry: (len(entry[0]), entry[0]))
        return by_make

    def brand_text_of(self, make_code: str | None, name: str | None) -> tuple[str, str] | None:
        """The registry's brand text a car name starts with, as (brand text, rule id).

        Only a name that goes on after the brand text has one: what follows is the
        model. Of several texts the shortest is the make's own name ("TOYOTA", not
        "TOYOTA RAV4"), which is how the registry divides the two today.
        """

        folded = " ".join((name or "").split()).casefold()
        for text, value, rule_id in self._brand_texts.get(make_code or "", ()):
            if folded.startswith(f"{text} "):
                return value, rule_id
        return None

    def without(self, family: str) -> CompletionRules:
        """These rules as they were before `family` existed."""

        return CompletionRules({name: rules for name, rules in self.rules.items() if name != family})


def load_completion_rules(connection: Connection) -> CompletionRules:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT rule_family, key_values, value, rule_id FROM {VEHICLE_ENRICHMENT_RULES_TABLE} "
            "WHERE status = 'active' AND rule_family = ANY(%s)",
            ([family.family for family in COMPLETION_FAMILIES],),
        )
        rules: dict[str, dict[tuple[str, ...], tuple[str, str]]] = {}
        for family, keys, value, rule_id in cursor.fetchall():
            rules.setdefault(str(family), {})[tuple(keys)] = (str(value), str(rule_id))
    return CompletionRules(rules)


def rule_ref(rule_id: str) -> SourceRef:
    return SourceRef(SOURCE_RULE, rule_id)

