"""Keep a learned model from contradicting the car it would fill.

The model rules (`vehicle_core_rules`) learn from vehicles TS gave a model. TS
normalization never named some models -- Bora, Jetta, Sharan, Sintra, Carens -- so
a key those cars share with a sibling (a VIN prefix, a type code) was learned from
the sibling alone and fills the sibling's model: every "VW BORA 1,6" became a Golf.
The holdout check cannot see this, since it only holds vehicles with a model.

The car's own registry text can. A fill is refused when the text's model word --
the registry model field, or the word after the make -- names a catalog model of
another family, read with the matcher's reader: "VW BORA 1,6" names BORA I, not a
Golf. A trim word elsewhere ("200 T", "1 6 FSI") is weaker evidence than the rule
and never refuses it. Unlike the matcher, the guard falls back to the brand text's
model word when the model field names nothing the catalog knows ("SL", the
Sportage's generation code): "KIA SPORTAGE 2,0 CRDI EX" is no Sorento, whatever its
variant code taught a rule.

Some texts name a family only in TS's own words, which the catalog does not spell:
the make glued to a number ("MAZDA2" is a 2, not the CX-3 its type code DJ1 taught),
a reviewed generation code or abbreviation ("SL" is a Sportage, "FIAT DOBL" a
Doblo), a reviewed name ("CC" is the VW CC, not a Passat). With the vocabulary the
model families learn by (`vehicle_core_rules.model_vocabulary`), the model field and
the brand text are read as `MOD-MT` and `MOD-BRT` read them, their sibling check
included: a reading the TS-named cars with the same text contradict is none, so
"RENAULT B" is the Clio TS states for all 60 such cars, not the "B" it misread on
five others. A fill is refused when a text names a family and no text names the
fill's. When the model field names the fill in TS's words, no catalog reading
refuses it: brand "JAGUAR XK8" with model "XKR CONVERTIBLE" is an XKR, though TecDoc
files the XKR under the XK 8. A model field that is exactly one family's name
("EX30") refuses a narrower sibling ("EX30 Cross Country") no text names.

The rule's key weighs as well. A VIN-descriptor rule that every TS-named car under
its key agreed with tells a car from its siblings better than one car's text in TS's
words, for a car of the years the key was learned from: a misspelt "LEXUS LS250"
under the descriptor of 446 TS-named IS is an IS, a "GRANTURISMO SPORT" convertible
under the GranCabrio's descriptor a GranCabrio. It holds only for a family TS states
elsewhere: by its absence under the key, a reviewed name TS never uses ("CC") says
nothing. Such a key also lets a narrower sibling stand (an EX30 Cross Country under
the Cross Country's descriptor, registered "EX30").

Reviewed checks need no catalog: a motorhome converter in the text ("MERCEDES-
BENZ RAPIDO") keeps only the van families it builds on, a car TS has no family for
("RENAULT 4 CV") fits no fill, a reviewed non-family (Renault "B") is no fill, and
a Volvo text naming both a 940 and a 960 type code ("944-964") names neither: the
engine says which it is.

The build year is no general check here: the catalog lacks many eras of models TS
names rightly (a 1969 Pontiac GTO, a 1988 Mercedes-Benz 250 D that TecDoc calls
E-Class only from 1993), so a year gap says more about the catalog than about the
fill. Rules whose key meant another car in another era get a reviewed era of their
own, and so do families whose name an older or newer car's text reads the same
(`vehicle_core_rules.REVIEWED_RULE_ERAS`, `REVIEWED_FAMILY_ERAS`); a text read as
such a family for a car built outside its years names none ("FORD GALAXIE" of 2009
is a misspelt Galaxy).

Families are compared by name, tolerant of how TS and TecDoc spell them: "3 Series"
is the family of "3 (E46)", "Ceed" of "CEE'D (ED)", "Mazda3" of "3 (BK)". Names TS
files together count as one (iX1 and X1), and so do names waiting for the data
owner's decision (`vehicle_model_patterns.NAMING_ON_HOLD`).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from psycopg import Connection

from ingestion.active_rules import load_active_rules
from ingestion.fuzzy_matching import same_model_family
from ingestion.tecdoc.match_run_adapters import (
    TecDocDryRunEvaluator,
    load_postgres_ktype_catalog,
    reviewed_export_names,
)
from ingestion.tecdoc.model_aliases import ReviewedModelAliasIndex
from ingestion.vehicle_core_rules import (
    ERA_SLACK,
    FAMILIES_BY_ID,
    make_words,
    model_vocabulary,
    outside_family_era,
    stated_text_families,
    tolerated_disagreements,
    unanimous_vin_rule_eras,
)
from ingestion.vehicle_model_patterns import (
    MISREAD_STATED_FAMILIES,
    REVIEWED_NON_FAMILIES,
    converter_names_another_family,
    engine_names_another_family,
    fuel_names_another_family,
    model_text_family,
    names_only,
    narrower_family,
    other_car_named,
    same_family_names,
    tolerated_names,
)


@dataclass(frozen=True)
class Verdict:
    """Why a model does not fit a car, or None when nothing contradicts it."""

    reason: str
    detail: str


#: The registry fields read for the family TS would give them, model field first,
#: with the pattern family that reads each (whose sibling check a reading passes).
_READER_OF_FIELD = {"model": "MOD-MT", "brand": "MOD-BRT"}
_TS_WORDS = "text_names_another_family"


@dataclass(frozen=True)
class _Judged:
    """A fill judged by the car's texts, before its build year and the rule's key
    weigh in (`ModelGuard._weigh`)."""

    verdict: Verdict | None
    #: The model field names only a broader family ("EX30" for an EX30 Cross Country).
    narrower: Verdict | None = None
    #: The families the texts name in TS's words, model field first.
    families: tuple[str, ...] = ()


class ModelGuard:
    """Judges a learned model against the model word in the car's own text."""

    def __init__(
        self,
        evaluator: TecDocDryRunEvaluator,
        vocabulary: Mapping[str, Collection[str]] | None = None,
        words_by_make: Mapping[str, Collection[str]] | None = None,
        *,
        stated_families: Mapping[str, Collection[str]] | None = None,
        stated_texts: Mapping[tuple[str, str, str], Mapping[str, int]] | None = None,
        vin_rule_eras: Mapping[str, tuple[int, int]] | None = None,
    ) -> None:
        self._evaluator = evaluator
        self._vocabulary = vocabulary or {}
        self._words_by_make = words_by_make or {}
        self._stated_families = stated_families or {}
        self._stated_texts = stated_texts or {}
        self._vin_rule_eras = vin_rule_eras or {}
        self._cache: dict[tuple[Any, ...], _Judged] = {}
        self._text_families: dict[tuple[str, str, str], str | None] = {}

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
    ) -> Verdict | None:
        if manufacturer:
            if model_family in REVIEWED_NON_FAMILIES.get(manufacturer, ()):
                return Verdict("not_a_family", model_family)
            # An electric car is not the combustion family of a maker that names its
            # electric version apart (a Mustang Mach-E is no Mustang).
            if other := fuel_names_another_family(manufacturer, model_family, fuel):
                return Verdict("fuel_names_another_model", other)
            texts = [str(evidence[field]) for field in _READER_OF_FIELD if evidence.get(field)]
            if other := engine_names_another_family(manufacturer, model_family, texts, engine_code):
                return Verdict("engine_names_another_model", other)
        texts_key = tuple(sorted((field, str(value)) for field, value in evidence.items() if value))
        key = (manufacturer, model_family, texts_key)
        if key not in self._cache:
            self._cache[key] = self._judge(manufacturer, model_family, dict(texts_key))
        return self._weigh(self._cache[key], manufacturer, year, rule_id)

    def _judge(
        self, manufacturer: str | None, model_family: str, evidence: Mapping[str, str]
    ) -> _Judged:
        if not manufacturer:
            return _Judged(None)
        texts = {field: evidence[field] for field in _READER_OF_FIELD if evidence.get(field)}
        if converter := converter_names_another_family(manufacturer, model_family, texts.values()):
            return _Judged(Verdict("text_names_a_converter", converter))
        if car := other_car_named(manufacturer, texts.values()):
            return _Judged(Verdict("text_names_another_car", car))
        families = {
            field: family
            for field, text in texts.items()
            if (family := self._text_family(manufacturer, field, text))
        }
        narrower = self._narrower(manufacturer, model_family, texts, families)
        # The model field names the fill in TS's words: no catalog reading overrules it.
        named = families.get("model")
        if named is not None and _fits(model_family, named, manufacturer):
            return _Judged(None, narrower)
        read = self._catalog_model(manufacturer, evidence)
        if read is not None:
            if _same_family(model_family, read, manufacturer):
                return _Judged(None, narrower)
            # "CHRYSLER GR VOYAGER" abbreviates Grand: read without the abbreviation
            # it names the Voyager. The Grand Voyager stands only if the spelled-out
            # text names it too.
            grand = {field: _GR.sub("GRAND", value) for field, value in evidence.items()}
            if grand != dict(evidence):
                spelled = self._catalog_model(manufacturer, grand)
                if spelled is not None and _same_family(model_family, spelled, manufacturer):
                    return _Judged(None, narrower)
            return _Judged(Verdict("text_names_another_model", read[1]), narrower)
        # The catalog reads nothing: the texts in TS's own words ("MAZDA2", "SL").
        read_families = tuple(families.values())
        if read_families and not any(_fits(model_family, f, manufacturer) for f in read_families):
            return _Judged(Verdict(_TS_WORDS, read_families[0]), narrower, read_families)
        return _Judged(None, narrower)

    def _weigh(
        self, judged: _Judged, manufacturer: str | None, year: int | None, rule_id: str | None
    ) -> Verdict | None:
        """The texts' verdict once the car's build year and the rule's key are weighed:
        a reading outside its family's reviewed years names no family, and a unanimous
        VIN-descriptor rule outranks a reading of a family TS states elsewhere."""

        key_tells_apart = self._key_tells_apart(rule_id, year)
        if judged.narrower is not None and not key_tells_apart:
            return judged.narrower
        if judged.verdict is None or judged.verdict.reason != _TS_WORDS:
            return judged.verdict
        families = [f for f in judged.families if not outside_family_era(manufacturer, f, year)]
        stated = self._stated_families.get(manufacturer or "", ())
        if not families or (key_tells_apart and all(family in stated for family in families)):
            return None
        return Verdict(_TS_WORDS, families[0])

    def _key_tells_apart(self, rule_id: str | None, year: int | None) -> bool:
        era = self._vin_rule_eras.get(rule_id) if rule_id else None
        if era is None or year is None:
            return False
        return era[0] - ERA_SLACK <= year <= era[1] + ERA_SLACK

    def _narrower(
        self,
        manufacturer: str,
        model_family: str,
        texts: Mapping[str, str],
        families: Mapping[str, str],
    ) -> Verdict | None:
        """The model field is exactly one family's name ("EX30"): a narrower sibling
        ("EX30 Cross Country") is not that car unless another text names it."""

        named = families.get("model")
        if (
            named is None
            or not names_only(manufacturer, texts["model"], named)
            or not narrower_family(model_family, named)
            or any(_names_the_fill(model_family, name, manufacturer) for name in families.values())
        ):
            return None
        return Verdict("model_text_names_another_family", named)

    def _catalog_model(
        self, manufacturer: str, evidence: Mapping[str, str]
    ) -> tuple[str, str] | None:
        """The catalog model the text names as the matcher reads it; when the model
        field names nothing the catalog knows, the brand text's model word."""

        read = self._evaluator.source_text_model(manufacturer, evidence)
        model, brand = evidence.get("model"), evidence.get("brand")
        if (
            read is None
            and model
            and brand
            and self._evaluator.source_text_model(manufacturer, {"model": model}) is None
        ):
            read = self._evaluator.source_text_model(manufacturer, {"brand": brand})
        return read

    def _text_family(self, manufacturer: str, field: str, text: str) -> str | None:
        """The TS family a registry text names, read as the model text families read
        it: a reading the TS-named cars with the same text contradict is none."""

        vocabulary = self._vocabulary.get(manufacturer)
        if not vocabulary:
            return None
        key = (manufacturer, field, text)
        if key not in self._text_families:
            family = model_text_family(
                manufacturer, text, vocabulary, make_words=self._words_by_make.get(manufacturer, ())
            )
            if family is not None and self._siblings_disagree(manufacturer, field, text, family):
                family = None
            self._text_families[key] = family
        return self._text_families[key]

    def _siblings_disagree(self, manufacturer: str, field: str, text: str, family: str) -> bool:
        """`pattern_rules`' check: more TS-named cars with this very text name another
        family than a reading tolerates."""

        stated = self._stated_texts.get((manufacturer, field, text.strip().upper()))
        if not stated:
            return False
        misread = MISREAD_STATED_FAMILIES.get(manufacturer, {}).get(family, ())
        agreeing = sum(
            count
            for name, count in stated.items()
            if name in misread or _same_ts_family(family, name, manufacturer)
        )
        total = sum(stated.values())
        min_agreement = FAMILIES_BY_ID[_READER_OF_FIELD[field]].min_agreement
        return total - agreeing > tolerated_disagreements(total, min_agreement)


