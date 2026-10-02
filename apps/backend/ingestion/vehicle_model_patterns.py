"""Reviewed patterns that read a model family out of the word registry text names it by.

The learned model rules (`vehicle_core_rules`) need siblings that already have a
model. Old and rare cars often have none: a 1988 Volvo registered as
"VOLVO 744-883 GL" or a BMW as "BMW 325" shares no key with a car TS named. These
patterns read the brand text's model word (`brand_token`) the way a person would.

Every pattern answers only with a model family TS itself uses for that make (the
vocabulary), so a filled vehicle looks exactly like one TS named. Before a pattern
rule is kept it is checked against the vehicles whose model is already known;
see `vehicle_core_rules.pattern_rules`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Mapping
from itertools import accumulate, combinations

#: BMW names a car by series and engine: 320I, 525TDS, 118D -> 3, 5 and 1 Series,
#: sometimes with a door count after a slash ("323I/2", "316I/3"). No series number has
#: a 0 second: "306 D5" is the 535d's engine (M57 "306D5"), and 501, 507 and 700 are the
#: 1950s cars of those names, not a 5 or 7 Series.
_BMW_CODE = re.compile(r"([1-8])[1-9]\d[A-Z]{0,3}(?:/\d)?")
#: Mercedes-Benz names a car by class letter, alone or joined to its engine
#: ("C 180", "E220CDI"). ML is the M-Class.
_MERCEDES_CODE = re.compile(r"([ABCEGSV])(?:\d{3}[A-Z]{0,4})?")
_MERCEDES_WORDS = {"ML": "M-Class"}
#: Volvo type codes: the first two digits name the series and the third the body
#: (744 is a 740 saloon, 245 a 240 estate), optionally followed by an engine
#: code ("744-883", "244-410-2111") or run together with one ("1421341", a 142).
_VOLVO_TYPE = r"24[2-5]|26[2-5]|70[4-5]|74[4-5]|76[0-5]|78[0-2]|85[45]|94[4-5]|96[4-5]|14[2-5]"
_VOLVO_CODE = re.compile(rf"({_VOLVO_TYPE})(?:[-/]?\d+)*")
_VOLVO_TYPE_CODE = re.compile(_VOLVO_TYPE)
_VOLVO_SERIES = {"70": "760"}
#: Older Volvo type codes whose family is not their first two digits and a zero
#: (TecDoc: "P 122 S AMAZON", "164 (164)", "P 1800 (P18)", "P 210 DUETT (P211,
#: P212)", "340-360 (343, 345)", "440 (445)", "460 (464)", "480 (482)").
_VOLVO_NAMED = {
    **dict.fromkeys(("120", "121", "122", "123", "131", "132", "133", "221", "222", "223"), "Amazon"),
    # 1958-65 PV 544s, registered as "11134 E", "11234 F" (TecDoc "PV 544 (P11)").
    **dict.fromkeys(("111", "112"), "Pv 544"),
    "164": "164",
    **dict.fromkeys(("183", "184", "185"), "P 1800"),
    **dict.fromkeys(("210", "211", "212"), "Duett"),
    **dict.fromkeys(("343", "344", "345"), "340"),
    **dict.fromkeys(("440", "445"), "440"),
    **dict.fromkeys(("460", "464"), "460"),
    **dict.fromkeys(("480", "482"), "480"),
}
_VOLVO_NAMED_CODE = re.compile(r"(\d{3})(?:[-/]?\d+)*")
#: The same digits after "P" or "PV" are the 1944-65 chassis codes: "VOLVO PV 445 DH"
#: and "VOLVO P 44507 M" are 1950s Duetts (P445), not the 1988-96 440 (445), and
#: "PV 444" / "P 544" the PV 444 / PV 544.
_VOLVO_PV_NAMED = {"444": "Pv 444", "445": "Duett", "544": "Pv 544"}
#: Saab names a car by its model number and an engine or trim code run together
#: ("900I", "96GL", "99L-2SNLHD-2,0-CM"). Only a number that is itself a family
#: answers: the 1950s 93 is the 93, never the 9-3.
_SAAB_CODE = re.compile(r"(9\d{1,3})(?:[A-Z-].*)?")


#: Model families TS has never stated for (enough) cars because no normalization
#: rule names them yet, reviewed one by one against the registry texts that carry
#: them (2026-09-30). Each is the maker's own name, spelled as TecDoc's family where
#: TecDoc has one ("ID.7 Tourer (ED5)", "EX40 (536)") -- a filled family is the
#: matcher's model evidence -- and as TS already spells the make's other families.
#: The model text reader takes the longest name a text starts with, so "ID.7 TOURER
#: GTX" is the ID.7 Tourer and "BORN 150 KW 58/62 KWH" the Born.
#:
#: Left out on purpose: camper and ambulance conversions named by the converter
#: (Adria "TWIN 640SLB", "MATRIX 670SL", Nilsson "XC90 AMBULANCE"), classic cars
#: registered by engine number ("300 D", "744 883"), and texts too short to name one
#: model ("A", "COUPE", "ROADSTER").
REVIEWED_MODEL_NAMES: dict[str, tuple[str, ...]] = {
    "Aiways": ("U5",),
    "Alfa Romeo": ("Spider", "4C", "159", "164"),
    "Aston Martin": (
        "V8 Vantage", "V12 Vantage", "Vantage", "DB9", "DB11", "DBS", "DBX707", "Vanquish", "Rapide",
    ),
    "Bentley": (
        "Continental GT", "Continental GTC", "Continental Flying Spur", "Continental Supersports",
        "Flying Spur", "Bentayga", "Mulsanne", "Turbo R",
    ),
    # Not "2002", "1602", "2000" or "2500": TecDoc keeps those as versions inside one
    # family ("1502-2002 (E10)" with its "2002 Turbo"), and the matcher reads the
    # version from the text better than from a family named after one of them.
    "BMW": ("iX1", "iX3"),
    "BYD": ("Atto 2", "Atto 3", "Seal", "Seal U", "Seal 6", "Sealion 7", "Dolphin", "Han", "Tang"),
    "Cadillac": ("Deville", "Series 62"),
    "Chevrolet": ("Alero", "Tacuma", "Monza", "Master", "210", "Nomad", "Fleetline", "Fleetmaster", "Delray", "Brookwood", "Parkwood", "Kingswood"),
    "Chrysler": ("Windsor", "Neon", "Vision"),
    "Citroën": ("C1", "C-Zero", "BX", "ZX"),
    "Cupra": ("Born", "Tavascan", "Raval"),
    "DeSoto": ("Firedome", "Fireflite", "Adventurer"),
    "DFSK": ("Seres 3", "Fengon 500"),
    "Dodge": ("Custom Royal", "Avenger"),
    "Edsel": ("Ranger", "Pacer"),
    "FAW": ("E-HS9",),
    "Ferrari": ("Purosangue", "812 GTS", "F12", "12Cilindri", "GTC4 Lusso", "430 Scuderia", "599", "550 Maranello"),
    "Fiat": (
        "126", "127", "128", "131", "850", "1100", "1500", "124", "X1/9", "Ritmo", "Regata",
        "Brava", "Stilo", "Marea", "Uno", "Croma", "Coupe", "Tipo", "500X", "500L", "Punto", "Grande Punto", "Panda", "600", "Ducato", "Sedici", "Bravo",
        "124 Spider", "Doblo", "Talento", "Qubo", "Fiorino", "Barchetta", "Scudo",
    ),
    "Fisker": ("Ocean", "Karma"),
    "Ford": (
        "Cortina", "Anglia", "Consul", "Zodiac",
        "Tourneo Custom", "Tourneo Connect", "Tourneo Courier", "Galaxie", "Galaxie 500", "Model A",
        "Model T", "Skyliner", "Falcon", "Street Ka", "Flex", "Windstar", "Scorpio", "Probe",
    ),
    "GMC": ("Yukon", "Yukon XL"),
    "Honda": ("e", "e:Ny1", "Logo", "Stream"),
    "Hummer": ("H2", "H3"),
    "Hyundai": ("Ioniq 5", "Ioniq 6", "Inster", "Nexo", "XG"),
    "INEOS": ("Grenadier",),
    "Isuzu": ("Trooper",),
    "Jaguar": ("I-Pace", "X-Type", "XKR", "XK8", "XJR", "XJS", "XJ8", "XJ6", "Sovereign"),
    "JAC": ("E-JS4",),
    "Kia": ("Pride", "EV2", "EV3", "EV4", "EV5", "EV9", "Seltos", "K4"),
    "Lada": ("Niva", "4x4", "Samara"),
    "Lamborghini": ("Urus", "Huracán", "Aventador", "Gallardo"),
    "Lincoln": ("Town Car", "Continental", "Premiere", "Navigator", "Capri"),
    "Lotus": ("Eletre", "Elise", "Exige", "Evora", "Emira"),
    "Lynk & Co": ("01", "02", "08"),
    "MAN": ("TGE",),
    "Maserati": ("Levante",),
    "Maxus": ("Euniq 5", "Euniq 6"),
    "Mazda": ("6e", "CX-6e", "Premacy"),
    "McLaren": ("720S", "750S", "570S", "570GT", "650S", "Artura", "GT", "MP4-12C"),
    "Mercury": ("Monterey", "Montclair", "Cougar", "Park Lane", "Marquis", "Grand Marquis"),
    "MG": ("MG3", "MG4", "MG5", "Marvel R", "EHS", "RX6", "S5", "S6"),
    "Morgan": ("Plus 4", "Plus Four", "Plus 8", "Plus Six", "Aero 8", "4/4"),
    "Morris": ("Minor",),
    "NIO": ("ET5", "ET5 Touring", "ET7", "EL6", "EL7"),
    "MINI": ("Aceman",),
    "Mitsubishi": ("Space Wagon", "Space Runner"),
    "Oldsmobile": (
        "Ninety-Eight", "98", "88", "Super 88", "Dynamic 88", "Delta 88", "Delmont 88", "Cutlass",
        "Cutlass Supreme", "Starfire", "442",
    ),
    "Opel": ("Adam", "Antara", "Calibra", "Sintra"),
    "ORA": ("Funky Cat",),
    "Peugeot": ("iOn", "309", "405", "605"),
    "Plymouth": ("Valiant", "Savoy", "Volare", "Barracuda", "Satellite", "Road Runner", "Fury", "Sport Fury", "GTX", "Belvedere", "Duster"),
    "Polestar": ("1", "3", "4"),
    "Rover": ("25", "45", "75"),
    "Pontiac": ("Star Chief", "Trans Sport"),
    "Renault": ("10", "12", "16", "Scenic", "Megane Scenic", "Scenic E-Tech", "Arkana", "Modus"),
    "Rolls-Royce": ("Ghost", "Silver Spirit", "Silver Shadow", "Silver Spur", "Wraith", "Phantom", "Corniche"),
    "SEAT": ("Born", "Formentor"),
    "Škoda": ("Felicia", "Favorit", "Epiq"),
    "SsangYong": ("Tivoli", "Korando", "XLV", "Rexton", "Rodius"),
    "Suzuki": ("S-Cross", "e Vitara"),
    "Toyota": ("bZ4X", "Mirai", "Picnic", "4 Runner"),
    "Saab": ("90", "92", "93", "95"),
    # "CC" is the 2012-16 CC (TecDoc "CC B7 (358)"), registered with the model text
    # "CC"; TS files the 2008-12 "PASSAT CC" under the Passat.
    "Volkswagen": (
        "ID. Polo", "ID.5", "ID.7", "ID.7 Tourer", "ID. Buzz", "Caravelle", "California", "Transporter", "Lupo", "Bora",
        "Corrado", "Santana", "Derby", "K70", "CC",
    ),
    "Volvo": ("EX30", "EX40", "EC40", "EX60", "EX90", "ES90", "Amazon", "140", "340", "440", "460", "Duett"),
    "XPENG": ("G6", "G9", "P7", "P7i"),
    "Zeekr": ("001", "7X", "7GT", "X"),
    "Austin": ("Mini", "Allegro", "Maxi", "A30", "A40", "Seven"),
    "BMC": ("Mini", "1100", "1300", "1800"),
    "Triumph": ("TR6", "Spitfire", "Herald", "Vitesse", "Stag", "GT6"),
    "Daewoo": ("Kalos", "Matiz"),
    "Nissan": ("Ariya", "Maxima", "300ZX"),
    "Audi": ("V8", "Cabriolet", "Coupe"),
    "Smart": ("#1", "#3", "#5"),
    "Subaru": ("Uncharted", "e-Outback"),
}

#: Registry spellings of a family under another name: TS files the Trans Am under the
#: Firebird, "BOXTER" misspells the Boxster, "2CV6" is the 2 CV with its engine.
REVIEWED_MODEL_ALIASES: dict[str, dict[str, str]] = {
    # TS files these under their base family (M3 cars as 3 Series, S6 as A6).
    "Audi": {"S6": "A6", "CABRIO": "Cabriolet"},
    "Cadillac": {
        "COUPE DE VILLE": "Deville", "SEDAN DE VILLE": "Deville", "COUPE DEVILLE": "Deville",
        "SEDAN DEVILLE": "Deville", "SERIE 62": "Series 62",
    },
    "Chevrolet": {"TWO TEN": "210"},
    # The registry abbreviates: "FIAT DOBL 1,6" and "FIAT DOB 1,6" are Doblos,
    # "FIAT COUP 20V" a Coupé.
    "Fiat": {"REGATTA": "Regata", "DOBL": "Doblo", "DOB": "Doblo", "COUP": "Coupe"},
    # The 2010-15 Sportage's generation code (TecDoc "SPORTAGE III (SL)"), which the
    # registry writes as the model text: "SL", "SLS" after the 2014 facelift.
    "Kia": {"SL": "Sportage", "SLS": "Sportage"},
    # Typing errors for the MX-5 on 1995-2008 roadsters: no Mazda 5 is a roadster.
    "Mazda": {"MIATA": "MX-5", "MZ-5": "MX-5", "5X-5": "MX-5"},
    "Plymouth": {"CUDA": "Barracuda"},
    # The Saab 97 is the Sonett III.
    "Saab": {"97": "Sonett"},
    "BMW": {"M3": "3 Series", "M5": "5 Series"},
    "Citroën": {"2CV4": "2 Cv", "2CV6": "2 Cv"},
    "Nissan": {"E-NV200": "NV200"},
    "Rover": {"R75": "75"},
    # The MCC Smart of 1998-2004 is the City-Coupé.
    "Smart": {"MCC": "City-coupe"},
    "DS": {"DS7": "Ds 7 Crossback"},
    "Pontiac": {"TRANS AM": "Firebird"},
    "Porsche": {"BOXTER": "Boxster"},
    "Toyota": {"FOUR RUNNER": "4 Runner"},
}

#: Families TS gave some cars by misreading a trim as a model, by the family the
#: text really names: TS read "GT 500" and "GT CONVERTIBLE PREMIUM" on six "FORD
#: MUSTANG" cars as the Ford GT. Such a sibling does not count against the answer.
MISREAD_STATED_FAMILIES: dict[str, dict[str, tuple[str, ...]]] = {
    "Ford": {"Mustang": ("Gt",)},
}

#: Names TS gave a few cars that are no model of the make, kept out of the vocabulary
#: so no text is read as one and no rule answers with one. Renault "B" is the Clio
#: II's body code: TS states it for 5 cars (3 of them "RENAULT B CLIO"), and Clio for
#: all 60 TS-named cars with the brand text "RENAULT B".
REVIEWED_NON_FAMILIES: dict[str, frozenset[str]] = {
    "Renault": frozenset({"B"}),
}

#: Other words the registry writes a make by, which a manufacturer rule cannot learn
#: because the make's own first word comes first: "MERCEDES B E240" and "M B 250 CE"
#: abbreviate Mercedes-Benz, so the "B" is no B-Class.
REVIEWED_MAKE_SPELLINGS: dict[str, tuple[str, ...]] = {
    "Mercedes-Benz": ("MERCEDES B", "M B"),
}

#: Cars the registry names like a family of the make, which TS gives no family: the
#: Renault 4CV of 1947-61 ("RENAULT 4 CV R 1062", "RENAULT R10624CV", type R 1062) is
#: not the Renault 4 of 1961-94, and TecDoc has no 4CV. No family fits such a car.
REVIEWED_OTHER_CARS: dict[str, tuple[tuple[re.Pattern[str], str], ...]] = {
    "Renault": (
        (re.compile(
            r"(?<![A-Z0-9])R ?106[02]|(?<![0-9])4 ?[CX]V(?![A-Z0-9])|(?<![A-Z0-9])CV 4(?![0-9])"
        ), "4CV"),
    ),
}

#: Families TS keeps apart in name only, which a text naming either does not hold
#: against the other: TS files every iX1 and iX3 it names under the X1 and X3 (2,376
#: and 1,999 cars), and TecDoc keeps their KTypes under "X1 (U11)" and "X3".
REVIEWED_SAME_FAMILIES: dict[str, tuple[frozenset[str], ...]] = {
    "BMW": (frozenset({"iX1", "X1"}), frozenset({"iX3", "X3"})),
}

#: Naming questions on hold with the data owner (2026-10-01), as (filled, read): until
#: they are decided a fill is not refused for a text naming the other name, so today's
#: fills stay. A T4/T5 with CARAVELLE text filled Multivan (TS states Multivan for type
#: 7HC); the Lada 4x4 that the Niva was renamed to; a Scénic II (type JM) filled Megane,
#: as TS states for 151 type-JM cars.
NAMING_ON_HOLD: dict[str, tuple[tuple[str, str], ...]] = {
    "Volkswagen": (("Multivan", "Caravelle"),),
    "Lada": (("4x4", "Niva"),),
    "Renault": (("Megane", "Scenic"),),
}


def same_family_names(manufacturer: str, family: str) -> tuple[str, ...]:
    """The family and the names TS files it under as well (`REVIEWED_SAME_FAMILIES`)."""

    partners = (group for group in REVIEWED_SAME_FAMILIES.get(manufacturer, ()) if family in group)
    return (family, *sorted({name for group in partners for name in group} - {family}))


def tolerated_names(manufacturer: str, family: str) -> tuple[str, ...]:
    """The names a text may give a car filled `family` without contradicting it: its
    same-family names, and the other name of a naming question on hold."""

    pairs = NAMING_ON_HOLD.get(manufacturer, ())
    on_hold = tuple(read for filled, read in pairs if filled == family)
    return (*same_family_names(manufacturer, family), *on_hold)


def other_car_named(manufacturer: str, texts: Collection[str]) -> str | None:
    """The car a text names that TS gives no family (`REVIEWED_OTHER_CARS`), or None."""

    for pattern, car in REVIEWED_OTHER_CARS.get(manufacturer, ()):
        if any(pattern.search(" ".join(_words(text))) for text in texts):
            return car
    return None


#: Families whose electric version the maker sells under its own name: an electric
#: car is never the combustion family, nor a combustion car the electric one. TS
#: registers the Mach-E as "FORD MUSTANG MACH-E", but AIS also sends an electric
#: "FORD MUSTANG" (VIN 3FMTK..., a Mach-E).
REVIEWED_ELECTRIC_FAMILIES: dict[str, dict[str, str]] = {
    "Ford": {"Mustang": "Mustang Mach-e"},
}


def fuel_names_another_family(manufacturer: str, family: str, fuel: str | None) -> str | None:
    """The family the car's fuel says it is instead, or None when the fuel fits."""

    pairs = REVIEWED_ELECTRIC_FAMILIES.get(manufacturer, {})
    electric = (fuel or "").strip().lower() == "electricity"
    if electric and family in pairs:
        return pairs[family]
    combustion = {electric_family: combustion_family for combustion_family, electric_family in pairs.items()}
    if fuel and not electric and family in combustion:
        return combustion[family]
    return None


