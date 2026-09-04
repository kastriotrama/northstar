"""Draft manufacturer-scoped ``model_family`` rules from TecDoc plus observed source models.

Read-only. The generator prints rule source for review; it never writes the
dictionary. Two inputs are combined:

1. ``core.tecdoc_canonical_candidates`` supplies the family vocabulary. Families
   are grouped by their leading token, so ``E-CLASS (W213)``, ``E-CLASS Coupe
   (C238)`` and ``E-CLASS T-Model (S214)`` collapse to one canonical ``E-Class``.
2. ``staging.transportstyrelsen_raw`` supplies every distinct registry ``model``
   string recorded for the manufacturer -- not just the ones that fell to
   ``candidates.model_family`` -- because the rules exist to carry TS through the
   normalization pipeline, so the terms have to span what TS actually holds.
   ``core.normalization_results`` is joined only to resolve the manufacturer.

The canonical value is load-bearing rather than cosmetic. ``ReviewedModelAliasIndex``
links a rule to a TecDoc family only when the canonical key is a whole-token
prefix of the family key, which is why ``GLA`` is used for both ``GLA (H247)``
and ``GLA-CLASS (X156)`` -- ``GLA-Class`` would silently miss the newer one.

Terms are emitted so the runtime matcher in ``_normalize_model_family`` can hit
them: it accepts a term that equals the model key or is a whole-token prefix of
it. ``E 220`` therefore covers ``E 220 CDI``, but a glued ``E220CDI`` has no
token boundary and needs its own term.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import psycopg

from ingestion.config import get_ingestion_settings
from ingestion.normalization_repository import SOURCE_TABLE

TECDOC_TABLE = "core.tecdoc_canonical_candidates"
_NON_WORD = re.compile(r"[^A-Z0-9ÅÄÖÉÜ]+")
_DESIGNATOR = re.compile(r"^(?P<prefix>[A-Z]{1,4})\s*(?P<number>\d{2,3})(?![0-9])")

# TecDoc leading token -> canonical model_family value.
CANONICAL_FAMILIES: dict[str, str] = {
    "8": "/8",
    "123": "123",
    "124": "124",
    "170": "170",
    "190": "190",
    "230": "230",
    "300": "300",
    "A": "A-Class",
    "B": "B-Class",
    "C": "C-Class",
    "E": "E-Class",
    "G": "G-Class",
    "M": "M-Class",
    "R": "R-Class",
    "S": "S-Class",
    "T": "T-Class",
    "V": "V-Class",
    "X": "X-Class",
    "AMG": "AMG GT",
    "CABRIOLET": "Cabriolet",
    "COUPE": "Coupe",
    "CITAN": "Citan",
    "CLA": "CLA",
    "CLC": "CLC",
    "CLE": "CLE",
    "CLK": "CLK",
    "CLS": "CLS",
    "GL": "GL",
    "GLA": "GLA",
    "GLB": "GLB",
    "GLC": "GLC",
    "GLE": "GLE",
    "GLK": "GLK",
    "GLS": "GLS",
    "GULLWING": "Gullwing",
    "HECKFLOSSE": "Heckflosse",
    "HENSCHEL": "Henschel",
    "MARCO": "Marco Polo",
    "MB": "MB",
    "PAGODE": "Pagode",
    "PONTON": "Ponton",
    "PULLMANN": "Pullmann",
    "SL": "SL",
    "SLC": "SLC",
    "SLK": "SLK",
    "SLR": "SLR",
    "SLS": "SLS AMG",
    "SPRINTER": "Sprinter",
    "T1": "T1",
    "T2": "T2",
    "VANEO": "Vaneo",
    "VARIO": "Vario",
    "VIANO": "Viano",
    "VITO": "Vito",
}

# Source-side designator prefix -> canonical family. Registry strings use the
# sales designation, which is not always the TecDoc family name: ML is TecDoc's
# M-CLASS, and the CL coupe is carried as S-CLASS Coupe (C215/C216).
SOURCE_PREFIXES: dict[str, str] = {
    "A": "A-Class",
    "B": "B-Class",
    "C": "C-Class",
    "E": "E-Class",
    "G": "G-Class",
    "R": "R-Class",
    "S": "S-Class",
    "V": "V-Class",
    "X": "X-Class",
    "ML": "M-Class",
    "CL": "S-Class",
    "CLA": "CLA",
    "CLC": "CLC",
    "CLE": "CLE",
    "CLK": "CLK",
    "CLS": "CLS",
    "GL": "GL",
    "GLA": "GLA",
    "GLB": "GLB",
    "GLC": "GLC",
    "GLE": "GLE",
    "GLK": "GLK",
    "GLS": "GLS",
    "SL": "SL",
    "SLC": "SLC",
    "SLK": "SLK",
    "SLR": "SLR",
    "SLS": "SLS AMG",
    "GT": "AMG GT",
    # EQ designations have no TecDoc family in this dump; they normalize but the
    # alias index will not link them until TecDoc publishes the families.
    "EQA": "EQA",
    "EQB": "EQB",
    "EQC": "EQC",
    "EQE": "EQE",
    "EQS": "EQS",
    # EQV is W447 and EQT is W420 -- the same chassis TecDoc files as
    # V-CLASS and T-CLASS. The other EQ cars have no TecDoc family.
    "EQV": "V-Class",
    "EQT": "T-Class",
}

# Source strings that name the family outright instead of a designator.
SOURCE_NAME_ALIASES: dict[str, str] = {
    "VITO": "Vito",
    "VIANO": "Viano",
    "VANEO": "Vaneo",
    "SPRINTER": "Sprinter",
    "CITAN": "Citan",
    "MARCO POLO": "Marco Polo",
    "VARIO": "Vario",
    "SLS AMG": "SLS AMG",
    "SLS": "SLS AMG",
    "AMG GT": "AMG GT",
    "EVITO": "Vito",
    "T CLASS": "T-Class",
}

# Bodybuilders type their own model codes into the registry on a Mercedes
# chassis. "B 690" and "T585S" are Hymer motorhomes, not a B-Class or T-Class,
# so their strings must not become terms.
_CONVERTER_BRANDS = frozenset(
    {"HYMER", "KABE", "BINZ", "NORDIC", "SUPERSONIC", "DETHLEFFS", "ADRIA"}
)

# Chassis codes that look like designations. S211 is the E-Class T-Model body,
# not an S-Class.
_EXCLUDED_MODEL_KEYS = frozenset({"S211", "S 211"})

# "CLASSE E" is the French/Italian registry rendering of the family name.
_CLASSE_PREFIX = "CLASSE "

# The G-Wagen is the one pre-1994 numeric designation whose suffix names the
# family outright: 280 GE and 300 GD are G-CLASS in every generation. The other
# legacy numerics (SL, SE/SEL/SEC, plain E/D/TD) split across TecDoc families by
# generation and are left for review rather than guessed.
_LEGACY_G_CLASS = re.compile(r"^(?:\d{3}\s*(?:GE|GD)|(?:GE|GD)\s*\d{3})")
_LEGACY_SL = re.compile(r"^(\d{3})\s*SL\b")
_AMBIGUOUS_SL_NUMBERS = frozenset({"300", "190"})

# Registry text says "-CLASS"/"-KLASS(E)" where TecDoc says the bare designator.
_CLASS_WORDS = ("CLASS", "KLASS", "KLASSE")

# Registry model text sometimes repeats the make before the designation.
_MAKE_PREFIXES = ("MERCEDES BENZ", "MERCEDS BENZ", "MERCEDES AMG", "MERCEDES")


def key(value: str) -> str:
    """Mirror ``_normalized_entity`` so generated terms match at runtime."""

    return _NON_WORD.sub(" ", unicodedata.normalize("NFKC", value).upper()).strip()


def matches(term: str, model: str) -> bool:
    """Replicate the runtime term test in ``_normalize_model_family``."""

    term_key, model_key = key(term), key(model)
    return bool(term_key) and (model_key == term_key or model_key.startswith(f"{term_key} "))


def covers(canonical: str, family_name: str) -> bool:
    """Replicate ``_family_contains_reviewed_canonical`` from the alias index."""

    family, canonical_key = key(family_name), key(canonical)
    return family == canonical_key or family.startswith(f"{canonical_key} ")


def fetch_tecdoc_families(
    connection: psycopg.Connection, pattern: str
) -> tuple[tuple[str, ...], dict[str, list[str]]]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT DISTINCT source_key FROM {TECDOC_TABLE}
            WHERE entity_type = 'manufacturer' AND attributes->>'canonical_name' ILIKE %s
            ORDER BY 1
            """,
            (pattern,),
        )
        keys = tuple(row[0] for row in cursor.fetchall())
        cursor.execute(
            f"""
            SELECT DISTINCT attributes->>'canonical_name' FROM {TECDOC_TABLE}
            WHERE entity_type = 'model_family'
              AND attributes->>'manufacturer_source_key' = ANY(%s)
            ORDER BY 1
            """,
            (list(keys),),
        )
        names = [row[0] for row in cursor.fetchall() if row[0]]
    grouped: dict[str, list[str]] = defaultdict(list)
    for name in names:
        grouped[key(name).split(" ")[0]].append(name)
    return keys, dict(grouped)


