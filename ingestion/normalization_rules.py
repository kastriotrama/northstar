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
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
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
    TranslationRuleSet,
    load_translation_rule_set,
)

MAPPING_VERSION = "ts-mapping-v1"
RULE_VERSION = REVIEWED_RULE_SET_VERSION
PIPELINE_VERSION = "normalization-pipeline-v5"
RULE_SET = load_translation_rule_set(RULE_VERSION)

NormalizationStatus = Literal["resolved", "provisional", "review_required", "failed"]
ManufacturerEntityRules = Mapping[str, Mapping[str, Any]]


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
    "DR ING H C F PORSCHE AG": "Porsche",
    "KIA": "Kia",
    "KIA MOTORS": "Kia",
    "KIAMOTORSCORPORATION": "Kia",
    "KIACORPORATION": "Kia",
    "HYUNDAI": "Hyundai",
    "HYUNDAI MOTOR COMPANY": "Hyundai",
    "HYUNDAI MOTOR MANUFACTURING CZECH": "Hyundai",
    "SUBARU": "Subaru",
    "SUBARU CORPORATION": "Subaru",
    "TESLA": "Tesla",
    "TESLA INC": "Tesla",
    "SEAT": "SEAT",
    "SEAT S A": "SEAT",
    "MINI": "MINI",
    "JEEP": "Jeep",
    "DAIMLER AG": "Mercedes-Benz",
    "BAYERISCHE MOTOREN WERKE AG": "BMW",
    "BRENDERUP AB": "Brenderup",
    "SORELPOL SP Z O O": "Sorelpol",
    "TEMARED SP Z O O": "Temared",
    "VARIANT A S": "Variant",
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
    "KABE AB": ("KABE", "MFR-107"),
}

# Exact legacy Brand values reviewed from the 250-record Transportstyrelsen
# sample. These records have no Tillverkare, and Brand combines make/model text.
# Exact canonical keys prevent a short manufacturer prefix from matching an
# unrelated company or vehicle.
_REVIEWED_LEGACY_BRAND_ENTITIES: dict[str, str] = {
    "AKTIV STABIL 1000 15": "Aktiv Stabil",
    "ALFA ROMEO 940": "Alfa Romeo",
    "ALFA ROMEO GIULIETTA SPR": "Alfa Romeo",
    "ÅTM 10014": "ÅTM",
    "ÅTM 7512": "ÅTM",
    "BELARUS 820": "Belarus",
    "BM VOLVO 861 DR": "Volvo BM",
    "BOJ SLÄP": "BOJ",
    "BOLINDER MUNKTELL VOLVO": "Volvo BM",
    "BRENDERUP": "Brenderup",
    "BRÖDERNA FRANSSON 850": "Bröderna Fransson",
    "BRP": "BRP",
    "BUYANG FA D300": "Buyang",
    "CABBY NOVA 472 CT SLÄP": "Cabby",
    "CASE 1490 4 WD": "Case",
    "CHEVROLET IMPALA": "Chevrolet",
    "CHEVROLET KL1T": "Chevrolet",
    "CHEVROLET VAN": "Chevrolet",
    "CI SMÅLANDIA 450": "CI Smålandia",
    "CLUB CAR DS": "Club Car",
    "CO SLÄPET 2000 BTR": "CO-Släpet",
    "CO SLÄPET 750 JUBI": "CO-Släpet",
    "DUCATI SUPERSPORT 900": "Ducati",
    "FOBO 10016": "FOBO",
    "FORDSON DEXTA": "Fordson",
    "FORDSON MAJOR": "Fordson",
    "GISEBO G 25 16 1": "Gisebo",
    "GISEBO G 25 18 2": "Gisebo",
    "GOES": "Goes",
    "GOES CF500A": "Goes",
    "HARLEY DAVIDSON": "Harley-Davidson",
    "HARLEY DAVIDSON FXE 1200": "Harley-Davidson",
    "HOLDER P70C 460": "Holder",
    "HUSQVARNA MOD KAF 256A": "Husqvarna",
    "HYDRO MEKANO": "Hydro Mekano",
    "IGASS 750": "IGASS",
    "INDIAN": "Indian",
    "INTERNATIONAL 584": "International",
    "J DEERE 2250 4WD MC1": "John Deere",
    "KAWASAKI ZR 550B": "Kawasaki",
    "KAWASAKI ZX900 C": "Kawasaki",
    "KYLINGEKÄRRAN 1500 HT 18": "Kylingekärran",
    "LAND ROVER 109S W PETROL": "Land Rover",
    "LINCOLN CONTINENTAL": "Lincoln",
    "LÖÖVESLÄPET LS 1300": "Löövesläpet",
    "LÖÖVESLÄPET LS 1300 CAR": "Löövesläpet",
    "MARKLUNDS BIL SMIDE MB": "Marklunds Bil & Smide",
    "MASSEY FERGUSON 135": "Massey Ferguson",
    "MASSEY FERGUSON 135 S": "Massey Ferguson",
    "MAZDA 626 KOMBI 2 0D GLX": "Mazda",
    "NEPTUN": "Neptun",
    "NUFFIELD 4 DM": "Nuffield",
    "OLDSMOBILE DYNAMIC88 4DR": "Oldsmobile",
    "OLDSMOBILE SUPER 88": "Oldsmobile",
    "PLYMOUTH SPECIAL DE LUXE": "Plymouth",
    "POLARIS XP 850 E": "Polaris",
    "POLARVAGNEN POLAR 425": "Polar",
    "POLARVAGNEN POLAR P520": "Polar",
    "REKOTRAILER": "Reko Trailer",
    "SAAB 9 3 S 5D 2 0I": "Saab",
    "SAAB 900 2 0I DX55J": "Saab",
    "SAAB 900 I8 AB25J": "Saab",
    "SAAB 9000 CSE 2 3 TURBO": "Saab",
    "SAAB 96 V4 LHD": "Saab",
    "SAAB 99 2 0 CM 2": "Saab",
    "SAAB SPORT": "Saab",
    "SAAB V 4": "Saab",
    "SCABIA 50 S": "Scania",
    "SCANIAVABIS B 5658": "Scania-Vabis",
    "SC SÄVSJÖ 5 6 SC SR": "SC-Sävsjö",
    "SKODA FELICIA": "Škoda",
    "SM SLÄPET 2565": "SM-Släpet",
    "SOLARIS URBINO 12LE CNG": "Solaris",
    "SOLIFER ARTIC 450": "Solifer",
    "STEYR": "Steyr",
    "STUDEBAKER PRESIDENT": "Studebaker",
    "SUZUKI WVCG": "Suzuki",
    "TEMARED": "Temared",
    "THULE 0600UVSL": "Thule",
    "THULE B01": "Thule",
    "THULE F1425650U": "Thule",
    "TIAB 7910": "TIAB",
    "TIKI": "Tiki Treiler",
    "TIKI B131": "Tiki Treiler",
    "TIKI TREILER B 350P": "Tiki Treiler",
    "TIKI TREILER BT 700": "Tiki Treiler",
    "TIKI TREILER C121": "Tiki Treiler",
    "TIKI TREILER C163": "Tiki Treiler",
    "TK TRAILER ES": "TK Trailer",
    "TRAILERGRUPPEN L 1001": "Trailergruppen",
    "TRANSPORTMEKANO": "Transportmekano",
    "TRI STAR TSF SD CHASSI": "Tri-Star",
    "TRIUMPH TIGER 900": "Triumph",
    "VALERYD FAVÖR 1300": "Valeryd",
    "VALTRA": "Valtra",
    "VESPA SUPER": "Vespa",
    "VOLKSW 1500LIM113 AVK": "Volkswagen",
    "VW CADDY SKÅP": "Volkswagen",
    "VW CARAVELLE": "Volkswagen",
    "VW PASSAT VAR TDI": "Volkswagen",
    "WILK S4 500": "Wilk",
    "WILK S4 560": "Wilk",
    "YAMAHA YZF1000R 4VD": "Yamaha",
}

_REVIEWED_EXACT_BRAND_REPAIRS: dict[str, str] = {
    "DAIMLER 2 5 V8": "Daimler",
    "DAIMLER SOVEREIGN XJ6L": "Daimler",
    "HYUNDAI EL": "Hyundai",
    "MERCEDES BENZ 197": "Mercedes-Benz",
}

_EVIDENCE_ONLY_MANUFACTURER_ALIASES: dict[str, str] = {
    "DS": "DS",
    "LYNK CO": "Lynk & Co",
    "ALFA ROMEO": "Alfa Romeo",
    "ALFA ROMEO SPA": "Alfa Romeo",
    "MITSUBISHI": "Mitsubishi",
}

_CORPORATE_GROUP_MARKERS = ("STELLANTIS", "PSA", "FCA")

_MODEL_MANUFACTURERS: dict[str, str] = {
    "NIRO": "Kia",
    "COOPER": "MINI",
    "COMPASS": "Jeep",
    "2008": "Peugeot",
    "C4 X": "Citroën",
    "ORA FUNKY CAT": "ORA",
    "DS 7 CROSSBACK": "DS",
    "LYNK CO 01": "Lynk & Co",
    "LYNK AND CO 01": "Lynk & Co",
    "PASSAT": "Volkswagen",
    "PASSAT CC": "Volkswagen",
    "GOLF": "Volkswagen",
    "TIGUAN": "Volkswagen",
    "POLO": "Volkswagen",
    "UP": "Volkswagen",
    "TRANSPORTER": "Volkswagen",
    "V60": "Volvo",
    "V70": "Volvo",
    "XC60": "Volvo",
    "XC70": "Volvo",
    "FOCUS": "Ford",
    "KUGA": "Ford",
    "TRANSIT CUSTOM": "Ford",
    "B MAX": "Ford",
    "CIVIC TOURER": "Honda",
    "CR V": "Honda",
    "PULSAR": "Nissan",
    "KING CAB": "Nissan",
    "CAPTUR": "Renault",
    "KANGOO": "Renault",
    "AURIS": "Toyota",
    "YARIS": "Toyota",
    "DUCATO": "Fiat",
    "IX35": "Hyundai",
    "SLS AMG": "Mercedes-Benz",
    "STELVIO": "Alfa Romeo",
}

