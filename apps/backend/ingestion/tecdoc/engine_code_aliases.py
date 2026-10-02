"""Reviewed, manufacturer-scoped engine-code spellings, families and equivalences.

The registry and TecDoc often write one engine differently. Three kinds of
reviewed knowledge live here, each scoped to the maker it was reviewed for, so a
BMW rule never reads a Volvo code:

- spellings (`engine_code_spellings`): the same code written another way
  (BMW `M52-TUB20` for TecDoc's `M52 B20`). Exact evidence.
- families (`engine_variant_family`, `type_name_shares_family`): which family
  a variant belongs to (Subaru `EJ253` -> EJ25). Family evidence only: it
  never says which variant the car has.
- equivalences (`engine_code_aliases`): two naming systems for one engine
  (Opel `A14XER`, GM `LDD`). Each entry lists every catalog name of that engine;
  `alias_completeness_gaps` checks that against a catalog.
- shared registry names (`engines_sharing_registry_name`): one registry code
  the maker used for two engines (Jaguar / Land Rover `204PT`). Family evidence
  on every KType of either engine.

Nothing here is derived automatically from the catalog. Bridging every TecDoc
`code (alternative)` pair was tested and rejected: it made sibling type codes
of one family the same engine (PSA `9HR` and `9HD` through `DV6C`).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

_NON_ALPHANUMERIC = re.compile(r"[^A-Z0-9]+")


@lru_cache(maxsize=4096)
def maker_key(manufacturer: str) -> str:
    """Accent-free compact maker name: `Citroën` -> CITROEN, `MERCEDES-BENZ` -> MERCEDESBENZ."""

    folded = unicodedata.normalize("NFKD", manufacturer)
    plain = "".join(char for char in folded if not unicodedata.combining(char))
    return _NON_ALPHANUMERIC.sub("", plain.upper())


def _compact(value: str) -> str:
    return _NON_ALPHANUMERIC.sub("", value.upper())


# --- Spellings: the same code written another way (exact evidence) -------------

# BMW's technical-update marker sits between the family and the displacement
# token in the registry (`M52-TUB20`, `M47-TU2D20`); TecDoc writes the engine
# without it (`M52 B20`). A trailing marker (`N63-B44TU3`) is another notation
# and is left alone.
_BMW_TECHNICAL_UPDATE = re.compile(r"^([MNS]\d{2})[\s-]*TU\d?[\s-]*([BD]\d{2})(.*)$")
# Saab's registry codes carry a one-letter suffix after a slash (`B205E/B`).
# Only that shape: a slash is part of other makers' codes (Citroën `M25/659`).
_SAAB_SLASH_LETTER = re.compile(r"^([A-Z0-9]{3,})/[A-Z]$")
# A Mercedes engine is its six-digit number; the letters before it vary by
# source (`M139.580` against `139.580`, `E780.998` against `EM 780.998`). The
# three-digit engine number never repeats across the prefixes, so the number
# alone names the engine. No `OM 642` family is derived from it: 642.955 and
# 642.950 are different engines.
_MERCEDES_NUMBER = re.compile(r"^(?:OM|EM|M|E)?\s*(\d{3})\.(\d{3})$")


def bmw_technical_update(code: str) -> str | None:
    """`M52-TUB20` -> `M52 B20`: the code without BMW's technical-update marker."""

    marked = _BMW_TECHNICAL_UPDATE.match(code.upper().strip())
    return f"{marked.group(1)} {marked.group(2)}{marked.group(3)}" if marked else None


@lru_cache(maxsize=250_000)
def engine_code_spellings(maker: str, code: str) -> frozenset[str]:
    """Other spellings of one code that name the same engine, for `maker_key` makers.

    `code` is one code without TecDoc's bracket alternative. Empty for every
    maker and shape without a reviewed spelling rule.
    """

    text = code.upper().strip()
    if maker == "BMW" and (unmarked := bmw_technical_update(text)):
        return frozenset({unmarked})
    if maker == "SAAB" and (suffixed := _SAAB_SLASH_LETTER.match(text)):
        return frozenset({suffixed.group(1)})
    if maker == "MERCEDESBENZ" and (number := _MERCEDES_NUMBER.match(text)):
        return frozenset({number.group(1) + number.group(2)})
    return frozenset()


# --- Families: a variant and the family it belongs to (family evidence) --------

