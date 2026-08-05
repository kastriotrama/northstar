"""Pure Transportstyrelsen normalization rules for SCRUM-82.

Only decisions marked accepted in the SCRUM-74/SCRUM-77 contract may populate
``normalized``. Proposed fuel and marketing decisions are retained under
``candidates`` so a pilot can measure them without silently promoting them to
canonical facts.
"""

from __future__ import annotations

import re
import unicodedata
from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Literal

from ingestion.normalization_pipeline import (
    DecisionTraceEntry,
    NormalizationContext,
    NormalizationPipeline,
    RuleMatch,
    Transformer,
)
from ingestion.text_canonicalization import TextCanonicalizationTransformer
from ingestion.translation_dictionaries import (
    REVIEWED_RULE_SET_VERSION,
    TranslationRule,
    load_translation_rule_set,
)

MAPPING_VERSION = "ts-mapping-v1"
RULE_VERSION = REVIEWED_RULE_SET_VERSION
PIPELINE_VERSION = "normalization-pipeline-v3"
RULE_SET = load_translation_rule_set(RULE_VERSION)

NormalizationStatus = Literal["resolved", "provisional", "review_required", "failed"]


@dataclass(frozen=True)
class NormalizationOutcome:
    """Sanitized result for one raw staging row."""

    status: NormalizationStatus
    normalized: dict[str, Any]
    candidates: dict[str, Any]
    applied_rule_ids: tuple[str, ...]
    candidate_rule_ids: tuple[str, ...]
    review_reasons: tuple[str, ...]
    confidence: float
    pipeline_version: str
    decision_trace: tuple[DecisionTraceEntry, ...]
    rule_matches: tuple[RuleMatch, ...]

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        expected_sequence = tuple(range(1, len(self.decision_trace) + 1))
        actual_sequence = tuple(entry.sequence for entry in self.decision_trace)
        if actual_sequence != expected_sequence:
            raise ValueError("decision trace sequence must be contiguous and ordered")

    def to_payload(self) -> dict[str, Any]:
        return {
            "normalized": self.normalized,
            "candidates": self.candidates,
            "candidate_rule_ids": list(self.candidate_rule_ids),
            "confidence": self.confidence,
            "pipeline_version": self.pipeline_version,
            "decision_trace": [entry.to_payload() for entry in self.decision_trace],
            "rule_matches": [match.to_payload() for match in self.rule_matches],
        }


RuleHandler = Callable[[NormalizationContext], None]


@dataclass(frozen=True)
class _RuleTransformer(Transformer):
    transformer_id: str
    order: int
    default_rule_id: str
    handler: RuleHandler
    source_fields: tuple[str, ...] = ()
    normalized_confidence_effect: float = 0.0
    candidate_confidence_effect: float = 0.0
    review_confidence_effect: float = -0.2

    def _source_evidence(self, context: NormalizationContext) -> Any:
        evidence = {
            field_name: context.canonical_record[field_name]
            for field_name in self.source_fields
            if context.canonical_record.get(field_name) not in (None, "")
        }
        if len(evidence) == 1:
            return next(iter(evidence.values()))
        return evidence or None

    def apply(self, context: NormalizationContext) -> None:
        normalized_before = dict(context.normalized)
        candidates_before = dict(context.candidates)
        applied_offset = len(context.applied_rule_ids)
        candidate_offset = len(context.candidate_rule_ids)
        reason_offset = len(context.review_reasons)

        self.handler(context)

        applied_rules = tuple(context.applied_rule_ids[applied_offset:]) or (self.default_rule_id,)
        candidate_rules = tuple(context.candidate_rule_ids[candidate_offset:]) or (
            self.default_rule_id,
        )
        for field_name in sorted(context.normalized.keys() | normalized_before.keys()):
            before = normalized_before.get(field_name)
            after = context.normalized.get(field_name)
            if before != after:
                context.record_change(
                    transformer_id=self.transformer_id,
                    target="normalized",
                    field_name=field_name,
                    rule_ids=applied_rules,
                    before=before if before is not None else self._source_evidence(context),
                    after=after,
                    confidence_effect=self.normalized_confidence_effect,
                )
        for field_name in sorted(context.candidates.keys() | candidates_before.keys()):
            before = candidates_before.get(field_name)
            after = context.candidates.get(field_name)
            if before != after:
                context.record_change(
                    transformer_id=self.transformer_id,
                    target="candidate",
                    field_name=field_name,
                    rule_ids=candidate_rules,
                    before=before if before is not None else self._source_evidence(context),
                    after=after,
                    confidence_effect=self.candidate_confidence_effect,
                )
        for reason in context.review_reasons[reason_offset:]:
            context.record_change(
                transformer_id=self.transformer_id,
                target="review",
                field_name="review_reason",
                rule_ids=tuple(dict.fromkeys((*applied_rules, *candidate_rules))),
                before=self._source_evidence(context),
                after=reason,
                confidence_effect=self.review_confidence_effect,
            )