def _same_family(model_family: str, read: tuple[str, str], manufacturer: str) -> bool:
    """A TS family names the catalog model the text reads, under its TecDoc name or
    a name the family is sold under elsewhere (Atto 3 for TecDoc's YUAN PLUS)."""

    catalog_manufacturer, model = read
    return any(
        same_model_family(family, name, catalog_manufacturer)
        for family in tolerated_names(manufacturer, model_family)
        for name in (model, *reviewed_export_names(catalog_manufacturer, model))
    )


def _spelled_alike(family: str, other: str, manufacturer: str) -> bool:
    """Either name spelled the other's way ("Mazda3", "3")."""

    return same_model_family(family, other, manufacturer) or same_model_family(
        other, family, manufacturer
    )


def _same_ts_family(family: str, other: str, manufacturer: str) -> bool:
    """Two TS families name one family: spelled alike, or filed together (iX1, X1)."""

    names = same_family_names(manufacturer, family)
    return any(_spelled_alike(name, other, manufacturer) for name in names)


def _fits(model_family: str, family: str, manufacturer: str) -> bool:
    """A text naming `family` does not contradict a fill of `model_family`: one TS
    family, or the other name of a naming question on hold (Caravelle for Multivan)."""

    names = tolerated_names(manufacturer, model_family)
    return any(_spelled_alike(name, family, manufacturer) for name in names)