def fetch_source_models(
    connection: psycopg.Connection, brand_pattern: str
) -> list[tuple[str, str | None, int]]:
    """Every distinct registry model string for the brand, with vehicle counts.

    Staging is read directly rather than through ``normalization_results``: only
    a tenth of the staged dump has been normalized so far, so joining would build
    the rules from a sample and miss model strings the registry really holds.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT staged.raw_record->>'model' AS model,
                   staged.raw_record->>'brand' AS brand,
                   count(*)
            FROM {SOURCE_TABLE} AS staged
            WHERE staged.raw_record->>'brand' ILIKE %s
              AND staged.raw_record->>'model' IS NOT NULL
            GROUP BY 1, 2 ORDER BY 3 DESC, 1
            """,
            (brand_pattern,),
        )
        return [(row[0], row[1], int(row[2])) for row in cursor.fetchall()]


def classify(model: str) -> tuple[str | None, list[str]]:
    """Return the canonical family for one raw source model plus its terms."""

    model_key = key(model)
    for make in _MAKE_PREFIXES:
        if model_key.startswith(f"{make} "):
            model_key = model_key[len(make) + 1 :]
            break
    amg = model_key.startswith("AMG ")
    stem = model_key[4:] if amg else model_key

    for alias, canonical in SOURCE_NAME_ALIASES.items():
        if stem == alias or stem.startswith(f"{alias} "):
            return canonical, [alias]

    if stem.startswith(_CLASSE_PREFIX):
        designator = stem[len(_CLASSE_PREFIX) :].split(" ")[0]
        canonical = SOURCE_PREFIXES.get(designator)
        if canonical is not None:
            return canonical, [f"{_CLASSE_PREFIX}{designator}"]

    legacy_g = _LEGACY_G_CLASS.match(stem)
    if legacy_g is not None:
        return "G-Class", [legacy_g.group(0)]

    legacy_sl = _LEGACY_SL.match(stem)
    if legacy_sl is not None and legacy_sl.group(1) not in _AMBIGUOUS_SL_NUMBERS:
        return "SL", [legacy_sl.group(0)]

    # "E-CLASS" / "V-KLASSE" name the family without a trim designator.
    head = stem.split(" ")
    if len(head) >= 2 and head[1] in _CLASS_WORDS:
        canonical = SOURCE_PREFIXES.get(head[0])
        if canonical is not None:
            return canonical, [f"{head[0]} {head[1]}"]

    if stem in SOURCE_PREFIXES:
        return SOURCE_PREFIXES[stem], [stem]

    match = _DESIGNATOR.match(stem)
    if match is None:
        # "AMG GT S" and "GT R" name a family with a trim word, not a number.
        head = stem.split(" ")[0]
        if len(head) >= 2 and head in SOURCE_PREFIXES:
            return SOURCE_PREFIXES[head], [head]
        return None, []
    prefix, number = match.group("prefix"), match.group("number")
    canonical = SOURCE_PREFIXES.get(prefix)
    if canonical is None:
        return None, []

    terms = [f"{prefix} {number}", f"{prefix}{number}"]
    if amg:
        terms += [f"AMG {prefix} {number}", f"AMG {prefix}{number}"]
    # A glued suffix ("E220CDI") has no token boundary for the matcher to use.
    if not any(matches(term, model_key) for term in terms):
        terms.append(model_key)
    return canonical, terms