#: Motorhome builders the registry writes where the model goes ("MERCEDES-BENZ
#: RAPIDO", "RENAULT ADRIA ACTIVE", "M B FRANKIA A700"). Such a car is a motorhome
#: on a van's chassis, never the saloon a sibling taught a rule: TS itself misread
#: 22 Rapidos as S-Classes, and the rules learned from them filled 207 more. Names
#: that are also a make's model (Dodge Challenger, Chevrolet Malibu) are left out.
REVIEWED_CONVERTER_WORDS = frozenset({
    "ADRIA", "AUTOSTAR", "BENIMAR", "BURSTNER", "CARADO", "CARTHAGO", "CHAUSSON", "CONCORDE",
    "DETHLEFFS", "ELNAGH", "ETRUSCO", "FRANKIA", "GLOBECAR", "HYMER", "ITINEO", "KABE", "KNAUS",
    "LAIKA", "MCLOUIS", "MOBILVETTA", "MORELO", "NIESMANN", "PILOTE", "POSSL", "PÖSSL", "RAPIDO",
    "RIMOR", "ROLLERTEAM", "SUNLIGHT", "TRIGANO", "WEINSBERG", "WESTFALIA",
})

#: The van and chassis families a converter builds on, by make. A make listed here
#: whose car names a converter keeps only these; makes not listed are not judged.
REVIEWED_CONVERTER_BASES: dict[str, tuple[str, ...]] = {
    "Mercedes-Benz": ("Sprinter", "Vito", "V-Class", "Viano"),
    "Fiat": ("Ducato", "Scudo", "Talento", "Doblo"),
    "Ford": (
        "Transit", "Transit Tourneo", "Transit Connect", "Tourneo Custom", "Tourneo Connect", "E",
        "Econoline",
    ),
    "Renault": ("Master", "Trafic", "Kangoo"),
    "Citroën": ("Jumper", "Jumpy", "Berlingo", "Spacetourer"),
    "Peugeot": ("Boxer", "J5", "Expert", "Partner", "Traveller", "Rifter"),
    "Volkswagen": (
        "Crafter", "Transporter", "Multivan", "Caravelle", "California", "Grand California",
        "Caddy",
    ),
    "Iveco": ("Daily",),
}