_MANUFACTURER_ALIASES: dict[str, str] = {
    "VOLVO": "Volvo",
    "VOLVO CAR": "Volvo",
    "VOLVO CAR CORPORATION": "Volvo",
    "VOLVO PERSONVAGNAR": "Volvo",
    "MERCEDES": "Mercedes-Benz",
    "MERCEDES BENZ": "Mercedes-Benz",
    "MERCEDES BENZ AG": "Mercedes-Benz",
    "BMW": "BMW",
    "BMW AG": "BMW",
    "AUDI": "Audi",
    "AUDI AG": "Audi",
    "VOLKSWAGEN": "Volkswagen",
    "VOLKSWAGEN AG": "Volkswagen",
    "IVECO": "Iveco",
    "IVECO SPA": "Iveco",
    "SCANIA": "Scania",
    "SCANIA CV AB": "Scania",
    "FORD": "Ford",
    "FORD MOTOR COMPANY": "Ford",
    "TOYOTA": "Toyota",
    "TOYOTA MOTOR CORPORATION": "Toyota",
    "RENAULT": "Renault",
    "RENAULT SAS": "Renault",
    "PEUGEOT": "Peugeot",
    "CITROEN": "Citroën",
    "CITROËN": "Citroën",
    "FIAT": "Fiat",
    "FIAT AUTO SPA": "Fiat",
    "OPEL": "Opel",
    "OPEL AUTOMOBILE GMBH": "Opel",
    "NISSAN": "Nissan",
    "NISSAN MOTOR CO LTD": "Nissan",
    "HONDA": "Honda",
    "HONDA MOTOR CO LTD": "Honda",
    "MAN": "MAN",
    "MAN TRUCK BUS": "MAN",
    "DAF": "DAF",
    "DAF TRUCKS NV": "DAF",
    "LEXUS": "Lexus",
    "ISUZU": "Isuzu",
    "TATRA": "Tatra",
    "MAXUS": "Maxus",
    "PORSCHE": "Porsche",
    "PORSCHE AG": "Porsche",
}

_CONVERTER_ALIASES: dict[str, tuple[str, str]] = {
    "BRABUS": ("Brabus", "MFR-105"),
    "BRABUS GMBH": ("Brabus", "MFR-105"),
    "DANGEL": ("Dangel", "MFR-105"),
    "NILSSON": ("Nilsson", "MFR-106"),
    "NILSSON SPECIAL VEHICLES": ("Nilsson", "MFR-106"),
    "BERCO PRODUKTION AB": ("Berco", "MFR-106"),
    "JUNGE": ("Junge", "MFR-106"),
    "K BUS": ("K-Bus", "MFR-106"),
    "LUNO CAMP": ("Luno Camp", "MFR-106"),
    "LUANO CAMP": ("Luno Camp", "MFR-106"),
    "BUS PRESTIGE": ("Bus-Prestige", "MFR-106"),
}

_CORPORATE_GROUP_MARKERS = ("STELLANTIS", "PSA", "FCA")

_NON_WORD = re.compile(r"[^A-Z0-9ÅÄÖÉÜ]+")


def normalize_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value).strip()
    return normalized or None


