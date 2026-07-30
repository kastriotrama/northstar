"""Pure Transportstyrelsen normalization rules for SCRUM-82.

Only decisions marked accepted in the SCRUM-74/SCRUM-77 contract may populate
``normalized``. Proposed fuel and marketing decisions are retained under
``candidates`` so a pilot can measure them without silently promoting them to
canonical facts.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

MAPPING_VERSION = "ts-mapping-v1"
RULE_VERSION = "ts-translation-v1"

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

    def to_payload(self) -> dict[str, Any]:
        return {
            "normalized": self.normalized,
            "candidates": self.candidates,
            "candidate_rule_ids": list(self.candidate_rule_ids),
            "confidence": self.confidence,
        }


_TRANSMISSION_RULES: dict[str, tuple[str, str, str | None]] = {
    "M": ("manual", "TRN-001", None),
    "A": ("automatic", "TRN-002", None),
    "V": ("cvt", "TRN-003", "Variomatic"),
    "T": ("amt", "TRN-007", "Automated manual"),
    "Z": ("automatic", "TRN-008", None),
}

_PASSENGER_BODY_RULES: dict[str, tuple[str | None, str, str]] = {
    "AA": ("sedan", "BDY-101", "Sedan"),
    "AB": ("hatchback", "BDY-109", "Halvkombi"),
    "AC": ("wagon", "BDY-110", "Stationsvagn (kombivagn)"),
    "AD": ("coupe", "BDY-107", "Kupé"),
    "AE": ("convertible", "BDY-111", "Cabriolet"),
    "AF": ("multi_purpose_vehicle", "BDY-113", "Fordon avsett för flera ändamål"),
    "AG": ("cargo_wagon", "BDY-117", "Lastkombi"),
    "01": ("covered_body", "BDY-102", "Täckt"),
    "02": ("open_body", "BDY-116", "Öppet"),
    "03": ("wagon", "BDY-103", "Kombi"),
    "04": ("covered_body", "BDY-104", "Täckt, taklucka"),
    "05": ("wagon", "BDY-112", "Kombi, taklucka"),
    "06": ("covered_body", "BDY-105", "Täckt, taxi"),
    "07": ("wagon", "BDY-106", "Kombi, taxi"),
    "08": ("motorhome", "BDY-118", "Bostadsinredning"),
    "96": (None, "BDY-119", "Polisbil"),
    "98": (None, "BDY-120", "Övrigt"),
}

_GOODS_BODY_RULES: dict[str, tuple[str | None, str, str]] = {
    "BA": (None, "BDY-115", "Lastbil"),
    "BB": ("van", "BDY-108", "Skåpbil"),
    "BC": ("semi_trailer_tractor", "BDY-N-BC", "Dragfordon för påhängsvagn"),
    "BD": ("trailer_tractor", "BDY-N-BD", "Dragfordon för släpvagn"),
    "BE": ("pickup", "BDY-N-BE", "Pick-up"),
    "20": ("box_body", "BDY-114", "Skåp"),
}

_TRAILER_BODY_RULES: dict[str, tuple[str | None, str, str]] = {
    "20": ("box_body", "BDY-114", "Skåp"),
    "DA": ("semi_trailer", "BDY-O-DA", "Påhängsvagn"),
    "DB": ("drawbar_trailer", "BDY-O-DB", "Släpvagn med dragstång"),
    "DC": ("centre_axle_trailer", "BDY-O-DC", "Släpkärra"),
    "DE": ("rigid_drawbar_trailer", "BDY-O-DE", "Släpvagn med fast dragstång"),
    "DF": ("link_semi_trailer", "BDY-O-DF", "Link-påhängsvagn"),
    "DG": ("link_drawbar_trailer", "BDY-O-DG", "Link-släpvagn med dragstång"),
}

_SPECIAL_BODY_RULES: dict[str, tuple[str | None, str, str]] = {
    "SA": ("motorhome", "BDY-SA", "Campingbil"),
    "SB": (None, "BDY-SB", "Bepansrat fordon"),
    "SC": (None, "BDY-SC", "Ambulans"),
    "SD": (None, "BDY-SD", "Likbil"),
    "SE": ("caravan", "BDY-SE", "Husvagn"),
    "SF": (None, "BDY-SF", "Mobilkran"),
    "SG": (None, "BDY-SG", "Annat fordon avsett för särskilt ändamål"),
    "SH": (None, "BDY-SH", "Rullstolsanpassat fordon"),
    "SJ": ("dolly", "BDY-SJ", "Dollyaxel"),
    "SK": ("exceptional_load_trailer", "BDY-SK", "Släpvagn för exceptionell last"),
    "SL": (None, "BDY-SL", "Motorfordon för exceptionell last"),
    "SM": (None, "BDY-SM", "Redskapsbärare"),
}

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

_FUEL_CANDIDATES: dict[str, tuple[str, str]] = {
    "1": ("petrol", "FUEL-001"),
    "2": ("diesel", "FUEL-002"),
    "3": ("electricity", "FUEL-003"),
    "4": ("kerosene", "FUEL-004"),
    "5": ("lpg", "FUEL-005"),
    "6": ("producer_gas", "FUEL-006"),
    "7": ("ethanol", "FUEL-007"),
    "8": ("methanol", "FUEL-008"),
    "9": ("motor_gas", "FUEL-009"),
    "10": ("rapeseed_oil", "FUEL-010"),
    "11": ("paraffin_oil", "FUEL-011"),
    "12": ("natural_gas", "FUEL-012"),
    "13": ("biogas", "FUEL-013"),
    "14": ("e85", "FUEL-014"),
    "15": ("rme", "FUEL-015"),
    "16": ("methane", "FUEL-016"),
    "17": ("hydrogen", "FUEL-017"),
    "18": ("other", "FUEL-018"),
    "19": ("biodiesel", "FUEL-019"),
    "20": ("cng", "FUEL-020"),
    "21": ("lng", "FUEL-021"),
    "B": ("petrol", "FUEL-001"),
    "D": ("diesel", "FUEL-002"),
    "E": ("electricity", "FUEL-003"),
    "EL": ("electricity", "FUEL-003"),
}

_FUEL_COMBINATION_CANDIDATES: dict[str, tuple[str, str]] = {
    "B": ("bi_fuel", "FCOM-B"),
    "D": ("dual_fuel", "FCOM-D"),
    "F": ("flex_fuel", "FCOM-F"),
    "T": ("tri_fuel", "FCOM-T"),
}

_NON_WORD = re.compile(r"[^A-Z0-9ÅÄÖÉÜ]+")


def normalize_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value).strip()
    return normalized or None


def normalize_ts_record(raw_record: object) -> NormalizationOutcome:
    """Normalize one TS raw record without copying sensitive identifiers."""

    if not isinstance(raw_record, dict):
        return NormalizationOutcome(
            status="failed",
            normalized={},
            candidates={},
            applied_rule_ids=(),
            candidate_rule_ids=(),
            review_reasons=("raw_record_not_object",),
            confidence=0.0,
        )

    normalized: dict[str, Any] = {
        "market": ["SE"],
        "alias_types_present": _alias_types_present(raw_record),
    }
    candidates: dict[str, Any] = {}
    applied: list[str] = []
    candidate_rules: list[str] = []
    reasons: list[str] = []

    _normalize_manufacturer(
        raw_record,
        normalized,
        candidates,
        applied,
        candidate_rules,
        reasons,
    )
    _normalize_model_family(raw_record, normalized, candidates)
    _normalize_production_year(raw_record, normalized, reasons)
    _normalize_transmission(raw_record, normalized, applied, reasons)
    _normalize_bodywork(raw_record, normalized, applied, reasons)
    _normalize_drive(raw_record, candidates, candidate_rules, reasons)
    _normalize_fuel(raw_record, candidates, candidate_rules, reasons)

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
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        if format_name == "month" and len(text) == 6:
            return date(int(text[:4]), int(text[4:6]), 1)
    except ValueError:
        return None
    return None


def _normalize_production_year(
    raw: dict[str, Any],
    normalized: dict[str, Any],
    reasons: list[str],
) -> None:
    build_date = normalize_text(raw.get("build_date"))
    if build_date is not None:
        parsed = _parse_date(build_date, "day")
        if parsed is None:
            reasons.append("build_date_malformed")
            return
        normalized["production_year"] = parsed.year
        normalized["production_date_precision"] = "day"
        return
    build_month = normalize_text(raw.get("build_month"))
    if build_month is not None:
        parsed = _parse_date(build_month, "month")
        if parsed is None:
            reasons.append("build_month_malformed")
            return
        normalized["production_year"] = parsed.year
        normalized["production_date_precision"] = "month"
        return
    for field_name in ("model_year", "vehicle_year"):
        value = raw.get(field_name)
        if isinstance(value, int) and 1886 <= value <= 2200:
            normalized["production_year"] = value
            normalized["production_date_precision"] = field_name
            return
        if value not in (None, ""):
            reasons.append(f"{field_name}_malformed")
            return


def _normalize_transmission(
    raw: dict[str, Any],
    normalized: dict[str, Any],
    applied: list[str],
    reasons: list[str],
) -> None:
    code = normalize_text(raw.get("gearbox"))
    if code is None:
        return
    rule = _TRANSMISSION_RULES.get(code.upper())
    if rule is None:
        reasons.append("transmission_code_unknown")
        return
    transmission_type, rule_id, display = rule
    normalized["transmission_type"] = transmission_type
    if display is not None:
        normalized["transmission_display"] = display
    applied.append(rule_id)


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


def _normalize_bodywork(
    raw: dict[str, Any],
    normalized: dict[str, Any],
    applied: list[str],
    reasons: list[str],
) -> None:
    code = normalize_text(raw.get("body_code"))
    if code is None:
        return
    code = code.upper()
    scope = _vehicle_scope(raw)
    rule: tuple[str | None, str, str] | None = None
    if code in _SPECIAL_BODY_RULES:
        rule = _SPECIAL_BODY_RULES[code]
    elif scope == "passenger":
        rule = _PASSENGER_BODY_RULES.get(code)
    elif scope == "goods":
        rule = _GOODS_BODY_RULES.get(code)
    elif scope == "trailer":
        rule = _TRAILER_BODY_RULES.get(code)
    elif scope == "bus" and len(code) == 2 and "CA" <= code <= "CJ":
        rule = ("bus", "BDY-BUS", "Buss")

    if rule is None:
        reasons.append("bodywork_code_unresolved_for_category")
        return
    form, rule_id, display = rule
    normalized["bodywork_registry_label_sv"] = display
    if form is not None:
        normalized["bodywork_form"] = form
    elif code in {"98", "SG"}:
        reasons.append("bodywork_requires_review")
    applied.append(rule_id)


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


def _normalize_fuel(
    raw: dict[str, Any],
    candidates: dict[str, Any],
    candidate_rules: list[str],
    reasons: list[str],
) -> None:
    carriers: list[str] = []
    for field_name in ("fuel1", "fuel2", "fuel3"):
        code = normalize_text(raw.get(field_name))
        if code is None or code == "0":
            continue
        canonical_code = code.upper().lstrip("0") or "0"
        rule = _FUEL_CANDIDATES.get(canonical_code)
        if rule is None:
            reasons.append(f"{field_name}_code_unknown")
            continue
        carrier, rule_id = rule
        if carrier not in carriers:
            carriers.append(carrier)
        candidate_rules.append(rule_id)
    if carriers:
        candidates["energy_sources"] = carriers

    combination = normalize_text(raw.get("fuel_combo"))
    if combination is not None:
        rule = _FUEL_COMBINATION_CANDIDATES.get(combination.upper())
        if rule is None:
            reasons.append("fuel_combination_code_unknown")
        else:
            combination_name, rule_id = rule
            candidates["fuel_combination"] = combination_name
            candidate_rules.append(rule_id)
            if combination_name == "tri_fuel" and len(carriers) != 3:
                reasons.append("tri_fuel_carrier_count_conflict")
            elif combination_name != "tri_fuel" and len(carriers) < 2:
                reasons.append("fuel_combination_carrier_count_conflict")

    ev_config = (_normalized_entity(raw.get("ev_config")) or "").replace(" ", "")
    if ev_config:
        electrification: str | None = None
        electricity = "electricity" in carriers
        combustion = any(carrier in {"petrol", "diesel"} for carrier in carriers)
        if ev_config == "EL":
            if electricity and not combustion:
                electrification = "battery_electric"
                candidate_rules.append("ELEC-001")
            elif combustion:
                reasons.append("electric_configuration_combustion_conflict")
        elif "LADDHYBRID" in ev_config:
            electrification = "plug_in_hybrid"
            candidate_rules.append("ELEC-003")
        elif "ELHYBRID" in ev_config:
            electrification = "hybrid"
            candidate_rules.append("ELEC-002")
        if electrification is not None:
            candidates["electrification_type"] = electrification


def _alias_types_present(raw: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    if normalize_text(raw.get("plate")) is not None:
        aliases.append("plate")
    if normalize_text(raw.get("vin")) is not None:
        aliases.append("vin")
    if normalize_text(raw.get("model")) is not None:
        aliases.append("model_name")
    return aliases