def converter_names_another_family(
    manufacturer: str, family: str, texts: Collection[str]
) -> str | None:
    """The converter a motorhome's registry text names, when `family` is not a van
    it is built on (REVIEWED_CONVERTER_BASES); None when nothing contradicts it."""

    bases = REVIEWED_CONVERTER_BASES.get(manufacturer)
    if bases is None or family in bases:
        return None
    family_key = _compact(family)
    for text in texts:
        for word in _words(text):
            # "SKODA RAPIDO" misspells the Rapid: a word that spells the family is its own.
            spells_family = bool(family_key) and word.startswith(family_key)
            if word in REVIEWED_CONVERTER_WORDS and not spells_family:
                return word
    return None


#: Volvo texts that name a 940 and a 960 type code at once ("944-964", "945-965"):
#: the registry wrote the shared range, and the engine tells which car it is.
_VOLVO_TWO_SERIES = re.compile(r"(?<!\d)9([46])[45][-/ ]9([46])[45](?!\d)")


def _names_two_volvo_series(numbers: Collection[str]) -> bool:
    """True when the numbers hold the type codes of two sibling series, the 240/260,
    740/760 or 940/960 ("944-964", "745 765"): the registry wrote the range they share,
    so the text names neither. Another number after a code is its engine or version
    ("944-855" is a 940, 855 no 850)."""

    codes = [number for number in numbers if _VOLVO_TYPE_CODE.fullmatch(number)]
    pairs = combinations(codes, 2)
    return any(code[0] == other[0] and {code[1], other[1]} == {"4", "6"} for code, other in pairs)