_VIN_WMI_MANUFACTURERS: dict[str, str] = {
    "KNA": "Kia",
    "KND": "Kia",
    "KNE": "Kia",
    "U5Y": "Kia",
    "WMW": "MINI",
    "WMX": "MINI",
    "YV1": "Volvo",
    "YV4": "Volvo",
    "WBA": "BMW",
    "WBS": "BMW",
    "WDB": "Mercedes-Benz",
    "WDC": "Mercedes-Benz",
    "WAU": "Audi",
    "WVW": "Volkswagen",
    "WVG": "Volkswagen",
    "WF0": "Ford",
    "VF7": "Citroën",
    "ZAR": "Alfa Romeo",
}

_FAB_CODE_MANUFACTURERS: dict[str, str] = {
    "AR": "Alfa Romeo",
    "CI": "Citroën",
    "HK": "Hyundai",
    "MB": "Mercedes-Benz",
    "NA": "Nissan",
    "OP": "Opel",
    "VW": "Volkswagen",
}

_PRIMARY_SPECIAL_PURPOSE_CODES: dict[str, tuple[str, str]] = {
    "75": ("fire_rescue_vehicle", "Brandfordon"),
    "88": ("customs_vehicle", "Tull"),
    "89": ("coast_guard_vehicle", "Kustbevakning"),
    "91": ("recovery_vehicle", "Bärgningsfordon"),
    "93": ("police", "Polis"),
    "96": ("police", "Polisbil"),
    "95": ("fire_rescue_vehicle", "Brandfordon övrigt"),
    "99": ("ambulance", "Ambulans"),
}

_SECONDARY_PURPOSE_CODES: dict[str, tuple[str, str, str]] = {
    "06": ("usage_type", "taxi", "Taxi"),
    "93": ("special_purpose_type", "police", "Polis"),
    "96": ("special_purpose_type", "police", "Polisbil"),
    "SA": ("special_purpose_type", "motor_caravan", "Campingbil"),
    "SB": ("special_purpose_type", "armoured_vehicle", "Bepansrat fordon"),
    "SC": ("special_purpose_type", "ambulance", "Ambulans"),
    "SD": ("special_purpose_type", "hearse", "Likbil"),
    "SG": ("special_purpose_type", "other_special_purpose", "Annat särskilt ändamål"),
    "SH": ("special_purpose_type", "wheelchair_accessible", "Rullstolsanpassat fordon"),
}

_TEXT_CODE_DEFINITIONS: dict[str, tuple[str, str, str]] = {
    "T12A": ("Amatörbyggt fordon", "Amateur-built vehicle", "amateur_built"),
    "T12B": ("Ombyggt fordon", "Rebuilt vehicle", "rebuilt"),
    "T12BF": (
        "Amatörbyggt fordon med besiktningsbefrielse",
        "Inspection-exempt amateur-built vehicle",
        "amateur_built_inspection_exempt",
    ),
    "T12C": ("Amatörbyggd bil, byggsats", "Amateur-built kit car", "amateur_built_kit"),
    "T12E": ("Provbil", "Test vehicle", "test_vehicle"),
    "T12S": ("Provfordon", "Trial vehicle", "trial_vehicle"),
    "T12D": ("Tidigare EEG-typgodkänt importfordon", "Previously EEG-approved import", "imported_vehicle"),
    "T13A": ("Sammansatt av delar från flera fordon", "Built from parts from multiple vehicles", "composite_vehicle"),
    "T13K": ("Första datum i trafik uppskattat", "Estimated first-use date", "estimated_registration_history"),
    "T14D": ("Tidigare EES-typgodkänt fordon", "Previously EEA type-approved vehicle", "imported_vehicle"),
    "T14F": ("Etappvis typgodkänt fordon", "Multi-stage type-approved vehicle", "multi_stage_vehicle"),
    "T14G": ("Ändrat fordon med grundfordon", "Modified vehicle with a base vehicle", "modified_vehicle"),
    "T16X": ("Identitetsbärare ersatt", "Identity carrier replaced", "identity_modified"),
    "T17A": ("Taxibil utan mellanvägg", "Taxi without partition", "taxi_equipment"),
    "T17B": ("Taxibil utan taxameter", "Taxi without taximeter", "taxi_equipment"),
    "T17BA": ("Taxibil med särskild utrustning", "Taxi with special equipment", "taxi_equipment"),
    "T17C": ("Särskild karosserikod i textfält", "Special body code carried in text", "special_body_code_carrier"),
    "T17U": ("Rallybil med utbytt karosseri", "Rally car with replaced body", "rally_modified"),
    "T20A": ("Sjukbil med bårutrustning", "Medical vehicle with stretcher equipment", "medical_transport"),
    "T20F": ("Baksäte ej för personbefordran", "Rear seat unavailable for passengers", "occupant_safety_modified"),
    "T20G": ("Sjuktransport med bårplatser", "Medical transport with stretcher positions", "medical_transport"),
    "T31A": ("Motorn utbytt", "Engine replaced", "engine_replaced"),
    "T31AX": ("Motorn utbytt med särskilda utsläppskrav", "Engine replaced under specific emissions requirements", "engine_replaced"),
    "T31AY": ("Motorn modifierad med särskilda utsläppskrav", "Engine modified under specific emissions requirements", "engine_modified"),
    "T31B": ("Motorn ändrad", "Engine output modified", "engine_modified"),
    "T31EA": ("Konverterad för etanoldrift", "Converted to ethanol operation", "fuel_converted_ethanol"),
    "T31EB": ("Konverterad för etanoldrift", "Converted to ethanol operation", "fuel_converted_ethanol"),
    "T31EC": ("Konverterad för etanoldrift", "Converted to ethanol operation", "fuel_converted_ethanol"),
    "T31ED": ("Konverterad för etanoldrift", "Converted to ethanol operation", "fuel_converted_ethanol"),
    "T31EE": ("Konverterad för metangasdrift", "Converted to methane operation", "fuel_converted_methane"),
    "T31EF": ("Konverterad för metangasdrift", "Converted to methane operation", "fuel_converted_methane"),
    "T71R": ("Rallybil av specialtyp", "Special-type rally car", "rally_vehicle"),
    "T71ZO": ("Rallybil av standardtyp", "Standard-type rally car", "rally_vehicle"),
}

_TEXT_CODE_FLAGS: dict[str, str] = {
    "T13A": "composite_vehicle",
    "T14F": "multi_stage_vehicle",
    "T14G": "modified_vehicle",
    "T16X": "identity_modified",
    "T17A": "taxi",
    "T17B": "taxi",
    "T17BA": "taxi",
    "T17C": "special_body_code_text",
    "T17U": "rally_vehicle",
    "T20A": "medical_transport_vehicle",
    "T20F": "occupant_safety_modified",
    "T20G": "medical_transport_vehicle",
    "T31A": "engine_replaced",
    "T31AX": "engine_replaced",
    "T31AY": "engine_modified",
    "T31B": "engine_modified",
    "T31EA": "fuel_converted",
    "T31EB": "fuel_converted",
    "T31EC": "fuel_converted",
    "T31ED": "fuel_converted",
    "T31EE": "fuel_converted",
    "T31EF": "fuel_converted",
    "T71R": "rally_vehicle",
    "T71ZO": "rally_vehicle",
}

_SPECIAL_MODIFIED_TEXT_CODES = frozenset({"T12A", "T12B", "T12BF", "T12C"})

_MOTORHOME_REGISTERED_MARQUE = re.compile(
    r"^(?:ADRIA|ADRIA\s+SUN\s+LIVING|MAN\s+ADRIA|DETHLEFFS|KABE|"
    r"B[ÜU]RSTNER|CAPRON|CARTHAGO|KNAUS|MCLOUIS|SUN\s+LIVING|"
    r"KARMANN[- ]MOBIL|RAPIDO|RIMOR|WEINSBERG|CARADO|HYMER|CHAUSSON|"
    r"POESSL|PÖSSL)(?:\b|$)",
    flags=re.IGNORECASE,
)

_MOTORHOME_MARQUE_FAB_CODES = frozenset(
    {"AA", "BN", "C*", "C8", "H7", "K+", "KB", "KN", "MA", "R+", "WG"}
)
_SPECIAL_BODY_CODE_FLAGS: dict[str, str] = {
    "06": "taxi",
    "75": "fire_rescue_vehicle",
    "88": "customs_vehicle",
    "89": "coast_guard_vehicle",
    "91": "recovery_vehicle",
    "93": "police_vehicle",
    "96": "police_vehicle",
    "95": "fire_rescue_vehicle",
    "99": "ambulance",
    "SA": "motor_caravan",
    "SB": "armoured_vehicle",
    "SC": "ambulance",
    "SD": "hearse",
    "SG": "other_special_purpose",
    "SH": "wheelchair_accessible",
}

_MARKETED_PARENT_CHILDREN: dict[str, frozenset[str]] = {
    "BMW": frozenset({"MINI"}),
    "PSA": frozenset({"Citroën", "Peugeot", "Opel", "DS"}),
    "FCA": frozenset({"Fiat", "Jeep"}),
    "TOYOTA": frozenset({"Lexus"}),
    "GEELY": frozenset({"Lynk & Co"}),
    "GREAT_WALL": frozenset({"ORA"}),
}

_NON_WORD = re.compile(r"[^A-Z0-9ÅÄÖÉÜ]+")


def normalize_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value).strip()
    return normalized or None


