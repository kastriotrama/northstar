"""Load the active reviewed normalization rules from PostgreSQL."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from psycopg import Connection

from ingestion.normalization_migrations import TRANSLATION_RULE_VERSIONS_TABLE
from ingestion.normalization_rules import (
    ManufacturerEntityRules,
    normalize_manufacturer_entity,
)
from ingestion.translation_dictionaries import (
    REVIEWED_RULE_SET_VERSION,
    TranslationRuleSet,
    load_translation_rule_set,
)


def load_active_rules(
    connection: Connection,
) -> tuple[TranslationRuleSet, ManufacturerEntityRules]:
    """Return the latest activated rule set and manufacturer entity rules.

    If no activation exists, the immutable reviewed base catalog is used. This
    keeps command-line normalization aligned with the rule-review application.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT version, base_rule_version, overrides "
            f"FROM {TRANSLATION_RULE_VERSIONS_TABLE} "
            "ORDER BY activated_at DESC, version DESC LIMIT 1"
        )
        row = cursor.fetchone()
    if row is None:
        return load_translation_rule_set(REVIEWED_RULE_SET_VERSION), {}

    version, base_version, overrides = str(row[0]), str(row[1]), dict(row[2] or {})
    base = load_translation_rule_set(base_version)
    effective: list[Any] = []
    for rule in base.rules:
        override = overrides.get(rule.rule_id)
        if isinstance(override, dict) and override.get("decision") is not None:
            effective.append(
                replace(
                    rule,
                    canonical_value=override.get("canonical_value"),
                    decision=override["decision"],
                    display_value=override.get("display_value"),
                )
            )
        else:
            effective.append(rule)
    rules = tuple(effective)
    effective_rules = TranslationRuleSet(version=version, rules=rules)
    entities: dict[str, dict[str, Any]] = {}
    for entity_id, override in overrides.items():
        if not isinstance(override, dict):
            continue
        if override.get("kind") in {
            "manufacturer_match_policy",
            "special_vehicle_policy",
        }:
            entities[f"policy:{entity_id}"] = dict(override)
            continue
        if override.get("kind") != "manufacturer_entity":
            continue
        source_field = override.get("source_field")
        source_term = normalize_manufacturer_entity(override.get("source_term"))
        if not isinstance(source_field, str) or source_term is None:
            continue
        entities[f"{source_field}:{source_term}"] = {
            "entity_id": str(entity_id),
            **override,
            "source_field": source_field,
            "source_term": source_term,
        }
    return effective_rules, entities
