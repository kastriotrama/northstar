"""Conservative TS manufacturer scope mapping for TecDoc candidate generation."""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

ManufacturerMappingStatus = Literal["resolved", "conflict", "unmatched"]

_GUARDED_RULE_FIELDS = frozenset(
    {
        "source_regex",
        "evidence_fields",
        "base_model_terms",
        "excludes_text_regex",
    }
)


def manufacturer_key(value: object) -> str:
    """Return an accent- and punctuation-tolerant compact manufacturer key."""

    text = unicodedata.normalize("NFKD", str(value or "").upper())
    return "".join(character for character in text if character.isalnum())


def manufacturer_words(value: object) -> tuple[str, ...]:
    """Return normalized words used for whole-token prefix matching."""

    text = unicodedata.normalize("NFKD", str(value or "").upper())
    normalized = "".join(
        character if character.isalnum() else " " for character in text
    )
    return tuple(normalized.split())


def _is_unconditional_vehicle_manufacturer_rule(rule: Mapping[str, Any]) -> bool:
    if (
        rule.get("kind") != "manufacturer_entity"
        or rule.get("entity_role") != "vehicle_manufacturer"
        or rule.get("match_type") == "evidence_regex"
    ):
        return False
    return not any(
        field_name.startswith("requires_") or field_name in _GUARDED_RULE_FIELDS
        for field_name in rule
    )


def _rule_source_alias(rule: Mapping[str, Any]) -> str | None:
    value = rule.get("exact_source_value") or rule.get("source_term")
    return value.strip() if isinstance(value, str) and value.strip() else None


@dataclass(frozen=True)
class ManufacturerMatchEvidence:
    source_field: str
    source_value: str
    matched_alias: str
    manufacturer: str
    rule_ids: tuple[str, ...]
    native_catalog_match: bool


@dataclass(frozen=True)
class ManufacturerMappingDecision:
    status: ManufacturerMappingStatus
    manufacturer: str | None
    evidence: tuple[ManufacturerMatchEvidence, ...]
    conflicting_manufacturers: tuple[str, ...] = ()


@dataclass(frozen=True)
class _AliasTarget:
    alias_words: tuple[str, ...]
    manufacturer: str
    rule_ids: tuple[str, ...]
    native_catalog_match: bool


class TecDocManufacturerIndex:
    """Map TS manufacturer evidence to one TecDoc manufacturer without guessing."""

    def __init__(
        self,
        tecdoc_manufacturers: Iterable[str],
        manufacturer_rules: Mapping[str, Mapping[str, Any]],
    ) -> None:
        catalog_by_key: dict[str, str] = {}
        for manufacturer in tecdoc_manufacturers:
            name = manufacturer.strip()
            key = manufacturer_key(name)
            if not key:
                continue
            existing = catalog_by_key.get(key)
            if existing is not None and existing != name:
                raise ValueError(f"ambiguous TecDoc manufacturer key: {key}")
            catalog_by_key[key] = name
        if not catalog_by_key:
            raise ValueError("TecDoc manufacturer catalog must not be empty")

        safe_rules = {
            rule_id: rule
            for rule_id, rule in manufacturer_rules.items()
            if _is_unconditional_vehicle_manufacturer_rule(rule)
        }
        canonical_bridges: dict[str, set[str]] = defaultdict(set)
        for rule in safe_rules.values():
            source_alias = _rule_source_alias(rule)
            canonical_name = rule.get("canonical_name")
            if not source_alias or not isinstance(canonical_name, str):
                continue
            target = catalog_by_key.get(manufacturer_key(source_alias))
            if target is not None:
                canonical_bridges[manufacturer_key(canonical_name)].add(target)

        canonical_targets: dict[str, str] = dict(catalog_by_key)
        canonical_targets.update(
            {
                canonical_key: next(iter(targets))
                for canonical_key, targets in canonical_bridges.items()
                if len(targets) == 1
            }
        )

        native_aliases = {
            manufacturer_words(name): name for name in catalog_by_key.values()
        }
        reviewed_aliases: dict[tuple[str, ...], dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        for rule_id, rule in safe_rules.items():
            source_alias = _rule_source_alias(rule)
            canonical_name = rule.get("canonical_name")
            if not source_alias or not isinstance(canonical_name, str):
                continue
            target = canonical_targets.get(manufacturer_key(canonical_name))
            alias_words = manufacturer_words(source_alias)
            if target is None or not alias_words:
                continue
            native_target = native_aliases.get(alias_words)
            if native_target is not None and native_target != target:
                continue
            reviewed_aliases[alias_words][target].add(rule_id)

        for canonical_key, target in canonical_targets.items():
            alias_words = manufacturer_words(canonical_key)
            if alias_words and alias_words not in native_aliases:
                reviewed_aliases[alias_words][target].add("TS-CANONICAL-BRIDGE")

        targets: list[_AliasTarget] = [
            _AliasTarget(alias, target, (), True)
            for alias, target in native_aliases.items()
        ]
        targets.extend(
            _AliasTarget(alias, target, tuple(sorted(rule_ids)), False)
            for alias, manufacturers in reviewed_aliases.items()
            if len(manufacturers) == 1
            for target, rule_ids in manufacturers.items()
            if alias not in native_aliases
        )
        by_first_word: dict[str, list[_AliasTarget]] = defaultdict(list)
        for alias_target in targets:
            by_first_word[alias_target.alias_words[0]].append(alias_target)
        self._by_first_word = {
            first_word: tuple(
                sorted(
                    values,
                    key=lambda value: (
                        -len(value.alias_words),
                        value.alias_words,
                        value.manufacturer,
                    ),
                )
            )
            for first_word, values in by_first_word.items()
        }

    def resolve(self, **source_fields: object) -> ManufacturerMappingDecision:
        """Resolve manufacturer evidence or retain all conflicting targets."""

        evidence: list[ManufacturerMatchEvidence] = []
        for source_field, source_value in source_fields.items():
            words = manufacturer_words(source_value)
            if not words:
                continue
            possible = self._by_first_word.get(words[0], ())
            matches = [
                target
                for target in possible
                if words[: len(target.alias_words)] == target.alias_words
            ]
            if not matches:
                continue
            longest = len(matches[0].alias_words)
            best = [target for target in matches if len(target.alias_words) == longest]
            for target in best:
                evidence.append(
                    ManufacturerMatchEvidence(
                        source_field=source_field,
                        source_value=str(source_value),
                        matched_alias=" ".join(target.alias_words),
                        manufacturer=target.manufacturer,
                        rule_ids=target.rule_ids,
                        native_catalog_match=target.native_catalog_match,
                    )
                )

        manufacturers = tuple(sorted({item.manufacturer for item in evidence}))
        if not manufacturers:
            return ManufacturerMappingDecision("unmatched", None, ())
        if len(manufacturers) > 1:
            return ManufacturerMappingDecision(
                "conflict",
                None,
                tuple(evidence),
                conflicting_manufacturers=manufacturers,
            )
        return ManufacturerMappingDecision(
            "resolved", manufacturers[0], tuple(evidence)
        )