def normalize_ts_record(raw_record: object) -> NormalizationOutcome:
    """Normalize one TS raw record without copying sensitive identifiers."""

    if not isinstance(raw_record, dict):
        context = NormalizationContext(raw_record={})
        context.record_change(
            transformer_id="ts.input-contract",
            target="review",
            field_name="review_reason",
            rule_ids=("INPUT-OBJECT-REQUIRED",),
            before=None,
            after="raw_record_not_object",
            confidence_effect=-1.0,
        )
        return NormalizationOutcome(
            status="failed",
            normalized={},
            candidates={},
            applied_rule_ids=(),
            candidate_rule_ids=(),
            review_reasons=("raw_record_not_object",),
            confidence=0.0,
            pipeline_version=PIPELINE_VERSION,
            decision_trace=tuple(context.decision_trace),
            rule_matches=(),
        )

    context = DEFAULT_PIPELINE.run(raw_record)
    normalized = context.normalized
    candidates = context.candidates
    applied = context.applied_rule_ids
    candidate_rules = context.candidate_rule_ids
    reasons = context.review_reasons

    if reasons:
        status: NormalizationStatus = "review_required"
        confidence = 0.55
    elif candidates or normalized.get("model_family_candidate"):
        status = "provisional"
        confidence = 0.8
    else:
        status = "resolved"
        confidence = 0.95

    return NormalizationOutcome(
        status=status,
        normalized=normalized,
        candidates=candidates,
        applied_rule_ids=tuple(dict.fromkeys(applied)),
        candidate_rule_ids=tuple(dict.fromkeys(candidate_rules)),
        review_reasons=tuple(dict.fromkeys(reasons)),
        confidence=confidence,
        pipeline_version=PIPELINE_VERSION,
        decision_trace=tuple(context.decision_trace),
        rule_matches=tuple(context.rule_matches),
    )


def _normalized_entity(value: object) -> str | None:
    text = normalize_text(value)
    if text is None:
        return None
    return _NON_WORD.sub(" ", text.upper()).strip()


def _resolve_manufacturer(value: object) -> str | None:
    entity = _normalized_entity(value)
    if entity is None:
        return None
    direct = _MANUFACTURER_ALIASES.get(entity)
    if direct is not None:
        return direct
    for alias, canonical in _MANUFACTURER_ALIASES.items():
        if entity.startswith(f"{alias} "):
            return canonical
    return None


def _normalize_manufacturer(
    raw: dict[str, Any],
    normalized: dict[str, Any],
    candidates: dict[str, Any],
    applied: list[str],
    candidate_rules: list[str],
    reasons: list[str],
) -> None:
    entity = _normalized_entity(raw.get("manufacturer"))
    base = _resolve_manufacturer(raw.get("base_manufacturer"))
    if entity is None:
        brand = _resolve_manufacturer(raw.get("brand"))
        if brand is not None:
            candidates["manufacturer"] = brand
            candidate_rules.append("MFR-BRAND-REVIEW")
            reasons.append("manufacturer_missing_compare_brand")
            return
        reasons.append("manufacturer_missing")
        return
    if any(marker in entity for marker in _CORPORATE_GROUP_MARKERS):
        reasons.append("manufacturer_corporate_group_unresolved")
        return

    converter = _CONVERTER_ALIASES.get(entity)
    if converter is not None:
        converter_name, rule_id = converter
        if base is None:
            reasons.append("converter_base_manufacturer_unresolved")
            return
        normalized["manufacturer"] = base
        normalized["manufacturer_role"] = "bodybuilder_converter"
        normalized["builder_converter_names"] = [converter_name]
        applied.append(rule_id)
        return

    manufacturer = _resolve_manufacturer(raw.get("manufacturer"))
    if manufacturer is None:
        reasons.append("manufacturer_unknown")
        return
    normalized["manufacturer"] = manufacturer
    normalized["manufacturer_role"] = "vehicle_manufacturer"
    normalized["builder_converter_names"] = []
    applied.append("MFR-102")


def _normalize_model_family(
    raw: dict[str, Any],
    normalized: dict[str, Any],
    candidates: dict[str, Any],
) -> None:
    model = normalize_text(raw.get("model"))
    if model is None:
        return
    candidates["model_family"] = model
    normalized["model_family_candidate"] = True


def _parse_date(value: object, format_name: str) -> date | None:
    text = normalize_text(value)
    if text is None or not text.isdigit():
        return None
    try:
        if format_name == "day" and len(text) == 8:
            parsed = date(int(text[:4]), int(text[4:6]), int(text[6:8]))
            return parsed if 1886 <= parsed.year <= 2200 else None
        if format_name == "month" and len(text) == 6:
            parsed = date(int(text[:4]), int(text[4:6]), 1)
            return parsed if 1886 <= parsed.year <= 2200 else None
    except ValueError:
        return None
    return None