#: Six-cylinder petrol engines (B6254, B6304) are the 960's; the four-cylinder
#: B200/B230/B234 the 940's. Diesels (D24) went in both and decide nothing.
_VOLVO_SERIES_BY_ENGINE = ((re.compile(r"B6\d"), "960"), (re.compile(r"B2\d"), "940"))


def engine_names_another_family(
    manufacturer: str, family: str, texts: Collection[str], engine_code: str | None
) -> str | None:
    """The Volvo series the engine names, when the text names both 940 and 960 and
    `family` is the other one; None when nothing contradicts it."""

    if manufacturer != "Volvo" or family not in {"940", "960"} or not engine_code:
        return None
    if not any(
        (match := _VOLVO_TWO_SERIES.search(_fold(text))) and match.group(1) != match.group(2)
        for text in texts
    ):
        return None
    engine = engine_code.strip().upper()
    for pattern, series in _VOLVO_SERIES_BY_ENGINE:
        if pattern.match(engine):
            return series if series != family else None
    return None


#: Chassis codes the registry writes instead of a model ("HONDA RD1", "RENAULT BA"),
#: each checked against the codes TecDoc gives that family ("CR-V I (RD_)", "MEGANE I
#: (BA0/1_)") and kept only where one family carries it. Ford "DM2" (C-Max, Focus
#: C-Max, Kuga) and Suzuki "SJ" (Samurai, Jimny) carry more than one and are left out.
REVIEWED_CHASSIS_CODES: dict[str, tuple[tuple[str, str], ...]] = {
    "Honda": (
        (r"RD\d", "Cr-v"), (r"E[PU]\d", "Civic"), (r"E[JK]\d", "Civic"), (r"MB\d", "Civic"),
        (r"C[EGHLM]\d", "Accord"), (r"GH\d", "Hr-v"), (r"BB\d", "Prelude"), (r"GA3", "Logo"),
        (r"RN\d", "Stream"),
    ),
    "Renault": (
        (r"[BDEKL]A", "Megane"), (r"JA", "Megane Scenic"), (r"KC", "Kangoo"), (r"[BK]56", "Laguna"),
        (r"C06", "Twingo"),
    ),
    "Ford": ((r"P3T[SC]|31F", "Taunus 17M"), (r"G[NF]R", "Scorpio"), (r"DBY", "Focus")),
    "Jaguar": ((r"XJ40|X300", "Xj"),),
    "Fiat": ((r"110F", "500"),),
}

