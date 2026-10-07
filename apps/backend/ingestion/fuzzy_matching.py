"""Deterministic, manufacturer-scoped fuzzy candidate generation."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import pairwise
from typing import Any, Literal

from ingestion.context_comparison import ContextComparison, ContextComparisonPolicy
from ingestion.phonetic_matching import PHONETIC_VERSION, has_phonetic_overlap
from ingestion.tecdoc.engine_code_aliases import (
    bmw_technical_update,
    engine_code_aliases,
    engine_code_spellings,
    engine_variant_family,
    engines_sharing_registry_name,
    maker_key,
    registry_motor_family,
    type_name_shares_family,
)
from ingestion.tecdoc.power_equivalences import reviewed_power_equivalent

MatchScope = Literal[
    "exact_manufacturer",
    "fuzzy_manufacturer",
    "phonetic_manufacturer",
    "global",
]

_NON_ALPHANUMERIC = re.compile(r"[^A-Z0-9ÅÄÖÉÜ]+")
_WHITESPACE = re.compile(r"\s+")
_DIGIT_GROUP = re.compile(r"\d+")


@lru_cache(maxsize=250_000)
def _normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).upper()
    normalized = _NON_ALPHANUMERIC.sub(" ", normalized)
    return _WHITESPACE.sub(" ", normalized).strip()


@lru_cache(maxsize=250_000)
def _normalized_code(value: str) -> str:
    return _NON_ALPHANUMERIC.sub("", unicodedata.normalize("NFKC", value).upper())


_ENGINE_BRACKET = re.compile(r"\(([^()]*)\)")
_ENGINE_HEAD_SPLIT = re.compile(r"[\s\-]+")
# A compact code whose last character is one revision letter after a digit.
_ENGINE_REVISION_SUFFIX = re.compile(r"^([A-Z0-9]*[0-9])[A-Z]$")


@lru_cache(maxsize=250_000)
def engine_code_forms(value: str, manufacturer: str = "") -> frozenset[str]:
    """Forms under which one engine code is the same engine, compared compactly.

    TecDoc writes a manufacturer code with its alternative in brackets
    (`BHZ (DV6FC)`); either part, or the whole, names that engine.

    With a `manufacturer`, that maker's reviewed spellings are forms too
    (`engine_code_aliases.engine_code_spellings`): BMW `M52-TUB20` is also
    M52B20, Saab `B205E/B` also B205E, Mercedes `M139.580` also 139580. A value
    listing several codes (`A, B`) is no single code and gets none; read its
    codes one by one with `engine_code_parts`.
    """

    text = unicodedata.normalize("NFKC", value).upper()
    head = re.sub(r"\([^()]*\)", " ", text)
    parts = {text, head, *_ENGINE_BRACKET.findall(text)}
    if manufacturer and "," not in text:
        parts |= engine_code_spellings(maker_key(manufacturer), head.strip())
    return frozenset(form for part in parts if (form := _normalized_code(part)))


@lru_cache(maxsize=250_000)
def engine_code_parts(value: str) -> tuple[str, ...]:
    """The codes one registry value lists, de-duplicated in order: `L2S, L2S` -> (L2S,).

    The registry writes a car's motors, or the alternative engines its type
    approval covers, in one value (`HF1002N0, HD1001N0`, `H5H-470, H5H-480`).
    Only a comma separates codes: a slash is part of one (Citroën `A06/635`).
    """

    parts: dict[str, str] = {}
    for part in value.split(","):
        if compact := _normalized_code(part):
            parts.setdefault(compact, part.strip())
    return tuple(parts.values())


@lru_cache(maxsize=250_000)
def engine_code_family(value: str, *, revision: bool = True) -> str | None:
    """The engine family a code's first token names: `K9K 276` and `K9K-H` -> K9K.

    A code written as one token names its family by dropping a single trailing
    revision letter after a digit: `FB20C` and `B47D20A` -> FB20, B47D20. A code
    ending in a digit (`B5254T6`), in several letters (`Z16XER`) or in letters
    only (`CFGB`) has none.

    A family is compatible only with a code that names the family alone, never
    with another variant of it.

    `revision=False` keeps only the first-token family. Whether the catalog
    knows an engine is decided on that alone, so a revision no KType carries
    (`B5252S` beside a catalog `B 5252`) stays unverified rather than a conflict.

    None when the code has no family, or the family is too short to name an
    engine on its own (`M 177.980`, `OM 651`, `20E`).
    """

    text = re.sub(r"\([^()]*\)", " ", unicodedata.normalize("NFKC", value).upper()).strip()
    tokens = [token for token in _ENGINE_HEAD_SPLIT.split(text) if token]
    if (
        revision
        and len(tokens) == 1
        and (suffixed := _ENGINE_REVISION_SUFFIX.match(_normalized_code(text)))
    ):
        head = suffixed.group(1)
    elif len(tokens) >= 2:
        head = _normalized_code(tokens[0])
    else:
        return None
    return head if len(head) >= 3 and not head.isdigit() else None


@lru_cache(maxsize=250_000)
def engine_code_families(value: str, manufacturer: str = "") -> frozenset[str]:
    """Every family one code is a variant of: family evidence, never the exact engine.

    Beside `engine_code_family`:

    - a code written in several tokens that ends in one revision letter after a
      digit names its family without the letter, as a one-token code does: BMW
      `N42 B18 A` and `M70-B50M` -> N42B18, M70B50 (and still N42, M70), Volvo
      `B 18 D` -> B18, Suzuki `H 25 A` -> H25;
    - with a `manufacturer`, the family that maker's reviewed grammar gives a
      variant (`engine_code_aliases.engine_variant_family`): Subaru `EJ253` ->
      EJ25, Nissan `HR13DDT` -> HR13, Honda `L15B3` -> L15B.

    As with `engine_code_family`, a family is compatible only with a code that
    names the family alone: `EJ251` and `EJ253`, `B230F` and `B 230 FD`, `16S`
    and `16SH`, `K9K 636` and `K9K 646` stay different engines. A value listing
    several codes (`A, B`) keeps only what `engine_code_family` gives it.
    """

    families = {family for family in (engine_code_family(value),) if family}
    text = unicodedata.normalize("NFKC", value).upper()
    if "," in text:
        return frozenset(families)
    text = re.sub(r"\([^()]*\)", " ", text).strip()
    compact = _normalized_code(text)
    if (
        len([token for token in _ENGINE_HEAD_SPLIT.split(text) if token]) >= 2
        and (suffixed := _ENGINE_REVISION_SUFFIX.match(compact))
        and len(head := suffixed.group(1)) >= 3
        and not head.isdigit()
    ):
        families.add(head)
    if manufacturer and (variant := engine_variant_family(maker_key(manufacturer), compact)):
        families.add(variant)
    return frozenset(families)


def _engine_name(code: str) -> str:
    """A code's own compact name, without TecDoc's bracket alternative: `8FR (EP3C)` -> 8FR."""

    return _normalized_code(re.sub(r"\([^()]*\)", " ", unicodedata.normalize("NFKC", code).upper()))


@lru_cache(maxsize=250_000)
def _engine_set_forms(codes: frozenset[str], manufacturer: str) -> frozenset[str]:
    return frozenset(form for code in codes for form in engine_code_forms(code, manufacturer))


@lru_cache(maxsize=250_000)
def _engine_set_families(codes: frozenset[str], manufacturer: str) -> frozenset[str]:
    return frozenset(
        family for code in codes for family in engine_code_families(code, manufacturer)
    )


@lru_cache(maxsize=250_000)
def _engine_set_names(codes: frozenset[str]) -> frozenset[str]:
    return frozenset(name for code in codes if (name := _engine_name(code)))


#: How a car's engine code relates to one KType (`EngineCodeCatalog.relation`).
#: "engine_code", "engine_code_alias" and "engine_code_list" all score as the
#: exact engine, but only "engine_code" says which engine the car has: it alone
#: may settle a candidate-only KType (`match_run_adapters._engine_confirms`).
EngineRelation = Literal[
    "missing",
    "engine_code",
    "engine_code_alias",
    "engine_code_list",
    "engine_code_family",
    "engine_code_unverified",
    "conflict",
]

#: Relations that earn `FuzzyMatchConfig.engine_match_bonus`.
EXACT_ENGINE_RELATIONS: frozenset[str] = frozenset(
    {"engine_code", "engine_code_alias", "engine_code_list"}
)

#: KType drivetrains whose whole engine set one registry list can name: the
#: motors of an electric car, or the motor and the engine that only charges it.
#: A list of alternative petrol engines (`LPA, LP1`) does not say which one the
#: car has, and a plug-in hybrid's list is left to the plug-in power decision.
_LISTED_DRIVETRAINS = frozenset({"battery_electric", "range_extender"})
_ELECTRIC_ONLY = frozenset({"ELECTRIC"})
_FOUR_WHEEL_DRIVE = "awd"
#: `missing_fields` entry of a veteran car's power that no candidate KType carries.
_VETERAN_POWER = "power_kw_veteran_unverified"
#: The rounding gap between the registry's power and TecDoc's
#: (`FuzzyMatchConfig.power_tolerance_kw`), where the engine catalog reads power.
_POWER_ROUNDING_KW = 2


class EngineCodeCatalog:
    """The engine codes the catalog's KTypes carry, and how a car's code relates to one."""

    def __init__(self, candidates: Iterable[VehicleCandidate]) -> None:
        known: set[str] = set()
        family_members: dict[tuple[str, str], list[VehicleCandidate]] = {}
        model_members: dict[tuple[str, str], list[VehicleCandidate]] = {}
        for candidate in candidates:
            if not candidate.engine_codes:
                continue
            known |= self._known_names(candidate)
            family_members.setdefault(self._model_family(candidate), []).append(candidate)
            model_members.setdefault(self._model(candidate), []).append(candidate)
        self._known = frozenset(known)
        self._family_members = {key: tuple(members) for key, members in family_members.items()}
        self._model_members = {key: tuple(members) for key, members in model_members.items()}
        # Filled on first use: most model families are never asked about.
        self._known_in_family: dict[tuple[str, str], frozenset[str]] = {}
        self._on_every_ktype: dict[tuple[str, str], frozenset[str]] = {}
        self._replaced_type: dict[tuple[str, str], str | None] = {}

    @staticmethod
    def _model(candidate: VehicleCandidate) -> tuple[str, str]:
        return _normalized_text(candidate.manufacturer), _normalized_text(candidate.model)

    @staticmethod
    def _model_family(candidate: VehicleCandidate) -> tuple[str, str]:
        """The maker and the word a model family's names start with: (BMW, 4) of `4 Gran Coupe (G26)`."""

        maker, model = EngineCodeCatalog._model(candidate)
        return maker, next(iter(model.split()), "")

    @staticmethod
    def _known_names(candidate: VehicleCandidate) -> frozenset[str]:
        """The names under which this KType makes a code known: its forms and first-token families."""

        names = set(_engine_set_forms(candidate.engine_codes, candidate.manufacturer))
        for code in candidate.engine_codes:
            if family := engine_code_family(code, revision=False):
                names.add(family)
        return frozenset(names)

    @staticmethod
    def _is_known(code: str, manufacturer: str, names: frozenset[str]) -> bool:
        family = engine_code_family(code, revision=False)
        return bool(engine_code_forms(code, manufacturer) & names) or (
            family is not None and family in names
        )

    def knows(self, code: str, manufacturer: str = "") -> bool:
        """True when any catalog KType carries this engine code or its family.

        `manufacturer` adds that maker's reviewed spellings of the code, so a
        Mercedes `M139.580` is known through the catalog's `139.580`.
        """

        return self._is_known(code, manufacturer, self._known)

    def relation(self, query: VehicleMatchQuery, candidate: VehicleCandidate) -> EngineRelation:
        """How the car's engine code relates to this KType's engines.

        Reviewed spellings and equivalences are read in the KType's maker scope.
        """

        code = query.engine_code or ""
        manufacturer = candidate.manufacturer
        candidate_forms = _engine_set_forms(candidate.engine_codes, manufacturer)
        if not candidate_forms:
            return "missing"
        if "," in code:
            return self._list_relation(query, candidate, candidate_forms)
        query_forms = engine_code_forms(code, manufacturer)
        maker = maker_key(manufacturer)
        if engines_sharing_registry_name(maker, _engine_name(code)) & _engine_set_names(
            candidate.engine_codes
        ):
            # One registry code the maker used for two engines (Jaguar `204PT`:
            # TecDoc's `204PT (GTDI)` and `PT204 (AJ20P4)`). It names neither
            # exactly, so it is family evidence on the KTypes of both.
            return "engine_code_family"
        if query_forms & candidate_forms:
            # BMW `M52-TUB20` names the updated engine; a KType listing only the
            # type code the update replaced carries its family, not this engine.
            if self._only_the_replaced_type(code, candidate):
                return "engine_code_family"
            return "engine_code"
        if engine_code_aliases(maker, _engine_name(code)) & _engine_set_names(candidate.engine_codes):
            # A reviewed equivalence across naming systems (Opel `A14XER`, GM
            # `LDD`): the same engine, but a reviewed table rather than the
            # KType's own code, so it never settles a candidate-only KType.
            return "engine_code_alias"
        if (named := registry_motor_family(maker, code)) is not None and named in {
            engine_code_family(listed, revision=False) for listed in candidate.engine_codes
        }:
            # The registry writes the motor family and no variant (Renault
            # `5AQ-60` on Zoes whose KTypes carry `5AQ 601` and `5AQ 605`):
            # family evidence on every KType of that family, and no conflict.
            return "engine_code_family"
        if (
            engine_code_families(code, manufacturer) & candidate_forms
            or _engine_set_families(candidate.engine_codes, manufacturer) & query_forms
            or type_name_shares_family(maker, code, candidate_forms)
        ):
            # One side names only the family (`K9K` against `K9K 276`, Subaru
            # `EJ253` against `EJ25`, Mazda `SH-VPTS` against `SHY1`). Two
            # different variants of a family (`D4F-742`, `D4F 740`) are
            # different engines and stay a conflict below.
            return "engine_code_family"
        if not self.knows(code, manufacturer):
            # A code no KType carries cannot contradict one; it cannot confirm
            # one either, so routing holds the car at provisional.
            return "engine_code_unverified"
        if (
            _normalized_values(query.fuels) == _ELECTRIC_ONLY
            and not self._is_known(code, manufacturer, self._family_names(candidate))
        ):
            # An electric car's motor code that no KType of this model family
            # carries comes from another code system (BMW i4 `XE2` against
            # `HA0001N0`): it is known only through other models, so here it
            # contradicts nothing. Where the family does carry the code or its
            # first-token family (ID.3 `EDCC` against `EDCA`, Zoe `5AQ-80`
            # against `5AQ 605`), the two sides speak one system and conflict.
            return "engine_code_unverified"
        return "conflict"

    def _list_relation(
        self, query: VehicleMatchQuery, candidate: VehicleCandidate, candidate_forms: frozenset[str]
    ) -> EngineRelation:
        """The relation of a value listing several codes (`HF1002N0, HD1001N0`).

        One strict rule: the list is this KType's engine only when every listed
        code is exactly on it. A list with any other code keeps the result the
        whole value had before lists were read, so nothing softens: Kia EV9
        `EM18, EM16` against a KType with EM16 alone stays a conflict, and a
        530e's `B48-B20A, GC1P25A` never matches the 520i's `B48 B20 A, JA1`.

        A KType that carries the whole list and further engines besides is
        family evidence only: a Porsche 993's `M64.21, M64.22` does not single
        out the Carrera listing M64.21 to M64.24 over the Carrera 4 listing
        two of them.
        """

        code = query.engine_code or ""
        manufacturer = candidate.manufacturer
        unsplit = self._unsplit_relation(code, candidate)
        parts = engine_code_parts(code)
        if unsplit == "engine_code" or not parts:
            return unsplit
        listed = [engine_code_forms(part, manufacturer) for part in parts]
        if not all(forms & candidate_forms for forms in listed):
            if self._one_listed_motor_of_a_four_wheel_drive(query, candidate, listed):
                return "engine_code_family"
            return unsplit
        if self._list_names_the_drivetrain(query, candidate, listed):
            return "engine_code"
        named = frozenset().union(*listed)
        if not all(engine_code_forms(each, manufacturer) & named for each in candidate.engine_codes):
            return "engine_code_family"
        return "engine_code_list"

    @staticmethod
    def _one_listed_motor_of_a_four_wheel_drive(
        query: VehicleMatchQuery, candidate: VehicleCandidate, listed: list[frozenset[str]]
    ) -> bool:
        """True when TecDoc lists one motor of a two-motor car that names both.

        The exception to the strict list rule, and a narrow one: an electric
        KType with a single code, that code among the car's, both four-wheel
        drive, and the same power up to rounding (an EV9 AWD stands at 282 kW in
        the registry, 283 in TecDoc). It is registered `EM18, EM16`; TecDoc gives
        its four-wheel-drive KTypes `EM16` alone, as it gives the rear-drive ones. The code is then family evidence, never the exact
        engine: drive and power are what name the KType. Proposed 2026-10-07;
        awaiting the data owner's confirmation.
        """

        electric = candidate.electrification in _LISTED_DRIVETRAINS or (
            candidate.electrification is None
            and _normalized_values(candidate.fuels) == _ELECTRIC_ONLY
        )
        if not electric or len(candidate.engine_codes) != 1:
            return False
        only = engine_code_forms(next(iter(candidate.engine_codes)), candidate.manufacturer)
        if not any(forms & only for forms in listed):
            return False
        four_wheel = _normalized_text(_FOUR_WHEEL_DRIVE)
        return (
            _normalized_text(query.drive_type or "") == four_wheel
            and _normalized_text(candidate.drive_type or "") == four_wheel
            and query.power_kw is not None
            and candidate.power_kw is not None
            and abs(query.power_kw - candidate.power_kw) <= _POWER_ROUNDING_KW
        )

    def _unsplit_relation(self, code: str, candidate: VehicleCandidate) -> EngineRelation:
        """The relation a list has when read as one code, without any reviewed rule."""

        query_forms = engine_code_forms(code)
        candidate_forms = _engine_set_forms(candidate.engine_codes, "")
        if query_forms & candidate_forms:
            return "engine_code"
        query_family = engine_code_family(code) or _normalized_code(code)
        candidate_families = {
            engine_code_family(candidate_code) or _normalized_code(candidate_code)
            for candidate_code in candidate.engine_codes
        }
        if query_family in candidate_forms or candidate_families & query_forms:
            return "engine_code_family"
        return "conflict" if self.knows(code) else "engine_code_unverified"

    def _list_names_the_drivetrain(
        self,
        query: VehicleMatchQuery,
        candidate: VehicleCandidate,
        listed: list[frozenset[str]],
    ) -> bool:
        """True when a list every code of which is on the KType says which engine the car has.

        That takes all of:

        - an electric drivetrain (`_LISTED_DRIVETRAINS`) whose every engine the
          list names: the two motors of an iX3, the motor and range extender of
          an i3. Dacia's `H5H-470, H5H-480` on a KType with three H5H engines
          lists alternatives, not the car's engine;
        - a build date inside the KType's production window: Tesla reuses motor
          codes across model years, and a Model Y built 2025-10 is not the
          KType that ended 2025-01;
        - a code that tells KTypes of the model apart: `L2S` is on every
          Model S KType, so it says nothing about which one this is.
        """

        manufacturer = candidate.manufacturer
        electric = candidate.electrification in _LISTED_DRIVETRAINS or (
            candidate.electrification is None
            and _normalized_values(candidate.fuels) == _ELECTRIC_ONLY
        )
        if not electric or not self._built_inside_window(query, candidate):
            return False
        named = frozenset().union(*listed)
        if not all(engine_code_forms(code, manufacturer) & named for code in candidate.engine_codes):
            return False
        on_every_ktype = self._forms_on_every_ktype(candidate)
        return not all(forms & on_every_ktype for forms in listed)

    @staticmethod
    def _built_inside_window(query: VehicleMatchQuery, candidate: VehicleCandidate) -> bool:
        """True when the car's build month, or else its year, lies inside the KType's window."""

        if query.build_month is not None and (
            candidate.month_from is not None or candidate.month_to is not None
        ):
            return not _built_outside_production_months(query.build_month, candidate)
        if query.year is None or (candidate.year_from is None and candidate.year_to is None):
            return False
        return (candidate.year_from is None or candidate.year_from <= query.year) and (
            candidate.year_to is None or query.year <= candidate.year_to
        )

    def _forms_on_every_ktype(self, candidate: VehicleCandidate) -> frozenset[str]:
        """Forms every KType of this model carries; none for a model with one KType."""

        key = self._model(candidate)
        shared = self._on_every_ktype.get(key)
        if shared is None:
            members = self._model_members.get(key, ())
            shared = (
                frozenset.intersection(
                    *(_engine_set_forms(member.engine_codes, member.manufacturer) for member in members)
                )
                if len(members) >= 2
                else frozenset()
            )
            self._on_every_ktype[key] = shared
        return shared

    def _family_names(self, candidate: VehicleCandidate) -> frozenset[str]:
        """The names the KTypes of this model family make known (`_known_names`)."""

        key = self._model_family(candidate)
        names = self._known_in_family.get(key)
        if names is None:
            names = frozenset().union(
                *(self._known_names(member) for member in self._family_members.get(key, ()))
            )
            self._known_in_family[key] = names
        return names

    def _only_the_replaced_type(self, code: str, candidate: VehicleCandidate) -> bool:
        """True when a BMW technical-update code meets a KType with only the replaced type code.

        TecDoc writes no TU marker but tells the engines apart by the type code
        in brackets: `M52 B20 (206S3)` before the update, `(206S4)` after. It
        allocates them loosely (E39 KTypes list both), so the earlier code is no
        contradiction. It is never the exact engine either: a KType listing
        only the replaced type code carries the engine before the update,
        whatever its model and production period.

        The replaced type code is decided per engine head over the maker's
        whole catalog, not per model: the X6 lists `M57 D30` only as `(306D3)`
        and `(306D5)`, both updated engines; the replaced one is `(306D1)`.
        """

        maker = maker_key(candidate.manufacturer)
        if maker != "BMW":
            return False
        unmarked = bmw_technical_update(code)
        if unmarked is None:
            return False
        head = _normalized_code(unmarked)
        key = (maker, head)
        if key not in self._replaced_type:
            self._replaced_type[key] = self._replaced_type_code(maker, head)
        replaced = self._replaced_type[key]
        return replaced is not None and _engine_type_codes(candidate, head) == {replaced}

    def _replaced_type_code(self, maker: str, head: str) -> str | None:
        """The type code a maker's engine head had before any technical update.

        The trailing digit counts the engine's technical revisions, so the
        lowest type code of a head is the engine before any update: 206S3 of
        `M52 B20`, 306D1 of `M57 D30`. None for a head with one type code
        (`M43 B19 (194E1)`): nothing tells its update apart.
        """

        type_codes = {
            code
            for members in self._family_members.values()
            for member in members
            if maker_key(member.manufacturer) == maker
            for code in _engine_type_codes(member, head)
        }
        return min(type_codes) if len(type_codes) >= 2 else None


