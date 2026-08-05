"""Deterministic, manufacturer-scoped fuzzy candidate generation."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

MatchScope = Literal["exact_manufacturer", "fuzzy_manufacturer", "global"]

_NON_ALPHANUMERIC = re.compile(r"[^A-Z0-9ÅÄÖÉÜ]+")
_WHITESPACE = re.compile(r"\s+")
_DIGIT_GROUP = re.compile(r"\d+")


def _normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).upper()
    normalized = _NON_ALPHANUMERIC.sub(" ", normalized)
    return _WHITESPACE.sub(" ", normalized).strip()


def _normalized_code(value: str) -> str:
    return _NON_ALPHANUMERIC.sub("", unicodedata.normalize("NFKC", value).upper())


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
    year_match_bonus: float = 0.05
    year_conflict_penalty: float = 0.20
    fuel_match_bonus: float = 0.05
    fuel_conflict_penalty: float = 0.15
    engine_match_bonus: float = 0.12
    engine_conflict_penalty: float = 0.25
    max_candidates: int = 5

    def __post_init__(self) -> None:
        threshold_fields = (
            self.candidate_threshold,
            self.automatic_threshold,
            self.automatic_margin,
            self.manufacturer_scope_threshold,
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
            self.year_match_bonus,
            self.year_conflict_penalty,
            self.fuel_match_bonus,
            self.fuel_conflict_penalty,
            self.engine_match_bonus,
            self.engine_conflict_penalty,
        )
        if any(value < 0.0 for value in effects):
            raise ValueError("context bonuses and penalties must not be negative")


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
    engine_codes: frozenset[str] = field(default_factory=frozenset)

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


@dataclass(frozen=True)
class VehicleMatchQuery:
    model: str
    manufacturer: str | None = None
    year: int | None = None
    fuels: frozenset[str] = field(default_factory=frozenset)
    engine_code: str | None = None

    def __post_init__(self) -> None:
        if not _normalized_text(self.model):
            raise ValueError("model must not be empty")
        if self.manufacturer is not None and not _normalized_text(self.manufacturer):
            raise ValueError("manufacturer must not be blank")
        if self.year is not None and not 1886 <= self.year <= 2200:
            raise ValueError("year must be between 1886 and 2200")


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
            },
        }


@dataclass(frozen=True)
class FuzzyMatchResult:
    scope: MatchScope
    candidates: tuple[FuzzyCandidateMatch, ...]
    eligible_for_auto_resolution: bool
    reason: str

    def review_candidates(self) -> tuple[dict[str, Any], ...]:
        return tuple(candidate.to_review_payload() for candidate in self.candidates)


class ManufacturerCandidateIndex:
    """Immutable candidate index with conservative manufacturer fallback."""

    def __init__(self, candidates: Iterable[VehicleCandidate]) -> None:
        by_reference: dict[str, VehicleCandidate] = {}
        by_manufacturer_key: dict[str, dict[str, VehicleCandidate]] = {}
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
        self._all = tuple(sorted(by_reference.values(), key=lambda item: item.candidate_reference))
        self._by_manufacturer_key = {
            key: tuple(sorted(values.values(), key=lambda item: item.candidate_reference))
            for key, values in by_manufacturer_key.items()
        }

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
        return self._all, "global"


class FuzzyVehicleMatcher:
    """Rank candidates without mutating or accepting canonical identity."""

    def __init__(
        self,
        index: ManufacturerCandidateIndex,
        config: FuzzyMatchConfig | None = None,
    ) -> None:
        self._index = index
        self._config = config or FuzzyMatchConfig()

    def match(self, query: VehicleMatchQuery) -> FuzzyMatchResult:
        candidates, scope = self._index.lookup(
            query.manufacturer,
            similarity_threshold=self._config.manufacturer_scope_threshold,
        )
        scored = [self._score(query, candidate) for candidate in candidates]
        qualifying = tuple(
            sorted(
                (
                    candidate
                    for candidate in scored
                    if candidate.confidence >= self._config.candidate_threshold
                ),
                key=lambda candidate: (-candidate.confidence, candidate.candidate_reference),
            )
        )
        ranked = qualifying[: self._config.max_candidates]
        if not ranked:
            return FuzzyMatchResult(scope, (), False, "no_candidate_above_threshold")
        top = ranked[0]
        if scope != "exact_manufacturer":
            return FuzzyMatchResult(scope, ranked, False, "manufacturer_scope_requires_review")
        if top.conflicting_fields:
            return FuzzyMatchResult(scope, ranked, False, "context_conflict_requires_review")
        if top.confidence < self._config.automatic_threshold:
            return FuzzyMatchResult(scope, ranked, False, "automatic_threshold_not_met")
        if (
            len(qualifying) > 1
            and top.confidence - qualifying[1].confidence < self._config.automatic_margin
        ):
            return FuzzyMatchResult(scope, ranked, False, "candidate_margin_not_met")
        return FuzzyMatchResult(scope, ranked, True, "automatic_candidate_threshold_met")

    def _score(
        self,
        query: VehicleMatchQuery,
        candidate: VehicleCandidate,
    ) -> FuzzyCandidateMatch:
        query_model = _normalized_text(query.model)
        labels = tuple(
            sorted(
                {
                    normalized
                    for value in (candidate.model, *candidate.model_aliases)
                    if (normalized := _normalized_text(value))
                }
            )
        )
        label_scores = []
        for label in labels:
            edit_score = _edit_similarity(query_model, label)
            token_score = _token_similarity(query_model, label)
            if len(query_model.split()) == len(label.split()) == 1:
                text_score = edit_score
            else:
                text_score = (
                    edit_score * self._config.edit_weight + token_score * self._config.token_weight
                )
            label_scores.append((_bounded_score(text_score), label))
        text_score, matched_label = max(label_scores, key=lambda item: (item[0], item[1]))

        context_effect = 0.0
        matched_fields: list[str] = ["model"]
        missing_fields: list[str] = []
        conflicting_fields: list[str] = []

        query_series = tuple(_DIGIT_GROUP.findall(query_model))
        candidate_series = tuple(_DIGIT_GROUP.findall(matched_label))
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
        if query_fuels:
            if not candidate_fuels:
                missing_fields.append("fuels")
            elif query_fuels & candidate_fuels:
                matched_fields.append("fuels")
                context_effect += self._config.fuel_match_bonus
            else:
                conflicting_fields.append("fuels")
                context_effect -= self._config.fuel_conflict_penalty

        query_engine = _normalized_code(query.engine_code) if query.engine_code else ""
        candidate_engines = {
            normalized for code in candidate.engine_codes if (normalized := _normalized_code(code))
        }
        if query_engine:
            if not candidate_engines:
                missing_fields.append("engine_code")
            elif query_engine in candidate_engines:
                matched_fields.append("engine_code")
                context_effect += self._config.engine_match_bonus
            else:
                conflicting_fields.append("engine_code")
                context_effect -= self._config.engine_conflict_penalty

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
        )