_SEPARATOR = re.compile(r"[^A-Z0-9ÅÄÖ]+")
_BETWEEN_DIGITS = re.compile(r"(?<=\d) (?=\d)")


def _fold(text: str) -> str:
    """Upper case without accents, Swedish letters kept: "Huracán" reads as "HURACAN",
    "Citroën" as "CITROEN"."""

    return "".join(
        char if char in "ÅÄÖ" else unicodedata.normalize("NFKD", char).encode("ascii", "ignore").decode()
        for char in text.upper()
    )


def _words(text: str) -> list[str]:
    return _SEPARATOR.sub(" ", _fold(text)).split()


def _compact(text: str) -> str:
    """Spelling without case, accents, spaces or punctuation -- except between two
    digits, where a separator is part of the name: Saab's 1950s 93 is not the 9-3."""

    return _BETWEEN_DIGITS.sub("-", " ".join(_words(text))).replace(" ", "")


#: What the registry separates its words by: a manufacturer rule learns a make word up
#: to the first of these ("VOLKSWAGEN" in "VOLKSWAGEN, VW", "FORD-T" in "FORD-T ...").
_REGISTRY_WORD_END = re.compile(r"[ ,&/+]+")


def _strip_make_once(
    groups: list[list[str]], makes: set[str], own: set[str]
) -> list[list[str]] | None:
    words = [word for group in groups for word in group]
    ends = set(accumulate(len(group) for group in groups))
    # Longest first: "ASTON MARTIN DB9" loses "ASTON MARTIN", "FORD-CNG-TECHNIK FOCUS"
    # all of its make word. A make written over several registry words ends where one
    # does: "FORD-T" is no make in "FORD T.E.C"; within the first it may end anywhere
    # ("VW-GOLF", "CHEV.ASTRO").
    for count in range(min(len(words), max(3, len(groups[0]))), 0, -1):
        at_a_word_end = count <= len(groups[0]) or count in ends
        if at_a_word_end and _compact(" ".join(words[:count])) in makes:
            return _drop_words(groups, count)
    # Only the make's own name is ever glued to the model ("MAZDA6").
    for make in own:
        if words[0].startswith(make) and len(words[0]) > len(make):
            return [[words[0][len(make):], *groups[0][1:]], *groups[1:]]
    return None