# Each pattern keeps the family in group 1. A family is compatible only with a
# code that names the family alone, so two variants (`EJ251`, `EJ253`) still
# contradict each other.
_VARIANT_FAMILIES: dict[str, tuple[re.Pattern[str], ...]] = {
    # Subaru counts variants with a digit: EJ251, EJ253, EJ204.
    "SUBARU": (re.compile(r"^((?:EJ|EZ|EG|FA|FB)\d{2})\d$"),),
    # Nissan appends the head and induction letters: HR13DDT, HR16DE, QG18DE, YD22DDTI.
    "NISSAN": (
        re.compile(r"^([A-Z]{2}\d{2})(?:DE|DET|DETT|DDT|DDTT|DDTI|DT|DTI|TI|ETI|T)$"),
    ),
    # Honda counts revisions with a digit after the family letter: L15B3, B20B3, K20A2.
    "HONDA": (re.compile(r"^([A-Z]\d{2}[A-Z])\d$"),),
}

# Mazda: the registry gives the engine type name (`SH-VPTS`), TecDoc its variant
# code (`SHY1`, `SHY4`); both start with the two-character engine family. The
# registry writes `SH-VPTS` for the 110 kW and the 129 kW engine alike, so the
# type name says no more than the family. The catalog lists the two forms
# together (`PE-VPS, PEY6, PEY7`).
_MAZDA_TYPE_NAME = re.compile(r"^([A-Z0-9]{2})-[A-Z]+$")
_MAZDA_VARIANT = re.compile(r"^([A-Z0-9]{2})Y\d$")


@lru_cache(maxsize=250_000)
def engine_variant_family(maker: str, compact_code: str) -> str | None:
    """The family a compact variant code belongs to, where the maker's codes were reviewed.

    Subaru `EJ253` -> EJ25, Nissan `HR13DDT` -> HR13, Honda `L15B3` -> L15B.
    """

    for pattern in _VARIANT_FAMILIES.get(maker, ()):
        if variant := pattern.match(compact_code):
            return variant.group(1)
    return None


@lru_cache(maxsize=250_000)
def type_name_shares_family(
    maker: str, registry_code: str, compact_catalog_codes: frozenset[str]
) -> bool:
    """True when a registry type name and one of the catalog's variant codes share their family.

    Mazda `SH-VPTS` and `SHY1` do; `SH-VPTS` and `PYY1`, or two type names, do not.
    """

    if maker != "MAZDA":
        return False
    type_name = _MAZDA_TYPE_NAME.match(registry_code.upper().strip())
    if type_name is None:
        return False
    return any(
        (variant := _MAZDA_VARIANT.match(code)) is not None
        and variant.group(1) == type_name.group(1)
        for code in compact_catalog_codes
    )


# --- Equivalences: two naming systems for one engine ----------------------------


@dataclass(frozen=True)
class EngineCodeAlias:
    """One engine and every name the registry and the catalog give it.

    `names` must list every catalog name of the engine for these makers. An
    entry that knows only one of two catalog names prefers the KType that
    happens to use it: the Captiva's 110 kW diesel is `Z 20 S` on one KType and
    `LLW` on its sibling.
    """

    makers: frozenset[str]
    names: frozenset[str]
    evidence: str


def _alias(makers: tuple[str, ...], names: tuple[str, ...], evidence: str) -> EngineCodeAlias:
    return EngineCodeAlias(
        frozenset(maker_key(maker) for maker in makers),
        frozenset(_compact(name) for name in names),
        evidence,
    )


_PSA_MAKERS = ("Citroën", "DS", "Opel", "Peugeot", "Toyota", "Vauxhall")