@dataclass(frozen=True)
class BrandProfile:
    """One manufacturer's vocabulary and its registry designation grammar.

    ``classify`` is per brand on purpose. Mercedes writes ``E 220 CDI`` -- a
    letter prefix naming the family and a trim number after it -- while BMW
    writes ``320D``, where the leading digit names the series and nothing
    separates it from the trim. One regex covering both would silently
    mis-assign either.
    """

    manufacturer: str
    tecdoc_pattern: str
    brand_pattern: str
    canonical_families: Mapping[str, str] | None = None
    classify: Callable[[str], tuple[str | None, list[str]]] | None = None
    make_tokens: tuple[str, ...] = ()
    default_family: str | None = None


# --- BMW -------------------------------------------------------------------
# TecDoc names BMW families by number alone ("3 (E90)", "X1 (U11)"), so the
# leading token of a registry string is what carries the family.
_BMW_SERIES = {str(digit): f"{digit} Series" for digit in range(1, 9)}
# The electric line is not a separate TecDoc family: TecDoc files each i model
# under the combustion family that shares its chassis code -- i4 is G26 under
# "4 Gran Coupe (G26)", i5 is G60 under "5 (G60, G90, G68)", i7 is G70, iX1 is
# U11 under "X1 (U11)" and iX3 is G08 under "X3 (G01, F97, G08)". Only i3, i8
# and iX stand alone, and iX has no family in this dump at all.
_BMW_ELECTRIC = {
    "I3": "i3",
    "I3S": "i3",
    "I8": "i8",
    "I4": "4 Series",
    "I5": "5 Series",
    "I7": "7 Series",
    "IX1": "X1",
    "IX2": "X2",
    "IX3": "X3",
    "IX": "iX",
}
_BMW_TOKEN = re.compile(r"^[A-Z]*\d+[A-Z]*|^[A-Z]+")