def _engine_type_codes(candidate: VehicleCandidate, head: str) -> frozenset[str]:
    """The bracket type codes this KType lists for one engine head: 206S3 of `M52 B20 (206S3)`."""

    codes: set[str] = set()
    for code in candidate.engine_codes:
        if _engine_name(code) == head:
            text = unicodedata.normalize("NFKC", code).upper()
            codes.update(
                compact for part in _ENGINE_BRACKET.findall(text) if (compact := _normalized_code(part))
            )
    return frozenset(codes)


#: Fuels that name a variant of a base petrol or diesel car: flex-fuel, gas, hybrid.
#: The registry records them as a second fuel (or its hybrid marker), so a car
#: registered with one is that variant, and a car registered without one is not.
_VARIANT_FUELS = frozenset({"ETHANOL", "LPG", "CNG", "HYBRID PETROL", "HYBRID DIESEL"})

#: KType electrification (from TecDoc's engine type) that charges from the grid: a
#: plug-in hybrid, or a range extender, whose engine only charges the battery.
PLUG_IN_ELECTRIFICATION = frozenset({"plug_in_hybrid", "range_extender"})
#: A car registered with one of these and electricity runs an engine beside the motor.
_COMBUSTION_FUELS = frozenset({"PETROL", "DIESEL", "HYBRID PETROL", "HYBRID DIESEL"})
#: `FuzzyMatchResult.guards` entry of the plug-in power guard (`_plug_in_power_lead`).
PLUG_IN_POWER_GUARD = "plug_in_power_unverified"
#: `FuzzyMatchResult.guards` entry: the top's cc is only rounded while a sibling
#: with the car's exact cc is held back by nothing but its engine code.
DISPLACEMENT_ROUNDING_SIBLING_GUARD = "displacement_rounding_exact_sibling"
#: `FuzzyMatchResult.guards` entry: the top's cc or power is only tolerated and
#: none of power, cc and engine code is exact.
TOLERATED_EVIDENCE_GUARD = "no_exact_technical_field"


def _valid_year_month(value: int) -> bool:
    return 188601 <= value <= 220012 and 1 <= value % 100 <= 12