def _names_the_fill(model_family: str, family: str, manufacturer: str) -> bool:
    """A text names the fill itself, not only a broader family it narrows."""

    return _fits(model_family, family, manufacturer) and not narrower_family(model_family, family)


_GR = re.compile(r"(?<![A-Z0-9])GR\.?(?![A-Z0-9])", re.IGNORECASE)


def build_model_guard(connection: Connection, batch_id: str) -> ModelGuard:
    """The guard over one pinned catalog batch, with the matcher's own reading rules,
    the vocabulary the model families learn by and what TS states itself."""

    catalog = load_postgres_ktype_catalog(connection, batch_id=batch_id)
    rule_set, manufacturer_rules = load_active_rules(connection)
    return ModelGuard(
        TecDocDryRunEvaluator(catalog, manufacturer_rules, ReviewedModelAliasIndex(rule_set)),
        model_vocabulary(connection),
        make_words(connection),
        stated_families=model_vocabulary(connection, reviewed=False),
        stated_texts=stated_text_families(connection),
        vin_rule_eras=unanimous_vin_rule_eras(connection),
    )


def count_verdicts(verdicts: Iterable[Verdict | None]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for verdict in verdicts:
        if verdict is not None:
            counts[verdict.reason] = counts.get(verdict.reason, 0) + 1
    return counts