def _classify_bmw(model: str) -> tuple[str | None, list[str]]:
    stem = key(model)
    stem = stem.removeprefix("BMW ")
    # "M 550D" and "M550D" are the same car; join the M to its digits first.
    stem = re.sub(r"^M\s+(?=\d)", "M", stem)
    # "ACTIVEHYBRID 3" names the series in the word after it.
    hybrid = re.fullmatch(r"ACTIVEHYBRID\s+([1-8])", stem)
    if hybrid is not None:
        return _BMW_SERIES[hybrid.group(1)], [stem]
    match = _BMW_TOKEN.match(stem)
    if match is None:
        return None, []
    token = match.group(0)

    # "3ER REIHE" and "3-SERIE" are the German and Swedish renderings.
    series = re.fullmatch(r"([1-8])(?:ER)?(?:REIHE|SERIE|SERIES)?", token)
    if series is not None:
        return _BMW_SERIES[series.group(1)], [token]
    if token in _BMW_ELECTRIC:
        return _BMW_ELECTRIC[token], [token]
    # M135I, M340I and M550I name their series in the leading digit. Bare M2..M8
    # are series trims too, but bare M1 is the E26 supercar, not a 1 Series.
    m_numbered = re.fullmatch(r"M([1-8])\d{2}[A-Z]*", token)
    if m_numbered is not None:
        # The registry writes both "M550D" and "M 550D"; the matcher needs each
        # spelling literally, because neither is a token prefix of the other.
        return _BMW_SERIES[m_numbered.group(1)], [token, f"M {token[1:]}"]
    m_car = re.fullmatch(r"M([2-8])", token)
    if m_car is not None:
        return _BMW_SERIES[m_car.group(1)], [token]
    if re.fullmatch(r"X[1-7]", token):
        return token, [token]
    if token == "XM":
        return "XM", [token]
    if re.fullmatch(r"Z[1348]", token):
        return token, [token]
    # 320D, 218I, 225XE: the leading digit is the series.
    numbered = re.fullmatch(r"([1-8])\d{2}[A-Z]*", token)
    if numbered is not None:
        return _BMW_SERIES[numbered.group(1)], [token]
    return None, []


# --- Porsche ---------------------------------------------------------------
# The registry names the family outright ("911 CARRERA 4S", "MACAN GTS"), so the
# leading family name is the whole rule. 911 is matched before CARRERA: TecDoc
# keeps CARRERA for the 356 Carrera and Carrera GT, not for a 911 trim.
_PORSCHE_FAMILIES = {
    "718": "718",
    "911": "911",
    "912E": "912E",
    "914": "914",
    "918": "918",
    "924": "924",
    "928": "928",
    "944": "944",
    "959": "959",
    "356": "356",
    "BOXSTER": "Boxster",
    "CAYMAN": "Cayman",
    "CAYENNE": "Cayenne",
    "MACAN": "Macan",
    "PANAMERA": "Panamera",
    "TAYCAN": "Taycan",
    "CARRERA": "Carrera",
    "912": "912",
    "BOXTER": "Boxster",  # recurring registry misspelling
}
# TecDoc names every 911 generation by chassis code -- "911 (997)", "911 (991)"
# -- so a registry row typed as the bare code is still a 911.
_PORSCHE_911_CHASSIS = re.compile(r"^(964|991|992|993|996|997)\b")
_PORSCHE_GLUED = re.compile(r"^(356|911|912|914|918|924|928|944|959)([A-Z]{1,2})\b")


def _classify_porsche(model: str) -> tuple[str | None, list[str]]:
    stem = key(model)
    stem = stem.removeprefix("PORSCHE ")
    for alias in sorted(_PORSCHE_FAMILIES, key=len, reverse=True):
        if stem == alias or stem.startswith(f"{alias} "):
            return _PORSCHE_FAMILIES[alias], [alias]
    glued = _PORSCHE_GLUED.match(stem)
    if glued is not None:
        family = _PORSCHE_FAMILIES.get(glued.group(1), glued.group(1))
        return family, [glued.group(0)]
    chassis = _PORSCHE_911_CHASSIS.match(stem)
    if chassis is not None:
        return "911", [chassis.group(1)]
    return None, []


# --- Lexus -----------------------------------------------------------------
# Registry rows read "LEXUS NX300H": the make, then a two- or three-letter
# family and a trim number glued to it.
_LEXUS_FAMILIES = frozenset(
    # CT has no TecDoc family in this dump but the registry holds 1 680 CT200h.
    {
        "CT",
        "ES",
        "GS",
        "GX",
        "HS",
        "IS",
        "LBX",
        "LC",
        "LFA",
        "LM",
        "LS",
        "LX",
        "NX",
        "RC",
        "RX",
        "RZ",
        "SC",
        "TX",
        "UX",
    }
)
_LEXUS_TOKEN = re.compile(r"^([A-Z]{2,3})\s*(\d{2,3}[A-Z]*)?")