def _source_text_codes(raw: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[object] = []
    if raw.get("text_code") is not None:
        values.append(raw["text_code"])
    configured = raw.get("text_codes")
    if isinstance(configured, list):
        values.extend(configured)
    codes: list[str] = []
    for value in values:
        candidate = value.get("code") if isinstance(value, dict) else value
        code = normalize_text(candidate)
        if code is not None and re.fullmatch(r"T[0-9A-Z]+", code.upper()):
            codes.append(code.upper())
    return tuple(dict.fromkeys(codes))


def _text_code_descriptions(raw: Mapping[str, Any]) -> tuple[str, ...]:
    configured = raw.get("text_code_descriptions")
    if not isinstance(configured, list):
        return ()
    return tuple(
        dict.fromkeys(
            text
            for value in configured
            if (text := normalize_text(value)) is not None
        )
    )


def _apply_special_vehicle_classification(context: NormalizationContext) -> None:
    raw = context.canonical_record
    normalized = context.normalized
    runtime_rules = context.runtime.get("manufacturer_entity_rules", {})
    policy = (
        runtime_rules.get("policy:TS-SPECIAL-VEHICLE-V1", {})
        if isinstance(runtime_rules, dict)
        else {}
    )
    special_modified_codes = frozenset(
        policy.get("special_modified_text_codes") or _SPECIAL_MODIFIED_TEXT_CODES
    )
    body_code_flags = dict(policy.get("special_body_code_flags") or _SPECIAL_BODY_CODE_FLAGS)
    text_code_flags = dict(policy.get("safety_text_code_flags") or _TEXT_CODE_FLAGS)
    manufacturer_group = str(policy.get("manufacturer_group") or "Special Modified")
    special_parts_policy = str(policy.get("parts_matching_policy") or "excluded")
    special_tecdoc_policy = str(policy.get("tecdoc_match_policy") or "exclude")
    other_special_parts_policy = str(
        policy.get("other_special_parts_matching_policy") or "manual_review"
    )
    codes = _source_text_codes(raw)
    descriptions = _text_code_descriptions(raw)
    text_code_evidence: list[dict[str, Any]] = []
    flags: list[str] = []
    modification_types: list[str] = []
    for code in codes:
        definition = _TEXT_CODE_DEFINITIONS.get(code)
        evidence: dict[str, Any] = {"code": code, "source": "transportstyrelsen"}
        if definition is not None:
            description_sv, description_en, classification = definition
            evidence.update(
                {"description_sv": description_sv, "description_en": description_en}
            )
            modification_types.append(classification)
        if code in text_code_flags:
            flags.append(text_code_flags[code])
        text_code_evidence.append(evidence)
    for description in descriptions:
        description_evidence: dict[str, Any] = {
            "code": None,
            "description_sv": description,
            "source": "source_description",
        }
        if _normalized_entity(description) == "AMATÖR":
            description_evidence["candidate_codes"] = ["T12A", "T12C", "T12BF"]
            modification_types.append("amateur_built")
        text_code_evidence.append(description_evidence)

    body_codes = tuple(
        dict.fromkeys(
            body_code
            for field_name in ("body_code", "body_code2", "body_code_extra")
            if (body_code := normalize_text(raw.get(field_name))) is not None
        )
    )
    for code in body_codes:
        flag = body_code_flags.get(code.upper())
        if flag is not None:
            flags.append(flag)

    descriptive_text = " ".join(
        text
        for field_name in ("brand", "model")
        if (text := _normalized_entity(raw.get(field_name))) is not None
    )
    descriptive_special_modified = bool(
        re.search(
            r"\b(?:HEMBYGG\w*|AMAT[ÖO]R\w*|REPLIK\w*|REPLICA\w*|"
            r"EGEN\s*TILLVERK\w*|EGEN\s*TILLV\w*|EGEN\s+T(?:ILL)?\b|"
            r"EGENTILLVERK\w*|EGENTILLV\w*|EGET(?:\b|\s+FABRIKAT))",
            descriptive_text,
        )
    ) or bool(re.search(r"(?:HEM+ABYGG|REPLIK|REPLICA|\bREPL\b)", descriptive_text))
    special_modified = bool(special_modified_codes.intersection(codes)) or any(
        _normalized_entity(description) == "AMATÖR" for description in descriptions
    ) or descriptive_special_modified
    if special_modified:
        flags.append("special_modified")
        normalized["vehicle_classification"] = "special_modified"
        normalized["manufacturer_group"] = manufacturer_group
        normalized["parts_matching_policy"] = special_parts_policy
        normalized["parts_matching_eligible"] = False
        normalized["parts_matching_exclusion_reason"] = "special_modified_vehicle"
        normalized["tecdoc_match_policy"] = special_tecdoc_policy
        if descriptive_special_modified and not special_modified_codes.intersection(codes):
            normalized["classification_source"] = "brand_model_text"
    elif (normalize_text(raw.get("brand")) or "").upper().startswith("TEST/"):
        normalized["record_route"] = "quarantine_test_record"
        normalized["parts_matching_policy"] = "excluded"
        normalized["parts_matching_eligible"] = False
        normalized["parts_matching_exclusion_reason"] = "test_record"
        flags.append("test_record")
    elif (
        "SA" in {code.upper() for code in body_codes}
        and (_normalized_entity(raw.get("vehicle_class")) or "") == "II"
    ) or (
        _MOTORHOME_REGISTERED_MARQUE.search(str(raw.get("brand") or ""))
        and (
            (_normalized_entity(raw.get("vehicle_class")) or "") == "II"
            or "SA" in {code.upper() for code in body_codes}
            or raw.get("base_manufacturer") not in (None, "")
            or (_normalized_entity(raw.get("body_code")) or "") == "AF"
            or _resolve_vin_manufacturer(raw.get("vin")) is not None
            or (_normalized_entity(raw.get("fab_code")) or "")
            in _MOTORHOME_MARQUE_FAB_CODES
        )
    ):
        normalized["record_route"] = "exclude_from_passenger_car_dataset"
        normalized["parts_matching_policy"] = "excluded"
        normalized["parts_matching_eligible"] = False
        normalized["parts_matching_exclusion_reason"] = "motorhome_out_of_passenger_scope"
        flags.append("motor_caravan")
    elif flags:
        normalized["parts_matching_policy"] = other_special_parts_policy
        normalized["parts_matching_eligible"] = False
    if modification_types:
        normalized["modification_types"] = list(dict.fromkeys(modification_types))
    if text_code_evidence:
        normalized["text_codes"] = text_code_evidence
    if body_codes and (text_code_evidence or flags or raw.get("body_code_extra")):
        normalized["registry_body_codes"] = list(body_codes)
    if flags:
        normalized["special_vehicle_flags"] = list(dict.fromkeys(flags))


def normalize_ts_record(
    raw_record: object,
    *,
    rule_set: TranslationRuleSet = RULE_SET,
    manufacturer_entity_rules: ManufacturerEntityRules | None = None,
) -> NormalizationOutcome:
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

    context = DEFAULT_PIPELINE.run(
        raw_record,
        runtime={
            "translation_rule_set": rule_set,
            "manufacturer_entity_rules": dict(manufacturer_entity_rules or {}),
        },
    )
    normalized = context.normalized
    candidates = context.candidates
    applied = context.applied_rule_ids
    candidate_rules = context.candidate_rule_ids
    reasons = context.review_reasons

    if normalized.get("record_route") in {
        "exclude_from_passenger_car_dataset",
        "quarantine_test_record",
    }:
        reasons[:] = [
            reason
            for reason in reasons
            if not reason.startswith("manufacturer_")
            and reason not in {"generic_custom_identity_unverified"}
        ]

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


def normalize_manufacturer_entity(value: object) -> str | None:
    """Return the exact canonical key used by reviewed manufacturer entities."""

    return _normalized_entity(value)


def _manufacturer_match_key(value: object) -> str | None:
    """Return an accent- and punctuation-tolerant key for reviewed alias matching."""

    text = normalize_text(value)
    if text is None:
        return None
    decomposed = unicodedata.normalize("NFKD", text.upper())
    folded = "".join(character for character in decomposed if not unicodedata.combining(character))
    return re.sub(r"[^A-Z0-9]+", " ", folded).strip()


def _manufacturer_compact_key(value: object) -> str | None:
    """Return a folded key for explicitly approved joined-name Brand rules."""

    key = _manufacturer_match_key(value)
    return key.replace(" ", "") if key is not None else None


def manufacturer_entity_catalog() -> tuple[Mapping[str, str | None], ...]:
    """Return the reviewed Tillverkare classifications used by the normalizer."""

    manufacturers = tuple(
        {
            "source_field": "manufacturer",
            "source_term": alias,
            "canonical_name": canonical,
            "entity_role": "vehicle_manufacturer",
            "base_behavior": "use_entity",
        }
        for alias, canonical in sorted(_MANUFACTURER_ALIASES.items())
    )
    converters = tuple(
        {
            "source_field": "manufacturer",
            "source_term": alias,
            "canonical_name": converter[0],
            "entity_role": "bodybuilder_converter",
            "base_behavior": "use_base_manufacturer",
        }
        for alias, converter in sorted(_CONVERTER_ALIASES.items())
    )
    corporate_groups = tuple(
        {
            "source_field": "manufacturer",
            "source_term": marker,
            "canonical_name": marker,
            "entity_role": "corporate_group",
            "base_behavior": "require_evidence_review",
        }
        for marker in _CORPORATE_GROUP_MARKERS
    )
    legacy_brand_entities = tuple(
        {
            "source_field": "brand",
            "source_term": source_term,
            "canonical_name": canonical,
            "entity_role": "vehicle_manufacturer",
            "base_behavior": "use_entity",
        }
        for source_term, canonical in sorted(_REVIEWED_LEGACY_BRAND_ENTITIES.items())
    )
    return manufacturers + converters + corporate_groups + legacy_brand_entities


def _manufacturer_entity_rule(
    raw: dict[str, Any],
    source_field: str,
    rules: ManufacturerEntityRules,
) -> Mapping[str, Any] | None:
    source_term = _normalized_entity(raw.get(source_field))
    if source_term is None:
        return None
    exact = rules.get(f"{source_field}:{source_term}")
    if exact is not None and _manufacturer_entity_evidence_matches(exact, raw):
        return exact
    match_key = _manufacturer_match_key(raw.get(source_field))
    if match_key is None:
        return None
    matches: list[tuple[int, Mapping[str, Any]]] = []
    for rule in rules.values():
        if (
            rule.get("kind") != "manufacturer_entity"
            or rule.get("source_field") != source_field
            or rule.get("match_type")
            not in {
                "diacritic_insensitive_prefix",
                "approved_compact_prefix",
                "evidence_regex",
                "exact_source_value",
            }
            or not _manufacturer_entity_evidence_matches(rule, raw)
        ):
            continue
        configured_aliases = rule.get("aliases")
        aliases = [
            rule.get("source_term"),
            *(configured_aliases if isinstance(configured_aliases, list) else []),
        ]
        if rule.get("match_type") == "evidence_regex":
            source_regex = rule.get("source_regex")
            if isinstance(source_regex, str) and re.search(
                source_regex, str(raw.get(source_field) or ""), flags=re.IGNORECASE
            ):
                matches.append((len(source_regex), rule))
            continue
        if rule.get("match_type") == "exact_source_value":
            exact_source_value = _normalized_entity(rule.get("exact_source_value"))
            if exact_source_value is not None and source_term == exact_source_value:
                matches.append((len(exact_source_value), rule))
            continue
        for alias in aliases:
            alias_key = _manufacturer_match_key(alias)
            if alias_key is None:
                continue
            if rule.get("match_type") == "approved_compact_prefix":
                compact_source = _manufacturer_compact_key(raw.get(source_field))
                compact_alias = _manufacturer_compact_key(alias)
                ordinary_prefix = match_key == alias_key or match_key.startswith(f"{alias_key} ")
                matched = (
                    not ordinary_prefix
                    and compact_source is not None
                    and compact_alias is not None
                    and compact_source.startswith(compact_alias)
                )
            else:
                matched = match_key == alias_key or match_key.startswith(f"{alias_key} ")
            if matched:
                matches.append((len(alias_key), rule))
                break
    if not matches:
        return None
    longest = max(length for length, _ in matches)
    best = [rule for length, rule in matches if length == longest]
    canonical_names = {rule.get("canonical_name") for rule in best}
    return best[0] if len(canonical_names) == 1 else None


def _manufacturer_entity_evidence_matches(
    rule: Mapping[str, Any], raw: dict[str, Any]
) -> bool:
    required_model_manufacturer = rule.get("requires_model_manufacturer")
    if isinstance(required_model_manufacturer, str) and (
        (
            _resolve_model_manufacturer(raw.get("model"))
            or _resolve_manufacturer(raw.get("model"))
        )
        != required_model_manufacturer
    ):
        return False
    required_terms = rule.get("requires_any_text_terms")
    if isinstance(required_terms, list):
        evidence = " ".join(
            value
            for field_name in ("brand", "model", "variant", "version")
            if (value := _normalized_entity(raw.get(field_name))) is not None
        )
        if not any(
            isinstance(term, str) and (_normalized_entity(term) or "") in evidence
            for term in required_terms
        ):
            return False
    evidence_values = [
        str(raw.get(field_name) or "")
        for field_name in ("brand", "model", "variant", "version", "type_text", "vin")
    ]
    evidence_text = " ".join(evidence_values)
    required_regex = rule.get("requires_any_field_regex")
    if isinstance(required_regex, str) and re.search(
        required_regex, evidence_text, flags=re.IGNORECASE
    ) is None:
        return False
    excluded_regex = rule.get("excludes_text_regex")
    if isinstance(excluded_regex, str) and re.search(
        excluded_regex, evidence_text, flags=re.IGNORECASE
    ) is not None:
        return False
    required_fab_code = rule.get("requires_fab_code")
    if isinstance(required_fab_code, str) and (
        _normalized_entity(raw.get("fab_code")) != _normalized_entity(required_fab_code)
    ):
        return False
    year_range = rule.get("requires_year_between")
    if isinstance(year_range, list) and len(year_range) == 2:
        year = _parse_year(raw.get("model_year")) or _parse_year(raw.get("vehicle_year"))
        if year is None or not int(year_range[0]) <= year <= int(year_range[1]):
            return False
    required_vin_regex = rule.get("requires_vin_regex")
    if isinstance(required_vin_regex, str) and re.search(
        required_vin_regex, str(raw.get("vin") or ""), flags=re.IGNORECASE
    ) is None:
        return False
    if rule.get("requires_no_manufacturer_conflict") is True:
        canonical = rule.get("canonical_name")
        recognized = {
            value
            for value in (
                _resolve_manufacturer(raw.get("brand")),
                _resolve_model_manufacturer(raw.get("model")),
                _resolve_base_manufacturer(raw.get("base_manufacturer")),
            )
            if value is not None
        }
        if isinstance(canonical, str) and any(value != canonical for value in recognized):
            return False
    return True


def _reviewed_brand_example_rule(
    raw: dict[str, Any], rules: ManufacturerEntityRules
) -> Mapping[str, Any] | None:
    brand = _normalized_entity(raw.get("brand"))
    if brand is None:
        return None
    for rule in rules.values():
        examples = rule.get("reviewed_examples")
        if (
            rule.get("kind") != "manufacturer_entity"
            or rule.get("source_field") != "brand"
            or rule.get("entity_role") != "vehicle_manufacturer"
            or not isinstance(examples, list)
        ):
            continue
        if brand in {
            normalized
            for example in examples
            if (normalized := _normalized_entity(example)) is not None
        }:
            return rule
    return None


def _manufacturer_policy(rules: ManufacturerEntityRules, rule_id: str) -> Mapping[str, Any] | None:
    return next(
        (
            rule
            for rule in rules.values()
            if rule.get("kind") == "manufacturer_match_policy" and rule.get("rule_id") == rule_id
        ),
        None,
    )


def _manufacturer_from_model_variant(
    raw: dict[str, Any],
    rules: ManufacturerEntityRules,
) -> tuple[str | None, tuple[str, ...], str | None, bool]:
    """Apply an approved whole-prefix fallback only when Brand is absent."""

    policy = _manufacturer_policy(rules, "MFR-MODEL-VARIANT-FALLBACK")
    if (
        policy is None
        or policy.get("match_type") != "whole_token_prefix"
        or normalize_text(raw.get("brand")) is not None
    ):
        return None, (), None, False
    allowed_fields = policy.get("allowed_fields")
    if not isinstance(allowed_fields, list):
        return None, (), None, False
    matches: dict[str, list[str]] = {}
    for field_name in allowed_fields:
        if field_name not in {"model", "variant"}:
            continue
        manufacturer = _resolve_manufacturer(raw.get(field_name))
        if manufacturer is not None:
            matches.setdefault(manufacturer, []).append(field_name)
    rule_id = policy.get("rule_id")
    normalized_rule_id = rule_id if isinstance(rule_id, str) else "MFR-MODEL-VARIANT-FALLBACK"
    if len(matches) > 1:
        return None, tuple(sorted(matches)), normalized_rule_id, True
    if not matches:
        return None, (), normalized_rule_id, False
    manufacturer, source_fields = next(iter(matches.items()))
    return manufacturer, tuple(source_fields), normalized_rule_id, False


def _manufacturer_from_brand_prefix(
    raw: dict[str, Any],
    rules: ManufacturerEntityRules,
) -> tuple[str | None, tuple[str, ...], bool, bool]:
    """Resolve a reviewed complete Brand prefix while rejecting compound marques."""

    policy = _manufacturer_policy(rules, "MFR-BRAND-PREFIX-FALLBACK")
    entity = _normalized_entity(raw.get("brand"))
    if policy is None or policy.get("match_type") != "whole_token_prefix" or entity is None:
        return None, (), False, False
    review_terms = policy.get("review_terms", [])
    if isinstance(review_terms, list):
        padded_entity = f" {entity} "
        if any(
            isinstance(term, str) and f" {_normalized_entity(term) or ''} " in padded_entity
            for term in review_terms
        ):
            return None, (str(policy.get("rule_id")),), True, False
    matches: dict[str, list[str]] = {}
    built_in = _resolve_manufacturer(entity)
    if built_in is not None:
        matches.setdefault(built_in, []).append(str(policy.get("rule_id")))
    for rule in rules.values():
        if (
            rule.get("kind") != "manufacturer_entity"
            or rule.get("source_field") != "brand"
            or rule.get("match_type") not in {"whole_token_prefix", "approved_compact_prefix"}
            or rule.get("entity_role") != "vehicle_manufacturer"
        ):
            continue
        alias = _normalized_entity(rule.get("source_term"))
        canonical = rule.get("canonical_name")
        required_model_manufacturer = rule.get("requires_model_manufacturer")
        if isinstance(required_model_manufacturer, str) and (
            _resolve_model_manufacturer(raw.get("model")) != required_model_manufacturer
        ):
            continue
        if (
            alias is not None
            and isinstance(canonical, str)
            and (entity == alias or entity.startswith(f"{alias} "))
        ):
            rule_id = rule.get("entity_id")
            matches.setdefault(canonical, []).append(
                rule_id if isinstance(rule_id, str) else str(policy.get("rule_id"))
            )
    if len(matches) > 1:
        return None, tuple(sorted(matches)), False, True
    if not matches:
        return None, (), False, False
    manufacturer, rule_ids = next(iter(matches.items()))
    policy_rule_id = str(policy.get("rule_id"))
    return manufacturer, tuple(dict.fromkeys((policy_rule_id, *rule_ids))), False, False


def _apply_manufacturer_entity_rule(
    rule: Mapping[str, Any],
    raw: dict[str, Any],
    normalized: dict[str, Any],
    candidates: dict[str, Any],
    applied: list[str],
    reasons: list[str],
) -> bool:
    role = rule.get("entity_role")
    behavior = rule.get("base_behavior")
    canonical_name = rule.get("canonical_name")
    entity_id = rule.get("entity_id") or "MFR-ENTITY-REVIEWED"
    marketed_brand_overrides = rule.get("marketed_brand_overrides")
    if role == "corporate_group" and isinstance(marketed_brand_overrides, Mapping):
        brand_key = _manufacturer_match_key(raw.get("brand"))
        child_matches = {
            _manufacturer_match_key(source): target
            for source, target in marketed_brand_overrides.items()
            if _manufacturer_match_key(source) is not None and isinstance(target, str)
        }
        marketed_manufacturer = child_matches.get(brand_key)
        if marketed_manufacturer is not None:
            normalized["manufacturer"] = marketed_manufacturer
            normalized["manufacturer_role"] = "vehicle_manufacturer"
            normalized["builder_converter_names"] = []
            normalized["manufacturer_evidence"] = ["brand", "manufacturer"]
            applied.extend((entity_id, "MFR-CORPORATE-BRAND-OVERRIDE"))
            return True
        return False
    if behavior == "require_evidence_review" or role in {"corporate_group", "unknown"}:
        source_term = normalize_text(rule.get("source_term")) or ""
        parent = _parent_key(_normalized_entity(source_term) or "", None)
        if parent is not None:
            brand_child = _resolve_manufacturer(raw.get("brand"))
            model_child = _resolve_model_manufacturer(raw.get("model"))
            allowed = _MARKETED_PARENT_CHILDREN[parent]
            child = (
                brand_child
                if brand_child in allowed and model_child == brand_child
                else model_child
                if model_child in allowed and brand_child not in allowed
                else None
            )
            if child in allowed:
                normalized["manufacturer"] = child
                normalized["manufacturer_role"] = "vehicle_manufacturer"
                normalized["builder_converter_names"] = []
                normalized["manufacturer_evidence"] = ["brand", "manufacturer", "model"]
                applied.extend((entity_id, "MFR-PARENT-CHILD-EVIDENCE"))
                return True
        if canonical_name:
            candidates["manufacturer"] = canonical_name
        reasons.append(
            "manufacturer_corporate_group_unresolved"
            if role == "corporate_group"
            else "manufacturer_entity_requires_review"
        )
        return True
    if role == "bodybuilder_converter" or behavior == "use_base_manufacturer":
        base = _resolve_base_manufacturer(raw.get("base_manufacturer"))
        if base is None:
            base = _resolve_manufacturer(rule.get("fallback_manufacturer"))
        if base is None:
            reasons.append("converter_base_manufacturer_unresolved")
            return True
        normalized["manufacturer"] = base
        normalized["manufacturer_role"] = "bodybuilder_converter"
        normalized["builder_converter_names"] = [canonical_name] if canonical_name else []
        normalized["manufacturer_evidence"] = ["base_manufacturer", "manufacturer"]
        applied.append(entity_id)
        return True
    if role == "vehicle_manufacturer" and canonical_name:
        normalized["manufacturer"] = canonical_name
        normalized["manufacturer_role"] = "vehicle_manufacturer"
        normalized["builder_converter_names"] = []
        source_field = rule.get("source_field")
        normalized["manufacturer_evidence"] = [
            source_field if isinstance(source_field, str) else "manufacturer"
        ]
        sub_brand = rule.get("sub_brand")
        if isinstance(sub_brand, str) and sub_brand:
            normalized["sub_brand"] = sub_brand
        if rule.get("registered_marque_converter") is True:
            normalized["builder_converter_names"] = [canonical_name]
            evidence_manufacturer = _resolve_base_manufacturer(raw.get("base_manufacturer"))
            if evidence_manufacturer is None:
                evidence_manufacturer = _resolve_vin_manufacturer(raw.get("vin"))
            if evidence_manufacturer is not None:
                normalized["base_vehicle_manufacturer"] = evidence_manufacturer
            model = _normalized_entity(raw.get("model")) or ""
            for candidate in rule.get("base_model_terms", []):
                if isinstance(candidate, str) and re.search(
                    rf"\b{re.escape(candidate)}\b", model, flags=re.IGNORECASE
                ):
                    normalized["base_model"] = candidate.upper()
                    break
        configured_base = rule.get("base_vehicle_manufacturer")
        if isinstance(configured_base, str):
            normalized["base_vehicle_manufacturer"] = configured_base
        configured_model = rule.get("base_model")
        if isinstance(configured_model, str):
            normalized["base_model"] = configured_model
        configured_builder = rule.get("coachbuilder")
        if isinstance(configured_builder, str):
            normalized["builder_converter_names"] = [configured_builder]
        special_purpose = rule.get("special_purpose_type")
        if isinstance(special_purpose, str):
            normalized["special_purpose_type"] = special_purpose
        manufacturer_group = rule.get("manufacturer_group")
        if isinstance(manufacturer_group, str):
            normalized["manufacturer_group"] = manufacturer_group
        configured_model_name = rule.get("model_name")
        if isinstance(configured_model_name, str):
            normalized["model"] = configured_model_name
        configured_model_family = rule.get("model_family")
        if isinstance(configured_model_family, str):
            normalized["model_family"] = configured_model_family
        parts_policy = rule.get("parts_matching_policy")
        conditional_parts_policy = rule.get("parts_matching_policy_when_special_purpose")
        if (
            parts_policy is None
            and isinstance(conditional_parts_policy, str)
            and (
                normalize_text(normalized.get("special_purpose_type")) is not None
                or normalize_text(raw.get("special_purpose_type")) is not None
                or any(
                    normalize_text(raw.get(field_name)) is not None
                    for field_name in ("body_code2", "body_code_extra")
                )
            )
        ):
            parts_policy = conditional_parts_policy
        if isinstance(parts_policy, str):
            normalized["parts_matching_policy"] = parts_policy
            normalized["parts_matching_eligible"] = parts_policy not in {
                "excluded",
                "restricted",
                "manual_review",
            }
        applied.append(entity_id)
        return True
    reasons.append("manufacturer_entity_configuration_invalid")
    return True


def _resolve_manufacturer(value: object) -> str | None:
    entity = _normalized_entity(value)
    if entity is None:
        return None
    aliases = {**_MANUFACTURER_ALIASES, **_EVIDENCE_ONLY_MANUFACTURER_ALIASES}
    direct = aliases.get(entity)
    if direct is not None:
        return direct
    for alias, canonical in aliases.items():
        if entity.startswith(f"{alias} "):
            return canonical
    return None


def _resolve_converter(value: object) -> tuple[str, str] | None:
    entity = _normalized_entity(value)
    if entity is None:
        return None
    direct = _CONVERTER_ALIASES.get(entity)
    if direct is not None:
        return direct
    for alias, converter in _CONVERTER_ALIASES.items():
        if entity.startswith(f"{alias} "):
            return converter
    return None


def _resolve_model_manufacturer(value: object) -> str | None:
    model = _normalized_entity(value)
    if model is None:
        return None
    direct = _MODEL_MANUFACTURERS.get(model)
    if direct is not None:
        return direct
    for model_name, manufacturer in _MODEL_MANUFACTURERS.items():
        if model.startswith(f"{model_name} ") or model.endswith(f" {model_name}"):
            return manufacturer
    return None


def _resolve_vin_manufacturer(value: object) -> str | None:
    vin = normalize_text(value)
    if vin is None or len(vin) < 3:
        return None
    return _VIN_WMI_MANUFACTURERS.get(vin[:3].upper())


def _resolve_fab_manufacturer(value: object) -> str | None:
    code = _normalized_entity(value)
    return _FAB_CODE_MANUFACTURERS.get(code) if code is not None else None


def _marketed_manufacturer_evidence(
    raw: dict[str, Any],
) -> tuple[str | None, tuple[str, ...], bool]:
    brand = _resolve_manufacturer(raw.get("brand"))
    if brand is None:
        return None, (), False
    corroborators = (
        ("MFR-BRAND-BASE", _resolve_base_manufacturer(raw.get("base_manufacturer"))),
        ("MFR-BRAND-MODEL", _resolve_model_manufacturer(raw.get("model"))),
        ("MFR-BRAND-FAB-CODE", _resolve_fab_manufacturer(raw.get("fab_code"))),
        ("MFR-BRAND-KTYPE", _resolve_manufacturer(raw.get("ktype_manufacturer"))),
    )
    present = tuple((rule_id, value) for rule_id, value in corroborators if value is not None)
    if any(value != brand for _, value in present):
        return brand, tuple(rule_id for rule_id, _ in present), True
    if not present:
        return brand, (), False
    return brand, tuple(rule_id for rule_id, _ in present), False


def _parent_key(entity: str, manufacturer: str | None) -> str | None:
    if manufacturer == "BMW":
        return "BMW"
    if "PSA" in entity:
        return "PSA"
    if "FCA" in entity:
        return "FCA"
    if "TOYOTA MOTOR" in entity:
        return "TOYOTA"
    if "ZHEJIANG GEELY" in entity:
        return "GEELY"
    if "GREAT WALL MOTOR" in entity:
        return "GREAT_WALL"
    return None


def _resolve_base_manufacturer(value: object) -> str | None:
    entity = _normalized_entity(value)
    if entity is None:
        return None
    if entity.startswith("FCA ITALY"):
        return "Fiat"
    return _resolve_manufacturer(value)


def _fragmented_manufacturer(raw: dict[str, Any]) -> str | None:
    """Repair only short manufacturer/base fragments confirmed by the Brand."""

    manufacturer = _manufacturer_compact_key(raw.get("manufacturer"))
    base = _manufacturer_compact_key(raw.get("base_manufacturer"))
    brand = _resolve_manufacturer(raw.get("brand"))
    if (
        manufacturer is None
        or base is None
        or brand is None
        or len(manufacturer) > 6
        or len(base) > 6
    ):
        return None
    repaired = _resolve_manufacturer(f"{manufacturer}{base}")
    return repaired if repaired == brand else None


def _retain_manufacturer_evidence(
    raw: dict[str, Any], normalized: dict[str, Any], *, fragmented: str | None = None
) -> None:
    legal = normalize_text(raw.get("manufacturer"))
    base = normalize_text(raw.get("base_manufacturer"))
    registered_make = normalize_text(raw.get("brand"))
    vin_manufacturing_entity = _resolve_vin_manufacturer(raw.get("vin"))
    if fragmented is not None:
        normalized["legal_manufacturer"] = fragmented
        normalized["manufacturer_source_repair"] = "concatenated_manufacturer_base_fragments"
        return
    if legal is not None:
        normalized["legal_manufacturer"] = legal
    if base is not None:
        normalized["base_manufacturer"] = base
    if registered_make is not None:
        normalized["registered_make"] = registered_make
    if vin_manufacturing_entity is not None:
        normalized["vin_manufacturing_entity"] = vin_manufacturing_entity


def _normalize_manufacturer(
    raw: dict[str, Any],
    normalized: dict[str, Any],
    candidates: dict[str, Any],
    applied: list[str],
    candidate_rules: list[str],
    reasons: list[str],
    entity_rules: ManufacturerEntityRules,
) -> None:
    if normalized.get("vehicle_classification") == "special_modified":
        _retain_manufacturer_evidence(raw, normalized)
        normalized["manufacturer_role"] = (
            "self_built"
            if _normalized_entity(raw.get("brand")) == "EGEN TILLVERKNING"
            else "special_modified"
        )
        normalized["builder_converter_names"] = []
        normalized["manufacturer_evidence"] = ["text_codes"]
        applied.append("MFR-SPECIAL-MODIFIED-GROUP")
        return
    if (_normalized_entity(raw.get("brand")) or "") == "HOT ROD":
        _retain_manufacturer_evidence(raw, normalized)
        normalized["manufacturer_role"] = "custom_identity_unverified"
        normalized["builder_converter_names"] = []
        normalized["manufacturer_evidence"] = ["brand"]
        normalized["parts_matching_policy"] = "restricted"
        normalized["parts_matching_eligible"] = False
        reasons.append("generic_custom_identity_unverified")
        applied.append("MFR-GENERIC-HOT-ROD-REVIEW-V1")
        return
    entity = _normalized_entity(raw.get("manufacturer"))
    base = _resolve_base_manufacturer(raw.get("base_manufacturer"))
    fragmented = _fragmented_manufacturer(raw)
    _retain_manufacturer_evidence(raw, normalized, fragmented=fragmented)
    if entity is None and _normalized_entity(raw.get("brand")) == "EGEN TILLVERKNING":
        normalized["manufacturer_role"] = "self_built"
        normalized["builder_converter_names"] = []
        normalized["manufacturer_evidence"] = ["brand"]
        applied.append("MFR-SELF-BUILT-EXACT")
        return
    if (
        entity is None
        and _normalized_entity(raw.get("brand")) == "DS"
        and _normalized_entity(raw.get("fab_code")) == "DSS"
        and (_normalized_entity(raw.get("model")) or "").startswith("DS")
    ):
        normalized["manufacturer"] = "DS"
        normalized["manufacturer_role"] = "vehicle_manufacturer"
        normalized["builder_converter_names"] = []
        normalized["manufacturer_evidence"] = ["brand", "fab_code", "model"]
        applied.append("MFR-DS-BRAND-FAB-MODEL")
        return
    marketed, evidence_rules, evidence_conflict = _marketed_manufacturer_evidence(raw)
    if fragmented is not None:
        normalized["manufacturer"] = fragmented
        normalized["manufacturer_role"] = "vehicle_manufacturer"
        normalized["builder_converter_names"] = []
        normalized["manufacturer_evidence"] = ["brand", "manufacturer", "base_manufacturer"]
        applied.append("MFR-FRAGMENTED-SOURCE-REPAIR")
        return
    if entity is None:
        reviewed_brand = _manufacturer_entity_rule(raw, "brand", entity_rules)
        if reviewed_brand is not None:
            _apply_manufacturer_entity_rule(
                reviewed_brand, raw, normalized, candidates, applied, reasons
            )
            return
        reviewed_example = _reviewed_brand_example_rule(raw, entity_rules)
        if reviewed_example is not None:
            _apply_manufacturer_entity_rule(
                reviewed_example, raw, normalized, candidates, applied, reasons
            )
            applied.append("MFR-BRAND-REVIEWED-EXAMPLE")
            return
        brand_key = _normalized_entity(raw.get("brand"))
        reviewed_exact_brand = (
            _REVIEWED_EXACT_BRAND_REPAIRS.get(brand_key) if brand_key is not None else None
        )
        reviewed_legacy_brand = (
            _REVIEWED_LEGACY_BRAND_ENTITIES.get(brand_key) if brand_key is not None else None
        )
        if reviewed_exact_brand is not None:
            normalized["manufacturer"] = reviewed_exact_brand
            normalized["manufacturer_role"] = "vehicle_manufacturer"
            normalized["builder_converter_names"] = []
            applied.append("MFR-BRAND-REVIEWED-EXACT")
            return
        if reviewed_legacy_brand is not None:
            normalized["manufacturer"] = reviewed_legacy_brand
            normalized["manufacturer_role"] = "vehicle_manufacturer"
            normalized["builder_converter_names"] = []
            applied.append("MFR-BRAND-LEGACY-EXACT")
            return
        if marketed is not None:
            if evidence_conflict:
                candidates["manufacturer"] = marketed
                candidate_rules.extend(evidence_rules)
                reasons.append("manufacturer_evidence_conflict")
                return
            if evidence_rules:
                normalized["manufacturer"] = marketed
                normalized["manufacturer_role"] = "vehicle_manufacturer"
                normalized["builder_converter_names"] = []
                applied.extend(("MFR-BRAND-CONFIRMED", *evidence_rules))
                return
        brand_fallback, brand_rule_ids, compound, brand_conflict = _manufacturer_from_brand_prefix(
            raw, entity_rules
        )
        if brand_conflict:
            candidates["manufacturer"] = list(brand_rule_ids)
            candidate_rules.append("MFR-BRAND-PREFIX-FALLBACK")
            reasons.append("manufacturer_brand_prefix_conflict")
            return
        if compound:
            candidate_rules.extend(brand_rule_ids)
            reasons.append("manufacturer_brand_compound_review")
            return
        if brand_fallback is not None:
            normalized["manufacturer"] = brand_fallback
            normalized["manufacturer_role"] = "vehicle_manufacturer"
            normalized["builder_converter_names"] = []
            candidates["manufacturer_confirmation"] = {
                "canonical_name": brand_fallback,
                "source_fields": ["brand"],
            }
            candidate_rules.extend(brand_rule_ids)
            return
        if marketed is not None:
            candidates["manufacturer"] = marketed
            candidate_rules.append("MFR-BRAND-REVIEW")
            reasons.append("manufacturer_missing_compare_brand")
            return
        fallback, source_fields, fallback_rule_id, conflict = _manufacturer_from_model_variant(
            raw, entity_rules
        )
        if conflict:
            candidates["manufacturer"] = list(source_fields)
            candidate_rules.append(fallback_rule_id or "MFR-MODEL-VARIANT-FALLBACK")
            reasons.append("manufacturer_model_variant_conflict")
            return
        if fallback is not None:
            normalized["manufacturer"] = fallback
            normalized["manufacturer_role"] = "vehicle_manufacturer"
            normalized["builder_converter_names"] = []
            candidates["manufacturer_confirmation"] = {
                "canonical_name": fallback,
                "source_fields": list(source_fields),
            }
            candidate_rules.append(fallback_rule_id or "MFR-MODEL-VARIANT-FALLBACK")
            return
        reasons.append("manufacturer_missing")
        return

    reviewed_entity = _manufacturer_entity_rule(raw, "manufacturer", entity_rules)
    if reviewed_entity is not None:
        handled = _apply_manufacturer_entity_rule(
            reviewed_entity, raw, normalized, candidates, applied, reasons
        )
        if handled:
            return

    converter = _resolve_converter(raw.get("manufacturer"))
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
    parent = _parent_key(entity, manufacturer)
    if (
        parent == "PSA"
        and _normalized_entity(raw.get("brand")) == "DS"
        and (_normalized_entity(raw.get("model")) or "").startswith("DS4")
    ):
        normalized["manufacturer"] = "DS"
        normalized["manufacturer_role"] = "vehicle_manufacturer"
        normalized["builder_converter_names"] = []
        normalized["manufacturer_evidence"] = ["brand", "manufacturer", "model"]
        applied.append("MFR-PSA-DS4-EXACT-EVIDENCE")
        return
    parent_model_child = _resolve_model_manufacturer(raw.get("model"))
    if (
        parent is not None
        and parent_model_child in _MARKETED_PARENT_CHILDREN[parent]
        and _resolve_manufacturer(raw.get("brand")) is None
    ):
        normalized["manufacturer"] = parent_model_child
        normalized["manufacturer_role"] = "vehicle_manufacturer"
        normalized["builder_converter_names"] = []
        normalized["manufacturer_evidence"] = ["brand", "manufacturer", "model"]
        applied.append("MFR-PARENT-MODEL-CHILD")
        return
    if (
        marketed is not None
        and evidence_rules
        and parent is not None
        and marketed in _MARKETED_PARENT_CHILDREN[parent]
    ):
        normalized["manufacturer"] = marketed
        normalized["manufacturer_role"] = "vehicle_manufacturer"
        normalized["builder_converter_names"] = []
        applied.extend(("MFR-PARENT-MARKETED", *evidence_rules))
        return
    if marketed is not None and evidence_conflict:
        candidates["manufacturer"] = marketed
        candidate_rules.extend(evidence_rules)
        reasons.append("manufacturer_evidence_conflict")
        return
    brand_manufacturer = _resolve_manufacturer(raw.get("brand"))
    if manufacturer is None and base is not None and brand_manufacturer == base:
        normalized["manufacturer"] = brand_manufacturer
        normalized["manufacturer_role"] = "bodybuilder_converter"
        normalized["builder_converter_names"] = [normalize_text(raw.get("manufacturer"))]
        normalized["manufacturer_evidence"] = ["brand", "base_manufacturer"]
        applied.append("MFR-BRAND-BASE-CONFIRMED")
        return
    if manufacturer is None and marketed is not None and evidence_rules and not evidence_conflict:
        normalized["manufacturer"] = marketed
        normalized["manufacturer_role"] = "vehicle_manufacturer"
        normalized["builder_converter_names"] = []
        normalized["manufacturer_evidence"] = [
            "brand",
            *(
                "model"
                if rule_id == "MFR-BRAND-MODEL"
                else "vin"
                if rule_id == "MFR-BRAND-VIN-WMI"
                else "fab_code"
                if rule_id == "MFR-BRAND-FAB-CODE"
                else "ktype_manufacturer"
                for rule_id in evidence_rules
            ),
        ]
        applied.extend(("MFR-BRAND-EVIDENCE-CONFIRMED", *evidence_rules))
        return
    if any(marker in entity for marker in _CORPORATE_GROUP_MARKERS):
        reasons.append("manufacturer_corporate_group_unresolved")
        return
    if manufacturer is None:
        reasons.append("manufacturer_unknown")
        return
    normalized["manufacturer"] = manufacturer
    normalized["manufacturer_role"] = "vehicle_manufacturer"
    normalized["builder_converter_names"] = []
    normalized["manufacturer_evidence"] = ["manufacturer"]
    applied.append("MFR-102")


def _normalize_model_family(context: NormalizationContext) -> None:
    raw = context.canonical_record
    normalized = context.normalized
    model = normalize_text(raw.get("model"))
    manufacturer = normalize_text(normalized.get("manufacturer")) or _resolve_manufacturer(
        raw.get("manufacturer")
    )
    manufacturer_key = _normalized_entity(manufacturer)
    if model is not None and manufacturer is not None and manufacturer_key is not None:
        model_key = _normalized_entity(model)
        if model_key is not None and model_key.startswith(f"{manufacturer_key} "):
            model = model[len(manufacturer) :].strip()
    if model is None and manufacturer_key is not None:
        brand = normalize_text(raw.get("brand"))
        brand_key = _normalized_entity(brand)
        if (
            brand is not None
            and brand_key is not None
            and brand_key.startswith(manufacturer_key)
            and not brand_key.startswith(f"{manufacturer_key} ")
            and len(brand_key) > len(manufacturer_key)
        ):
            compact_remainder = brand_key[len(manufacturer_key) :]
            if compact_remainder[0].isdigit():
                model = compact_remainder
    if model is None:
        return

    model_key = _normalized_entity(model)
    matches: list[tuple[int, TranslationRule, str]] = []
    if model_key is not None:
        for rule in _rule_set(context).rules:
            if rule.area != "model_family" or not _manufacturer_is_in_scope(rule, manufacturer):
                continue
            for term in rule.source_terms:
                term_key = _normalized_entity(term)
                if term_key is not None and (
                    model_key == term_key or model_key.startswith(f"{term_key} ")
                ):
                    matches.append((len(term_key), rule, term))
    if matches:
        _, rule, source_term = max(matches, key=lambda match: (match[0], match[1].rule_id))
        normalized[rule.canonical_field] = rule.canonical_value
        context.applied_rule_ids.append(rule.rule_id)
        _record_dictionary_match(
            context,
            rule,
            source_field="model",
            source_term=source_term,
        )
        return

    context.candidates["model_family"] = model
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
    converted = int((parsed * multiplier).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return converted if 0 < converted <= maximum else None


def _record_dictionary_match(
    context: NormalizationContext,
    rule: TranslationRule,
    *,
    source_field: str,
    source_term: str,
) -> None:
    context.record_rule_match(
        rule_set_version=_rule_set(context).version,
        rule_id=rule.rule_id,
        decision=rule.decision,
        source_field=source_field,
        source_term=source_term,
        target_field=rule.canonical_field,
        canonical_value=rule.canonical_value,
    )


def _rule_set(context: NormalizationContext) -> TranslationRuleSet:
    value = context.runtime.get("translation_rule_set", RULE_SET)
    if not isinstance(value, TranslationRuleSet):
        raise TypeError("translation_rule_set runtime value is invalid")
    return value


def _marketing_match(
    context: NormalizationContext,
    area: Literal[
        "transmission_marketing",
        "bodywork_marketing",
        "electrification_marketing",
        "drive_marketing",
    ],
    *,
    vehicle_scope: str | None = None,
    extra_fields: tuple[str, ...] = (),
) -> tuple[TranslationRule, str, str] | None:
    matches: list[tuple[int, TranslationRule, str, str]] = []
    raw = context.canonical_record
    for rule in _rule_set(context).rules:
        if rule.area != area or (rule.vehicle_scopes and vehicle_scope not in rule.vehicle_scopes):
            continue
        for field_name in (*rule.source_fields, *extra_fields):
            text = normalize_text(raw.get(field_name))
            if text is None:
                continue
            for term in rule.source_terms:
                trailing_boundary = r"(?=\d|\b)" if term.casefold() == "xdrive" else r"(?!\w)"
                pattern = rf"(?<!\w){re.escape(term)}{trailing_boundary}"
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


def _raw_fuel_carriers(context: NormalizationContext) -> set[str]:
    raw = context.canonical_record
    carriers: set[str] = set()
    for field_name in ("fuel1", "fuel2", "fuel3"):
        code = normalize_text(raw.get(field_name))
        if code is None or code == "0":
            continue
        matches = _rule_set(context).match("fuel_carrier", code.upper().lstrip("0") or "0")
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
        ("kw", Decimal(1), "UNIT-POWER-KW-V1"),
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
        ("ccm", Decimal(1), "UNIT-DISPLACEMENT-CCM-V1"),
        ("displacement_l", Decimal(1000), "UNIT-DISPLACEMENT-LITRE-V1"),
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
        matches = _rule_set(context).match("transmission_code", code.upper())
        if not matches:
            context.review_reasons.append("transmission_code_unknown")
            return
        code_rule = matches[0]
        _record_dictionary_match(context, code_rule, source_field="gearbox", source_term=code)
        normalized[code_rule.canonical_field] = code_rule.canonical_value
        if code_rule.display_value is not None:
            normalized["transmission_display"] = code_rule.display_value
        context.applied_rule_ids.append(code_rule.rule_id)

    marketing = _marketing_match(context, "transmission_marketing")
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
    if rule.requires_electrification and "electricity" not in _raw_fuel_carriers(context):
        context.candidate_rule_ids.append(rule.rule_id)
        context.review_reasons.append("transmission_electrification_evidence_missing")
        return
    if code_rule is not None and code_rule.canonical_value != rule.canonical_value:
        if code_rule.canonical_value == "automatic" and rule.canonical_value in {"cvt", "dct"}:
            normalized["transmission_name"] = source_term
            context.applied_rule_ids.append(rule.rule_id)
            return
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
    secondary_code = normalize_text(raw.get("body_code2"))
    scope = _vehicle_scope(raw)
    code_rule: TranslationRule | None = None
    special_purpose: tuple[str, str] | None = None

    if secondary_code is not None:
        canonical_secondary = secondary_code.upper()
        normalized["secondary_registry_body_code"] = canonical_secondary
        secondary = _SECONDARY_PURPOSE_CODES.get(canonical_secondary)
        if secondary is not None:
            field_name, canonical_value, display_value = secondary
            normalized[field_name] = canonical_value
            normalized["secondary_registry_body_label_sv"] = display_value
            context.applied_rule_ids.append(f"PURPOSE-SECONDARY-{canonical_secondary}")

    if code is not None:
        canonical_code = code.upper()
        normalized["bodywork_registry_code"] = canonical_code
        special_purpose = _PRIMARY_SPECIAL_PURPOSE_CODES.get(canonical_code)
        if special_purpose is not None:
            special_type, display_value = special_purpose
            normalized["special_purpose_type"] = special_type
            normalized["bodywork_registry_label_sv"] = display_value
            normalized["bodywork_source"] = "special_purpose_registry"
            context.applied_rule_ids.append(f"PURPOSE-PRIMARY-{canonical_code}")
        else:
            matches = _rule_set(context).match("bodywork_code", canonical_code, vehicle_scope=scope)
            if not matches:
                context.review_reasons.append("bodywork_code_unresolved_for_category")
                return
            code_rule = matches[0]
            _record_dictionary_match(context, code_rule, source_field="body_code", source_term=code)
            normalized["bodywork_registry_label_sv"] = code_rule.display_value
            if code_rule.canonical_value is not None:
                normalized[code_rule.canonical_field] = code_rule.canonical_value
                normalized["bodywork_source"] = "registry"
            elif canonical_code in {"98", "SG"}:
                context.review_reasons.append("bodywork_requires_review")
            context.applied_rule_ids.append(code_rule.rule_id)

    marketing = _marketing_match(
        context,
        "bodywork_marketing",
        vehicle_scope=scope,
        extra_fields=("brand",) if special_purpose is not None else (),
    )
    if marketing is None:
        return
    rule, source_field, source_term = marketing
    _record_dictionary_match(
        context,
        rule,
        source_field=source_field,
        source_term=source_term,
    )
    if code_rule is not None and code_rule.canonical_value == rule.canonical_value:
        normalized["marketing_body_style"] = rule.canonical_value
        context.applied_rule_ids.append(rule.rule_id)
        return
    manufacturer = normalized.get("manufacturer")
    if not _manufacturer_is_in_scope(rule, manufacturer):
        if code_rule is not None:
            context.candidates["marketing_body_style"] = rule.canonical_value
            context.candidate_rule_ids.append(rule.rule_id)
            return
        context.review_reasons.append("bodywork_marketing_scope_unresolved")
        return
    if special_purpose is not None:
        normalized["marketing_body_style"] = rule.canonical_value
        context.candidates["bodywork_form"] = rule.canonical_value
        context.candidates["bodywork_confidence"] = 0.8
        context.candidate_rule_ids.append(rule.rule_id)
        return
    if rule.rule_id == "BDY-013" and (
        code_rule is None or code_rule.rule_id not in {"BDY-118", "BDY-SA"}
    ):
        if (
            normalized.get("manufacturer") == "Volkswagen"
            and (_normalized_entity(raw.get("model")) or "").startswith("CALIFORNIA")
        ):
            context.candidates["marketing_body_style"] = rule.canonical_value
            context.candidate_rule_ids.append(rule.rule_id)
            return
        if code_rule is not None:
            return
        context.candidates[rule.canonical_field] = rule.canonical_value
        context.candidate_rule_ids.append(rule.rule_id)
        context.review_reasons.append("motorhome_supporting_evidence_missing")
        return
    if code_rule is not None and code_rule.canonical_value != rule.canonical_value:
        compatible_forms = frozenset({code_rule.canonical_value, rule.canonical_value})
        if compatible_forms == frozenset({"multi_purpose_vehicle", "passenger_van"}):
            normalized["marketing_body_style"] = rule.canonical_value
            context.applied_rule_ids.append(rule.rule_id)
            return
        normalized["marketing_body_style"] = rule.canonical_value
        context.applied_rule_ids.append(rule.rule_id)
        return
    normalized[rule.canonical_field] = rule.canonical_value
    normalized["bodywork_source"] = "registry" if code_rule is not None else "marketing"
    normalized["marketing_body_style"] = rule.canonical_value
    context.applied_rule_ids.append(rule.rule_id)


def _normalize_drive(context: NormalizationContext) -> None:
    raw = context.canonical_record
    normalized = context.normalized
    flag = normalize_text(raw.get("is_4wd"))
    flag_rule: TranslationRule | None = None
    if flag == "1":
        matches = _rule_set(context).match("drive_flag", flag)
        if matches:
            flag_rule = matches[0]
            _record_dictionary_match(
                context,
                flag_rule,
                source_field="is_4wd",
                source_term=flag,
            )
            normalized[flag_rule.canonical_field] = flag_rule.canonical_value
            context.applied_rule_ids.append(flag_rule.rule_id)
    elif flag not in {None, "0"}:
        context.review_reasons.append("is_4wd_malformed")

    marketing = _marketing_match(context, "drive_marketing")
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
    if flag_rule is not None:
        if flag_rule.canonical_value == rule.canonical_value and _manufacturer_is_in_scope(
            rule, manufacturer
        ):
            context.applied_rule_ids.append(rule.rule_id)
        elif flag_rule.canonical_value != rule.canonical_value:
            context.candidate_rule_ids.append(rule.rule_id)
            context.review_reasons.append("drive_registry_marketing_conflict")
        return
    if not _manufacturer_is_in_scope(rule, manufacturer):
        context.candidate_rule_ids.append(rule.rule_id)
        context.review_reasons.append("drive_marketing_scope_unresolved")
        return
    normalized[rule.canonical_field] = rule.canonical_value
    context.applied_rule_ids.append(rule.rule_id)


def _normalize_fuel(context: NormalizationContext) -> None:
    raw = context.canonical_record
    normalized = context.normalized
    carriers: list[str] = []
    for field_name in ("fuel1", "fuel2", "fuel3"):
        code = normalize_text(raw.get(field_name))
        if code is None or code == "0":
            continue
        canonical_code = code.upper().lstrip("0") or "0"
        matches = _rule_set(context).match("fuel_carrier", canonical_code)
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
        matches = _rule_set(context).match("fuel_combination", combination.upper())
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
        matches = _rule_set(context).match("electrification", ev_config.upper())
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
            evidence_valid = combustion
        if not evidence_valid:
            context.candidate_rule_ids.append(rule.rule_id)
            context.review_reasons.append("electrification_fuel_evidence_conflict")
        else:
            if rule.rule_id in {"ELEC-002", "ELEC-003"} and not electricity:
                carriers.append("electricity")
                normalized["energy_sources"] = carriers
            normalized[rule.canonical_field] = rule.canonical_value
            context.applied_rule_ids.append(rule.rule_id)
            _record_dictionary_match(
                context,
                rule,
                source_field="ev_config",
                source_term=ev_config,
            )

    marketing = _marketing_match(context, "electrification_marketing")
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
    entity_rules = context.runtime.get("manufacturer_entity_rules", {})
    if not isinstance(entity_rules, dict):
        raise TypeError("manufacturer_entity_rules runtime value is invalid")
    _normalize_manufacturer(
        context.canonical_record,
        context.normalized,
        context.candidates,
        context.applied_rule_ids,
        context.candidate_rule_ids,
        context.review_reasons,
        entity_rules,
    )


def _apply_special_vehicle(context: NormalizationContext) -> None:
    _apply_special_vehicle_classification(context)


def _apply_model_family(context: NormalizationContext) -> None:
    _normalize_model_family(context)


def _apply_dates(context: NormalizationContext) -> None:
    _normalize_dates(context)


def _apply_engine_measurements(context: NormalizationContext) -> None:
    _normalize_engine_measurements(context)


def _apply_transmission(context: NormalizationContext) -> None:
    _normalize_transmission(context)


def _apply_bodywork(context: NormalizationContext) -> None:
    _normalize_bodywork(context)


def _apply_drive(context: NormalizationContext) -> None:
    _normalize_drive(context)


_HYBRID_COMBINATION_TOKENS: tuple[tuple[str, str], ...] = (
    ("petrol", "hybrid_petrol"),
    ("diesel", "hybrid_diesel"),
)


def _derive_fuel_match_tokens(context: NormalizationContext) -> None:
    """Publish the fuel tokens a TecDoc KType can be compared against.

    Transportstyrelsen records a hybrid as its separate carriers -- electricity
    plus petrol -- while TecDoc names the combination with a single token,
    hybrid_petrol. Matching intersects the two fuel sets, so without the
    combined token no hybrid can ever intersect a hybrid KType, and every one
    of them conflicts on fuel instead.

    The combined token is published separately rather than appended to
    `energy_sources`, because a hybrid does not run on "hybrid_petrol": that is
    a classification, not an energy carrier. `energy_sources` stays exactly what
    it claims to be -- the carriers the registry recorded -- and this field
    carries the comparison vocabulary.

    Component carriers are kept alongside the combined token so a hybrid can
    still match a KType catalogued under the combustion fuel alone.
    """

    carriers = context.normalized.get("energy_sources")
    if not isinstance(carriers, list) or not carriers:
        return
    tokens = list(carriers)
    if "electricity" in carriers:
        for carrier, combined in _HYBRID_COMBINATION_TOKENS:
            if carrier in carriers and combined not in tokens:
                tokens.append(combined)
    context.normalized["fuel_match_tokens"] = tokens


def _apply_fuel(context: NormalizationContext) -> None:
    _normalize_fuel(context)
    _derive_fuel_match_tokens(context)


def _apply_reviewed_record_policies(context: NormalizationContext) -> None:
    runtime_rules = context.runtime.get("manufacturer_entity_rules", {})
    if not isinstance(runtime_rules, dict):
        raise TypeError("manufacturer_entity_rules runtime value is invalid")
    raw = context.canonical_record
    for policy_key, policy in sorted(runtime_rules.items()):
        if not policy_key.startswith("policy:") or policy.get("kind") != "reviewed_record_policy":
            continue
        match_fields = policy.get("match_fields")
        if not isinstance(match_fields, dict) or not match_fields:
            continue
        if any(
            _normalized_entity(raw.get(field_name)) != _normalized_entity(expected)
            for field_name, expected in match_fields.items()
            if isinstance(field_name, str) and isinstance(expected, str)
        ):
            continue
        updates = policy.get("normalized_updates")
        if isinstance(updates, dict):
            context.normalized.update(updates)
        remove_fields = policy.get("normalized_remove")
        if isinstance(remove_fields, list):
            for field_name in remove_fields:
                if isinstance(field_name, str):
                    context.normalized.pop(field_name, None)
        candidate_remove = policy.get("candidate_remove")
        if isinstance(candidate_remove, list):
            for field_name in candidate_remove:
                if isinstance(field_name, str):
                    context.candidates.pop(field_name, None)
        clear_reasons = policy.get("clear_review_reasons")
        if isinstance(clear_reasons, list):
            cleared = {reason for reason in clear_reasons if isinstance(reason, str)}
            context.review_reasons[:] = [
                reason for reason in context.review_reasons if reason not in cleared
            ]
        rule_id = policy.get("rule_id")
        context.applied_rule_ids.append(
            rule_id if isinstance(rule_id, str) else policy_key.removeprefix("policy:")
        )
        break


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
            transformer_id="ts.special-vehicle",
            order=15,
            default_rule_id="TS-SPECIAL-VEHICLE-V1",
            handler=_apply_special_vehicle,
            source_fields=(
                "text_code",
                "text_codes",
                "text_code_descriptions",
                "body_code",
                "body_code2",
                "body_code_extra",
            ),
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
            normalized_confidence_effect=0.1,
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
                "body_code2",
                "body_code_extra",
                "eu_category",
                "vehicle_type",
                "brand",
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
            source_fields=("is_4wd", "model", "variant", "version", "type"),
            normalized_confidence_effect=0.05,
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
        _RuleTransformer(
            transformer_id="ts.reviewed-record-policy",
            order=90,
            default_rule_id="REVIEWED-RECORD-POLICY-V1",
            handler=_apply_reviewed_record_policies,
            normalized_confidence_effect=0.1,
        ),
    ),
)