REVIEWED_ENGINE_CODE_ALIASES: tuple[EngineCodeAlias, ...] = (
    # Chevrolet: the registry gives the Opel/Daewoo name, TecDoc mostly the GM
    # RPO code. Evidence is the catalog's own co-listing where it exists;
    # otherwise the Opel KTypes carrying the Opel name have the displacement and
    # power of the Chevrolet KTypes carrying the RPO code, and no other code
    # shares them. `B12D1 = LMU` is left out on purpose: TecDoc lists the Spark
    # twice (`B12D1, LMU` and `LMU`), so the pair turns resolved cars into ties.
    _alias(("Chevrolet",), ("A14XER", "LDD"), "co-listed 'A 14 XER, LDD' (Aveo T300, 1398 cc, 74 kW)"),
    _alias(("Chevrolet",), ("A12XER", "LDC"), "1229 cc, 63 kW: Opel 'A 12 XER', Chevrolet 'LDC'"),
    _alias(("Chevrolet",), ("A13DTE", "LSF"), "1248 cc, 70 kW: Opel 'A 13 DTE', Chevrolet 'LSF'"),
    _alias(("Chevrolet",), ("A16XER", "F16D4", "LDE"), "co-listed 'F16D4, LDE'; 1598 cc, Opel 'A 16 XER'"),
    _alias(("Chevrolet",), ("A17DTF", "LUD"), "1686 cc, 96 kW: Opel 'A 17 DTF', Chevrolet 'LUD'"),
    _alias(
        ("Chevrolet",), ("A22DMH", "LNQ"),
        "2231 cc: Opel 'A 22 DMH' (135 kW); 'LNQ' is the only code on all four Captiva 2.2 D "
        "KTypes (120 and 135 kW), so the alias never ranks them: power and drive do",
    ),
    _alias(("Chevrolet",), ("A14NET", "LUJ"), "co-listed 'A 14 NET, LUJ' (Cruze, Aveo, 1364 cc, 103 kW)"),
    # `LUW` and `LWE` are left out on purpose. They sit on the other Chevrolet
    # 1796 cc KTypes (Cruze, Aveo: 101 and 103 kW, petrol and flex-fuel), but
    # TecDoc never co-lists them with `2H0`, `F18D4` or `A 18 XER`, so nothing in
    # the catalog says they are this engine. The Cruze (J300) has both a 104 kW
    # `2H0` KType and 101/103 kW `LUW, LWE` KTypes: an `A18XER` car goes to the
    # `2H0` one and contradicts the others.
    _alias(
        ("Chevrolet",), ("A18XER", "F18D4", "2H0"),
        "co-listed '2H0, F18D4' (Orlando); 1796 cc, Opel 'A 18 XER'; 'LUW' / 'LWE' never co-listed",
    ),
    _alias(
        ("Chevrolet",), ("Z20DMH", "LLW", "Z20S"),
        "co-listed 'LLW, Z 20 DMH' (Cruze); the Captiva 110 kW KTypes carry 'LLW' or 'Z 20 S'",
    ),
    # PSA: the registry gives the type code. Other brands' KTypes name the same
    # engine by its family code or their own type code; TecDoc pairs them in
    # brackets ('HNS (EB2ADTS)', 'F 12 XHT (EB2ADTS)', 'D 12 XHT (EB2ADTS)',
    # '8FP (EP3C)'). A name matches only a catalog code that is that name: `8FP`
    # reaches a KType listing a bare 'EP3C', never '8FR (EP3C)', whose own type
    # code is a sibling. `ZLC = ZKZ` is left out: `ZLC` is the bracket name of
    # four type codes (ZKM, ZKT, ZKV, ZKZ), so no entry can list its names.
    _alias(_PSA_MAKERS, ("HNS", "EB2ADTS", "F12XHT", "D12XHT"), "TecDoc bracket pairs with (EB2ADTS)"),
    _alias(_PSA_MAKERS, ("8FP", "EP3C"), "TecDoc bracket pair '8FP (EP3C)'"),
    # BMW: the registry writes the E39 M5 / Z8 engine `S62-B49`; `S62 B50` is the
    # only S62 in the catalog.
    _alias(("BMW",), ("S62B49", "S62B50"), "'S62 B50 (508S1)' is the catalog's only S62"),
    # MINI: the registry's `B38-A15M` is the 100 kW three-cylinder TecDoc lists
    # as 'B38 A15 A' on MINI KTypes. Every front-wheel-drive Cooper KType
    # co-lists 'B36 A15 A, B38 A15 A', and the Clubman (F54) Cooper ALL4 lists
    # 'B36 A15 A' alone: without that name the entry preferred the front-wheel
    # -drive Clubman and contradicted its ALL4 sibling. MINI only: BMW's catalog
    # has its own 'B38 A15 M'.
    _alias(
        ("MINI",), ("B38A15M", "B38A15A", "B36A15A"),
        "MINI 100 kW 1499 cc KTypes carry 'B38 A15 A', 'B36 A15 A' or both",
    ),
)

# One registry code the maker used for two engines. Jaguar and Land Rover
# register `204PT` for the Ford-derived 1,999 cc petrol (TecDoc '204PT (GTDI)',
# 177 kW, to 2016) and for the Ingenium 1,997 cc petrol that replaced it (TecDoc
# 'PT204 (AJ20P4)', registered 147 and 184 kW from 2017). The code alone
# cannot say which, so it is family evidence on the KTypes of both and the
# exact engine on neither; displacement, power and build date decide. The
# registry's `PT204` names the Ingenium only and is not in this table.
REVIEWED_SHARED_REGISTRY_NAMES: tuple[EngineCodeAlias, ...] = (
    _alias(
        ("Jaguar", "Land Rover"), ("204PT", "PT204"),
        "registry '204PT' on 1999 cc 177 kW cars (2012-2016) and on 1997 cc 147 / 184 kW cars "
        "(2017-2019); every 1999 cc petrol KType is '204PT (GTDI)', every 1997 cc one 'PT204 (AJ20P4)'",
    ),
)
#: The registry name of each `REVIEWED_SHARED_REGISTRY_NAMES` entry, in order.
_SHARED_REGISTRY_NAME = ("204PT",)