def _classify_lexus(model: str) -> tuple[str | None, list[str]]:
    stem = key(model)
    stem = stem.removeprefix("LEXUS ")
    match = _LEXUS_TOKEN.match(stem)
    if match is None or match.group(1) not in _LEXUS_FAMILIES:
        return None, []
    family, trim = match.group(1), match.group(2)
    if trim is None:
        return family, [family]
    # The registry writes both "CT200H" and "CT 200 H".
    return family, [f"{family}{trim}", f"{family} {trim}"]


# --- Audi ------------------------------------------------------------------
# S and RS are engine trims of the numbered family, not families of their own:
# TecDoc files RS 6 under A6 and RS Q8 under Q8.
_AUDI_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"^RS\s*Q([1-8])", "Q{0}"),
    (r"^SQ\s*([1-8])", "Q{0}"),
    (r"^S\s*Q([1-8])", "Q{0}"),
    (r"^RS\s*([1-8])", "A{0}"),
    (r"^([AQ])\s*([1-8])", "{0}{1}"),
    (r"^S\s*([1-8])", "A{0}"),
    (r"^E\s*TRON", "e-tron"),
    (r"^TT", "TT"),
    (r"^R8", "R8"),
    (r"^ALLROAD", "allroad"),
    (r"^(80|90|100|200)\b", "{0}"),
)


def _classify_audi(model: str) -> tuple[str | None, list[str]]:
    stem = key(model)
    stem = stem.removeprefix("AUDI ")
    for pattern, template in _AUDI_PATTERNS:
        match = re.match(pattern, stem)
        if match is None:
            continue
        canonical = template.format(*match.groups()) if match.groups() else template
        return canonical, [match.group(0)]
    return None, []


# --- generic name brands ---------------------------------------------------
# Most manufacturers write the family name itself into the registry ("OCTAVIA",
# "SANDERO", "FIAT TIPO"), so their rules can be derived from TecDoc without a
# hand-written grammar. Only the letter-and-number brands above need one.
_BODY_WORDS = (
    "Closed Off-Road Vehicle",
    "Mixto (Double Cabin)",
    "Municipal Vehicle",
    "Platform/Chassis",
    "Box Body/MPV",
    "Cab with engine",
    "Shooting Brake",
    "Sports Tourer",
    "Familiare/Panorama",
    "All-Terrain",
    "Convertible",
    "Estate Van",
    "Tourer Bus",
    "T-Model",
    "Familiare",
    "Panorama",
    "Hatchback",
    "Saloon",
    "Cabriolet",
    "Cabrio",
    "Estate",
    "Camper",
    "Pickup",
    "Tourer",
    "Coupe",
    "Bus",
    "Van",
    "MPV",
)
_CHASSIS_SUFFIX = re.compile(r"\s*\([^)]*\)")
_TONNAGE_SUFFIX = re.compile(r"\s+\d+(?:[.,]\d+)?\s*-?\s*[tT]\b")


def _family_base(name: str) -> str:
    """Strip TecDoc's chassis codes and bodywork words back to the model name."""

    base = _CHASSIS_SUFFIX.sub("", name).strip()
    base = _TONNAGE_SUFFIX.sub("", base).strip()
    changed = True
    while changed:
        changed = False
        for word in _BODY_WORDS:
            if base.upper().endswith(" " + word.upper()):
                base = base[: -(len(word) + 1)].strip()
                changed = True
    return base


def _display(base: str) -> str:
    """Title-case a family name while leaving codes such as 500X alone."""

    return " ".join(
        word if any(ch.isdigit() for ch in word) else word.capitalize() for word in base.split()
    )


# TecDoc marks generations with a trailing letter or Roman numeral -- AGILA A,
# AGILA B, 100 C1, V70 II. Those must collapse to one family, but Tesla's
# MODEL S and MODEL 3 must not, so a marker is only stripped when a sibling
# family shares the stem under a *different* marker.
_GENERATION_MARKER = re.compile(r"^(?:[A-K]|[IVX]{1,4}|C\d)$")


def _collapse_generations(bases: Sequence[str]) -> dict[str, str]:
    """Map each family base onto the stem shared by its generations."""

    markers: dict[str, set[str]] = defaultdict(set)
    for base in bases:
        parts = base.split()
        if len(parts) >= 2 and _GENERATION_MARKER.fullmatch(parts[-1].upper()):
            markers[" ".join(parts[:-1])].add(parts[-1].upper())
    collapsed: dict[str, str] = {}
    for base in bases:
        parts = base.split()
        stem = " ".join(parts[:-1])
        marker = parts[-1].upper() if len(parts) >= 2 else ""
        # "XC70 II" is unambiguously a generation even with no sibling, but a
        # lone single letter is not: Tesla's MODEL X must stay its own family.
        unambiguous = len(marker) > 1 and _GENERATION_MARKER.fullmatch(marker) is not None
        collapsed[base] = stem if unambiguous or len(markers.get(stem, ())) > 1 else base
    return collapsed