def _drop_words(groups: list[list[str]], count: int) -> list[list[str]]:
    rest = [list(group) for group in groups]
    while count and rest:
        dropped = min(count, len(rest[0]))
        rest[0], count = rest[0][dropped:], count - dropped
        if not rest[0]:
            rest.pop(0)
    return rest


def _without_make(
    text: str, manufacturer: str, make_words: Collection[str] = ()
) -> list[str] | None:
    """The words after the make, when the text starts with it: "FIAT TIPO", "MAZDA6",
    AIS brand texts that repeat it ("TOYOTA TOYOTA YARIS CROSS", "LYNK & CO LYNK AND
    CO 08"), and the other words the registry writes the make by ("VOLKSWAGEN, VW")."""

    own = {_compact(manufacturer), _compact(manufacturer.replace("&", " AND "))} - {""}
    # A learned make word that is the make glued to a number ("BMW525", "SAAB900") is
    # the model, read as "MAZDA6" is: "BMW 525 IX" is a 525iX, no electric iX.
    learned = {
        word for word in map(_compact, make_words)
        if not any(word.startswith(make) and word[len(make):][:1].isdigit() for make in own)
    }
    reviewed = {_compact(name) for name in REVIEWED_MAKE_SPELLINGS.get(manufacturer, ())}
    makes = own | learned | reviewed
    groups = [words for part in _REGISTRY_WORD_END.split(_fold(text)) if (words := _words(part))]
    rest, stripped = groups, False
    while rest and (shorter := _strip_make_once(rest, makes, own)) is not None:
        rest, stripped = shorter, True
    words = [word for group in rest for word in group]
    return words if stripped and words else None


_E_PREFIX_SAME_FAMILY = frozenset({"Citroën", "Peugeot", "Opel", "Fiat", "DS"})

_VOLVO_YEAR_LETTER = re.compile(r"(?:^|\s)[A-Z]\s*\+\s*(\S.*)$")
#: A number with a decimal point or comma is an engine size ("MAZDA 2.3 KOMBI" is a
#: Mazda 6 2.3, "KIA SPORTAGE 2,0"), not a model's name unless it spells one. A type
#: code with a point is no engine size ("VOLVO 945.211", "FIAT 1100,103D").
_ENGINE_SIZE = re.compile(r"(?<![0-9])(\d{1,2}[.,]\d{1,2})(?![0-9])[A-Z]*", re.IGNORECASE)


def preferred_spellings(counts: Mapping[str, int]) -> set[str]:
    """One name per spelling: the one TS uses most ("RAV4" over "Rav 4", "Megane"
    over "Mégane"), so a text that spells both is not taken for two families."""

    best: dict[str, tuple[int, str]] = {}
    for name, count in counts.items():
        key = _compact(name)
        if key not in best or (count, name) > best[key]:
            best[key] = (count, name)
    return {name for _, name in best.values()}


