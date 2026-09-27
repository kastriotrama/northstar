"""Deterministic, manufacturer-scoped fuzzy candidate generation."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

from ingestion.context_comparison import ContextComparison, ContextComparisonPolicy
from ingestion.phonetic_matching import PHONETIC_VERSION, has_phonetic_overlap

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


@lru_cache(maxsize=250_000)
def engine_code_forms(value: str) -> frozenset[str]:
    """Forms under which one engine code is the same engine, compared compactly.

    TecDoc writes a manufacturer code with its alternative in brackets
    (`BHZ (DV6FC)`); either part, or the whole, names that engine.
    """

    text = unicodedata.normalize("NFKC", value).upper()
    parts = {text, re.sub(r"\([^()]*\)", " ", text), *_ENGINE_BRACKET.findall(text)}
    return frozenset(form for part in parts if (form := _normalized_code(part)))


@lru_cache(maxsize=250_000)
def engine_code_family(value: str) -> str | None:
    """The engine family a code's first token names: `K9K 276` and `K9K-H` -> K9K.

    A family is compatible only with a code that names the family alone, never
    with another variant of it.

    None when the code has no separate head, or the head is too short to name
    an engine on its own (`M 177.980`, `OM 651`).
    """

    text = re.sub(r"\([^()]*\)", " ", unicodedata.normalize("NFKC", value).upper()).strip()
    tokens = [token for token in _ENGINE_HEAD_SPLIT.split(text) if token]
    if len(tokens) < 2:
        return None
    head = _normalized_code(tokens[0])
    return head if len(head) >= 3 and not head.isdigit() else None


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
    fuel_match_bonus: float = 0.05
    fuel_conflict_penalty: float = 0.15
    engine_match_bonus: float = 0.12
    # Same engine family, different variant suffix (`K9K 276` against `K9K`):
    # compatible, but weaker than the exact engine.
    engine_family_match_bonus: float = 0.06
    engine_conflict_penalty: float = 0.25
    displacement_match_bonus: float = 0.05
    displacement_conflict_penalty: float = 0.25
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
        effects = (
            self.model_series_conflict_penalty,
            self.phonetic_match_bonus,
            self.year_match_bonus,
            self.year_conflict_penalty,
            self.fuel_match_bonus,
            self.fuel_conflict_penalty,
            self.engine_match_bonus,
            self.engine_family_match_bonus,
            self.engine_conflict_penalty,
            self.displacement_match_bonus,
            self.displacement_conflict_penalty,
            self.power_match_bonus,
            self.power_conflict_penalty,
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
    fuels: frozenset[str] = field(default_factory=frozenset)
    fuel_components: frozenset[str] = field(default_factory=frozenset)
    engine_codes: frozenset[str] = field(default_factory=frozenset)
    displacement_cc: int | None = None
    power_kw: int | None = None
    drive_type: str | None = None
    bodyworks: frozenset[str] = field(default_factory=frozenset)

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
    fuels: frozenset[str] = field(default_factory=frozenset)
    engine_code: str | None = None
    displacement_cc: int | None = None
    power_kw: int | None = None
    drive_type: str | None = None
    bodywork: str | None = None
    source_context: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not _normalized_text(self.model):
            raise ValueError("model must not be empty")
        if self.manufacturer is not None and not _normalized_text(self.manufacturer):
            raise ValueError("manufacturer must not be blank")
        if self.year is not None and not 1886 <= self.year <= 2200:
            raise ValueError("year must be between 1886 and 2200")
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


def same_model_family(value: str, catalog_model: str, manufacturer: str) -> bool:
    """True when a TS model family and a catalog model name the same family."""

    value_words, model_words = _words(value), _words(catalog_model)
    if not value_words or not model_words:
        return False
    first, catalog_first = value_words[0], model_words[0]
    if first == catalog_first:
        return True
    compact, catalog_compact = "".join(value_words), "".join(model_words)
    if catalog_compact.startswith(compact) or compact.startswith(catalog_compact):
        return True
    # "Mazda3" against "3 (BK)": TS joins the make to the model number.
    for make in _words(manufacturer):
        if first.startswith(make) and first[len(make):] == catalog_first:
            return True
    # "3 Series" against "320": a series is named by the first digit of its codes.
    if value_words[1:2] == ["SERIES"] and len(first) == 1 and first.isdigit():
        return catalog_first.isdigit() and len(catalog_first) == 3 and catalog_first.startswith(first)
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


#: Words that start the names of unrelated families ("GRAND C4 PICASSO", "GRAND
#: SANTA FE"): only a whole name that starts with one names a family.
_PREFIX_WORDS = frozenset({"GRAND", "NEW"})


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
        and all(
            _normalized_text(canonical) == label or _normalized_text(canonical).startswith(f"{label} ")
            for canonical in canonicals
        )
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
        known: set[str] = set()
        for candidate in self._all:
            for code in candidate.engine_codes:
                known |= engine_code_forms(code)
                if family := engine_code_family(code):
                    known.add(family)
        self._known_engine_forms = frozenset(known)
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

    def knows_engine(self, code: str) -> bool:
        """True when any catalog KType carries this engine code or its family."""

        family = engine_code_family(code)
        return bool(engine_code_forms(code) & self._known_engine_forms) or (
            family is not None and family in self._known_engine_forms
        )

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
            anchor = None if legacy else model_position_token(field_name, value, manufacturer_key)
            anchored_text = None
            if anchor is not None and f" {anchor} " in text:
                anchored_text = f" {text[text.index(f' {anchor} ') + 1:]}"
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
                    matches.update((2, len(anchor), anchor, canonical, field_name) for canonical in family)
            field_matches: list[tuple[int, int, str, str, str]] = []
            for label, canonical_models in labels:
                forms = (label,) if legacy else _label_forms(label)
                form = next((form for form in forms if f" {form} " in text), None)
                if form is None:
                    continue
                anchored = anchored_text is not None and anchored_text.startswith(f" {form} ")
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
        # Score once with bodywork held neutral to find which candidates are
        # plausible at all, then decide whether bodywork discriminates among
        # exactly those. Judging discriminating power over the whole
        # manufacturer would always say "yes" -- every marque has several body
        # styles -- and would miss that one model family offers only one.
        baseline = [
            self._score(query, candidate, bodywork_discriminates=False)
            for candidate in candidates
        ]
        plausible = [
            (candidate, match)
            for candidate, match in zip(candidates, baseline, strict=True)
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
        # Judged over the plausible candidates rather than the whole
        # manufacturer: every marque offers several body styles, so a
        # manufacturer-wide test would always say "discriminating" and would
        # miss that one model family offers only one.
        bodywork_discriminates = len(distinct_bodyworks) > 1

        scored = (
            [
                self._score(query, candidate, bodywork_discriminates=True)
                for candidate, _ in plausible
            ]
            if bodywork_discriminates and needs_bodywork_rescore
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
        if top.confidence < self._config.automatic_threshold:
            return FuzzyMatchResult(scope, ranked, False, "automatic_threshold_not_met")
        if (
            len(qualifying) > 1
            and top.separation_score - qualifying[1].separation_score
            < self._config.automatic_margin
        ):
            return FuzzyMatchResult(scope, ranked, False, "candidate_margin_not_met")
        return FuzzyMatchResult(scope, ranked, True, "automatic_candidate_threshold_met")

    def _score(
        self,
        query: VehicleMatchQuery,
        candidate: VehicleCandidate,
        *,
        bodywork_discriminates: bool = True,
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
            if candidate.year_from is None and candidate.year_to is None:
                missing_fields.append("year")
            elif (candidate.year_from is None or query.year >= candidate.year_from) and (
                candidate.year_to is None or query.year <= candidate.year_to
            ):
                matched_fields.append("year")
                context_effect += self._config.year_match_bonus
            else:
                conflicting_fields.append("year")
                context_effect -= self._config.year_conflict_penalty

        query_fuels = _normalized_values(query.fuels)
        candidate_fuels = _normalized_values(candidate.fuels)
        candidate_fuel_components = _normalized_values(candidate.fuel_components)
        if query_fuels:
            if not candidate_fuels and not candidate_fuel_components:
                missing_fields.append("fuels")
            elif _fuel_evidence_matches(query_fuels, candidate_fuels):
                matched_fields.append("fuels")
                context_effect += self._config.fuel_match_bonus
            elif any(
                (left, right) in self._fuel_compatible_pairs
                for left in query_fuels for right in candidate_fuels
            ):
                missing_fields.append("fuels_compatible_not_confirmed")
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

        query_engine = query.engine_code or ""
        query_forms = engine_code_forms(query_engine) if query_engine else frozenset()
        if query_forms:
            candidate_forms: set[str] = set()
            candidate_families: set[str] = set()
            for code in candidate.engine_codes:
                candidate_forms |= engine_code_forms(code)
                candidate_families.add(engine_code_family(code) or _normalized_code(code))
            query_family = engine_code_family(query_engine) or _normalized_code(query_engine)
            if not candidate_forms:
                missing_fields.append("engine_code")
            elif query_forms & candidate_forms:
                matched_fields.append("engine_code")
                context_effect += self._config.engine_match_bonus
            elif query_family in candidate_forms or (candidate_families & query_forms):
                # One side names only the family (`K9K` against `K9K 276`).
                # Two different variants of a family (`D4F-742`, `D4F 740`)
                # are different engines and stay a conflict below.
                matched_fields.append("engine_code_family")
                context_effect += self._config.engine_family_match_bonus
            elif not self._index.knows_engine(query_engine):
                # A code no KType carries cannot contradict one; it cannot
                # confirm one either, so routing holds the car at provisional.
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
            else:
                conflicting_fields.append("displacement_cc")
                context_effect -= self._config.displacement_conflict_penalty

        if query.power_kw is not None:
            if candidate.power_kw is None:
                missing_fields.append("power_kw")
            elif query.power_kw == candidate.power_kw:
                matched_fields.append("power_kw")
                context_effect += self._config.power_match_bonus
            elif abs(query.power_kw - candidate.power_kw) <= self._config.power_tolerance_kw:
                # Within rounding noise: unverified, so neither a match nor a
                # contradiction. The mild penalty keeps an exactly matching
                # k-type clear of the automatic margin instead of tying with it.
                missing_fields.append("power_kw")
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