def _name_brand_classifier(
    family_names: Sequence[str],
    make_tokens: tuple[str, ...],
    default_family: str | None = None,
) -> Callable[[str], tuple[str | None, list[str]]]:
    bases = [base for name in family_names if (base := _family_base(name))]
    collapsed = _collapse_generations(bases)
    aliases: dict[str, str] = {}
    for base in bases:
        canonical = _display(collapsed[base])
        aliases.setdefault(key(collapsed[base]), canonical)
        aliases.setdefault(key(base), canonical)
        for token in make_tokens:
            for variant in (collapsed[base], base):
                variant_key = key(variant)
                if variant_key.startswith(f"{token} "):
                    aliases.setdefault(variant_key[len(token) + 1 :], canonical)
    ordered = sorted(aliases, key=len, reverse=True)

    def classify(model: str) -> tuple[str | None, list[str]]:
        full = key(model)
        stripped = full
        for token in make_tokens:
            if full.startswith(f"{token} "):
                stripped = full[len(token) + 1 :]
                break
        # MINI's families are literally named "MINI ...", so the make-stripped
        # stem is tried first but the full string still has to be matchable.
        for candidate in (stripped, full):
            for alias in ordered:
                if candidate == alias or candidate.startswith(f"{alias} "):
                    return aliases[alias], [alias]
        if default_family is not None:
            return default_family, [stripped.split(" ")[0]]
        return None, []

    return classify