def model_text_family(
    manufacturer: str, text: str, vocabulary: Collection[str], *, make_words: Collection[str] = ()
) -> str | None:
    """The model family the registry's model text names, or None.

    The text names a family when it starts with that family's name, apart from
    case, spaces and punctuation, and goes on only with other words: "MAZDA6" is
    the 6, "LE SABRE" the Lesabre, "ID.7 TOURER GTX" the ID.7 Tourer. The make may
    come first ("FIAT TIPO"). The longest name wins ("V40 CROSS COUNTRY" is the
    V40 Cross Country, not the V40); two names of that length name neither.
    Volvo's 1990s "<model-year letter> + <model>" ("VOLVO V + V40") is read after
    the plus. A text naming a car TS has no family for ("RENAULT 4 CV") names none.
    """

    by_spelling: dict[str, set[str]] = {}
    for name in vocabulary:
        by_spelling.setdefault(_compact(name), set()).add(name)
    # An alias never competes with a name TS uses itself ("allroad" is its own family).
    for alias, family in REVIEWED_MODEL_ALIASES.get(manufacturer, {}).items():
        if family in vocabulary and _compact(alias) not in by_spelling:
            by_spelling[_compact(alias)] = {family}
    if plus := _VOLVO_YEAR_LETTER.search(text.upper()):
        text = plus.group(1)
    # An engine size names nothing ("4,2Q", "3,0I" with their letters), unless it
    # spells a name: "SAAB 9.3" is a 9-3.
    text = _ENGINE_SIZE.sub(lambda size: size[0] if _compact(size[1]) in by_spelling else " ", text)
    words = _words(text)
    if not words or other_car_named(manufacturer, (text,)):
        return None
    # A word the make is written by is skipped only whole, only when longer than a
    # letter, and never when it is a model's own name ("MGB" is a model of MG).
    all_make_words = {_compact(word) for word in make_words}
    make_words = [
        word for word in make_words if len(_compact(word)) > 1 and _compact(word) not in by_spelling
    ]
    readings = [words]
    rest = _without_make(text, manufacturer, make_words)
    # "VOLVO 944-964": the registry wrote the 940/960 range, so the text names neither
    # series (the guard lets the engine decide).
    if manufacturer.upper() == "VOLVO" and _names_two_volvo_series((rest or words)[:2]):
        return None
    if rest:
        readings.append(rest)
    # Stellantis names its electric versions of a family "e-" ("E-C3 AIRCROSS" is
    # the C3 Aircross); elsewhere an "e-" model is a car of its own (e-Outback).
    if manufacturer in _E_PREFIX_SAME_FAMILY:
        readings += [reading[1:] for reading in readings if len(reading) > 1 and reading[0] == "E"]
    # "GR VOYAGER" is the Grand Voyager -- but only where that spells a family:
    # Toyota's "GR YARIS" is Gazoo Racing, not a Grand Yaris.
    readings += [["GRAND" if word == "GR" else word for word in reading] for reading in readings if "GR" in reading]
    # Lexus names a car by its family and engine size: "IS200" is the IS.
    if manufacturer.upper() == "LEXUS":
        readings += [[match.group(1)] for reading in readings if (match := _LEXUS_CODE.fullmatch(reading[0]))]
    best: tuple[int, set[str]] = (0, set())
    tails: list[list[str]] = []
    for reading in readings:
        for length in range(len(reading), 0, -1):
            names = by_spelling.get(_compact(" ".join(reading[:length])))
            if names:
                if length > best[0]:
                    best, tails = (length, set(names)), [reading[length:]]
                elif length == best[0]:
                    best[1].update(names)
                    tails.append(reading[length:])
                break
    # A family named like the make ("MINI") is read off the make itself, so it answers
    # only when nothing after it names another family of the make ("MINI JCW
    # COUNTRYMAN", "CHEVY CAPRICE"). After a real model word the first name stands
    # ("911 CARRERA").
    if (
        len(best[1]) == 1
        and _named_like_the_make(
            chosen := next(iter(best[1])), manufacturer, words[0], make_words, all_make_words
        )
        and any(_names_another_family(tail, chosen, by_spelling) for tail in tails)
    ):
        return None
    if not best[1] and rest:
        # The make's own code patterns, also after Volvo's "P"/"PV" ("P 120-12134"),
        # where a code is read as the chassis code of its era ("PV 445" is a Duett).
        chassis_word = [(rest[1], True)] if rest[0] in {"P", "PV"} and len(rest) > 1 else []
        words_to_try = [(rest[0], False), *chassis_word]
        for word, after_p in words_to_try:
            if coded := pattern_model(manufacturer, word, vocabulary, after_p=after_p):
                return coded
    if not best[1] and rest and len(rest) > 1 and _is_chassis_code(rest[0], by_spelling):
        after_code = _whole_name(rest[1:], by_spelling)
        return None if after_code in _NOT_AFTER_CODE.get(manufacturer, ()) else after_code
    return best[1].pop() if len(best[1]) == 1 else None


def _named_like_the_make(
    name: str,
    manufacturer: str,
    first_word: str,
    make_words: Collection[str],
    all_make_words: Collection[str],
) -> bool:
    """True when `name` is spelled like the make: its own name, a word it is written by,
    or -- as the text's first word -- a make word that is also a family ("CHEVY")."""

    spelled = _compact(name)
    return spelled in {_compact(manufacturer), *map(_compact, make_words)} or (
        spelled == _compact(first_word) and spelled in all_make_words
    )


def names_only(manufacturer: str, text: str, family: str) -> bool:
    """True when `text` is the family's name and nothing more, the make allowed in
    front or glued on, a reviewed alias counting as the name: "EX30", "VOLVO EX30"
    and "MAZDA6" name only their family, "EX30 TWIN MOTOR" says more."""

    aliases = REVIEWED_MODEL_ALIASES.get(manufacturer, {})
    spellings = {_compact(family)} | {_compact(alias) for alias, name in aliases.items() if name == family}
    makes = {_compact(manufacturer), _compact(manufacturer.replace("&", " AND "))} - {""}
    whole = _compact(text)
    return whole in spellings or any(
        whole == make + spelling for make in makes for spelling in spellings
    )


def narrower_family(family: str, name: str) -> bool:
    """True when `family` is `name` with more words after it: "EX30 Cross Country"
    narrows "EX30", "Golf Sportsvan" narrows "Golf"; "Mazda3" does not narrow "3"."""

    family_words, name_words = _words(family), _words(name)
    return len(family_words) > len(name_words) and family_words[: len(name_words)] == name_words


# "E" is not a code: it marks the electric model ("SUBARU E-OUTBACK" is not the Outback).
def _names_another_family(tail: list[str], chosen: str, by_spelling: Mapping[str, set[str]]) -> bool:
    """True when the words after the chosen name name another family as well: a run
    of words that spells one ("FOCUS C-MAX" holds the C-Max), or a word of five or
    more letters only another family's name has ("MINI JCW COUNTRYMAN" holds the
    Countryman). Body words, short trims and numbers name nothing here: "128 COUPE
    1100" is a 128 coupé with an 1100 engine, "MUSTANG GT" a Mustang."""

    chosen_words = set(_words(chosen))
    others = {name for names in by_spelling.values() for name in names} - {chosen}
    distinctive = {
        word for name in others for word in _words(name)
        if len(word) >= 5 and word.isalpha() and word not in chosen_words and word not in _DESCRIBING_WORDS
    }
    if any(word in distinctive for word in tail):
        return True
    for start in range(len(tail)):
        for end in range(len(tail), start, -1):
            run = tail[start:end]
            spelled = _compact(" ".join(run))
            if (
                len(spelled) >= 4
                and any(char.isalpha() for char in spelled)
                and not set(run) <= _DESCRIBING_WORDS
                and by_spelling.get(spelled, set()) - {chosen}
            ):
                return True
    return False