def _shift_year_month(value: int, months: int) -> int:
    year, month = divmod(value, 100)
    total = year * 12 + month - 1 + months
    return (total // 12) * 100 + total % 12 + 1


def _built_outside_production_months(
    build_month: int | None, candidate: VehicleCandidate, tolerance: int = 0
) -> bool:
    """True when the car was built outside the KType's production months, each
    boundary widened by `tolerance` months; False when either side has no months."""

    if build_month is None:
        return False
    if candidate.month_from is not None and build_month < _shift_year_month(candidate.month_from, -tolerance):
        return True
    return candidate.month_to is not None and build_month > _shift_year_month(candidate.month_to, tolerance)


#: Kilowatts per metric horsepower (PS) and per US horsepower (hp). A US-spec
#: car's "300 hp" read as 300 PS gives 221 kW where TecDoc lists 224 kW.
_KW_PER_PS = 0.7355
_KW_PER_HP = 0.7457
#: Reviewed US-market makers (TecDoc names) whose power is quoted in hp, so the
#: registry and TecDoc may have converted the same figure with different units.
#: Plain "FORD" is the European Ford and stays out; the US cars are "FORD USA".
#: Measured without this list, the same gap made 12 non-US cars wrong matches.
HORSEPOWER_UNIT_MAKERS = frozenset({
    "FORD USA", "CHEVROLET", "DODGE", "CHRYSLER", "JEEP",
    "CADILLAC", "GMC", "BUICK", "LINCOLN", "PONTIAC",
})
#: Mazda's rotary engines as TecDoc codes them ("13B-MSP" is the RX-8 Renesis,
#: "RE13B", "12AN2", and "N8Y1", the 830 cc range extender of the MX-30 R-EV),
#: read on the code with separators removed.
_MAZDA_ROTARY_CODE = re.compile(r"^(?:RE)?(?:10A|12A|13A|13B|20B)|^N8Y1$|RENESIS")


def _is_mazda_rotary(candidate: VehicleCandidate) -> bool:
    """True for a Mazda KType whose engine code names a Wankel rotary engine."""

    return _normalized_text(candidate.manufacturer) == "MAZDA" and any(
        _MAZDA_ROTARY_CODE.search(_normalized_code(code)) for code in candidate.engine_codes
    )


def _horsepower_unit_gap(left_kw: int, right_kw: int, slack_kw: float) -> bool:
    """True when one figure is the other converted with the other horsepower unit
    (221 kW is 300 PS, 224 kW is 300 hp), within `slack_kw` for the two roundings."""

    low, high = sorted((left_kw, right_kw))
    return abs(high - low * _KW_PER_HP / _KW_PER_PS) <= slack_kw


def _is_hybrid(fuels: Iterable[str]) -> bool:
    return any(fuel.startswith("HYBRID") for fuel in fuels)


@lru_cache(maxsize=250_000)
def _edit_similarity(left: str, right: str) -> float:
    left_compact = left.replace(" ", "")
    right_compact = right.replace(" ", "")
    if left_compact == right_compact:
        return 1.0
    if not left_compact or not right_compact:
        return 0.0
    distances = [list(range(len(right_compact) + 1))]
    distances.extend(
        [[left_index] + [0] * len(right_compact) for left_index in range(1, len(left_compact) + 1)]
    )
    for left_index, left_character in enumerate(left_compact, start=1):
        for right_index, right_character in enumerate(right_compact, start=1):
            substitution = distances[left_index - 1][right_index - 1] + (
                left_character != right_character
            )
            distances[left_index][right_index] = min(
                distances[left_index - 1][right_index] + 1,
                distances[left_index][right_index - 1] + 1,
                substitution,
            )
            if (
                left_index > 1
                and right_index > 1
                and left_character == right_compact[right_index - 2]
                and left_compact[left_index - 2] == right_character
            ):
                distances[left_index][right_index] = min(
                    distances[left_index][right_index],
                    distances[left_index - 2][right_index - 2] + 1,
                )
    distance = distances[-1][-1]
    return 1.0 - (distance / max(len(left_compact), len(right_compact)))


@lru_cache(maxsize=250_000)
def _token_similarity(left: str, right: str) -> float:
    left_tokens = frozenset(left.split())
    right_tokens = frozenset(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _bounded_score(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 6)


def _normalized_values(values: Iterable[str]) -> frozenset[str]:
    return frozenset(normalized for value in values if (normalized := _normalized_text(value)))


def _fuel_evidence_matches(
    query_fuels: frozenset[str], candidate_fuels: frozenset[str]
) -> bool:
    if query_fuels & candidate_fuels:
        return True
    # "ELECTRIC" is TecDoc's own native spelling and the canonical term the
    # live tecdoc_resolution_rules electricity/electric row normalizes TS's
    # "electricity" into (see ingestion/vocabulary_alignment.py), applied to
    # both `query_fuels` and `candidate_fuels` before this function ever runs.
    hybrid_requirements = {
        "HYBRID PETROL": frozenset({"PETROL", "ELECTRIC"}),
        "HYBRID DIESEL": frozenset({"DIESEL", "ELECTRIC"}),
    }
    return any(
        hybrid in candidate_fuels and required.issubset(query_fuels)
        for hybrid, required in hybrid_requirements.items()
    )


@dataclass(frozen=True)
class FuzzyMatchConfig:
    """Injected Stage 2a thresholds and scoring weights."""

    candidate_threshold: float = 0.55
    automatic_threshold: float = 0.90
    automatic_margin: float = 0.08
    manufacturer_scope_threshold: float = 0.80
    edit_weight: float = 0.65
    token_weight: float = 0.35
    model_series_conflict_penalty: float = 0.35
    phonetic_match_bonus: float = 0.08
    phonetic_min_text_score: float = 0.35
    year_match_bonus: float = 0.05
    year_conflict_penalty: float = 0.20
    # A car's registry year and a KType's production run are counted
    # differently (model year against build year, the last cars of a run first
    # registered the next year), so a year just outside the run is unverified:
    # never a match, never a hard conflict.
    year_tolerance: int = 1
    # Same role as `power_tolerance_penalty`: a KType whose run covers the year
    # must clear the automatic margin over a sibling that only nearly does.
    year_tolerance_penalty: float = 0.05
    # A car built in a year the KType's run covers, but in a month outside it
    # (built 03/2008, facelift from 05/2008): unverified, never a conflict, since
    # TecDoc's month boundaries are approximate. As with the other tolerances,
    # the gap to `year_match_bonus` must exceed the automatic margin, so the
    # sibling whose months cover the build month clears it.
    production_month_penalty: float = 0.05
    # Months a build month may lie outside a KType's TecDoc months and still count
    # as inside. Measured 2026-10-01 on the 30k and an independent 20k (changed cars
    # only): 0 months gained 686 and lost 72 to review, 1 month 563 and 52, 2 months
    # 444 and 37, 3 months 341 and 21. Lost cars go to review, not to another KType,
    # so 0 was chosen; changing it means re-measuring both samples. Months only
    # choose within the car's model line (`_model_line`): with that rule, against
    # no months, the 30k gained 410, lost 32 to review, moved 8, and the 20k gained
    # 279, lost 24, moved 6 (every lost and moved car checked).
    production_month_tolerance: int = 0
    fuel_match_bonus: float = 0.05
    fuel_conflict_penalty: float = 0.15
    # A KType that shares only the base fuel with a car registered as a variant
    # (a flex-fuel car against the petrol KType), or that is a variant the car
    # is not registered as (a petrol car against the LPG KType), is unverified:
    # never a match, never a conflict. As with `power_tolerance_penalty`, the
    # gap to `fuel_match_bonus` must exceed the automatic margin, otherwise the
    # KType carrying the car's exact fuel ties with its sibling.
    fuel_variant_penalty: float = 0.05
    engine_match_bonus: float = 0.12
    # Same engine family, different variant suffix (`K9K 276` against `K9K`):
    # compatible, but weaker than the exact engine. The gap to
    # `engine_match_bonus` must exceed the automatic margin, otherwise a KType
    # carrying the car's exact code ties with a family sibling (FB25 / FB25B).
    engine_family_match_bonus: float = 0.03
    engine_conflict_penalty: float = 0.25
    displacement_match_bonus: float = 0.05
    displacement_conflict_penalty: float = 0.25
    # The registry and TecDoc round the same engine's swept volume differently
    # (Volvo D5 2,400 against 2,401 cc, Mazda 2.2 D 2,184 against 2,183), so a
    # gap of up to 3 cc is unverified: never a match, never a hard conflict.
    displacement_tolerance_cc: int = 3
    # Same role as `power_tolerance_penalty`: the sibling with the car's exact cc
    # earns +0.05 and the rounded one loses 0.05, so they are 0.10 apart, which
    # exceeds the automatic margin of 0.08. Two rounded siblings stay equal on
    # displacement, so the tolerance never separates a tie.
    displacement_tolerance_penalty: float = 0.05
    power_match_bonus: float = 0.05
    power_conflict_penalty: float = 0.25
    # TS and TecDoc quote power through different PS/kW roundings, so a gap of
    # a kilowatt or two is measurement noise rather than a contradiction. Real
    # variants of one model are frequently 1-2 kW apart too, so a near miss is
    # treated as unverified evidence: never a match, never a hard conflict.
    power_tolerance_kw: int = 2
    # Approximate power is weaker evidence than an exact figure. The gap
    # between the two must exceed the automatic margin, otherwise an exactly
    # matching k-type cannot separate itself from a rounded sibling and both
    # are sent to review as ambiguous.
    power_tolerance_penalty: float = 0.05
    # A US-spec car's power is quoted in hp (0.7457 kW); read as PS (0.7355 kW),
    # or the other way round, the same figure lands 1.4% apart: a Mustang's 300 hp
    # is 224 kW in TecDoc and 221 kW (300 PS) in the registry. Such a gap, within
    # this slack for the two roundings, is unverified for the reviewed US-market
    # makers only (`HORSEPOWER_UNIT_MAKERS`) and never for a car or KType with
    # electricity or a hybrid fuel. It costs `power_tolerance_penalty`, so a
    # sibling with the exact power stays 0.10 ahead, more than the margin.
    # The slack is the two integer roundings: 0.5 kW on the higher figure plus
    # 0.5 kW on the lower one scaled by 0.7457 / 0.7355 = 1.0139, at most
    # 0.5 + 0.5 * 1.0139 = 1.007 kW. 1.01 misses no pair reachable from an integer
    # horsepower figure (checked 100-700 hp); 1.2 also took plain 3 kW gaps
    # between engine versions (132/135, 139/142, 295/298).
    horsepower_unit_slack_kw: float = 1.01
    # --- Proposals of 2026-10-07, in force pending the data owner's confirmation ---
    # A battery electric car's registry power and TecDoc's are a few kW apart
    # for one drivetrain (Toyota bZ4X 167 against 165 kW, C-HR+ 255 against 252,
    # Zeekr 7X 475 against 470): more than `power_tolerance_kw` on a strong
    # motor. A gap of up to this share of the KType's figure is unverified for a
    # car and a KType that are both electric only: never a match, never a
    # conflict. It costs `power_tolerance_penalty`, so a sibling with the exact
    # figure stays 0.10 ahead. It holds only where that KType's figure is the
    # one figure of its model that near: real variants are a few kW apart too
    # (a Tesla Model Y at 255 and at 258 kW), and a car between two of them is
    # neither's on power alone. 0 is off.
    electric_power_tolerance: float = 0.02
    # The reviewed pairs of `tecdoc.power_equivalences` (registry 270 kW is
    # TecDoc's 280 kW on an Audi A6 e-tron) count as the same figure.
    reviewed_power_equivalences: bool = True
    # The registry's power for a car of the 1950s and 60s is no figure TecDoc
    # lists (a Volvo Amazon stands at 55 kW; TecDoc knows 49, 59, 63 and 66): the
    # two count horsepower by different standards. For a car built before this
    # year a differing power is unverified -- never a match, never a conflict --
    # with the usual penalty, and which KType it is rests on year, displacement
    # and engine code. Only while no candidate KType carries the car's figure:
    # where one does, exactly or within rounding, power decides as for any car.
    # Without that condition every veteran that had resolved on its power tied
    # with its siblings (38 of 50,000 sample cars lost). 0 is off.
    veteran_power_before_year: int = 1975
    drive_match_bonus: float = 0.05
    drive_conflict_penalty: float = 0.15
    bodywork_match_bonus: float = 0.05
    bodywork_conflict_penalty: float = 0.15
    # Keep the existing weight until stronger weighting is independently
    # calibrated. Merely differing body styles does not justify a new policy.
    bodywork_discriminating_weight: float = 1.0
    max_candidates: int = 5

    def __post_init__(self) -> None:
        threshold_fields = (
            self.candidate_threshold,
            self.automatic_threshold,
            self.automatic_margin,
            self.manufacturer_scope_threshold,
            self.phonetic_min_text_score,
        )
        if any(not 0.0 <= value <= 1.0 for value in threshold_fields):
            raise ValueError("matching thresholds and margin must be between 0.0 and 1.0")
        if self.candidate_threshold > self.automatic_threshold:
            raise ValueError("candidate_threshold must not exceed automatic_threshold")
        if self.edit_weight < 0.0 or self.token_weight < 0.0:
            raise ValueError("text weights must not be negative")
        if abs((self.edit_weight + self.token_weight) - 1.0) > 1e-9:
            raise ValueError("edit_weight and token_weight must sum to 1.0")
        if self.max_candidates < 1:
            raise ValueError("max_candidates must be positive")
        if self.year_tolerance < 0:
            raise ValueError("year_tolerance must not be negative")
        if self.production_month_tolerance < 0:
            raise ValueError("production_month_tolerance must not be negative")
        if self.displacement_tolerance_cc < 0:
            raise ValueError("displacement_tolerance_cc must not be negative")
        if self.power_tolerance_kw < 0:
            raise ValueError("power_tolerance_kw must not be negative")
        if self.horsepower_unit_slack_kw < 0.0:
            raise ValueError("horsepower_unit_slack_kw must not be negative")
        if not 0.0 <= self.electric_power_tolerance <= 0.1:
            raise ValueError("electric_power_tolerance must be between 0.0 and 0.1")
        if self.veteran_power_before_year < 0:
            raise ValueError("veteran_power_before_year must not be negative")
        effects = (
            self.model_series_conflict_penalty,
            self.phonetic_match_bonus,
            self.year_match_bonus,
            self.year_conflict_penalty,
            self.year_tolerance_penalty,
            self.production_month_penalty,
            self.fuel_match_bonus,
            self.fuel_conflict_penalty,
            self.fuel_variant_penalty,
            self.engine_match_bonus,
            self.engine_family_match_bonus,
            self.engine_conflict_penalty,
            self.displacement_match_bonus,
            self.displacement_conflict_penalty,
            self.displacement_tolerance_penalty,
            self.power_match_bonus,
            self.power_conflict_penalty,
            self.power_tolerance_penalty,
            self.drive_match_bonus,
            self.drive_conflict_penalty,
            self.bodywork_match_bonus,
            self.bodywork_conflict_penalty,
        )
        if any(not 0.0 <= value <= 1.0 for value in effects):
            raise ValueError("context bonuses and penalties must be between 0.0 and 1.0")


@dataclass(frozen=True)
class VehicleCandidate:
    """One canonical variant or TecDoc k-type available to Stage 2a."""

    candidate_reference: str
    manufacturer: str
    model: str
    candidate_type: str = "VehicleVariant"
    model_aliases: tuple[str, ...] = ()
    manufacturer_aliases: tuple[str, ...] = ()
    year_from: int | None = None
    year_to: int | None = None
    #: Production months as YYYYMM, when the catalog carries them.
    month_from: int | None = None
    month_to: int | None = None
    fuels: frozenset[str] = field(default_factory=frozenset)
    fuel_components: frozenset[str] = field(default_factory=frozenset)
    engine_codes: frozenset[str] = field(default_factory=frozenset)
    displacement_cc: int | None = None
    power_kw: int | None = None
    drive_type: str | None = None
    bodyworks: frozenset[str] = field(default_factory=frozenset)
    #: What TecDoc's engine type says of the drive: "plug_in_hybrid",
    #: "range_extender", "full_hybrid", "mild_hybrid", "battery_electric" or
    #: "combustion". None when the catalog did not load an engine type: unknown,
    #: which no electrification check ever reads as "not a plug-in".
    electrification: str | None = None

    def __post_init__(self) -> None:
        if not self.candidate_reference.strip():
            raise ValueError("candidate_reference must not be empty")
        if not _normalized_text(self.manufacturer):
            raise ValueError("manufacturer must not be empty")
        if not _normalized_text(self.model):
            raise ValueError("model must not be empty")
        if not self.candidate_type.strip():
            raise ValueError("candidate_type must not be empty")
        if self.year_from is not None and not 1886 <= self.year_from <= 2200:
            raise ValueError("year_from must be between 1886 and 2200")
        if self.year_to is not None and not 1886 <= self.year_to <= 2200:
            raise ValueError("year_to must be between 1886 and 2200")
        if (
            self.year_from is not None
            and self.year_to is not None
            and self.year_to < self.year_from
        ):
            raise ValueError("year_to must not be before year_from")
        for month in (self.month_from, self.month_to):
            if month is not None and not _valid_year_month(month):
                raise ValueError("production months must be YYYYMM")
        if self.displacement_cc is not None and self.displacement_cc <= 0:
            raise ValueError("displacement_cc must be positive")
        if self.power_kw is not None and self.power_kw <= 0:
            raise ValueError("power_kw must be positive")
        if self.drive_type is not None and not _normalized_text(self.drive_type):
            raise ValueError("drive_type must not be blank")
        if any(not _normalized_text(fuel) for fuel in self.fuel_components):
            raise ValueError("fuel_components must not contain blanks")
        if any(not _normalized_text(bodywork) for bodywork in self.bodyworks):
            raise ValueError("bodyworks must not contain blanks")


@dataclass(frozen=True)
class VehicleMatchQuery:
    model: str
    manufacturer: str | None = None
    year: int | None = None
    #: The car's build month as YYYYMM, when the registry gives one.
    build_month: int | None = None
    fuels: frozenset[str] = field(default_factory=frozenset)
    engine_code: str | None = None
    displacement_cc: int | None = None
    power_kw: int | None = None
    drive_type: str | None = None
    bodywork: str | None = None
    source_context: tuple[tuple[str, str], ...] = ()
    #: The registry's electrification type ("hybrid", "plug_in_hybrid", ...), when stated.
    electrification: str | None = None

    def __post_init__(self) -> None:
        if not _normalized_text(self.model):
            raise ValueError("model must not be empty")
        if self.manufacturer is not None and not _normalized_text(self.manufacturer):
            raise ValueError("manufacturer must not be blank")
        if self.year is not None and not 1886 <= self.year <= 2200:
            raise ValueError("year must be between 1886 and 2200")
        if self.build_month is not None and not _valid_year_month(self.build_month):
            raise ValueError("build_month must be YYYYMM")
        if self.displacement_cc is not None and self.displacement_cc <= 0:
            raise ValueError("displacement_cc must be positive")
        if self.power_kw is not None and self.power_kw <= 0:
            raise ValueError("power_kw must be positive")
        if self.drive_type is not None and not _normalized_text(self.drive_type):
            raise ValueError("drive_type must not be blank")
        if self.bodywork is not None and not _normalized_text(self.bodywork):
            raise ValueError("bodywork must not be blank")


@dataclass(frozen=True)
class FuzzyCandidateMatch:
    candidate_reference: str
    candidate_type: str
    manufacturer: str
    model: str
    confidence: float
    text_score: float
    context_effect: float
    matched_label: str
    matched_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]
    conflicting_fields: tuple[str, ...]
    phonetic_match: bool
    context_rule_ids: tuple[str, ...] = ()
    context_policy_digest: str | None = None

    @property
    def separation_score(self) -> float:
        """Unclamped ranking score used to order and separate candidates.

        `confidence` saturates at 1.0, so two candidates whose evidence differs
        sharply -- an exact model name with every technical field matched versus
        the same name with a conflicting field -- both report 1.0 and appear
        indistinguishable. Ranking and margin decisions therefore use this
        unclamped value; `confidence` remains the bounded score for thresholds
        and reporting.
        """
        return round(self.text_score + self.context_effect, 6)

    def to_review_payload(self) -> dict[str, Any]:
        return {
            "candidate_reference": self.candidate_reference,
            "candidate_type": self.candidate_type,
            "confidence": self.confidence,
            "evidence": {
                "manufacturer": self.manufacturer,
                "model": self.model,
                "matched_label": self.matched_label,
                "text_score": self.text_score,
                "context_effect": self.context_effect,
                "matched_fields": list(self.matched_fields),
                "missing_fields": list(self.missing_fields),
                "conflicting_fields": list(self.conflicting_fields),
                "phonetic_match": self.phonetic_match,
                "phonetic_version": PHONETIC_VERSION if self.phonetic_match else None,
                **({
                    "context_rule_ids": list(self.context_rule_ids),
                    "context_policy_digest": self.context_policy_digest,
                } if self.context_rule_ids else {}),
            },
        }


@dataclass(frozen=True)
class FuzzyMatchResult:
    scope: MatchScope
    candidates: tuple[FuzzyCandidateMatch, ...]
    eligible_for_auto_resolution: bool
    reason: str
    #: Guards that held the top back although its score would have cleared
    #: (`PLUG_IN_POWER_GUARD`, `DISPLACEMENT_ROUNDING_SIBLING_GUARD`,
    #: `TOLERATED_EVIDENCE_GUARD`); the evaluator reports each as `match_guard:<guard>`.
    guards: tuple[str, ...] = ()

    @property
    def phonetic_version(self) -> str | None:
        if self.scope == "phonetic_manufacturer" or any(
            candidate.phonetic_match for candidate in self.candidates
        ):
            return PHONETIC_VERSION
        return None

    def review_candidates(self) -> tuple[dict[str, Any], ...]:
        payloads: list[dict[str, Any]] = []
        for candidate in self.candidates:
            payload = candidate.to_review_payload()
            evidence = payload["evidence"]
            if not isinstance(evidence, dict):
                raise TypeError("candidate evidence must be an object")
            evidence["match_scope"] = self.scope
            evidence["phonetic_version"] = self.phonetic_version
            payloads.append(payload)
        return tuple(payloads)


MODEL_RECOVERY_VERSION = "shared-family-query-recovery-v2-saab-hyphenated"

_MODEL_EVIDENCE_FIELD_PRIORITY = (
    # The registry's own model field is the most direct statement of the model,
    # so it outranks fields that merely happen to contain the name.
    "model",
    "eeg_type_approval",
    "model_no",
    "version",
    "variant",
    "type_text",
    "brand",
)


def _eligible_recovery_label(
    manufacturer: str, label: str, canonicals: tuple[str, ...], field_name: str, value: str,
) -> bool:
    if len(label.replace(" ", "")) >= 3 and not label.isdigit() or re.fullmatch(r"[A-Z][0-9]", label):
        return True
    # The generic minimum-length guard intentionally excludes numbers. Saab's
    # explicit 9-3/9-5 names are a narrow exception, not permission to recover
    # models from decimal displacements, approval numbers or arbitrary aliases.
    if manufacturer != "SAAB" or label not in {"9 3", "9 5"} or field_name not in {"brand", "model"}:
        return False
    if not canonicals or not all(
        _normalized_text(canonical) == label or _normalized_text(canonical).startswith(f"{label} ")
        for canonical in canonicals
    ):
        return False
    return re.search(rf"(?<!\w)9\s*[-‐‑–]\s*{label[-1]}(?!\w)", value) is not None


#: Registry spellings of a make that differ from the catalog's name for it.
_REGISTRY_MAKE_WORDS: dict[str, frozenset[str]] = {
    "VW": frozenset({"VOLKSWAGEN", "VW"}),
    # AMG is Mercedes-Benz's performance brand: "MERCEDES-AMG C 63" is a C-Class.
    "MERCEDES BENZ": frozenset({"MERCEDES", "BENZ", "AMG", "MB"}),
}


def model_position_token(field_name: str, value: str, manufacturer_key: str) -> str | None:
    """The word in the position a registry field names the model in, if it has one.

    The model field is the model: its first word ("911 CARRERA 4 GTS" -> 911),
    after any make words it repeats ("TOYOTA BZ4X" -> BZ4X, "MB SL 500" -> SL).
    Brand text names the make first and the model next ("PEUGEOT 307 1,6" -> 307,
    "MERCEDES BENZ C 180" -> C); Volvo's 1990s text is "<model-year letter> +
    <model>" ("VOLVO 9 + 940" -> 940). Brand text that does not start with the
    make has no model position, nor does any other field.
    """

    make_words = set(manufacturer_key.split()) | _REGISTRY_MAKE_WORDS.get(manufacturer_key, frozenset())
    if field_name == "model":
        tokens = _normalized_text(value).split()
        while len(tokens) > 1 and tokens[0] in make_words:
            tokens = tokens[1:]
        return tokens[0] if tokens else None
    if field_name != "brand":
        return None
    tokens = _normalized_text(value).split()
    if not tokens or tokens[0] not in make_words:
        return None
    if "+" in value:
        after = _normalized_text(value.split("+", 1)[1]).split()
        return after[0] if after else None
    rest = tokens[1:]
    while rest and rest[0] in make_words:
        rest = rest[1:]
    return rest[0] if rest else None


def _words(name: str) -> list[str]:
    return _normalized_text(name).split()


_ROMAN_GENERATION = frozenset({"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"})


def _is_model_number(word: str) -> bool:
    """A number or a single letter that names a model ("4", "Y"), not a TecDoc
    generation numeral ("IV") or a word ("PLUS")."""

    return word.isdigit() or (len(word) == 1 and word.isalpha() and word not in _ROMAN_GENERATION)


def _numbers_differ(word: list[str], other: list[str]) -> bool:
    """True when two names go on with different model numbers or letters ("3", "2")."""

    return bool(
        word and other and word != other and _is_model_number(word[0]) and _is_model_number(other[0])
    )


def same_model_family(value: str, catalog_model: str, manufacturer: str) -> bool:
    """True when a TS model family and a catalog model name the same family."""

    value_words, model_words = _words(value), _words(catalog_model)
    if not value_words or not model_words:
        return False
    first, catalog_first = value_words[0], model_words[0]
    if first == catalog_first:
        # A model number or letter after the shared word is the model itself: "ID.4" is
        # not "ID.3 (E11)", "Ioniq 5" not "IONIQ 6", "Model 3" not "MODEL Y".
        return not _numbers_differ(value_words[1:2], model_words[1:2])
    compact, catalog_compact = "".join(value_words), "".join(model_words)
    if catalog_compact.startswith(compact) or compact.startswith(catalog_compact):
        return True
    # "Mazda3" against "3 (BK)": TS joins the make to the model number.
    for make in _words(manufacturer):
        if first.startswith(make) and first[len(make):] == catalog_first:
            return True
    # "4" against "POLESTAR 4 (004)", "H2" against "HUMMER H2": TecDoc puts the make first.
    if catalog_first in _words(manufacturer) and model_words[1:2] == [first]:
        return True
    # "3 Series" against "320": a series is named by the first digit of its codes.
    if value_words[1:2] == ["SERIES"] and len(first) == 1 and first.isdigit():
        return catalog_first.isdigit() and len(catalog_first) == 3 and catalog_first.startswith(first)
    return False


def same_model_text(left: str, right: str) -> bool:
    """True when two model texts are one query to the matcher ("Ibiza", "IBIZA")."""

    return _normalized_text(left) == _normalized_text(right)


_ALPHANUMERIC_RUN = re.compile(r"[A-Z0-9ÅÄÖÉÜ]+")


@lru_cache(maxsize=100_000)
def _decimal_points(value: str) -> frozenset[int]:
    """Where a decimal point falls in the padded text f" {_normalized_text(value)} ".

    Normalizing reads "2.0" and "1,6" as two words, "2 0" and "1 6"; these are the
    positions of the spaces that stand for the point, so the two stay one number.
    """

    text = unicodedata.normalize("NFKC", value).upper()
    runs = list(_ALPHANUMERIC_RUN.finditer(text))
    points: set[int] = set()
    space_after = 0
    for run, following in pairwise(runs):
        space_after += len(run.group()) + 1
        if (
            run.group().isdigit() and following.group().isdigit()
            and text[run.end():following.start()] in {".", ","}
        ):
            points.add(space_after)
    return frozenset(points)


def _reads_without_cutting_a_number(form: str, text: str, points: frozenset[int]) -> bool:
    """True when `form` occurs in the padded text keeping its numbers whole.

    "NISSAN QASHQAI 2.0 ACENT" names no "QASHQAI 2" (the +2): its 2 is the engine
    size 2.0. A form holding the whole number ("1 6 FSI") still reads it.
    """

    position = text.find(f" {form} ")
    while position != -1:
        if position not in points and position + len(form) + 1 not in points:
            return True
        position = text.find(f" {form} ", position + 1)
    return False


RecoveryReading = Literal["legacy", "model_word", "strict"]


def _label_forms(label: str) -> tuple[str, ...]:
    """A catalog label and its unspaced spelling when it mixes letters and digits.

    TecDoc writes "RAV 4" where the registry writes "RAV4"; an unspaced form is
    only offered where it still reads as one model word.
    """

    compact = label.replace(" ", "")
    if compact != label and len(compact) >= 3 and re.search(r"[A-Z]", compact) and re.search(r"[0-9]", compact):
        return (label, compact)
    return (label,)


#: Words that name a body style. TecDoc names some old models by one ("COUPE
#: (W111, W112)", "VARIANT II"), but in registry text they describe the car.
_BODY_WORDS = frozenset({
    "CABRIO", "CABRIOLET", "COMBI", "COMPACT", "CONVERTIBLE", "COUPE", "ESTATE", "HATCHBACK",
    "KOMBI", "LIMOUSINE", "MPV", "PICKUP", "ROADSTER", "SALOON", "SEDAN", "SPORTWAGON",
    "SPORTSWAGON", "SUV", "TARGA", "TOURER", "TOURING", "VAN", "VARIANT", "WAGON",
})


#: Body words TecDoc writes into a sibling KType's name ("OCTAVIA III Combi",
#: "A4 Avant", "307 Break"), by the body each one names. A word is dropped only
#: for a car registered with that body, so it says nothing the body field does
#: not. "GLC Coupe" (an SUV in TecDoc), "XC60 I SUV" and "COROLLA Compact" keep
#: their words: there the word tells the model apart, not the body.
_LABEL_BODY_WORDS: dict[str, str] = {
    **dict.fromkeys(
        ("AVANT", "BREAK", "COMBI", "ESTATE", "KOMBI", "SPORTSWAGON", "SPORTWAGON", "SW",
         "TOURER", "TOURING", "VARIANT", "WAGON"),
        "ESTATE",
    ),
    **dict.fromkeys(("LIMOUSINE", "SALOON", "SEDAN"), "SEDAN"),
    "HATCHBACK": "HATCHBACK",
    "COUPE": "COUPE",
    **dict.fromkeys(("CABRIO", "CABRIOLET", "CONVERTIBLE"), "CONVERTIBLE"),
    "MPV": "MULTI PURPOSE VEHICLE",
}


def _without_body_words(value: str, body: str) -> str:
    return " ".join(
        word for word in _normalized_text(value).split() if _LABEL_BODY_WORDS.get(word) != body
    )


#: Words that start the names of unrelated families ("GRAND C4 PICASSO", "GRAND
#: SANTA FE"): only a whole name that starts with one names a family.
_PREFIX_WORDS = frozenset({"GRAND", "NEW"})

_GENERATION_NUMERALS = frozenset({"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"})
#: A generation written as letters: Opel's "ASTRA J", VW/Audi's "PASSAT B8", "A6 C8".
_GENERATION_CODE = re.compile(r"[A-Z][0-9]?")
#: Body names in TecDoc model names, by the body each names, for model lines: the
#: label body words plus the makers' own estate names. Measured 2026-10-01 against
#: the catalog: each names an estate on (nearly) every KType carrying it. Names
#: that tell a door count or another car apart ("SPORTBACK", "SC", "GTC",
#: "ALLROAD", "CROSS COUNTRY") are not bodies here: months never choose between them.
_LINE_BODY_NAMES: dict[str, str] = {
    **_LABEL_BODY_WORDS,
    **dict.fromkeys(
        ("AERODECK", "GRANDTOUR", "SPORTSTOURER", "ST", "TRAVELLER", "TURNIER", "VARIABLE", "WEEKEND"),
        "ESTATE",
    ),
}
_LINE_BODY_WORDS = _BODY_WORDS | frozenset(_LINE_BODY_NAMES) | {"SPORTS"}
#: Body names of two words, neither word a body alone, by the body each names (""
#: for none in particular): Mercedes' estate is the "T-Model", Opel's Insignia B
#: liftback the "Grand Sport" ("PAJERO SPORT" stays its own line).
_LINE_BODY_PHRASES: dict[tuple[str, str], str] = {
    ("T", "MODEL"): "ESTATE",
    ("SHOOTING", "BRAKE"): "ESTATE",
    ("STATION", "WAGON"): "ESTATE",
    ("GRAND", "SPORT"): "",
}
#: First words after which a letter is the model itself (Tesla's "MODEL S", "MODEL Y").
_LETTER_NAMED_LINES = frozenset({"MODEL"})


#: Bodies a car is registered with that a body word in a KType name can contradict.
_REGISTERED_BODIES = frozenset(_LABEL_BODY_WORDS.values()) | {"SUV", "PICKUP", "VAN"}


@lru_cache(maxsize=100_000)
def _model_line(model: str, body: str = "") -> str:
    """A TecDoc model name without its chassis code, generation (numeral or code)
    and body names: the line within which a car's build month may choose a KType.
    "LEGACY IV Estate (BP)" and "LEGACY V Estate (BR)" are one line, as are "PASSAT
    B7 Variant" and "PASSAT B8 Variant", "ASTRA J Sports Tourer" and "ASTRA K
    Sports Tourer", or "INSIGNIA A (G09)" and "INSIGNIA B Grand Sport (Z18)";
    "PAJERO SPORT I" is not "PAJERO III", "IBIZA IV SC" not "IBIZA IV", "PASSAT
    ALLTRACK" not "PASSAT", and "MODEL S" not "MODEL Y".

    `body` is the car's registered body, normalized. A body name naming another
    body stays: for a registered SUV, "GLC Coupe (C253)" is not the line of "GLC
    (X253)", while for a registered coupe it is; for a registered estate, or a
    body registered only as closed, "E-CLASS T-Model (S210)" is that of "E-CLASS
    (W210)". The model's own first word is never a body name ("TRAVELLER Bus")."""

    contradicts = body in _REGISTERED_BODIES

    def names_the_body(named: str) -> bool:
        return not (contradicts and named and named != body)

    words: list[str] = []
    for word in _normalized_text(re.sub(r"\s*\([^()]*\)\s*$", "", model)).split():
        phrase = (words[-1], word) if len(words) > 1 else None
        if phrase in _LINE_BODY_PHRASES and names_the_body(_LINE_BODY_PHRASES[phrase]):
            words.pop()
        else:
            words.append(word)
    letters_name_models = bool(words) and words[0] in _LETTER_NAMED_LINES
    kept = [
        word for index, word in enumerate(words)
        if index == 0
        or not (
            (word in _LINE_BODY_WORDS and names_the_body(_LINE_BODY_NAMES.get(word, body)))
            or (
                (word in _GENERATION_NUMERALS or _GENERATION_CODE.fullmatch(word))
                and not letters_name_models
            )
        )
    ]
    return " ".join(kept or words)


def _is_model_name(label: str, canonical: str) -> bool:
    """True when the label is the catalog model's own name, not a trim or code on it.

    A name starts with the model's first word and keeps its other words in order,
    decoration left out: "GOLF VARIANT" names "GOLF VII Variant", "FOCUS" names
    "FOCUS I (DAW, DBW)". "200 T" on "123 T-Model (S123)" and the chassis code "ED"
    on "CEE'D SW (ED)" name nothing.
    """

    words = _normalized_text(canonical).split()
    parts = label.split()
    if not parts or not words or parts[0] != words[0]:
        return False
    rest = iter(words[1:])
    return all(any(part == word for word in rest) for part in parts[1:])


def _anchored_label_eligible(label: str) -> bool:
    """A short or all-digit catalog label is a model only in the model position.

    Anywhere else "911", "307" or "7X" could be a displacement, a door count or an
    approval number; as the word the registry names the model by, it is the model.
    """

    return " " not in label and (
        (label.isdigit() and len(label) >= 2) or re.fullmatch(r"[0-9][A-Z]", label) is not None
    )


def _evidence_field_rank(field_name: str) -> tuple[int, str]:
    """Rank an evidence field by specificity, most specific first.

    Fields outside the known order sort last but stay deterministic, so a
    caller passing an unlisted field never breaks recovery.
    """

    try:
        return (_MODEL_EVIDENCE_FIELD_PRIORITY.index(field_name), field_name)
    except ValueError:
        return (len(_MODEL_EVIDENCE_FIELD_PRIORITY), field_name)


# An apostrophe inside a word: Kia's "CEE'D", which the registry writes "CEED".
_APOSTROPHE_IN_WORD = re.compile(r"(?<=\w)['’](?=\w)")
# Letters of the registry's own (Swedish) alphabet: never an accented A or O.
_SWEDISH_LETTERS = frozenset("ÅÄÖåäö")


def registry_spelling(name: str) -> str:
    """A TecDoc model name as the registry spells it: no apostrophe, no accent.

    TecDoc writes "CEE'D (JD)", "SANTA FÉ III (DM, DMA)", "SCÉNIC IV (J9_)" and
    "MURCIÉLAGO"; the registry writes "CEED", "SANTA FE", "SCENIC" and
    "MURCIELAGO". Matching reads an apostrophe as a word break ("CEE D") and keeps
    É a letter of its own, so neither spelling ever meets the other exactly.

    Only catalog names are respelled: the aliases the catalog loaders add
    (`match_run_adapters.tecdoc_model_aliases`) and the canonical a shared label
    is checked against (`_unique_or_family`). The registry's text and the
    manufacturer names are compared as before, and Å, Ä and Ö are kept: they are
    letters of the registry's alphabet (Opel's "KAPITÄN" is no "KAPITAN").
    """

    joined = _APOSTROPHE_IN_WORD.sub("", unicodedata.normalize("NFC", name))
    return "".join(
        character
        if character in _SWEDISH_LETTERS
        else "".join(
            part for part in unicodedata.normalize("NFD", character) if not unicodedata.combining(part)
        )
        for character in joined
    )


def _names_family(label: str, canonical: str) -> bool:
    """True when a label is the family name a canonical model starts with.

    The canonical is read as written and in the registry's spelling: "CEED" is the
    family of "CEED (CD)" and of "CEE'D (JD)", "SANTA FE" of "SANTA FÉ III (DM, DMA)".
    """

    return any(
        name == label or name.startswith(f"{label} ")
        for name in (_normalized_text(canonical), _normalized_text(registry_spelling(canonical)))
    )


def _unique_or_family(group: set[tuple[str, str, str]]) -> tuple[str, str] | None:
    """One canonical model from equally strong labels, or the family they share.

    A named family may span catalog generations ("V70" on V70 II and V70 III):
    then the shared label is recovered as a query and no generation is chosen.
    Labels naming unrelated families recover nothing.
    """

    canonicals = {canonical for _, canonical, _ in group}
    if len(canonicals) == 1:
        canonical = next(iter(canonicals))
        return canonical, min((field for _, c, field in group if c == canonical), key=_evidence_field_rank)
    family_labels = {
        (label, field)
        for label, _, field in group
        if {canonical for other, canonical, _ in group if other == label} == canonicals
        and all(_names_family(label, canonical) for canonical in canonicals)
    }
    if len({label for label, _ in family_labels}) != 1:
        return None
    label = next(iter(family_labels))[0]
    return label, min((field for matched, field in family_labels if matched == label), key=_evidence_field_rank)


class ManufacturerCandidateIndex:
    """Immutable candidate index with conservative manufacturer fallback."""

    def __init__(self, candidates: Iterable[VehicleCandidate]) -> None:
        by_reference: dict[str, VehicleCandidate] = {}
        by_manufacturer_key: dict[str, dict[str, VehicleCandidate]] = {}
        model_labels_by_manufacturer_key: dict[str, dict[str, set[str]]] = {}
        families_by_manufacturer_key: dict[str, dict[str, set[str]]] = {}
        for candidate in candidates:
            reference = candidate.candidate_reference.strip()
            if reference in by_reference:
                raise ValueError(f"duplicate candidate_reference: {reference}")
            by_reference[reference] = candidate
            manufacturer_keys = {
                _normalized_text(candidate.manufacturer),
                *(_normalized_text(alias) for alias in candidate.manufacturer_aliases),
            }
            for manufacturer_key in manufacturer_keys:
                if manufacturer_key:
                    by_manufacturer_key.setdefault(manufacturer_key, {})[reference] = candidate
                    labels = model_labels_by_manufacturer_key.setdefault(manufacturer_key, {})
                    for value in (candidate.model, *candidate.model_aliases):
                        if normalized_model := _normalized_text(value):
                            labels.setdefault(normalized_model, set()).add(candidate.model)
                    if first_word := next(iter(_normalized_text(candidate.model).split()), None):
                        families_by_manufacturer_key.setdefault(manufacturer_key, {}).setdefault(
                            first_word, set()
                        ).add(candidate.model)
        self._all = tuple(sorted(by_reference.values(), key=lambda item: item.candidate_reference))
        self._engines = EngineCodeCatalog(self._all)
        powers: dict[tuple[str, str], set[int]] = {}
        for candidate in self._all:
            if candidate.power_kw is not None:
                powers.setdefault(
                    (_normalized_text(candidate.manufacturer), _normalized_text(candidate.model)), set()
                ).add(candidate.power_kw)
        self._model_powers = {key: frozenset(values) for key, values in powers.items()}
        self._by_manufacturer_key = {
            key: tuple(sorted(values.values(), key=lambda item: item.candidate_reference))
            for key, values in by_manufacturer_key.items()
        }
        # The word every catalog name of a model family starts with ("PASSAT" of
        # "PASSAT B5 (3B2)"): a model word even where no alias spells it alone.
        self._families_by_manufacturer_key = {
            key: {word: tuple(sorted(models)) for word, models in words.items()}
            for key, words in families_by_manufacturer_key.items()
        }
        self._model_labels_by_manufacturer_key = {
            key: tuple(
                (label, tuple(sorted(canonical_models)))
                for label, canonical_models in sorted(values.items())
            )
            for key, values in model_labels_by_manufacturer_key.items()
        }

    def knows_engine(self, code: str, manufacturer: str = "") -> bool:
        """True when any catalog KType carries this engine code or its family.

        `manufacturer` reads the code in that maker's reviewed spellings too
        (`EngineCodeCatalog.knows`).
        """

        return self._engines.knows(code, manufacturer)

    def only_power_near(self, candidate: VehicleCandidate, power_kw: int, share: float) -> bool:
        """True when this KType's power is the only figure of its model near `power_kw`.

        Near is within `share` of the KType's own figure. Two KTypes of one model
        a few kW apart are two drivetrains (a Tesla Model Y at 255 and at 258 kW),
        and a car between them is neither's by that alone.
        """

        if candidate.power_kw is None:
            return False
        key = (_normalized_text(candidate.manufacturer), _normalized_text(candidate.model))
        near = {
            power
            for power in self._model_powers.get(key, frozenset())
            if abs(power - power_kw) <= share * power
        }
        return near == {candidate.power_kw}

    def engine_relation(
        self, query: VehicleMatchQuery, candidate: VehicleCandidate
    ) -> EngineRelation:
        """How the car's engine code relates to one KType (`EngineCodeCatalog.relation`)."""

        return self._engines.relation(query, candidate)

    def recover_model_from_brand(self, manufacturer: str, brand: str) -> str | None:
        """Return one unique longest catalog model explicitly present in Brand text."""

        recovered = self.recover_model_from_evidence(manufacturer, {"brand": brand})
        return recovered[0] if recovered is not None else None

    def recover_model_from_evidence(
        self,
        manufacturer: str,
        evidence: Mapping[str, str],
        *,
        reading: RecoveryReading = "model_word",
    ) -> tuple[str, str] | None:
        """Return one unique longest catalog model and its non-sensitive source field.

        `reading` says how text is read:

        - "model_word": a catalog model's name at the word a field names the model
          by outranks any other label ("911 CARRERA 4 GTS" is a 911, not the longer
          "CARRERA GT"). Otherwise the longest label.
        - "strict": only a model's name counts. Used against a model inferred
          elsewhere, which a trim, body word or number does not overrule.
        - "legacy": the longest label anywhere, as read before model words. The
          fail-closed check between model field and brand text keeps using it.
        """

        manufacturer_key = _normalized_text(manufacturer)
        labels = self._model_labels_by_manufacturer_key.get(manufacturer_key, ())
        legacy, strict = reading == "legacy", reading == "strict"
        # Explicit model text must not lose to a longer label in another field.
        # An unrecognized explicit model is not permission to substitute a brand.
        if evidence.get("model", "").strip():
            evidence = {"model": evidence["model"]}
        # (tier, length, label, canonical, field). A label that is a catalog model's
        # name ("FOCUS" of "FOCUS I (DAW, DBW)", "911" of "911 (992)") read at the
        # model word is tier 2; a trim or code label there ("200 D", the chassis
        # code "ED") gets no priority. Elsewhere a name is tier 1, any other label 0.
        matches: set[tuple[int, int, str, str, str]] = set()
        for field_name, value in evidence.items():
            text = f" {_normalized_text(value)} "
            points = _decimal_points(value)
            anchor = None if legacy else model_position_token(field_name, value, manufacturer_key)
            anchored_text = None
            anchor_at = text.find(f" {anchor} ") if anchor is not None else -1
            if anchor is not None and anchor_at != -1:
                anchored_text = f" {text[anchor_at + 1:]}"
                # The model word counts even where no alias spells it alone:
                # "PASSAT" starts every "PASSAT B5 (3B2)". Not from a model field,
                # whose own text reaches the matcher anyway, and never a number
                # ("300" is an engine as often as the 1950s 300).
                if (
                    (strict or field_name == "brand")
                    and len(anchor) >= 3 and not anchor.isdigit()
                    and anchor not in _BODY_WORDS | _PREFIX_WORDS
                ):
                    family = self._families_by_manufacturer_key.get(manufacturer_key, {}).get(anchor, ())
                    # A model number after the word is part of the model: "ATTO 3"
                    # does not start "ATTO 2", whatever word they share.
                    following = anchored_text.split()[1:2]
                    # Nor does an engine size there: "QASHQAI 2.0" goes on with no number.
                    if following and anchor_at + len(anchor) + len(following[0]) + 2 in points:
                        following = []
                    matches.update(
                        (2, len(anchor), anchor, canonical, field_name)
                        for canonical in family
                        if not _numbers_differ(following, _normalized_text(canonical).split()[1:2])
                    )
            field_matches: list[tuple[int, int, str, str, str]] = []
            for label, canonical_models in labels:
                forms = (label,) if legacy else _label_forms(label)
                form = next(
                    (
                        form for form in forms
                        if f" {form} " in text
                        and (not points or _reads_without_cutting_a_number(form, text, points))
                    ),
                    None,
                )
                if form is None:
                    continue
                anchored = (
                    anchored_text is not None and anchored_text.startswith(f" {form} ")
                    and not {anchor_at, anchor_at + len(form) + 1} & points
                )
                eligible = _eligible_recovery_label(manufacturer_key, label, canonical_models, field_name, value)
                # A label that names some models only borrows the others' pairs
                # through a broader reviewed alias ("RANGE ROVER EVOQUE" on every
                # Range Rover): those pairs never compete with what it names.
                names_any = any(_is_model_name(label, canonical) for canonical in canonical_models)
                for canonical in canonical_models:
                    name = _is_model_name(label, canonical)
                    if not (eligible or (anchored and name and _anchored_label_eligible(label))):
                        continue
                    tier = 2 if anchored and name else 1 if name else 0
                    # A trim label that continues the model name at its position
                    # reads the same model word more precisely: "190 B" is the
                    # Ponton, not the 190 (W201) the name "190" alone would be.
                    if anchored and not names_any and label.split()[0] == anchor and " " in label:
                        tier = -1
                    elif names_any and not name and not legacy:
                        continue
                    field_matches.append((tier, len(label.replace(" ", "")), label, canonical, field_name))
            named_at_position = {label for tier, _, label, _, _ in field_matches if tier == 2}
            matches.update(
                (2 if tier == -1 and any(label.startswith(f"{named} ") for named in named_at_position)
                 else max(tier, 0),
                 length, label, canonical, field)
                for tier, length, label, canonical, field in field_matches
            )
        # Against a model inferred elsewhere only a model's name counts: a trim
        # word ("200 T", "1 6 FSI") is weaker evidence than the inference, and so
        # is a body word read anywhere but the model position ("S 600 COUPE" is no
        # 1960s COUPE (W111)). A number in the model position is too ambiguous to
        # overrule it ("300 TD" and "124" are an engine and a chassis number on
        # cars TS rightly calls E-Class), yet it still is the model word: nothing
        # later in the text is read instead ("911 CARRERA" is no CARRERA GT).
        if strict:
            at_position = {match for match in matches if match[0] == 2}
            if at_position:
                matches = {match for match in at_position if not match[2].replace(" ", "").isdigit()}
            else:
                matches = {
                    match for match in matches
                    if match[0] == 1 and not set(match[2].split()) <= _BODY_WORDS
                    and not match[2].replace(" ", "").isdigit()
                }
        if not matches:
            return None
        # Only the strongest kind of evidence present is read, and within it the
        # longest label: a model named at its position never yields to a label
        # elsewhere, and labels naming unrelated families recover nothing.
        top = max(tier if strict else int(tier == 2) for tier, *_ in matches)
        strongest = [match for match in matches if (match[0] if strict else int(match[0] == 2)) == top]
        longest = max(n for _, n, _, _, _ in strongest)
        recovered = _unique_or_family(
            {(label, canonical, field) for _, n, label, canonical, field in strongest if n == longest}
        )
        if recovered is None:
            return None
        # Position decides which model; the credit goes to the most specific field
        # that named that same model anywhere.
        model = recovered[0]
        fields = {field for _, _, label, canonical, field in matches if model in (label, canonical)}
        return model, min(fields, key=_evidence_field_rank)

    def lookup(
        self,
        manufacturer: str | None,
        *,
        similarity_threshold: float,
    ) -> tuple[tuple[VehicleCandidate, ...], MatchScope]:
        if manufacturer is None:
            return self._all, "global"
        manufacturer_key = _normalized_text(manufacturer)
        exact = self._by_manufacturer_key.get(manufacturer_key)
        if exact is not None:
            manufacturers = {_normalized_text(candidate.manufacturer) for candidate in exact}
            scope: MatchScope = "exact_manufacturer" if len(manufacturers) == 1 else "global"
            return exact, scope

        fuzzy_references: dict[str, VehicleCandidate] = {}
        for key, candidates in self._by_manufacturer_key.items():
            if _edit_similarity(manufacturer_key, key) >= similarity_threshold:
                fuzzy_references.update(
                    (candidate.candidate_reference, candidate) for candidate in candidates
                )
        if fuzzy_references:
            fuzzy = tuple(
                sorted(fuzzy_references.values(), key=lambda item: item.candidate_reference)
            )
            return fuzzy, "fuzzy_manufacturer"

        phonetic_references: dict[str, VehicleCandidate] = {}
        for key, candidates in self._by_manufacturer_key.items():
            if _edit_similarity(
                manufacturer_key, key
            ) >= similarity_threshold / 2 and has_phonetic_overlap(
                manufacturer_key,
                key,
                left_field="manufacturer",
                right_field="manufacturer_alias",
            ):
                phonetic_references.update(
                    (candidate.candidate_reference, candidate) for candidate in candidates
                )
        if phonetic_references:
            phonetic = tuple(
                sorted(phonetic_references.values(), key=lambda item: item.candidate_reference)
            )
            return phonetic, "phonetic_manufacturer"
        return self._all, "global"


@lru_cache(maxsize=100_000)
def _best_model_label(
    query_model: str, model: str, aliases: tuple[str, ...],
    edit_weight: float, token_weight: float, candidate_threshold: float,
) -> tuple[float, str]:
    """Reuse text-only work across KTypes; never cache technical evidence or decisions."""
    labels = tuple(sorted({
        normalized for value in (model, *aliases)
        if (normalized := _normalized_text(value))
    }))
    query_tokens = frozenset(query_model.split())
    label_scores = []
    for label in labels:
        edit_score = _edit_similarity(query_model, label)
        token_score = _token_similarity(query_model, label)
        if len(query_model.split()) == len(label.split()) == 1:
            text_score = edit_score
        else:
            text_score = edit_score * edit_weight + token_score * token_weight
        if query_tokens and query_tokens < frozenset(label.split()):
            text_score = max(text_score, candidate_threshold)
        label_scores.append((_bounded_score(text_score), label))
    return max(
        label_scores, key=lambda item: (item[0], -len(item[1].split()), -len(item[1]), item[1])
    )


class FuzzyVehicleMatcher:
    """Rank candidates without mutating or accepting canonical identity."""

    def __init__(
        self,
        index: ManufacturerCandidateIndex,
        config: FuzzyMatchConfig | None = None,
        *,
        fuel_compatible_pairs: frozenset[tuple[str, str]] = frozenset(),
        drive_compatible_pairs: frozenset[tuple[str, str]] = frozenset(),
        bodywork_compatible_pairs: frozenset[tuple[str, str]] = frozenset(),
        context_policy: ContextComparisonPolicy | None = None,
    ) -> None:
        self._index = index
        self._config = config or FuzzyMatchConfig()
        self._context_policy = context_policy or ContextComparisonPolicy()
        self._fuel_compatible_pairs = frozenset(
            (_normalized_text(left), _normalized_text(right))
            for left, right in fuel_compatible_pairs
            if _normalized_text(left) and _normalized_text(right)
        )
        # Same directional-pair shape as fuel: a global, reviewed structural
        # fact ("this term can't tell these two apart"), checked before the
        # per-manufacturer reviewed context policy rather than through it --
        # `ContextComparisonPolicy` is for scoped exceptions with evidence,
        # not a universal vocabulary fact true for every manufacturer.
        self._drive_compatible_pairs = frozenset(
            (_normalized_text(left), _normalized_text(right))
            for left, right in drive_compatible_pairs
            if _normalized_text(left) and _normalized_text(right)
        )
        # Same shape again: a reviewed global fact that one registry body term
        # is broader than a TecDoc body (TS "covered body" against a sedan).
        self._bodywork_compatible_pairs = frozenset(
            (_normalized_text(left), _normalized_text(right))
            for left, right in bodywork_compatible_pairs
            if _normalized_text(left) and _normalized_text(right)
        )

    def match(self, query: VehicleMatchQuery) -> FuzzyMatchResult:
        candidates, scope = self._index.lookup(
            query.manufacturer,
            similarity_threshold=self._config.manufacturer_scope_threshold,
        )
        # Score once with bodywork held neutral and no month demotion to find which
        # candidates are plausible at all, then decide whether bodywork
        # discriminates among exactly those. Judging discriminating power over the
        # whole manufacturer would always say "yes" -- every marque has several
        # body styles -- and would miss that one model family offers only one.
        baseline = [
            self._score(query, candidate, bodywork_discriminates=False, month_lines=frozenset())
            for candidate in candidates
        ]
        plausible = [
            (candidate, match)
            for candidate, match in zip(candidates, baseline, strict=True)
            if match.confidence >= self._config.candidate_threshold
        ]
        # A veteran's power says nothing only while it is no plausible KType's
        # figure (a Volvo Amazon at 55 kW beside KTypes of 49, 59, 63 and 66).
        # Where a KType the car could be does carry its power, exactly or within
        # rounding, the registry and TecDoc count alike for this car, and the
        # other KTypes contradict it as they always did: otherwise a sibling
        # 20 kW off that fits the year better ties with the KType the power names.
        veteran_power = not any(
            candidate.power_kw is not None
            and "power_kw" not in match.conflicting_fields
            and _VETERAN_POWER not in match.missing_fields
            for candidate, match in plausible
        )
        if not veteran_power and any(_VETERAN_POWER in match.missing_fields for _, match in plausible):
            plausible = [
                (
                    candidate,
                    self._score(
                        query, candidate, bodywork_discriminates=False, month_lines=frozenset(),
                        veteran_power=False,
                    )
                    if _VETERAN_POWER in match.missing_fields
                    else match,
                )
                for candidate, match in plausible
            ]
            plausible = [
                (candidate, match)
                for candidate, match in plausible
                if match.confidence >= self._config.candidate_threshold
            ]
        # Unit weight produces identical scores in both passes. Avoid repeated
        # scoring without changing bodywork conflicts or their normal penalty.
        needs_bodywork_rescore = bool(
            query.bodywork and self._config.bodywork_discriminating_weight != 1.0
        )
        distinct_bodyworks = {
            body
            for candidate, _ in plausible
            for body in _normalized_values(candidate.bodyworks)
        } if needs_bodywork_rescore else set()
        bodywork_discriminates = len(distinct_bodyworks) > 1
        # The model lines in which a candidate that fits the car (no conflicting
        # field) was produced in the car's build month. A KType built outside the
        # car's month is demoted when it belongs to another line than the one the
        # car's own text names (a Passat built 01/2015 is no Passat Alltrack, which
        # TecDoc starts later), and within the car's own line only when a fitting
        # KType of that line covers the month: months choose between the line's
        # generations and facelifts, but never send a car to another line whose run
        # happens to cover the month (a Pajero built a month before TecDoc's Pajero
        # III starts is no Pajero Sport, an Ibiza no 3-door Ibiza SC).
        car_body = _normalized_text(query.bodywork) if query.bodywork else ""
        month_lines = frozenset(
            _model_line(candidate.model, car_body)
            for candidate, match in plausible
            if query.build_month is not None
            and not match.conflicting_fields
            and (candidate.month_from is not None or candidate.month_to is not None)
            and not _built_outside_production_months(
                query.build_month, candidate, self._config.production_month_tolerance
            )
        )
        rescore_bodywork = bodywork_discriminates and needs_bodywork_rescore
        # A KType that conflicts with the car can never be its match, so its own
        # line earns it no shelter: outside the month it is demoted as before and
        # never rises above the KTypes that fit (an electric "MINI COOPER (J01)"
        # over the petrol MINI (F56) a "MINI COOPER S" built a month earlier is).
        scored = (
            [
                self._score(
                    query, candidate, bodywork_discriminates=rescore_bodywork,
                    month_lines=None if match.conflicting_fields else month_lines,
                    veteran_power=veteran_power,
                )
                for candidate, match in plausible
            ]
            if month_lines or rescore_bodywork
            else [match for _, match in plausible]
        )
        qualifying = tuple(
            sorted(
                (
                    candidate
                    for candidate in scored
                    if candidate.confidence >= self._config.candidate_threshold
                ),
                key=lambda candidate: (
                    -candidate.separation_score,
                    candidate.candidate_reference,
                ),
            )
        )
        ranked = qualifying[: self._config.max_candidates]
        if not ranked:
            return FuzzyMatchResult(scope, (), False, "no_candidate_above_threshold")
        top = ranked[0]
        if top.conflicting_fields:
            return FuzzyMatchResult(scope, ranked, False, "context_conflict_requires_review")
        if scope != "exact_manufacturer":
            return FuzzyMatchResult(scope, ranked, False, "manufacturer_scope_requires_review")
        if top.phonetic_match:
            return FuzzyMatchResult(scope, ranked, False, "phonetic_candidate_requires_review")
        if "model_partial" in top.matched_fields:
            return FuzzyMatchResult(scope, ranked, False, "partial_model_requires_review")
        if self._plug_in_power_lead(query, top, qualifying, (candidate for candidate, _ in plausible)):
            return FuzzyMatchResult(
                scope, ranked, False, "candidate_margin_not_met", guards=(PLUG_IN_POWER_GUARD,)
            )
        if top.confidence < self._config.automatic_threshold:
            return FuzzyMatchResult(scope, ranked, False, "automatic_threshold_not_met")
        if (
            len(qualifying) > 1
            and top.separation_score - qualifying[1].separation_score
            < self._config.automatic_margin
        ):
            return FuzzyMatchResult(scope, ranked, False, "candidate_margin_not_met")
        if "displacement_cc_rounding_unverified" in top.missing_fields and any(
            "displacement_cc" in rival.matched_fields
            and rival.conflicting_fields == ("engine_code",)
            for rival in qualifying[1:]
        ):
            # A 1-3 cc gap can be another engine (Jaguar XE: 1,999 cc is the GTDi,
            # 1,997 cc the Ingenium, and the registry writes one code for both).
            # A sibling with the car's exact cc that only the engine code holds
            # back is as likely the car's KType, so the two tie for the user.
            return FuzzyMatchResult(
                scope, ranked, False, "candidate_margin_not_met",
                guards=(DISPLACEMENT_ROUNDING_SIBLING_GUARD,),
            )
        if (
            {"power_kw_horsepower_unit_unverified", "displacement_cc_rounding_unverified"}
            & set(top.missing_fields)
        ) and not {"power_kw", "displacement_cc", *EXACT_ENGINE_RELATIONS} & set(top.matched_fields):
            # Tolerances stack: year adjacent, cc rounded and power 2 kW off still
            # reach the automatic threshold with no exact technical figure. A cc
            # that is only rounded or a power that is only an hp/PS gap needs one
            # exact field among power, cc and engine code beside it; otherwise the
            # car goes to review. The older +-2 kW power tolerance alone is left as
            # it was (35 cars of the 20k resolve on it; measured 2026-10-02). The
            # reason is the one routing sends to review whatever the confidence.
            return FuzzyMatchResult(
                scope, ranked, False, "candidate_margin_not_met",
                guards=(TOLERATED_EVIDENCE_GUARD,),
            )
        return FuzzyMatchResult(scope, ranked, True, "automatic_candidate_threshold_met")

    def _plug_in_power_lead(
        self,
        query: VehicleMatchQuery,
        top: FuzzyCandidateMatch,
        qualifying: tuple[FuzzyCandidateMatch, ...],
        candidates: Iterable[VehicleCandidate],
    ) -> bool:
        """True when only power separates the top from a plug-in sibling the car may be.

        The registry gives a hybrid's combustion-engine power, TecDoc a plug-in's
        system power. A GLC 300 e registered with petrol and electricity at 150 kW
        matches the mild-hybrid GLC 200 4MATIC (150 kW) exactly, while its own
        plug-in KType (230 kW) is only unverified: that 0.10 swing alone cleared the
        margin, and the car resolved to the mild hybrid. Exact power says nothing
        against a plug-in sibling, so with the swing taken out (`power_match_bonus`
        off the top, `power_tolerance_penalty` off the rival) the two must still be
        `automatic_margin` apart; otherwise they tie for review.

        It holds the top back only for a car with electricity and a combustion fuel
        that is not registered as a non-plug-in hybrid ("hybrid": ELHYBRID or a
        marketing word, whose exact power on a mild hybrid is right: 540i xDrive,
        XC60 B6), a top matched on exact power that TecDoc knows is no plug-in, and
        a rival TecDoc knows is one (a range extender counts) whose higher power is
        the unverified kind and that conflicts on nothing. Other evidence still
        counts: a plug-in with another engine code conflicts and never holds the top
        back, and an exact engine code a margin apart keeps the car resolved. A KType
        whose electrification is unknown is never the top or the rival here. A top
        already within the margin of its runner-up ties without it.
        """

        if query.electrification == "hybrid" or "power_kw" not in top.matched_fields:
            return False
        runner_up = qualifying[1].separation_score if len(qualifying) > 1 else None
        if runner_up is not None and top.separation_score - runner_up < self._config.automatic_margin:
            return False
        fuels = _normalized_values(query.fuels)
        if "ELECTRIC" not in fuels or not fuels & _COMBUSTION_FUELS:
            return False
        electrification = {
            candidate.candidate_reference: candidate.electrification for candidate in candidates
        }
        top_electrification = electrification.get(top.candidate_reference)
        if top_electrification is None or top_electrification in PLUG_IN_ELECTRIFICATION:
            return False
        lead = top.separation_score - self._config.power_match_bonus
        # A rival whose cc is only rounded (1,498 against the registered 1,495) may
        # be the same engine, so the top's exact cc is no evidence against it
        # either: that swing comes out as well.
        cc_swing = (
            self._config.displacement_match_bonus + self._config.displacement_tolerance_penalty
            if "displacement_cc" in top.matched_fields else 0.0
        )
        return any(
            not rival.conflicting_fields
            and "power_kw_hybrid_unverified" in rival.missing_fields
            and electrification.get(rival.candidate_reference) in PLUG_IN_ELECTRIFICATION
            and round(
                lead
                - (rival.separation_score + self._config.power_tolerance_penalty)
                - (cc_swing if "displacement_cc_rounding_unverified" in rival.missing_fields else 0.0),
                6,
            )
            < self._config.automatic_margin
            for rival in qualifying[1:]
        )

    def _score(
        self,
        query: VehicleMatchQuery,
        candidate: VehicleCandidate,
        *,
        bodywork_discriminates: bool = True,
        month_lines: frozenset[str] | None = None,
        veteran_power: bool = True,
    ) -> FuzzyCandidateMatch:
        query_model = _normalized_text(query.model)
        query_tokens = frozenset(query_model.split())
        # Cache keys include labels and scoring policy, not candidate IDs:
        # changed catalogs/configurations cannot reuse an incompatible score.
        text_score, matched_label = _best_model_label(
            query_model, candidate.model, candidate.model_aliases,
            self._config.edit_weight, self._config.token_weight,
            self._config.candidate_threshold,
        )
        # A registered estate reads "OCTAVIA"; its KType is "OCTAVIA III Combi"
        # while the hatchback sibling is plain "OCTAVIA III". Scored on the
        # decorated name the right KType loses on text more than the wrong one
        # loses on body, and the car goes to review. When the car's body is
        # exactly the KType's body, the body word is already accounted for.
        car_body = _normalized_text(query.bodywork) if query.bodywork else ""
        if (
            text_score < 1.0
            and car_body in _LABEL_BODY_WORDS.values()
            and car_body in _normalized_values(candidate.bodyworks)
        ):
            bare_labels = tuple(dict.fromkeys(
                bare for value in (candidate.model, *candidate.model_aliases)
                if (bare := _without_body_words(value, car_body))
            ))
            if bare_labels:
                bare_score, bare_label = _best_model_label(
                    query_model, bare_labels[0], bare_labels[1:],
                    self._config.edit_weight, self._config.token_weight,
                    self._config.candidate_threshold,
                )
                if bare_score > text_score:
                    text_score, matched_label = bare_score, bare_label

        context_effect = 0.0
        matched_fields: list[str] = ["model"]

        if query_tokens < frozenset(matched_label.split()):
            matched_fields.append("model_partial")
        missing_fields: list[str] = []
        conflicting_fields: list[str] = []
        phonetic_match = False

        if (
            text_score < 1.0
            and text_score >= self._config.phonetic_min_text_score
            and has_phonetic_overlap(
                query_model,
                matched_label,
                left_field="model",
                right_field="model_alias",
            )
        ):
            phonetic_match = True
            matched_fields.append("model_phonetic")
            context_effect += self._config.phonetic_match_bonus

        query_series = tuple(_DIGIT_GROUP.findall(query_model))
        # Strip only a source-marked, trailing TecDoc chassis/variant suffix.
        # Never remove commercial series digits (C 43 versus C 63).
        original_label = next(
            (value for value in (candidate.model, *candidate.model_aliases)
             if _normalized_text(value) == matched_label),
            matched_label,
        )
        series_label = re.sub(r"\s+\(\d{3}\.\d{3}\)\s*$", "", original_label)
        candidate_series = tuple(_DIGIT_GROUP.findall(_normalized_text(series_label)))
        if query_series and candidate_series and query_series != candidate_series:
            conflicting_fields.append("model_series")
            context_effect -= self._config.model_series_conflict_penalty

        if query.year is not None:
            years_outside = max(
                0,
                (candidate.year_from - query.year) if candidate.year_from is not None else 0,
                (query.year - candidate.year_to) if candidate.year_to is not None else 0,
            )
            if candidate.year_from is None and candidate.year_to is None:
                missing_fields.append("year")
            elif (
                years_outside == 0
                and _built_outside_production_months(
                    query.build_month, candidate, self._config.production_month_tolerance
                )
                and (
                    month_lines is None
                    or _model_line(candidate.model, car_body) != _model_line(query.model, car_body)
                    or _model_line(candidate.model, car_body) in month_lines
                )
            ):
                missing_fields.append("year_month_outside_unverified")
                context_effect -= self._config.production_month_penalty
            elif years_outside == 0:
                matched_fields.append("year")
                context_effect += self._config.year_match_bonus
            elif years_outside <= self._config.year_tolerance:
                # Just outside the run (Rekord 1985 against 1977-1984): the
                # counting differs, so it is unverified rather than a conflict.
                missing_fields.append("year_adjacent_unverified")
                context_effect -= self._config.year_tolerance_penalty
            else:
                conflicting_fields.append("year")
                context_effect -= self._config.year_conflict_penalty

        query_fuels = _normalized_values(query.fuels)
        candidate_fuels = _normalized_values(candidate.fuels)
        candidate_fuel_components = _normalized_values(candidate.fuel_components)
        if query_fuels:
            if not candidate_fuels and not candidate_fuel_components:
                missing_fields.append("fuels")
            elif _fuel_evidence_matches(query_fuels, candidate_fuels) and not (
                query_fuels & _VARIANT_FUELS and not candidate_fuels & query_fuels & _VARIANT_FUELS
            ):
                matched_fields.append("fuels")
                context_effect += self._config.fuel_match_bonus
            elif _fuel_evidence_matches(query_fuels, candidate_fuels):
                # The car is registered as a variant (flex-fuel, gas, hybrid);
                # this KType shares only its base fuel.
                missing_fields.append("fuels_variant_not_confirmed")
                context_effect -= self._config.fuel_variant_penalty
            elif any(
                (left, right) in self._fuel_compatible_pairs
                for left in query_fuels for right in candidate_fuels
            ):
                missing_fields.append("fuels_compatible_not_confirmed")
                if (candidate_fuels & _VARIANT_FUELS) - query_fuels:
                    # A variant KType for a car not registered as that variant.
                    context_effect -= self._config.fuel_variant_penalty
            elif _fuel_evidence_matches(query_fuels, candidate_fuel_components) or any(
                (left, right) in self._fuel_compatible_pairs
                for left in query_fuels for right in candidate_fuel_components
            ):
                # Engine fuel components describe a possible capability set.
                # Containment avoids a false conflict but is not an exact
                # vehicle-fuel observation and therefore adds no score.
                missing_fields.append("fuels_compatible_not_confirmed")
            else:
                conflicting_fields.append("fuels")
                context_effect -= self._config.fuel_conflict_penalty

        if query.engine_code and _normalized_code(query.engine_code):
            # Every engine-code rule lives in `EngineCodeCatalog.relation`. The
            # field names the relation: only a plain "engine_code" says which
            # engine the car has; a reviewed alias or a list of codes scores
            # the same but never settles a candidate-only KType.
            engine_relation = self._index.engine_relation(query, candidate)
            if engine_relation == "missing":
                missing_fields.append("engine_code")
            elif engine_relation in EXACT_ENGINE_RELATIONS:
                matched_fields.append(engine_relation)
                context_effect += self._config.engine_match_bonus
            elif engine_relation == "engine_code_family":
                matched_fields.append("engine_code_family")
                context_effect += self._config.engine_family_match_bonus
            elif engine_relation == "engine_code_unverified":
                missing_fields.append("engine_code_unverified")
            else:
                conflicting_fields.append("engine_code")
                context_effect -= self._config.engine_conflict_penalty

        if query.displacement_cc is not None:
            if candidate.displacement_cc is None:
                missing_fields.append("displacement_cc")
            elif query.displacement_cc == candidate.displacement_cc:
                matched_fields.append("displacement_cc")
                context_effect += self._config.displacement_match_bonus
            elif (
                abs(query.displacement_cc - candidate.displacement_cc)
                <= self._config.displacement_tolerance_cc
            ):
                # The same engine rounded differently (2,400 against 2,401 cc):
                # unverified, neither a match nor a contradiction. The mild
                # penalty keeps a sibling with the car's exact cc clear of the
                # automatic margin instead of tying with it.
                missing_fields.append("displacement_cc_rounding_unverified")
                context_effect -= self._config.displacement_tolerance_penalty
            elif (
                query.displacement_cc == 2 * candidate.displacement_cc
                and _is_mazda_rotary(candidate)
            ):
                # The registry counts a Wankel's chamber volume twice (an RX-8's
                # 1,308 cc is registered as 2,616, an MX-30 R-EV's 830 as
                # 1,660). Exactly double on a Mazda
                # rotary KType is the same figure, so it matches; any other
                # maker, engine or ratio stays a conflict.
                matched_fields.append("displacement_cc")
                context_effect += self._config.displacement_match_bonus
            else:
                conflicting_fields.append("displacement_cc")
                context_effect -= self._config.displacement_conflict_penalty

        if query.power_kw is not None:
            if candidate.power_kw is None:
                missing_fields.append("power_kw")
            elif query.power_kw == candidate.power_kw:
                matched_fields.append("power_kw")
                context_effect += self._config.power_match_bonus
            elif (
                self._config.reviewed_power_equivalences
                and query_fuels == candidate_fuels == _ELECTRIC_ONLY
                and reviewed_power_equivalent(
                    candidate.manufacturer, candidate.model, query.power_kw
                ) == candidate.power_kw
            ):
                # One electric drivetrain under its two figures (rated in the
                # registry, peak in TecDoc): a reviewed pair, so the same power.
                matched_fields.append("power_kw")
                context_effect += self._config.power_match_bonus
            elif abs(query.power_kw - candidate.power_kw) <= self._config.power_tolerance_kw:
                # Within rounding noise: unverified, so neither a match nor a
                # contradiction. The mild penalty keeps an exactly matching
                # k-type clear of the automatic margin instead of tying with it.
                missing_fields.append("power_kw")
                context_effect -= self._config.power_tolerance_penalty
            elif query_fuels == candidate_fuels == _ELECTRIC_ONLY and self._index.only_power_near(
                candidate, query.power_kw, self._config.electric_power_tolerance
            ):
                # An electric drivetrain quoted a few kW apart by the two sources,
                # and no other KType of the model near the car's figure.
                missing_fields.append("power_kw_electric_unverified")
                context_effect -= self._config.power_tolerance_penalty
            elif (
                query.power_kw < candidate.power_kw
                and _is_hybrid(query_fuels | candidate_fuels)
            ):
                # The registry gives a hybrid's combustion-engine power, TecDoc
                # the combined system power, which is always higher. The two
                # cannot be compared until the catalog carries engine power, so
                # a lower figure is unverified. A higher one still contradicts.
                # The mild penalty keeps a KType whose power the car matches
                # exactly clear of the margin (Corolla 72 kW against 72/103).
                missing_fields.append("power_kw_hybrid_unverified")
                context_effect -= self._config.power_tolerance_penalty
            elif (
                _normalized_text(candidate.manufacturer) in HORSEPOWER_UNIT_MAKERS
                and not any(
                    fuel.startswith(("ELECTRIC", "HYBRID"))
                    for fuel in query_fuels | candidate_fuels
                )
                and _horsepower_unit_gap(
                    query.power_kw, candidate.power_kw, self._config.horsepower_unit_slack_kw
                )
            ):
                # A US-spec figure converted once as hp and once as PS (a Mustang
                # at 221 kW against TecDoc's 224): unverified, with the penalty
                # that keeps an exact-power sibling clear of the margin. Only for
                # the reviewed US makers and never with electricity, where a
                # 1.4% gap separates real variants.
                missing_fields.append("power_kw_horsepower_unit_unverified")
                context_effect -= self._config.power_tolerance_penalty
            elif (
                veteran_power
                and query.year is not None
                and query.year < self._config.veteran_power_before_year
            ):
                # Horsepower counted by another standard: the registry's figure
                # for a car this old is none of TecDoc's, so it says nothing.
                # `match` takes this back where a candidate does carry the figure.
                missing_fields.append(_VETERAN_POWER)
                context_effect -= self._config.power_tolerance_penalty
            else:
                conflicting_fields.append("power_kw")
                context_effect -= self._config.power_conflict_penalty

        query_drive = _normalized_text(query.drive_type) if query.drive_type else ""
        candidate_drive = _normalized_text(candidate.drive_type) if candidate.drive_type else ""
        if query_drive and candidate_drive and (
            (query_drive, candidate_drive) in self._drive_compatible_pairs
        ):
            # A reviewed global fact (e.g. TS's undifferentiated `2wd` next to
            # TecDoc's `fwd`/`rwd`) settles this before any per-manufacturer
            # policy lookup runs -- it is true everywhere, not a scoped
            # exception, so it must never fall through to that policy's
            # unscoped default of "conflicting".
            missing_fields.append("drive_type_compatible_not_confirmed")
            # No per-manufacturer context rule fired -- this came from the
            # global vocabulary alignment instead -- but `drive_comparison`
            # is still read unconditionally below, alongside `body_comparison`.
            drive_comparison = ContextComparison(state="compatible")
        else:
            drive_comparison = self._context_policy.compare(
                field="drive_type", source_value=query_drive,
                candidate_values=frozenset({candidate_drive}) if candidate_drive else frozenset(),
                manufacturer=candidate.manufacturer, model=candidate.model,
                source_evidence=query.source_context,
            )
            if query_drive or drive_comparison.rule_ids:
                if drive_comparison.state == "unknown":
                    missing_fields.append("drive_type")
                elif drive_comparison.state == "equivalent":
                    matched_fields.append("drive_type")
                    context_effect += self._config.drive_match_bonus
                elif drive_comparison.state == "compatible":
                    missing_fields.append("drive_type_compatible_not_confirmed")
                else:
                    conflicting_fields.append("drive_type")
                    context_effect -= self._config.drive_conflict_penalty

        query_bodywork = _normalized_text(query.bodywork) if query.bodywork else ""
        candidate_bodyworks = _normalized_values(candidate.bodyworks)
        # A bodywork conflict is always recorded, whatever the candidate set
        # looks like: suppressing it would let a disagreeing candidate resolve.
        # Only the ranking weight varies. When the surviving candidates differ
        # in bodywork the field is the discriminator between siblings -- a
        # PASSAT B7 and a PASSAT B7 Variant are separated by nothing else -- so
        # it must be able to outrank the better text score the undecorated name
        # gets. When they share one body it decides nothing and keeps its
        # ordinary weight.
        weight = self._config.bodywork_discriminating_weight if bodywork_discriminates else 1.0
        if (
            query_bodywork
            and query_bodywork not in candidate_bodyworks
            and any(
                (query_bodywork, body) in self._bodywork_compatible_pairs
                for body in candidate_bodyworks
            )
        ):
            # Only keeps a broader registry term from reading as a contradiction.
            # The mild penalty keeps an exactly matching sibling clear of the
            # margin (an MPV KType against a van KType for a car registered MPV).
            body_comparison = ContextComparison(state="compatible")
            context_effect -= self._config.power_tolerance_penalty
        else:
            body_comparison = self._context_policy.compare(
                field="bodywork", source_value=query_bodywork,
                candidate_values=candidate_bodyworks,
                manufacturer=candidate.manufacturer, model=candidate.model,
                source_evidence=query.source_context,
            )
        if query_bodywork or body_comparison.rule_ids:
            if body_comparison.state == "unknown":
                missing_fields.append("bodywork")
            elif body_comparison.state == "equivalent":
                matched_fields.append("bodywork")
                context_effect += self._config.bodywork_match_bonus * weight
            elif body_comparison.state == "compatible":
                missing_fields.append("bodywork_compatible_not_confirmed")
            else:
                conflicting_fields.append("bodywork")
                context_effect -= self._config.bodywork_conflict_penalty * weight

        return FuzzyCandidateMatch(
            candidate_reference=candidate.candidate_reference,
            candidate_type=candidate.candidate_type,
            manufacturer=candidate.manufacturer,
            model=candidate.model,
            confidence=_bounded_score(text_score + context_effect),
            text_score=text_score,
            context_effect=round(context_effect, 6),
            matched_label=matched_label,
            matched_fields=tuple(matched_fields),
            missing_fields=tuple(missing_fields),
            conflicting_fields=tuple(conflicting_fields),
            phonetic_match=phonetic_match,
            context_rule_ids=tuple(sorted(set(
                drive_comparison.rule_ids + body_comparison.rule_ids
            ))),
            context_policy_digest=(
                self._context_policy.content_digest
                if drive_comparison.rule_ids or body_comparison.rule_ids else None
            ),
        )
