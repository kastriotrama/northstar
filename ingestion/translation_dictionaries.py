"""Versioned, stakeholder-reviewed Transportstyrelsen translation dictionaries."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Literal

RuleArea = Literal[
    "transmission_code",
    "transmission_marketing",
    "fuel_carrier",
    "fuel_combination",
    "electrification",
    "electrification_marketing",
    "bodywork_code",
    "bodywork_marketing",
    "drive_flag",
    "drive_marketing",
    "model_family",
]
RuleDecision = Literal["accepted", "proposed"]

RULE_SET_VERSION_V2 = "ts-translation-v2"
RULE_SET_VERSION_V3 = "ts-translation-v3"
RULE_SET_VERSION_V4 = "ts-translation-v4"
RULE_SET_VERSION_V5 = "ts-translation-v5"
RULE_SET_VERSION_V6 = "ts-translation-v6"
REVIEWED_RULE_SET_VERSION = "ts-translation-v7"


class RuleSetNotFoundError(LookupError):
    """Raised when a caller requests an unavailable immutable rule-set version."""


@dataclass(frozen=True)
class TranslationRule:
    rule_id: str
    area: RuleArea
    source_fields: tuple[str, ...]
    source_terms: tuple[str, ...]
    canonical_field: str
    canonical_value: str | None
    decision: RuleDecision = "accepted"
    display_value: str | None = None
    vehicle_scopes: tuple[str, ...] = ()
    manufacturers: tuple[str, ...] = ()
    requires_electrification: bool = False

    def __post_init__(self) -> None:
        if not self.rule_id.strip() or not self.source_fields or not self.source_terms:
            raise ValueError("translation rules require an ID, source fields, and source terms")
        if not self.canonical_field.strip():
            raise ValueError("translation rules require a canonical field")

    def matches(self, source_term: str, *, vehicle_scope: str | None = None) -> bool:
        if source_term.casefold() not in {term.casefold() for term in self.source_terms}:
            return False
        return not self.vehicle_scopes or vehicle_scope in self.vehicle_scopes


@dataclass(frozen=True)
class TranslationRuleSet:
    version: str
    rules: tuple[TranslationRule, ...]

    def __post_init__(self) -> None:
        ids = [rule.rule_id for rule in self.rules]
        if not self.version.strip():
            raise ValueError("rule-set version must not be empty")
        if len(ids) != len(set(ids)):
            raise ValueError("translation rule IDs must be unique")
        if ids != sorted(ids):
            raise ValueError("translation rules must be sorted by rule ID")

    @property
    def by_id(self) -> MappingProxyType[str, TranslationRule]:
        return MappingProxyType({rule.rule_id: rule for rule in self.rules})

    @property
    def accepted_rules(self) -> tuple[TranslationRule, ...]:
        return tuple(rule for rule in self.rules if rule.decision == "accepted")

    @property
    def proposed_rules(self) -> tuple[TranslationRule, ...]:
        return tuple(rule for rule in self.rules if rule.decision == "proposed")

    def get(self, rule_id: str) -> TranslationRule:
        try:
            return self.by_id[rule_id]
        except KeyError as exc:
            raise KeyError(f"unknown translation rule {rule_id!r}") from exc

    def match(
        self,
        area: RuleArea,
        source_term: str,
        *,
        vehicle_scope: str | None = None,
    ) -> tuple[TranslationRule, ...]:
        return tuple(
            rule
            for rule in self.rules
            if rule.area == area and rule.matches(source_term, vehicle_scope=vehicle_scope)
        )


def _rule(
    rule_id: str,
    area: RuleArea,
    fields: str | tuple[str, ...],
    terms: str | tuple[str, ...],
    canonical_field: str,
    canonical_value: str | None,
    *,
    decision: RuleDecision = "accepted",
    display: str | None = None,
    scopes: tuple[str, ...] = (),
    manufacturers: tuple[str, ...] = (),
    requires_electrification: bool = False,
) -> TranslationRule:
    return TranslationRule(
        rule_id=rule_id,
        area=area,
        source_fields=(fields,) if isinstance(fields, str) else fields,
        source_terms=(terms,) if isinstance(terms, str) else terms,
        canonical_field=canonical_field,
        canonical_value=canonical_value,
        decision=decision,
        display_value=display,
        vehicle_scopes=scopes,
        manufacturers=manufacturers,
        requires_electrification=requires_electrification,
    )


_RULES = (
    _rule(
        "BDY-001",
        "bodywork_marketing",
        ("model", "variant", "version", "type"),
        "Touring",
        "bodywork_form",
        "wagon",
        manufacturers=("BMW",),
    ),
    _rule(
        "BDY-002",
        "bodywork_marketing",
        ("model", "variant", "version", "type"),
        "Avant",
        "bodywork_form",
        "wagon",
        manufacturers=("Audi",),
    ),
    _rule(
        "BDY-003",
        "bodywork_marketing",
        ("model", "variant", "version", "type"),
        "Variant",
        "bodywork_form",
        "wagon",
        manufacturers=("Volkswagen",),
    ),
    _rule(
        "BDY-004",
        "bodywork_marketing",
        ("model", "variant", "version", "type"),
        (
            "Estate",
            "Wagon",
            "Kombi",
            "Combi",
            "Break",
            "Sport Tourer",
            "Shooting Brake",
            "SW",
        ),
        "bodywork_form",
        "wagon",
        manufacturers=("*",),
    ),
    _rule(
        "BDY-005",
        "bodywork_marketing",
        ("model", "variant", "version", "type"),
        ("Cabrio", "Cabriolet", "Convertible", "Roadster", "Spider", "Spyder"),
        "bodywork_form",
        "convertible",
        manufacturers=("*",),
    ),
    _rule(
        "BDY-006",
        "bodywork_marketing",
        ("model", "variant", "version", "type"),
        ("Sedan", "Saloon", "Limousine", "Limusine", "Limo"),
        "bodywork_form",
        "sedan",
        manufacturers=("*",),
    ),
    _rule(
        "BDY-007",
        "bodywork_marketing",
        ("model", "variant", "version", "type"),
        ("Coupe", "Coupé", "Gran Coupe", "Gran Coupé"),
        "bodywork_form",
        "coupe",
        manufacturers=("*",),
    ),
    _rule(
        "BDY-008",
        "bodywork_marketing",
        ("model", "variant", "version", "type"),
        ("SUV", "Crossover", "Off-road"),
        "bodywork_form",
        "suv",
        manufacturers=("*",),
    ),
    _rule(
        "BDY-009",
        "bodywork_marketing",
        ("model", "variant", "version", "type"),
        ("Panel Van", "Cargo Van", "Van", "Skåp", "Furgon"),
        "bodywork_form",
        "van",
        scopes=("goods",),
        manufacturers=("*",),
    ),
    _rule(
        "BDY-010",
        "bodywork_marketing",
        ("model", "variant", "version", "type"),
        ("Minivan", "Multivan", "Caravelle", "Shuttle", "Passenger Van"),
        "bodywork_form",
        "multi_purpose_vehicle",
        scopes=("passenger",),
        manufacturers=("*",),
        display="Passenger van",
    ),
    _rule(
        "BDY-011",
        "bodywork_marketing",
        ("model", "variant", "version", "type"),
        ("Pickup", "Pick-up"),
        "bodywork_form",
        "pickup",
        scopes=("goods",),
        manufacturers=("*",),
    ),
    _rule(
        "BDY-012",
        "bodywork_marketing",
        ("model", "variant", "version", "type"),
        ("Platform/Chassis", "Flak/Chassi", "Flatbed", "Chassis Cab"),
        "bodywork_form",
        "chassis_cab",
        scopes=("goods",),
        manufacturers=("*",),
    ),
    _rule(
        "BDY-013",
        "bodywork_marketing",
        ("model", "variant", "version", "type"),
        ("Camper", "California", "Motorhome"),
        "bodywork_form",
        "motorhome",
        manufacturers=("*",),
    ),
    _rule(
        "BDY-101",
        "bodywork_code",
        "body_code",
        "AA",
        "bodywork_form",
        "sedan",
        display="Sedan",
        scopes=("passenger",),
    ),
    _rule(
        "BDY-102",
        "bodywork_code",
        "body_code",
        "01",
        "bodywork_form",
        "covered_body",
        display="Täckt",
        scopes=("passenger",),
    ),
    _rule(
        "BDY-103",
        "bodywork_code",
        "body_code",
        "03",
        "bodywork_form",
        "wagon",
        display="Kombi",
        scopes=("passenger",),
    ),
    _rule(
        "BDY-104",
        "bodywork_code",
        "body_code",
        "04",
        "bodywork_form",
        "covered_body",
        display="Täckt, taklucka",
        scopes=("passenger",),
    ),
    _rule(
        "BDY-105",
        "bodywork_code",
        "body_code",
        "06",
        "bodywork_form",
        "covered_body",
        display="Täckt, taxi",
        scopes=("passenger",),
    ),
    _rule(
        "BDY-106",
        "bodywork_code",
        "body_code",
        "07",
        "bodywork_form",
        "wagon",
        display="Kombi, taxi",
        scopes=("passenger",),
    ),
    _rule(
        "BDY-107",
        "bodywork_code",
        "body_code",
        "AD",
        "bodywork_form",
        "coupe",
        display="Kupé",
        scopes=("passenger",),
    ),
    _rule(
        "BDY-108",
        "bodywork_code",
        "body_code",
        "BB",
        "bodywork_form",
        "van",
        display="Skåpbil",
        scopes=("goods",),
    ),
    _rule(
        "BDY-109",
        "bodywork_code",
        "body_code",
        "AB",
        "bodywork_form",
        "hatchback",
        display="Halvkombi",
        scopes=("passenger",),
    ),
    _rule(
        "BDY-110",
        "bodywork_code",
        "body_code",
        "AC",
        "bodywork_form",
        "wagon",
        display="Stationsvagn (kombivagn)",
        scopes=("passenger",),
    ),
    _rule(
        "BDY-111",
        "bodywork_code",
        "body_code",
        "AE",
        "bodywork_form",
        "convertible",
        display="Cabriolet",
        scopes=("passenger",),
    ),
    _rule(
        "BDY-112",
        "bodywork_code",
        "body_code",
        "05",
        "bodywork_form",
        "wagon",
        display="Kombi, taklucka",
        scopes=("passenger",),
    ),
    _rule(
        "BDY-113",
        "bodywork_code",
        "body_code",
        "AF",
        "bodywork_form",
        "multi_purpose_vehicle",
        display="Fordon avsett för flera ändamål",
        scopes=("passenger",),
    ),
    _rule(
        "BDY-114",
        "bodywork_code",
        "body_code",
        "20",
        "bodywork_form",
        "box_body",
        display="Skåp",
        scopes=("goods", "trailer"),
    ),
    _rule(
        "BDY-115",
        "bodywork_code",
        "body_code",
        "BA",
        "bodywork_form",
        "truck",
        display="Lastbil",
        scopes=("goods",),
    ),
    _rule(
        "BDY-116",
        "bodywork_code",
        "body_code",
        "02",
        "bodywork_form",
        "open_body",
        display="Öppet",
        scopes=("passenger",),
    ),
    _rule(
        "BDY-117",
        "bodywork_code",
        "body_code",
        "AG",
        "bodywork_form",
        "cargo_wagon",
        display="Lastkombi",
        scopes=("passenger",),
    ),
    _rule(
        "BDY-118",
        "bodywork_code",
        "body_code",
        "08",
        "bodywork_form",
        "motorhome",
        display="Bostadsinredning",
        scopes=("passenger",),
    ),
    _rule(
        "BDY-119",
        "bodywork_code",
        "body_code",
        "96",
        "bodywork_form",
        None,
        display="Polisbil",
        scopes=("passenger",),
    ),
    _rule(
        "BDY-120",
        "bodywork_code",
        "body_code",
        "98",
        "bodywork_form",
        None,
        display="Övrigt",
        scopes=("passenger",),
    ),
    _rule(
        "BDY-BUS",
        "bodywork_code",
        "body_code",
        tuple(f"C{letter}" for letter in "ABCDEFGHIJ"),
        "bodywork_form",
        "bus",
        display="Buss",
        scopes=("bus",),
    ),
    _rule(
        "BDY-N-BC",
        "bodywork_code",
        "body_code",
        "BC",
        "bodywork_form",
        "semi_trailer_tractor",
        display="Dragfordon för påhängsvagn",
        scopes=("goods",),
    ),
    _rule(
        "BDY-N-BD",
        "bodywork_code",
        "body_code",
        "BD",
        "bodywork_form",
        "trailer_tractor",
        display="Dragfordon för släpvagn",
        scopes=("goods",),
    ),
    _rule(
        "BDY-N-BE",
        "bodywork_code",
        "body_code",
        "BE",
        "bodywork_form",
        "pickup",
        display="Pick-up",
        scopes=("goods",),
    ),
    _rule(
        "BDY-O-DA",
        "bodywork_code",
        "body_code",
        "DA",
        "bodywork_form",
        "semi_trailer",
        display="Påhängsvagn",
        scopes=("trailer",),
    ),
    _rule(
        "BDY-O-DB",
        "bodywork_code",
        "body_code",
        "DB",
        "bodywork_form",
        "drawbar_trailer",
        display="Släpvagn med dragstång",
        scopes=("trailer",),
    ),
    _rule(
        "BDY-O-DC",
        "bodywork_code",
        "body_code",
        "DC",
        "bodywork_form",
        "centre_axle_trailer",
        display="Släpkärra",
        scopes=("trailer",),
    ),
    _rule(
        "BDY-O-DE",
        "bodywork_code",
        "body_code",
        "DE",
        "bodywork_form",
        "rigid_drawbar_trailer",
        display="Släpvagn med fast dragstång",
        scopes=("trailer",),
    ),
    _rule(
        "BDY-O-DF",
        "bodywork_code",
        "body_code",
        "DF",
        "bodywork_form",
        "link_semi_trailer",
        display="Link-påhängsvagn",
        scopes=("trailer",),
    ),
    _rule(
        "BDY-O-DG",
        "bodywork_code",
        "body_code",
        "DG",
        "bodywork_form",
        "link_drawbar_trailer",
        display="Link-släpvagn med dragstång",
        scopes=("trailer",),
    ),
    _rule(
        "BDY-SA",
        "bodywork_code",
        "body_code",
        "SA",
        "bodywork_form",
        "motorhome",
        display="Campingbil",
    ),
    _rule(
        "BDY-SB",
        "bodywork_code",
        "body_code",
        "SB",
        "bodywork_form",
        None,
        display="Bepansrat fordon",
    ),
    _rule("BDY-SC", "bodywork_code", "body_code", "SC", "bodywork_form", None, display="Ambulans"),
    _rule("BDY-SD", "bodywork_code", "body_code", "SD", "bodywork_form", None, display="Likbil"),
    _rule(
        "BDY-SE", "bodywork_code", "body_code", "SE", "bodywork_form", "caravan", display="Husvagn"
    ),
    _rule("BDY-SF", "bodywork_code", "body_code", "SF", "bodywork_form", None, display="Mobilkran"),
    _rule(
        "BDY-SG",
        "bodywork_code",
        "body_code",
        "SG",
        "bodywork_form",
        None,
        display="Annat fordon avsett för särskilt ändamål",
    ),
    _rule(
        "BDY-SH",
        "bodywork_code",
        "body_code",
        "SH",
        "bodywork_form",
        None,
        display="Rullstolsanpassat fordon",
    ),
    _rule(
        "BDY-SJ", "bodywork_code", "body_code", "SJ", "bodywork_form", "dolly", display="Dollyaxel"
    ),
    _rule(
        "BDY-SK",
        "bodywork_code",
        "body_code",
        "SK",
        "bodywork_form",
        "exceptional_load_trailer",
        display="Släpvagn för exceptionell last",
    ),
    _rule(
        "BDY-SL",
        "bodywork_code",
        "body_code",
        "SL",
        "bodywork_form",
        None,
        display="Motorfordon för exceptionell last",
    ),
    _rule(
        "BDY-SM",
        "bodywork_code",
        "body_code",
        "SM",
        "bodywork_form",
        None,
        display="Redskapsbärare",
    ),
    _rule(
        "ELEC-001", "electrification", "ev_config", "EL", "electrification_type", "battery_electric"
    ),
    _rule("ELEC-002", "electrification", "ev_config", "ELHYBRID", "electrification_type", "hybrid"),
    _rule(
        "ELEC-003",
        "electrification",
        "ev_config",
        "LADDHYBRID",
        "electrification_type",
        "plug_in_hybrid",
    ),
    _rule(
        "ELEC-004",
        "electrification",
        "ev_config",
        ("ELHYBRID BRÄNSLECELL", "LADDHYBRID BRÄNSLECELL"),
        "electrification_type",
        "fuel_cell_hybrid",
    ),
    _rule(
        "ELEC-005",
        "electrification_marketing",
        ("model", "variant", "version", "type"),
        ("MHEV", "Mild Hybrid", "48V", "eTSI", "EQ Boost"),
        "electrification_type",
        "hybrid",
        manufacturers=("*",),
    ),
    _rule("FCOM-B", "fuel_combination", "fuel_combo", "B", "fuel_combination", "bi_fuel"),
    _rule("FCOM-D", "fuel_combination", "fuel_combo", "D", "fuel_combination", "dual_fuel"),
    _rule("FCOM-F", "fuel_combination", "fuel_combo", "F", "fuel_combination", "flex_fuel"),
    _rule("FCOM-T", "fuel_combination", "fuel_combo", "T", "fuel_combination", "tri_fuel"),
    _rule(
        "FUEL-000",
        "fuel_carrier",
        ("fuel1", "fuel2", "fuel3"),
        ("0", ""),
        "energy_sources",
        None,
        decision="proposed",
    ),
    _rule(
        "FUEL-001",
        "fuel_carrier",
        ("fuel1", "fuel2", "fuel3"),
        ("1", "B"),
        "energy_sources",
        "petrol",
    ),
    _rule(
        "FUEL-002",
        "fuel_carrier",
        ("fuel1", "fuel2", "fuel3"),
        ("2", "D"),
        "energy_sources",
        "diesel",
    ),
    _rule(
        "FUEL-003",
        "fuel_carrier",
        ("fuel1", "fuel2", "fuel3"),
        ("3", "E", "EL"),
        "energy_sources",
        "electricity",
        display="EV / Electric / El",
    ),
    _rule(
        "FUEL-004", "fuel_carrier", ("fuel1", "fuel2", "fuel3"), "4", "energy_sources", "kerosene"
    ),
    _rule("FUEL-005", "fuel_carrier", ("fuel1", "fuel2", "fuel3"), "5", "energy_sources", "lpg"),
    _rule("FUEL-006", "fuel_carrier", ("fuel1", "fuel2", "fuel3"), "6", "energy_sources", "gengas"),
    _rule(
        "FUEL-007", "fuel_carrier", ("fuel1", "fuel2", "fuel3"), "7", "energy_sources", "ethanol"
    ),
    _rule(
        "FUEL-008", "fuel_carrier", ("fuel1", "fuel2", "fuel3"), "8", "energy_sources", "methanol"
    ),
    _rule("FUEL-009", "fuel_carrier", ("fuel1", "fuel2", "fuel3"), "9", "energy_sources", "cng"),
    _rule("FUEL-010", "fuel_carrier", ("fuel1", "fuel2", "fuel3"), "10", "energy_sources", "rme"),
    _rule(
        "FUEL-011",
        "fuel_carrier",
        ("fuel1", "fuel2", "fuel3"),
        "11",
        "energy_sources",
        "paraffin_oil",
    ),
    _rule("FUEL-012", "fuel_carrier", ("fuel1", "fuel2", "fuel3"), "12", "energy_sources", "cng"),
    _rule(
        "FUEL-013",
        "fuel_carrier",
        ("fuel1", "fuel2", "fuel3"),
        "13",
        "energy_sources",
        "renewable_cng",
        display="rCNG / Renewable CNG",
    ),
    _rule("FUEL-014", "fuel_carrier", ("fuel1", "fuel2", "fuel3"), "14", "energy_sources", "e85"),
    _rule("FUEL-015", "fuel_carrier", ("fuel1", "fuel2", "fuel3"), "15", "energy_sources", "rme"),
    _rule(
        "FUEL-016",
        "fuel_carrier",
        ("fuel1", "fuel2", "fuel3"),
        "16",
        "energy_sources",
        "methane",
        display="Metan",
    ),
    _rule(
        "FUEL-017", "fuel_carrier", ("fuel1", "fuel2", "fuel3"), "17", "energy_sources", "hydrogen"
    ),
    _rule("FUEL-018", "fuel_carrier", ("fuel1", "fuel2", "fuel3"), "18", "energy_sources", "other"),
    _rule(
        "FUEL-019", "fuel_carrier", ("fuel1", "fuel2", "fuel3"), "19", "energy_sources", "diesel"
    ),
    _rule("FUEL-020", "fuel_carrier", ("fuel1", "fuel2", "fuel3"), "20", "energy_sources", "cng"),
    _rule("FUEL-021", "fuel_carrier", ("fuel1", "fuel2", "fuel3"), "21", "energy_sources", "lng"),
    _rule("TRN-001", "transmission_code", "gearbox", "M", "transmission_type", "manual"),
    _rule("TRN-002", "transmission_code", "gearbox", "A", "transmission_type", "automatic"),
    _rule(
        "TRN-003",
        "transmission_code",
        "gearbox",
        "V",
        "transmission_type",
        "cvt",
        display="Variomatic",
    ),
    _rule(
        "TRN-004A",
        "transmission_marketing",
        ("model", "variant", "version", "type"),
        "Geartronic",
        "transmission_type",
        "automatic",
        manufacturers=("Volvo",),
    ),
    _rule(
        "TRN-004B",
        "transmission_marketing",
        ("model", "variant", "version", "type"),
        "Tiptronic",
        "transmission_type",
        "automatic",
        manufacturers=("*",),
    ),
    _rule(
        "TRN-004C",
        "transmission_marketing",
        ("model", "variant", "version", "type"),
        "Steptronic",
        "transmission_type",
        "automatic",
        manufacturers=("BMW",),
    ),
    _rule(
        "TRN-005A",
        "transmission_marketing",
        ("model", "variant", "version", "type"),
        ("CVT", "Xtronic", "Multitronic"),
        "transmission_type",
        "cvt",
        manufacturers=("*",),
    ),
    _rule(
        "TRN-005B",
        "transmission_marketing",
        ("model", "variant", "version", "type"),
        ("e-CVT", "ECVT"),
        "transmission_type",
        "automatic",
        manufacturers=("*",),
        requires_electrification=True,
        display="e-CVT",
    ),
    _rule(
        "TRN-006A",
        "transmission_marketing",
        ("model", "variant", "version", "type"),
        "DSG",
        "transmission_type",
        "dct",
        manufacturers=("Volkswagen", "Audi"),
    ),
    _rule(
        "TRN-006B",
        "transmission_marketing",
        ("model", "variant", "version", "type"),
        ("S tronic", "S-Tronic"),
        "transmission_type",
        "dct",
        manufacturers=("Audi",),
    ),
    _rule(
        "TRN-006C",
        "transmission_marketing",
        ("model", "variant", "version", "type"),
        "PDK",
        "transmission_type",
        "dct",
        manufacturers=("Porsche",),
    ),
    _rule(
        "TRN-007",
        "transmission_code",
        "gearbox",
        "T",
        "transmission_type",
        "amt",
        display="Automated manual",
    ),
    _rule("TRN-008", "transmission_code", "gearbox", "Z", "transmission_type", "automatic"),
)

_V2_RULE_SET = TranslationRuleSet(
    version=RULE_SET_VERSION_V2,
    rules=tuple(sorted(_RULES, key=lambda rule: rule.rule_id)),
)

_V3_RULES = tuple(
    replace(rule, canonical_value="passenger_van", display_value="Passenger van")
    if rule.rule_id == "BDY-010"
    else rule
    for rule in _RULES
)
_V3_RULE_SET = TranslationRuleSet(
    version=RULE_SET_VERSION_V3,
    rules=tuple(sorted(_V3_RULES, key=lambda rule: rule.rule_id)),
)
_BODYWORK_FORM_V4 = {"wagon": "estate", "cargo_wagon": "cargo_estate"}
_LATEST_RULES = tuple(
    replace(rule, canonical_value=_BODYWORK_FORM_V4[rule.canonical_value])
    if rule.canonical_value in _BODYWORK_FORM_V4
    else rule
    for rule in _V3_RULES
)
_V4_RULE_SET = TranslationRuleSet(
    version=RULE_SET_VERSION_V4,
    rules=tuple(sorted(_LATEST_RULES, key=lambda rule: rule.rule_id)),
)
_DRIVE_RULES = (
    _rule(
        "DRV-001",
        "drive_marketing",
        ("model", "variant", "version", "type"),
        "4MATIC",
        "drive_type",
        "awd",
        manufacturers=("Mercedes-Benz",),
        display="All-wheel drive",
    ),
    _rule(
        "DRV-002",
        "drive_marketing",
        ("model", "variant", "version", "type"),
        "xDrive",
        "drive_type",
        "awd",
        manufacturers=("BMW",),
        display="All-wheel drive",
    ),
    _rule(
        "DRV-003",
        "drive_marketing",
        ("model", "variant", "version", "type"),
        "quattro",
        "drive_type",
        "awd",
        manufacturers=("Audi",),
        display="All-wheel drive",
    ),
    _rule(
        "DRV-004",
        "drive_marketing",
        ("model", "variant", "version", "type"),
        ("4Motion", "4 Motion"),
        "drive_type",
        "awd",
        manufacturers=("Volkswagen",),
        display="All-wheel drive",
    ),
    _rule(
        "DRV-008",
        "drive_flag",
        "is_4wd",
        "1",
        "drive_type",
        "awd",
        display="All-wheel drive",
    ),
)
_V5_RULE_SET = TranslationRuleSet(
    version=RULE_SET_VERSION_V5,
    rules=tuple(sorted((*_LATEST_RULES, *_DRIVE_RULES), key=lambda rule: rule.rule_id)),
)
_MODEL_FAMILY_RULES = (
    _rule(
        "MOD-001",
        "model_family",
        "model",
        "PASSAT",
        "model_family",
        "Passat",
        manufacturers=("Volkswagen",),
    ),
    _rule(
        "MOD-002",
        "model_family",
        "model",
        "GOLF",
        "model_family",
        "Golf",
        manufacturers=("Volkswagen",),
    ),
    _rule(
        "MOD-003", "model_family", "model", "XC60", "model_family", "XC60", manufacturers=("Volvo",)
    ),
    _rule(
        "MOD-004", "model_family", "model", "V60", "model_family", "V60", manufacturers=("Volvo",)
    ),
    _rule(
        "MOD-005", "model_family", "model", "V70", "model_family", "V70", manufacturers=("Volvo",)
    ),
    _rule(
        "MOD-006", "model_family", "model", "CEED", "model_family", "Ceed", manufacturers=("Kia",)
    ),
    _rule(
        "MOD-007", "model_family", "model", "XC40", "model_family", "XC40", manufacturers=("Volvo",)
    ),
    _rule(
        "MOD-008",
        "model_family",
        "model",
        "RAV4",
        "model_family",
        "RAV4",
        manufacturers=("Toyota",),
    ),
    _rule(
        "MOD-009",
        "model_family",
        "model",
        "TIGUAN",
        "model_family",
        "Tiguan",
        manufacturers=("Volkswagen",),
    ),
    _rule(
        "MOD-010",
        "model_family",
        "model",
        "OCTAVIA",
        "model_family",
        "Octavia",
        manufacturers=("Škoda",),
    ),
    _rule(
        "MOD-011", "model_family", "model", "NIRO", "model_family", "Niro", manufacturers=("Kia",)
    ),
    _rule(
        "MOD-012",
        "model_family",
        "model",
        "AURIS",
        "model_family",
        "Auris",
        manufacturers=("Toyota",),
    ),
    _rule(
        "MOD-013",
        "model_family",
        "model",
        "YARIS",
        "model_family",
        "Yaris",
        manufacturers=("Toyota",),
    ),
    _rule(
        "MOD-014",
        "model_family",
        "model",
        "QASHQAI",
        "model_family",
        "Qashqai",
        manufacturers=("Nissan",),
    ),
    _rule(
        "MOD-015",
        "model_family",
        "model",
        "CLIO",
        "model_family",
        "Clio",
        manufacturers=("Renault",),
    ),
    _rule(
        "MOD-016",
        "model_family",
        "model",
        "FOCUS",
        "model_family",
        "Focus",
        manufacturers=("Ford",),
    ),
    _rule(
        "MOD-017", "model_family", "model", "XC70", "model_family", "XC70", manufacturers=("Volvo",)
    ),
    _rule(
        "MOD-018", "model_family", "model", "V90", "model_family", "V90", manufacturers=("Volvo",)
    ),
    _rule(
        "MOD-019",
        "model_family",
        "model",
        "FABIA",
        "model_family",
        "Fabia",
        manufacturers=("Škoda",),
    ),
    _rule(
        "MOD-020", "model_family", "model", "V40", "model_family", "V40", manufacturers=("Volvo",)
    ),
    _rule(
        "MOD-021",
        "model_family",
        "model",
        "POLO",
        "model_family",
        "Polo",
        manufacturers=("Volkswagen",),
    ),
    _rule(
        "MOD-022",
        "model_family",
        "model",
        "COROLLA",
        "model_family",
        "Corolla",
        manufacturers=("Toyota",),
    ),
    _rule(
        "MOD-023",
        "model_family",
        "model",
        "AVENSIS",
        "model_family",
        "Avensis",
        manufacturers=("Toyota",),
    ),
    _rule(
        "MOD-024",
        "model_family",
        "model",
        "MODEL Y",
        "model_family",
        "Model Y",
        manufacturers=("Tesla",),
    ),
    _rule(
        "MOD-025",
        "model_family",
        "model",
        ("T-ROC", "T ROC"),
        "model_family",
        "T-Roc",
        manufacturers=("Volkswagen",),
    ),
)
_V6_RULE_SET = TranslationRuleSet(
    version=RULE_SET_VERSION_V6,
    rules=tuple(
        sorted(
            (*_LATEST_RULES, *_DRIVE_RULES, *_MODEL_FAMILY_RULES),
            key=lambda rule: rule.rule_id,
        )
    ),
)
_MODEL_FAMILY_PHASE_2_SPECS = (
    ("MOD-101", "Peugeot", ("308",), "308"),
    ("MOD-102", "Peugeot", ("208",), "208"),
    ("MOD-103", "Peugeot", ("2008",), "2008"),
    ("MOD-104", "Peugeot", ("3008",), "3008"),
    ("MOD-105", "Peugeot", ("5008",), "5008"),
    ("MOD-106", "Peugeot", ("508",), "508"),
    ("MOD-107", "Ford", ("KUGA",), "Kuga"),
    ("MOD-108", "Ford", ("FIESTA",), "Fiesta"),
    ("MOD-109", "Ford", ("MONDEO",), "Mondeo"),
    ("MOD-110", "Ford", ("S-MAX", "S MAX"), "S-Max"),
    ("MOD-111", "Toyota", ("C-HR", "C HR"), "C-HR"),
    ("MOD-112", "Toyota", ("AYGO",), "Aygo"),
    ("MOD-113", "Toyota", ("VERSO",), "Verso"),
    ("MOD-114", "Kia", ("SPORTAGE",), "Sportage"),
    ("MOD-115", "Kia", ("RIO",), "Rio"),
    ("MOD-116", "Kia", ("PICANTO",), "Picanto"),
    ("MOD-117", "Kia", ("STONIC",), "Stonic"),
    ("MOD-118", "Kia", ("EV6",), "EV6"),
    ("MOD-119", "Kia", ("SORENTO",), "Sorento"),
    ("MOD-120", "Kia", ("VENGA",), "Venga"),
    ("MOD-121", "Kia", ("XCEED", "X CEED"), "XCeed"),
    ("MOD-122", "Kia", ("OPTIMA",), "Optima"),
    ("MOD-123", "Tesla", ("MODEL 3",), "Model 3"),
    ("MOD-124", "Volvo", ("V50",), "V50"),
    ("MOD-125", "Volvo", ("XC90",), "XC90"),
    ("MOD-126", "Volvo", ("S60",), "S60"),
    ("MOD-127", "Volvo", ("C40",), "C40"),
    ("MOD-128", "Škoda", ("SUPERB",), "Superb"),
    ("MOD-129", "Škoda", ("KODIAQ",), "Kodiaq"),
    ("MOD-130", "Škoda", ("RAPID",), "Rapid"),
    ("MOD-131", "Škoda", ("YETI",), "Yeti"),
    ("MOD-132", "Škoda", ("KAMIQ",), "Kamiq"),
    ("MOD-133", "Škoda", ("KAROQ",), "Karoq"),
    ("MOD-134", "Škoda", ("ENYAQ",), "Enyaq"),
    ("MOD-135", "Renault", ("CAPTUR",), "Captur"),
    ("MOD-136", "Renault", ("MEGANE", "MÉGANE"), "Mégane"),
    ("MOD-137", "Renault", ("ZOE",), "Zoe"),
    ("MOD-138", "Renault", ("KADJAR",), "Kadjar"),
    ("MOD-139", "Honda", ("CR-V", "CR V"), "CR-V"),
    ("MOD-140", "Honda", ("CIVIC",), "Civic"),
    ("MOD-141", "Mitsubishi", ("OUTLANDER",), "Outlander"),
    ("MOD-142", "Mitsubishi", ("ASX",), "ASX"),
    ("MOD-143", "Dacia", ("SANDERO",), "Sandero"),
    ("MOD-144", "Dacia", ("DUSTER",), "Duster"),
    ("MOD-145", "Hyundai", ("I10", "I 10"), "i10"),
    ("MOD-146", "Hyundai", ("I20", "I 20"), "i20"),
    ("MOD-147", "Hyundai", ("I30", "I 30"), "i30"),
    ("MOD-148", "Hyundai", ("I40", "I 40"), "i40"),
    ("MOD-149", "Hyundai", ("TUCSON",), "Tucson"),
    ("MOD-150", "Hyundai", ("KONA",), "Kona"),
    ("MOD-151", "Hyundai", ("IX35", "IX 35"), "ix35"),
    ("MOD-152", "Volkswagen", ("TOURAN",), "Touran"),
    ("MOD-153", "Volkswagen", ("CADDY",), "Caddy"),
    ("MOD-154", "Volkswagen", ("T-CROSS", "T CROSS"), "T-Cross"),
    ("MOD-155", "Volkswagen", ("SHARAN",), "Sharan"),
    ("MOD-156", "Volkswagen", ("TOUAREG",), "Touareg"),
    ("MOD-157", "Volkswagen", ("MULTIVAN",), "Multivan"),
    ("MOD-158", "Volkswagen", ("ID.4", "ID 4"), "ID.4"),
    ("MOD-159", "Volkswagen", ("ID.3", "ID 3"), "ID.3"),
    ("MOD-160", "Volkswagen", ("UP!", "UP"), "up!"),
    ("MOD-161", "Opel", ("ASTRA",), "Astra"),
    ("MOD-162", "Opel", ("CORSA",), "Corsa"),
    ("MOD-163", "Opel", ("MOKKA",), "Mokka"),
    ("MOD-164", "Mazda", ("CX-5", "CX 5"), "CX-5"),
    ("MOD-165", "Mazda", ("CX-30", "CX 30"), "CX-30"),
    ("MOD-166", "Mazda", ("CX-3", "CX 3"), "CX-3"),
    ("MOD-167", "Mazda", ("MAZDA3", "MAZDA 3", "3"), "Mazda3"),
    ("MOD-168", "SEAT", ("ARONA",), "Arona"),
    ("MOD-169", "SEAT", ("LEON",), "Leon"),
    ("MOD-170", "SEAT", ("IBIZA",), "Ibiza"),
    ("MOD-171", "Subaru", ("LEGACY",), "Legacy"),
    ("MOD-172", "Subaru", ("FORESTER",), "Forester"),
    ("MOD-173", "Subaru", ("OUTBACK",), "Outback"),
    ("MOD-174", "Subaru", ("XV",), "XV"),
    ("MOD-175", "Nissan", ("LEAF",), "Leaf"),
    ("MOD-176", "Nissan", ("JUKE",), "Juke"),
    ("MOD-177", "Citroën", ("C3",), "C3"),
    ("MOD-178", "Citroën", ("C4",), "C4"),
    ("MOD-179", "Citroën", ("C5",), "C5"),
    ("MOD-180", "Fiat", ("500",), "500"),
    ("MOD-181", "Fiat", ("FREEMONT",), "Freemont"),
    ("MOD-182", "MG", ("ZS",), "ZS"),
    ("MOD-183", "Suzuki", ("SX4",), "SX4"),
    ("MOD-184", "Suzuki", ("SWIFT",), "Swift"),
    ("MOD-185", "Saab", ("9-3", "9 3"), "9-3"),
    ("MOD-186", "Polestar", ("2",), "2"),
    ("MOD-187", "MINI", ("COOPER",), "Cooper"),
    ("MOD-188", "Audi", ("A1",), "A1"),
    ("MOD-189", "Audi", ("A3",), "A3"),
    ("MOD-190", "Audi", ("A4",), "A4"),
    ("MOD-191", "Audi", ("A5",), "A5"),
    ("MOD-192", "Audi", ("A6",), "A6"),
    ("MOD-193", "Audi", ("Q2",), "Q2"),
    ("MOD-194", "Audi", ("Q3",), "Q3"),
    ("MOD-195", "Audi", ("Q5",), "Q5"),
    (
        "MOD-196",
        "BMW",
        ("116D", "116I", "118D", "118I", "120D", "120I", "125I", "M135I"),
        "1 Series",
    ),
    (
        "MOD-197",
        "BMW",
        (
            "316I",
            "318D",
            "320D",
            "320I",
            "325I",
            "325XI",
            "328I",
            "330D",
            "330E",
            "330I",
            "335I",
            "M3",
            "M340I",
        ),
        "3 Series",
    ),
    (
        "MOD-198",
        "BMW",
        (
            "520D",
            "523I",
            "525D",
            "530D",
            "530E",
            "530I",
            "535D",
            "540D",
            "M 550D",
            "M550I",
            "M5",
        ),
        "5 Series",
    ),
    ("MOD-199", "BMW", ("X1",), "X1"),
    ("MOD-200", "BMW", ("X3",), "X3"),
    (
        "MOD-201",
        "Mercedes-Benz",
        ("A 160", "A160", "A 180", "A180", "A 200", "A200", "A 220", "A220", "A 250", "A250"),
        "A-Class",
    ),
    ("MOD-202", "Mercedes-Benz", ("GLC",), "GLC"),
)
_MODEL_FAMILY_PHASE_2_RULES = tuple(
    _rule(
        rule_id,
        "model_family",
        "model",
        terms,
        "model_family",
        canonical_value,
        manufacturers=(manufacturer,),
    )
    for rule_id, manufacturer, terms, canonical_value in _MODEL_FAMILY_PHASE_2_SPECS
)
_REVIEWED_RULE_SET = TranslationRuleSet(
    version=REVIEWED_RULE_SET_VERSION,
    rules=tuple(
        sorted(
            (
                *_LATEST_RULES,
                *_DRIVE_RULES,
                *_MODEL_FAMILY_RULES,
                *_MODEL_FAMILY_PHASE_2_RULES,
            ),
            key=lambda rule: rule.rule_id,
        )
    ),
)
_RULE_SETS = MappingProxyType(
    {
        RULE_SET_VERSION_V2: _V2_RULE_SET,
        RULE_SET_VERSION_V3: _V3_RULE_SET,
        RULE_SET_VERSION_V4: _V4_RULE_SET,
        RULE_SET_VERSION_V5: _V5_RULE_SET,
        RULE_SET_VERSION_V6: _V6_RULE_SET,
        REVIEWED_RULE_SET_VERSION: _REVIEWED_RULE_SET,
    }
)


def load_translation_rule_set(version: str) -> TranslationRuleSet:
    """Load one exact immutable rule-set version; never fall back silently."""

    try:
        return _RULE_SETS[version]
    except KeyError as error:
        raise RuleSetNotFoundError(f"translation rule set {version!r} is unavailable") from error