#: Words that describe a car's body or trim, never another model, after a model name.
_DESCRIBING_WORDS = frozenset({
    "AVANT", "BREAK", "CABRIO", "CABRIOLET", "COMBI", "CONVERTIBLE", "COUPE", "ESTATE", "HATCHBACK",
    "KOMBI", "LIMOUSINE", "PICKUP", "ROADSTER", "SALOON", "SEDAN", "SPIDER", "SPYDER", "TARGA",
    "TOURER", "TOURING", "VAN", "VARIANT", "WAGON", "SPORT", "SPORTS",
})


_CHASSIS_CODE = re.compile(r"[A-DF-Z]|[A-Z]{2,3}|(?=.*\d)[A-Z0-9]{2,3}")
#: Two- and three-letter words that are abbreviations, not chassis codes.
_ABBREVIATIONS = frozenset({"GR", "GT", "GTI", "RS", "ST", "XL", "SW", "CC", "NEW", "BIG", "MAX"})
_LEXUS_CODE = re.compile(r"([A-Z]{2})\d{3}[A-Z]{0,2}")
#: Names a code in front makes another car: "450 SLC" is TecDoc's SL Coupe (C107),
#: not the 2016 SLC; "DB7 VANTAGE" is a DB7.
_NOT_AFTER_CODE: dict[str, tuple[str, ...]] = {
    "Mercedes-Benz": ("SLC",),
    "Aston Martin": ("Vantage",),
}


def _is_chassis_code(word: str, by_spelling: Mapping[str, set[str]]) -> bool:
    """A chassis code in front of the model ("FORD DAW FOCUS", "FORD B5Y MONDEO",
    "RENAULT BA - MEGANE"): a letter, two or three letters, or a short code with a
    digit -- never the start of a model's own name, and never an abbreviation ("GR
    VOYAGER" is the Grand Voyager)."""

    code = _compact(word)
    return (
        _CHASSIS_CODE.fullmatch(word) is not None
        and word not in _ABBREVIATIONS
        # A word that starts a family's name is part of it: "ID" in "ID. POLO".
        and not (len(code) > 1 and any(key.startswith(code) for key in by_spelling))
    )


def _whole_name(words: list[str], by_spelling: Mapping[str, set[str]]) -> str | None:
    """The one name the words spell, followed at most by engine sizes ("MONDEO 2,0"):
    after a chassis code, "FOCUS C-MAX" or "MEGANE SCENIC" name no family for sure."""

    for length in range(len(words), 0, -1):
        names = by_spelling.get(_compact(" ".join(words[:length])))
        if names:
            tail_is_engine = all(any(char.isdigit() for char in word) for word in words[length:])
            return next(iter(names)) if len(names) == 1 and tail_is_engine else None
    return None


def pattern_model(
    manufacturer: str, token: str, vocabulary: Collection[str], *, after_p: bool = False
) -> str | None:
    """The model family `token` names for this make, or None when it names none.

    `vocabulary` is the make's model families as TS spells them; a pattern whose
    answer TS never uses is not an answer. `after_p`: the token follows Volvo's
    "P"/"PV", so a code is the 1944-65 chassis code it was then.
    """

    word = token.strip().upper()
    if not word:
        return None
    by_key = {name.upper(): name for name in vocabulary}
    for alias, family in REVIEWED_MODEL_ALIASES.get(manufacturer, {}).items():
        by_key.setdefault(alias, family)
    for code, family in REVIEWED_CHASSIS_CODES.get(manufacturer, ()):
        if re.fullmatch(code, word):
            return by_key.get(family.upper())
    # The word is a model TS already names this make's cars by ("COROLLA", "307").
    if word in by_key:
        return by_key[word]
    answer: str | None = None
    make = manufacturer.strip().upper()
    if make == "BMW" and (match := _BMW_CODE.fullmatch(word)):
        answer = f"{match.group(1)} Series"
    elif make == "MERCEDES-BENZ":
        if word in _MERCEDES_WORDS:
            answer = _MERCEDES_WORDS[word]
        elif match := _MERCEDES_CODE.fullmatch(word):
            answer = f"{match.group(1)}-Class"
    elif make == "VOLVO" and after_p and (match := _VOLVO_NAMED_CODE.fullmatch(word)) and (
        match.group(1) in _VOLVO_PV_NAMED
    ):
        answer = _VOLVO_PV_NAMED[match.group(1)]
    elif make == "VOLVO" and (match := _VOLVO_CODE.fullmatch(word)):
        if _names_two_volvo_series(re.findall(r"\d+", word)):
            return None
        code = match.group(1)
        answer = _VOLVO_SERIES.get(code[:2], f"{code[:2]}0")
    elif make == "VOLVO" and (match := _VOLVO_NAMED_CODE.fullmatch(word)) and match.group(1) in _VOLVO_NAMED:
        answer = _VOLVO_NAMED[match.group(1)]
    elif make == "SAAB" and (match := _SAAB_CODE.fullmatch(word)):
        answer = match.group(1)
    if answer is None:
        return None
    return by_key.get(answer.upper())