_ALIASES_BY_MAKER_AND_NAME: dict[tuple[str, str], frozenset[str]] = {}
for _entry in REVIEWED_ENGINE_CODE_ALIASES:
    for _maker in _entry.makers:
        for _name in _entry.names:
            _key = (_maker, _name)
            _ALIASES_BY_MAKER_AND_NAME[_key] = (
                _ALIASES_BY_MAKER_AND_NAME.get(_key, frozenset()) | (_entry.names - {_name})
            )


def engine_code_aliases(maker: str, compact_name: str) -> frozenset[str]:
    """Every other compact name of the engine `compact_name` names, for a `maker_key` maker."""

    return _ALIASES_BY_MAKER_AND_NAME.get((maker, compact_name), frozenset())


_ENGINES_BY_MAKER_AND_REGISTRY_NAME: dict[tuple[str, str], frozenset[str]] = {
    (_maker, _registry_name): _entry.names
    for _entry, _registry_name in zip(
        REVIEWED_SHARED_REGISTRY_NAMES, _SHARED_REGISTRY_NAME, strict=True
    )
    for _maker in _entry.makers
}


def engines_sharing_registry_name(maker: str, compact_name: str) -> frozenset[str]:
    """The catalog names of every engine a `maker_key` maker registers as `compact_name`.

    Jaguar `204PT` -> {204PT, PT204}: two engines, so the code is family
    evidence on a KType carrying either name. Empty for an unambiguous name.
    """

    return _ENGINES_BY_MAKER_AND_REGISTRY_NAME.get((maker, compact_name), frozenset())


# --- Completeness of the reviewed names against a catalog -----------------------

_BRACKET = re.compile(r"\([^()]*\)")


class _CatalogKType(Protocol):
    """What the completeness check reads of a KType (`fuzzy_matching.VehicleCandidate`)."""

    @property
    def candidate_reference(self) -> str: ...
    @property
    def manufacturer(self) -> str: ...
    @property
    def model(self) -> str: ...
    @property
    def displacement_cc(self) -> int | None: ...
    @property
    def power_kw(self) -> int | None: ...
    @property
    def fuels(self) -> frozenset[str]: ...
    @property
    def engine_codes(self) -> frozenset[str]: ...


@dataclass(frozen=True)
class AliasGap:
    """A KType that is the twin of one carrying a reviewed name, but carries none itself."""

    entry: EngineCodeAlias
    candidate_reference: str
    manufacturer: str
    model: str
    displacement_cc: int | None
    power_kw: int | None
    fuels: frozenset[str]
    #: The names this KType does carry: the candidates for the entry's missing name.
    names: frozenset[str]


def _catalog_names(ktype: _CatalogKType) -> frozenset[str]:
    """A KType's own compact engine names, without TecDoc's bracket alternative."""

    return frozenset(
        name for code in ktype.engine_codes if (name := _compact(_BRACKET.sub(" ", code)))
    )


def alias_completeness_gaps(
    catalog: Iterable[_CatalogKType],
    entries: Iterable[EngineCodeAlias] | None = None,
) -> tuple[AliasGap, ...]:
    """KTypes showing that a reviewed entry does not list every catalog name of its engine.

    For each entry and maker: a KType with the model, displacement, power and
    fuel of a KType carrying one of the entry's names must carry one too. A
    twin with engine codes but none of the names is the same engine under a
    name the entry misses, and the entry then prefers its sibling (MINI
    `B38 A15 A` beside the Clubman ALL4's `B36 A15 A`). A twin without engine
    codes is no gap. `entries` defaults to both reviewed tables.
    """

    if entries is None:
        entries = (*REVIEWED_ENGINE_CODE_ALIASES, *REVIEWED_SHARED_REGISTRY_NAMES)
    by_maker: dict[str, list[tuple[_CatalogKType, frozenset[str]]]] = {}
    for ktype in catalog:
        if names := _catalog_names(ktype):
            by_maker.setdefault(maker_key(ktype.manufacturer), []).append((ktype, names))
    gaps: list[AliasGap] = []
    for entry in entries:
        for maker in sorted(entry.makers):
            members = by_maker.get(maker, ())
            carriers = {
                (ktype.model, ktype.displacement_cc, ktype.power_kw, ktype.fuels)
                for ktype, names in members
                if names & entry.names
            }
            gaps.extend(
                AliasGap(
                    entry, ktype.candidate_reference, ktype.manufacturer, ktype.model,
                    ktype.displacement_cc, ktype.power_kw, ktype.fuels, names,
                )
                for ktype, names in members
                if not names & entry.names
                and (ktype.model, ktype.displacement_cc, ktype.power_kw, ktype.fuels) in carriers
            )
    return tuple(gaps)