BRANDS: dict[str, BrandProfile] = {
    "mercedes-benz": BrandProfile(
        manufacturer="Mercedes-Benz",
        brand_pattern="%MERCEDES%",
        tecdoc_pattern="MERCEDES-BENZ%",
        canonical_families=CANONICAL_FAMILIES,
        classify=classify,
    ),
    "bmw": BrandProfile(
        manufacturer="BMW",
        brand_pattern="%BMW%",
        tecdoc_pattern="BMW%",
        canonical_families={
            **{str(digit): f"{digit} Series" for digit in range(1, 9)},
            **{f"X{digit}": f"X{digit}" for digit in range(1, 8)},
            "XM": "XM",
            "Z1": "Z1",
            "Z3": "Z3",
            "Z4": "Z4",
            "Z8": "Z8",
            "I3": "i3",
            "I8": "i8",
        },
        classify=_classify_bmw,
    ),
    "audi": BrandProfile(
        manufacturer="Audi",
        brand_pattern="%AUDI%",
        tecdoc_pattern="AUDI%",
        canonical_families={
            **{f"A{d}": f"A{d}" for d in range(1, 9)},
            **{f"Q{d}": f"Q{d}" for d in range(1, 9)},
            "TT": "TT",
            "R8": "R8",
            "E": "e-tron",
            "ALLROAD": "allroad",
            "80": "80",
            "90": "90",
            "100": "100",
            "200": "200",
        },
        classify=_classify_audi,
    ),
    "porsche": BrandProfile(
        manufacturer="Porsche",
        brand_pattern="%PORSCHE%",
        tecdoc_pattern="PORSCHE%",
        canonical_families={k: v for k, v in _PORSCHE_FAMILIES.items()},
        classify=_classify_porsche,
    ),
    **{
        slug: BrandProfile(
            manufacturer=manufacturer,
            brand_pattern=brand_pattern,
            tecdoc_pattern=tecdoc_pattern,
            make_tokens=make_tokens,
            default_family=default_family,
        )
        for slug, manufacturer, brand_pattern, tecdoc_pattern, make_tokens, default_family in (
            ("volvo", "Volvo", "%VOLVO%", "VOLVO%", ("VOLVO",), None),
            ("volkswagen", "Volkswagen", "%VOLKSWAGEN%", "VW%", ("VOLKSWAGEN", "VW"), None),
            ("toyota", "Toyota", "%TOYOTA%", "TOYOTA%", ("TOYOTA",), None),
            ("kia", "Kia", "%KIA%", "KIA%", ("KIA",), None),
            ("ford", "Ford", "%FORD%", "FORD%", ("FORD",), None),
            ("skoda", "Škoda", "%SKODA%", "SKODA%", ("SKODA",), None),
            ("renault", "Renault", "%RENAULT%", "RENAULT%", ("RENAULT",), None),
            ("hyundai", "Hyundai", "%HYUNDAI%", "HYUNDAI%", ("HYUNDAI",), None),
            ("peugeot", "Peugeot", "%PEUGEOT%", "PEUGEOT%", ("PEUGEOT",), None),
            ("nissan", "Nissan", "%NISSAN%", "NISSAN%", ("NISSAN",), None),
            ("opel", "Opel", "%OPEL%", "OPEL%", ("OPEL",), None),
            # Abarth is Fiat's performance marque: "ABARTH 500" is a 500.
            ("fiat", "Fiat", "%FIAT%", "FIAT%", ("FIAT", "ABARTH"), None),
            ("mazda", "Mazda", "%MAZDA%", "MAZDA%", ("MAZDA",), None),
            ("seat", "SEAT", "%SEAT%", "SEAT%", ("SEAT",), None),
            ("citroen", "Citroën", "%CITRO%", "CITRO%", ("CITROEN", "CITROËN"), None),
            ("suzuki", "Suzuki", "%SUZUKI%", "SUZUKI%", ("SUZUKI",), None),
            ("mini", "MINI", "%MINI%", "MINI%", ("MINI",), "MINI"),
            ("mg", "MG", "%MG%", "MG%", ("MG",), None),
            ("chevrolet", "Chevrolet", "%CHEVROLET%", "CHEVROLET%", ("CHEVROLET",), None),
            ("subaru", "Subaru", "%SUBARU%", "SUBARU%", ("SUBARU",), None),
            ("mitsubishi", "Mitsubishi", "%MITSUBISHI%", "MITSUBISHI%", ("MITSUBISHI",), None),
            ("dacia", "Dacia", "%DACIA%", "DACIA%", ("DACIA",), None),
            ("honda", "Honda", "%HONDA%", "HONDA%", ("HONDA",), None),
            ("tesla", "Tesla", "%TESLA%", "TESLA%", ("TESLA",), None),
            ("land-rover", "Land Rover", "%LAND%ROVER%", "LAND ROVER%", ("LAND ROVER", "LANDROVER"), None),
            ("jeep", "Jeep", "%JEEP%", "JEEP%", ("JEEP",), None),
            ("jaguar", "Jaguar", "%JAGUAR%", "JAGUAR%", ("JAGUAR",), None),
            ("lynk", "Lynk & Co", "%LYNK%", "LYNK%", ("LYNK CO", "LYNK"), None),
            ("byd", "BYD", "%BYD%", "BYD%", ("BYD",), None),
            ("cupra", "Cupra", "%CUPRA%", "CUPRA%", ("CUPRA",), None),
            ("alfa-romeo", "Alfa Romeo", "%ALFA%", "ALFA ROMEO%", ("ALFA ROMEO", "ALFA"), None),
            ("dodge", "Dodge", "%DODGE%", "DODGE%", ("DODGE",), None),
            ("chrysler", "Chrysler", "%CHRYSLER%", "CHRYSLER%", ("CHRYSLER",), None),
            ("cadillac", "Cadillac", "%CADILLAC%", "CADILLAC%", ("CADILLAC",), None),
            ("lancia", "Lancia", "%LANCIA%", "LANCIA%", ("LANCIA",), None),
            ("maserati", "Maserati", "%MASERATI%", "MASERATI%", ("MASERATI",), None),
            ("ds", "DS", "%DS%", "DS%", ("DS",), None),
            ("pontiac", "Pontiac", "%PONTIAC%", "PONTIAC%", ("PONTIAC",), None),
            ("buick", "Buick", "%BUICK%", "BUICK%", ("BUICK",), None),
            ("ferrari", "Ferrari", "%FERRARI%", "FERRARI%", ("FERRARI",), None),
            ("smart", "Smart", "%SMART%", "SMART%", ("SMART",), None),
            ("saab", "Saab", "%SAAB%", "SAAB%", ("SAAB",), None),
            ("iveco", "Iveco", "%IVECO%", "IVECO%", ("IVECO",), None),
        )
    },
    "lexus": BrandProfile(
        manufacturer="Lexus",
        brand_pattern="%LEXUS%",
        tecdoc_pattern="LEXUS%",
        canonical_families={f: f for f in _LEXUS_FAMILIES},
        classify=_classify_lexus,
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brand", default="mercedes-benz", choices=sorted(BRANDS))
    parser.add_argument("--first-rule-id", type=int, default=203)
    parser.add_argument(
        "--include-unused-families",
        action="store_true",
        help=(
            "Also emit a bare-name rule for TecDoc families the registry never "
            'names. Off by default: a bare numeric term such as "300" would '
            'capture legacy strings like "300 SEL" that belong to S-Class.'
        ),
    )
    parser.add_argument("--json-out")
    arguments = parser.parse_args()
    profile = BRANDS[arguments.brand]
    manufacturer = profile.manufacturer

    with psycopg.connect(str(get_ingestion_settings().database_url), connect_timeout=10) as conn:
        tecdoc_keys, families = fetch_tecdoc_families(conn, profile.tecdoc_pattern)
        source_models = fetch_source_models(conn, profile.brand_pattern)

    family_names = [name for names in families.values() for name in names]
    classify_model = profile.classify or _name_brand_classifier(
        family_names,
        profile.make_tokens or (key(profile.manufacturer),),
        profile.default_family,
    )
    canonical_for_group = {
        group: profile.canonical_families[group]
        for group in families
        if group in (profile.canonical_families or {})
    }
    unmapped_groups = sorted(set(families) - set(canonical_for_group))

    terms_by_family: dict[str, set[str]] = defaultdict(set)
    rows_by_family: dict[str, int] = defaultdict(int)
    unclassified: list[tuple[str, int]] = []
    for model, brand, rows in source_models:
        brand_key = key(brand) if brand else ""
        converter = any(token in brand_key.split(" ") for token in _CONVERTER_BRANDS)
        canonical, terms = (None, []) if converter else classify_model(model)
        if canonical is None or key(model) in _EXCLUDED_MODEL_KEYS:
            unclassified.append((model, rows))
            continue
        terms_by_family[canonical].update(
            term for term in terms if term not in _EXCLUDED_MODEL_KEYS
        )
        rows_by_family[canonical] += rows

    # A bare designator lets "CLK CABRIO 63 AMG" and "GLS 5004MATIC" match without
    # enumerating every trim. Only multi-character prefixes are safe: a bare "C"
    # would capture "C-TOURER T 143 LE", which is a motorhome.
    for prefix, canonical in (
        SOURCE_PREFIXES if profile.manufacturer == "Mercedes-Benz" else {}
    ).items():
        if len(prefix) >= 2 and canonical in terms_by_family:
            terms_by_family[canonical].add(prefix)

    if arguments.include_unused_families:
        for canonical in canonical_for_group.values():
            terms_by_family.setdefault(canonical, set()).add(key(canonical))

    # Classification is an intent; the runtime matcher only sees the emitted
    # terms. Replay its longest-term rule so the reported number is what the
    # pipeline will actually resolve, not what the generator hoped for.
    term_index = [
        (key(term), canonical) for canonical, terms in terms_by_family.items() for term in terms
    ]
    verified_rows = 0
    unrealized: list[tuple[str, int]] = []
    for model, brand, rows in source_models:
        model_key = key(model)
        make = key(manufacturer)
        if model_key.startswith(f"{make} "):
            model_key = model_key[len(make) + 1 :]
        best = max(
            (
                (len(term_key), canonical)
                for term_key, canonical in term_index
                if model_key == term_key or model_key.startswith(f"{term_key} ")
            ),
            default=None,
        )
        if best is None:
            if key(model) not in {key(m) for m, _ in unclassified}:
                unrealized.append((model, rows))
        else:
            verified_rows += rows

    report: dict[str, Any] = {
        "manufacturer": manufacturer,
        "runtime_verified_rows": verified_rows,
        "classified_not_matchable": [{"model": m, "rows": r} for m, r in unrealized],
        "tecdoc_manufacturer_keys": list(tecdoc_keys),
        "tecdoc_family_names": sum(len(names) for names in families.values()),
        "canonical_families": len(set(canonical_for_group.values())),
        "unmapped_tecdoc_groups": unmapped_groups,
        "source_models_total": len({model for model, _, _ in source_models}),
        "source_rows_total": sum(rows for _, _, rows in source_models),
        "source_rows_classified": sum(rows_by_family.values()),
        "unclassified": [{"model": m, "rows": r} for m, r in unclassified],
        "families": {
            canonical: {
                "terms": sorted(terms_by_family[canonical]),
                "rows": rows_by_family.get(canonical, 0),
                "tecdoc_names": sorted(
                    name for names in families.values() for name in names if covers(canonical, name)
                ),
            }
            for canonical in sorted(terms_by_family)
        },
    }

    print(
        f"{manufacturer}: {report['tecdoc_family_names']} TecDoc family names -> "
        f"{report['canonical_families']} canonical families; "
        f"{verified_rows}/{report['source_rows_total']} source rows runtime-verified "
        f"({len({m for m, _ in unclassified})} of {report['source_models_total']} "
        f"distinct models unclassified)"
    )
    if unmapped_groups:
        print(f"  TecDoc groups with no canonical mapping: {', '.join(unmapped_groups)}")

    if unclassified:
        uncovered_rows = sum(rows for _, rows in unclassified)
        print(f"\n# --- {len(unclassified)} TS values still uncovered ({uncovered_rows} rows) ---")
        for model, rows in sorted(unclassified, key=lambda item: (-item[1], item[0])):
            print(f"    {rows:>6}  {model}")

    print("\n# --- generated rule specs ---")
    rule_number = arguments.first_rule_id
    for canonical, detail in sorted(report["families"].items()):
        terms = ", ".join(f'"{term}"' for term in detail["terms"])
        print(
            f'    ("MOD-{rule_number}", "{manufacturer}", ({terms},), "{canonical}"),'
            f"  # {detail['rows']} rows, {len(detail['tecdoc_names'])} TecDoc families"
        )
        rule_number += 1

    if arguments.json_out:
        with open(arguments.json_out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(f"\nreport -> {arguments.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