def _parse_year(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    text = str(value).strip() if isinstance(value, (int, str)) else ""
    if not (len(text) == 4 and text.isdigit()):
        return None
    year = int(text)
    return year if 1886 <= year <= 2200 else None


def _parse_flexible_date(value: object) -> tuple[date, Literal["day", "month", "year"]] | None:
    text = normalize_text(value)
    if text is None and isinstance(value, int):
        text = str(value)
    if text is None:
        return None
    compact = text.replace("-", "")
    if len(compact) == 8:
        parsed = _parse_date(compact, "day")
        return (parsed, "day") if parsed is not None else None
    if len(compact) == 6:
        parsed = _parse_date(compact, "month")
        return (parsed, "month") if parsed is not None else None
    year = _parse_year(compact)
    return (date(year, 1, 1), "year") if year is not None else None


def _date_text(parsed: date, precision: str) -> str:
    if precision == "day":
        return parsed.isoformat()
    if precision == "month":
        return parsed.strftime("%Y-%m")
    return str(parsed.year)


def _date_upper_bound(parsed: date, precision: str) -> date:
    if precision == "year":
        return date(parsed.year, 12, 31)
    if precision == "month":
        return date(parsed.year, parsed.month, monthrange(parsed.year, parsed.month)[1])
    return parsed


def _parse_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip().replace(",", ".")
    else:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _positive_rounded_int(value: object, *, multiplier: Decimal, maximum: int) -> int | None:
    parsed = _parse_decimal(value)
    if parsed is None or parsed <= 0:
        return None
    converted = int((parsed * multiplier).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return converted if 0 < converted <= maximum else None


def _record_dictionary_match(
    context: NormalizationContext,
    rule: TranslationRule,
    *,
    source_field: str,
    source_term: str,
) -> None:
    context.record_rule_match(
        rule_set_version=RULE_VERSION,
        rule_id=rule.rule_id,
        decision=rule.decision,
        source_field=source_field,
        source_term=source_term,
        target_field=rule.canonical_field,
        canonical_value=rule.canonical_value,
    )


def _marketing_match(
    raw: dict[str, Any],
    area: Literal["transmission_marketing", "bodywork_marketing", "electrification_marketing"],
    *,
    vehicle_scope: str | None = None,
) -> tuple[TranslationRule, str, str] | None:
    matches: list[tuple[int, TranslationRule, str, str]] = []
    for rule in RULE_SET.rules:
        if rule.area != area or (rule.vehicle_scopes and vehicle_scope not in rule.vehicle_scopes):
            continue
        for field_name in rule.source_fields:
            text = normalize_text(raw.get(field_name))
            if text is None:
                continue
            for term in rule.source_terms:
                pattern = rf"(?<!\w){re.escape(term)}(?!\w)"
                if re.search(pattern, text, flags=re.IGNORECASE):
                    matches.append((len(term), rule, field_name, term))
    if not matches:
        return None
    _, rule, field_name, term = max(matches, key=lambda match: (match[0], match[1].rule_id))
    return rule, field_name, term


def _manufacturer_is_in_scope(rule: TranslationRule, manufacturer: object) -> bool:
    if not rule.manufacturers:
        return True
    if not isinstance(manufacturer, str) or not manufacturer:
        return False
    return "*" in rule.manufacturers or manufacturer in rule.manufacturers


def _raw_fuel_carriers(raw: dict[str, Any]) -> set[str]:
    carriers: set[str] = set()
    for field_name in ("fuel1", "fuel2", "fuel3"):
        code = normalize_text(raw.get(field_name))
        if code is None or code == "0":
            continue
        matches = RULE_SET.match("fuel_carrier", code.upper().lstrip("0") or "0")
        if matches and matches[0].canonical_value is not None:
            carriers.add(matches[0].canonical_value)
    return carriers


def _normalize_dates(context: NormalizationContext) -> None:
    raw = context.canonical_record
    normalized = context.normalized
    reasons = context.review_reasons

    registration_value = raw.get("registration_date")
    if registration_value not in (None, ""):
        registration = _parse_flexible_date(registration_value)
        if registration is None or registration[1] != "day":
            reasons.append("registration_date_malformed")
        else:
            normalized["registration_date"] = registration[0].isoformat()
            context.applied_rule_ids.append("DATE-REGISTRATION-V1")

    range_values = (raw.get("production_from"), raw.get("production_to"))
    if any(value not in (None, "") for value in range_values):
        parsed_range = tuple(
            _parse_flexible_date(value) if value not in (None, "") else None
            for value in range_values
        )
        if parsed_range[0] is None:
            reasons.append("production_from_malformed")
        if range_values[1] not in (None, "") and parsed_range[1] is None:
            reasons.append("production_to_malformed")
        if parsed_range[0] is None or (
            range_values[1] not in (None, "") and parsed_range[1] is None
        ):
            return
        start, start_precision = parsed_range[0]
        normalized["production_year_from"] = start.year
        normalized["production_from"] = _date_text(start, start_precision)
        normalized["production_from_precision"] = start_precision
        if parsed_range[1] is not None:
            end, end_precision = parsed_range[1]
            if _date_upper_bound(end, end_precision) < start:
                reasons.append("production_range_reversed")
                for field_name in (
                    "production_year_from",
                    "production_from",
                    "production_from_precision",
                ):
                    normalized.pop(field_name, None)
                return
            normalized["production_year_to"] = end.year
            normalized["production_to"] = _date_text(end, end_precision)
            normalized["production_to_precision"] = end_precision
        context.applied_rule_ids.append("DATE-PRODUCTION-RANGE-V1")
        return

    build_date = normalize_text(raw.get("build_date"))
    if build_date is not None:
        parsed = _parse_date(build_date, "day")
        if parsed is None:
            reasons.append("build_date_malformed")
            return
        normalized["production_date"] = parsed.isoformat()
        normalized["production_year"] = parsed.year
        normalized["production_date_precision"] = "day"
        context.applied_rule_ids.append("DATE-PRODUCTION-DAY-V1")
        return
    build_month = normalize_text(raw.get("build_month"))
    if build_month is not None:
        parsed = _parse_date(build_month, "month")
        if parsed is None:
            reasons.append("build_month_malformed")
            return
        normalized["production_date"] = parsed.strftime("%Y-%m")
        normalized["production_year"] = parsed.year
        normalized["production_date_precision"] = "month"
        context.applied_rule_ids.append("DATE-PRODUCTION-MONTH-V1")
        return
    for field_name in ("model_year", "vehicle_year"):
        value = raw.get(field_name)
        year = _parse_year(value)
        if year is not None:
            normalized["production_year"] = year
            normalized["production_date_precision"] = field_name
            context.applied_rule_ids.append("DATE-PRODUCTION-YEAR-V1")
            return
        if value not in (None, ""):
            reasons.append(f"{field_name}_malformed")
            return


def _normalize_engine_measurements(context: NormalizationContext) -> None:
    raw = context.canonical_record
    normalized = context.normalized
    reasons = context.review_reasons

    engine_code = normalize_text(raw.get("engine_code"))
    if engine_code is not None:
        normalized["engine_code"] = engine_code.upper()
        context.applied_rule_ids.append("ENGINE-CODE-V1")

    family_code = normalize_text(raw.get("engine_family_code"))
    if family_code is not None:
        normalized["engine_family_code"] = family_code.upper()
        context.applied_rule_ids.append("ENGINE-FAMILY-CODE-V1")

    family_name = normalize_text(raw.get("engine_family_name"))
    if family_name is not None:
        normalized["engine_family_name"] = family_name
        context.applied_rule_ids.append("ENGINE-FAMILY-NAME-V1")

    power_fields = (
        ("kw", Decimal("1"), "UNIT-POWER-KW-V1"),
        ("power_ps", Decimal("0.73549875"), "UNIT-POWER-PS-V1"),
    )
    populated_power = [item for item in power_fields if raw.get(item[0]) not in (None, "")]
    if len(populated_power) > 1:
        reasons.append("power_source_ambiguous")
    elif populated_power:
        field_name, multiplier, rule_id = populated_power[0]
        power_kw = _positive_rounded_int(raw[field_name], multiplier=multiplier, maximum=2000)
        if power_kw is None:
            reasons.append(f"{field_name}_malformed")
        else:
            normalized["power_kw"] = power_kw
            normalized["power_source_unit"] = "kw" if field_name == "kw" else "metric_hp"
            context.applied_rule_ids.append(rule_id)

    displacement_fields = (
        ("ccm", Decimal("1"), "UNIT-DISPLACEMENT-CCM-V1"),
        ("displacement_l", Decimal("1000"), "UNIT-DISPLACEMENT-LITRE-V1"),
    )
    populated_displacement = [
        item for item in displacement_fields if raw.get(item[0]) not in (None, "")
    ]
    if len(populated_displacement) > 1:
        reasons.append("displacement_source_ambiguous")
    elif populated_displacement:
        field_name, multiplier, rule_id = populated_displacement[0]
        displacement_cc = _positive_rounded_int(
            raw[field_name], multiplier=multiplier, maximum=50_000
        )
        if displacement_cc is None:
            reasons.append(f"{field_name}_malformed")
        else:
            normalized["displacement_cc"] = displacement_cc
            normalized["displacement_source_unit"] = "ccm" if field_name == "ccm" else "litre"
            context.applied_rule_ids.append(rule_id)


def _normalize_transmission(context: NormalizationContext) -> None:
    raw = context.canonical_record
    normalized = context.normalized
    code = normalize_text(raw.get("gearbox"))
    code_rule: TranslationRule | None = None
    if code is not None:
        matches = RULE_SET.match("transmission_code", code.upper())
        if not matches:
            context.review_reasons.append("transmission_code_unknown")
            return
        code_rule = matches[0]
        _record_dictionary_match(context, code_rule, source_field="gearbox", source_term=code)
        normalized[code_rule.canonical_field] = code_rule.canonical_value
        if code_rule.display_value is not None:
            normalized["transmission_display"] = code_rule.display_value
        context.applied_rule_ids.append(code_rule.rule_id)

    marketing = _marketing_match(raw, "transmission_marketing")
    if marketing is None:
        return
    rule, source_field, source_term = marketing
    _record_dictionary_match(
        context,
        rule,
        source_field=source_field,
        source_term=source_term,
    )
    manufacturer = normalized.get("manufacturer")
    if not _manufacturer_is_in_scope(rule, manufacturer):
        context.review_reasons.append("transmission_marketing_scope_unresolved")
        return
    if rule.requires_electrification and "electricity" not in _raw_fuel_carriers(raw):
        context.candidate_rule_ids.append(rule.rule_id)
        context.review_reasons.append("transmission_electrification_evidence_missing")
        return
    if code_rule is not None and code_rule.canonical_value != rule.canonical_value:
        context.candidate_rule_ids.append(rule.rule_id)
        context.review_reasons.append("transmission_structured_marketing_conflict")
        return
    normalized[rule.canonical_field] = rule.canonical_value
    if rule.display_value is not None:
        normalized["transmission_display"] = rule.display_value
    context.applied_rule_ids.append(rule.rule_id)


def _vehicle_scope(raw: dict[str, Any]) -> str:
    vehicle_type = (_normalized_entity(raw.get("vehicle_type")) or "").replace(" ", "")
    eu_category = (_normalized_entity(raw.get("eu_category")) or "").replace(" ", "")
    if eu_category.startswith("M1") or vehicle_type in {"PERSONBIL", "PB"}:
        return "passenger"
    if eu_category.startswith("N") or vehicle_type in {"LASTBIL", "LB"}:
        return "goods"
    if eu_category.startswith("O") or vehicle_type in {"SLAPFORDON", "SLÄPFORDON"}:
        return "trailer"
    if eu_category.startswith(("M2", "M3")) or vehicle_type in {"BUSS", "BUS"}:
        return "bus"
    return "other"


def _normalize_bodywork(context: NormalizationContext) -> None:
    raw = context.canonical_record
    normalized = context.normalized
    code = normalize_text(raw.get("body_code"))
    scope = _vehicle_scope(raw)
    code_rule: TranslationRule | None = None
    if code is not None:
        matches = RULE_SET.match("bodywork_code", code.upper(), vehicle_scope=scope)
        if not matches:
            context.review_reasons.append("bodywork_code_unresolved_for_category")
            return
        code_rule = matches[0]
        _record_dictionary_match(context, code_rule, source_field="body_code", source_term=code)
        normalized["bodywork_registry_label_sv"] = code_rule.display_value
        if code_rule.canonical_value is not None:
            normalized[code_rule.canonical_field] = code_rule.canonical_value
        elif code.upper() in {"98", "SG"}:
            context.review_reasons.append("bodywork_requires_review")
        context.applied_rule_ids.append(code_rule.rule_id)

    marketing = _marketing_match(raw, "bodywork_marketing", vehicle_scope=scope)
    if marketing is None:
        return
    rule, source_field, source_term = marketing
    _record_dictionary_match(
        context,
        rule,
        source_field=source_field,
        source_term=source_term,
    )
    manufacturer = normalized.get("manufacturer")
    if not _manufacturer_is_in_scope(rule, manufacturer):
        context.review_reasons.append("bodywork_marketing_scope_unresolved")
        return
    if rule.rule_id == "BDY-013" and (
        code_rule is None or code_rule.rule_id not in {"BDY-118", "BDY-SA"}
    ):
        context.candidates[rule.canonical_field] = rule.canonical_value
        context.candidate_rule_ids.append(rule.rule_id)
        context.review_reasons.append("motorhome_supporting_evidence_missing")
        return
    if code_rule is not None and code_rule.canonical_value != rule.canonical_value:
        context.candidate_rule_ids.append(rule.rule_id)
        context.review_reasons.append("bodywork_structured_marketing_conflict")
        return
    normalized[rule.canonical_field] = rule.canonical_value
    context.applied_rule_ids.append(rule.rule_id)


def _normalize_drive(
    raw: dict[str, Any],
    candidates: dict[str, Any],
    candidate_rules: list[str],
    reasons: list[str],
) -> None:
    flag = normalize_text(raw.get("is_4wd"))
    if flag is None:
        return
    if flag == "1":
        candidates["drive_type"] = "awd"
        candidate_rules.append("DRV-008")
    elif flag != "0":
        reasons.append("is_4wd_malformed")


def _normalize_fuel(context: NormalizationContext) -> None:
    raw = context.canonical_record
    normalized = context.normalized
    carriers: list[str] = []
    for field_name in ("fuel1", "fuel2", "fuel3"):
        code = normalize_text(raw.get(field_name))
        if code is None or code == "0":
            continue
        canonical_code = code.upper().lstrip("0") or "0"
        matches = RULE_SET.match("fuel_carrier", canonical_code)
        if not matches:
            context.review_reasons.append(f"{field_name}_code_unknown")
            continue
        rule = matches[0]
        if rule.decision != "accepted" or rule.canonical_value is None:
            context.candidate_rule_ids.append(rule.rule_id)
            continue
        carrier = rule.canonical_value
        if carrier not in carriers:
            carriers.append(carrier)
        context.applied_rule_ids.append(rule.rule_id)
        _record_dictionary_match(
            context,
            rule,
            source_field=field_name,
            source_term=code,
        )
    if carriers:
        normalized["energy_sources"] = carriers

    combination = normalize_text(raw.get("fuel_combo"))
    if combination is not None:
        matches = RULE_SET.match("fuel_combination", combination.upper())
        if not matches:
            context.review_reasons.append("fuel_combination_code_unknown")
        else:
            rule = matches[0]
            combination_name = rule.canonical_value
            normalized[rule.canonical_field] = combination_name
            context.applied_rule_ids.append(rule.rule_id)
            _record_dictionary_match(
                context,
                rule,
                source_field="fuel_combo",
                source_term=combination,
            )
            if combination_name == "tri_fuel" and len(carriers) != 3:
                context.review_reasons.append("tri_fuel_carrier_count_conflict")
            elif combination_name != "tri_fuel" and len(carriers) < 2:
                context.review_reasons.append("fuel_combination_carrier_count_conflict")

    ev_config = normalize_text(raw.get("ev_config"))
    if ev_config:
        matches = RULE_SET.match("electrification", ev_config.upper())
        if not matches:
            context.review_reasons.append("electrification_configuration_unknown")
            return
        rule = matches[0]
        electricity = "electricity" in carriers
        combustion = any(carrier in {"petrol", "diesel"} for carrier in carriers)
        evidence_valid = True
        if rule.rule_id == "ELEC-001":
            evidence_valid = electricity and not combustion
        elif rule.rule_id in {"ELEC-002", "ELEC-003"}:
            evidence_valid = electricity and combustion
        if not evidence_valid:
            context.candidate_rule_ids.append(rule.rule_id)
            context.review_reasons.append("electrification_fuel_evidence_conflict")
        else:
            normalized[rule.canonical_field] = rule.canonical_value
            context.applied_rule_ids.append(rule.rule_id)
            _record_dictionary_match(
                context,
                rule,
                source_field="ev_config",
                source_term=ev_config,
            )

    marketing = _marketing_match(raw, "electrification_marketing")
    if marketing is None:
        return
    rule, source_field, source_term = marketing
    _record_dictionary_match(
        context,
        rule,
        source_field=source_field,
        source_term=source_term,
    )
    if not _manufacturer_is_in_scope(rule, normalized.get("manufacturer")):
        context.review_reasons.append("electrification_marketing_scope_unresolved")
        return
    existing = normalized.get(rule.canonical_field)
    if existing is not None and existing != rule.canonical_value:
        context.candidate_rule_ids.append(rule.rule_id)
        context.review_reasons.append("electrification_structured_marketing_conflict")
        return
    normalized[rule.canonical_field] = rule.canonical_value
    context.applied_rule_ids.append(rule.rule_id)


def _alias_types_present(raw: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    if normalize_text(raw.get("plate")) is not None:
        aliases.append("plate")
    if normalize_text(raw.get("vin")) is not None:
        aliases.append("vin")
    if normalize_text(raw.get("model")) is not None:
        aliases.append("model_name")
    return aliases


def _initialize_context(context: NormalizationContext) -> None:
    context.normalized["market"] = ["SE"]
    context.normalized["alias_types_present"] = _alias_types_present(context.canonical_record)


def _apply_manufacturer(context: NormalizationContext) -> None:
    _normalize_manufacturer(
        context.canonical_record,
        context.normalized,
        context.candidates,
        context.applied_rule_ids,
        context.candidate_rule_ids,
        context.review_reasons,
    )


def _apply_model_family(context: NormalizationContext) -> None:
    _normalize_model_family(
        context.canonical_record,
        context.normalized,
        context.candidates,
    )


def _apply_dates(context: NormalizationContext) -> None:
    _normalize_dates(context)


def _apply_engine_measurements(context: NormalizationContext) -> None:
    _normalize_engine_measurements(context)


def _apply_transmission(context: NormalizationContext) -> None:
    _normalize_transmission(context)


def _apply_bodywork(context: NormalizationContext) -> None:
    _normalize_bodywork(context)


def _apply_drive(context: NormalizationContext) -> None:
    _normalize_drive(
        context.canonical_record,
        context.candidates,
        context.candidate_rule_ids,
        context.review_reasons,
    )


def _apply_fuel(context: NormalizationContext) -> None:
    _normalize_fuel(context)


DEFAULT_PIPELINE = NormalizationPipeline(
    version=PIPELINE_VERSION,
    transformers=(
        TextCanonicalizationTransformer(),
        _RuleTransformer(
            transformer_id="ts.initialize",
            order=10,
            default_rule_id="SYS-TS-INIT",
            handler=_initialize_context,
        ),
        _RuleTransformer(
            transformer_id="ts.manufacturer",
            order=20,
            default_rule_id="MFR-CLASSIFY-V1",
            handler=_apply_manufacturer,
            source_fields=("manufacturer", "brand", "base_manufacturer"),
            normalized_confidence_effect=0.2,
            candidate_confidence_effect=0.05,
        ),
        _RuleTransformer(
            transformer_id="ts.model-family",
            order=30,
            default_rule_id="MODEL-CANDIDATE-V1",
            handler=_apply_model_family,
            source_fields=("model",),
            candidate_confidence_effect=0.05,
        ),
        _RuleTransformer(
            transformer_id="ts.dates",
            order=40,
            default_rule_id="DATE-EXTRACT-V1",
            handler=_apply_dates,
            source_fields=(
                "registration_date",
                "production_from",
                "production_to",
                "build_date",
                "build_month",
                "model_year",
                "vehicle_year",
            ),
            normalized_confidence_effect=0.1,
        ),
        _RuleTransformer(
            transformer_id="ts.engine-measurements",
            order=45,
            default_rule_id="ENGINE-MEASUREMENT-EXTRACT-V1",
            handler=_apply_engine_measurements,
            source_fields=(
                "engine_code",
                "engine_family_code",
                "engine_family_name",
                "kw",
                "power_ps",
                "ccm",
                "displacement_l",
            ),
            normalized_confidence_effect=0.1,
        ),
        _RuleTransformer(
            transformer_id="ts.transmission",
            order=50,
            default_rule_id="TRN-LOOKUP-V1",
            handler=_apply_transmission,
            source_fields=("gearbox", "model", "variant", "version", "type"),
            normalized_confidence_effect=0.1,
        ),
        _RuleTransformer(
            transformer_id="ts.bodywork",
            order=60,
            default_rule_id="BDY-LOOKUP-V1",
            handler=_apply_bodywork,
            source_fields=(
                "body_code",
                "eu_category",
                "vehicle_type",
                "model",
                "variant",
                "version",
                "type",
            ),
            normalized_confidence_effect=0.1,
        ),
        _RuleTransformer(
            transformer_id="ts.drive",
            order=70,
            default_rule_id="DRV-LOOKUP-V1",
            handler=_apply_drive,
            source_fields=("is_4wd",),
            candidate_confidence_effect=0.05,
        ),
        _RuleTransformer(
            transformer_id="ts.fuel",
            order=80,
            default_rule_id="FUEL-LOOKUP-V1",
            handler=_apply_fuel,
            source_fields=(
                "fuel1",
                "fuel2",
                "fuel3",
                "fuel_combo",
                "ev_config",
                "model",
                "variant",
                "version",
                "type",
            ),
            candidate_confidence_effect=0.1,
        ),
    ),
)
