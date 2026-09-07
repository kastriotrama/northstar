"""Reviewed, manufacturer-scoped model aliases for TecDoc candidates."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, replace

from ingestion.confidence_routing import ConfidenceRoutingDecision
from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.translation_dictionaries import TranslationRuleSet

_NON_ALPHANUMERIC = re.compile(r"[^A-Z0-9ÅÄÖÉÜ]+")
_WHITESPACE = re.compile(r"\s+")


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).upper()
    return _WHITESPACE.sub(" ", _NON_ALPHANUMERIC.sub(" ", normalized)).strip()


def _family_contains_reviewed_canonical(family: str, canonical: str) -> bool:
    """Require a whole-token canonical prefix; never match compact numeric prefixes."""

    return family == canonical or family.startswith(f"{canonical} ")


@dataclass(frozen=True)
class ReviewedModelAliasEvidence:
    aliases: tuple[str, ...]
    rule_ids: tuple[str, ...]


class ReviewedModelAliasIndex:
    """Attach only accepted model rules inside their reviewed manufacturer scope."""

    def __init__(self, rule_set: TranslationRuleSet) -> None:
        by_manufacturer: dict[str, list[tuple[str, tuple[str, ...], str]]] = defaultdict(list)
        for rule in rule_set.accepted_rules:
            if (
                rule.area != "model_family"
                or not rule.canonical_value
                or not rule.manufacturers
            ):
                continue
            canonical = _key(rule.canonical_value)
            aliases = tuple(
                sorted(
                    {
                        alias.strip()
                        for alias in (*rule.source_terms, rule.canonical_value)
                        if alias.strip()
                    },
                    key=lambda value: (_key(value), value),
                )
            )
            for manufacturer in rule.manufacturers:
                by_manufacturer[_key(manufacturer)].append(
                    (canonical, aliases, rule.rule_id)
                )
        self._by_manufacturer = {
            manufacturer: tuple(sorted(entries, key=lambda item: (item[0], item[2])))
            for manufacturer, entries in by_manufacturer.items()
        }

    def evidence_for(
        self,
        *,
        manufacturer: str,
        model_family: str,
    ) -> ReviewedModelAliasEvidence:
        family = _key(model_family)
        aliases: set[str] = set()
        rule_ids: set[str] = set()
        for canonical, rule_aliases, rule_id in self._by_manufacturer.get(
            _key(manufacturer), ()
        ):
            if _family_contains_reviewed_canonical(family, canonical):
                aliases.update(rule_aliases)
                rule_ids.add(rule_id)
        return ReviewedModelAliasEvidence(
            aliases=tuple(sorted(aliases, key=lambda value: (_key(value), value))),
            rule_ids=tuple(sorted(rule_ids)),
        )

    def expand(self, candidate: VehicleCandidate) -> VehicleCandidate:
        evidence = self.evidence_for(
            manufacturer=candidate.manufacturer,
            model_family=candidate.model,
        )
        if not evidence.aliases:
            return candidate
        return replace(
            candidate,
            model_aliases=tuple(
                sorted(
                    {*candidate.model_aliases, *evidence.aliases},
                    key=lambda value: (_key(value), value),
                )
            ),
        )


def prefer_non_degrading_alias_decision(
    base: ConfidenceRoutingDecision,
    alias_assisted: ConfidenceRoutingDecision,
) -> ConfidenceRoutingDecision:
    """Allow reviewed aliases to improve a route, never to weaken base evidence."""

    rank = {"review_required": 0, "provisional": 1, "resolved": 2}
    base_rank = rank[base.route]
    alias_rank = rank[alias_assisted.route]
    if alias_rank != base_rank:
        return alias_assisted if alias_rank > base_rank else base
    return alias_assisted if alias_assisted.confidence > base.confidence else base
