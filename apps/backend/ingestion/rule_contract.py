"""Semantic validation for portable normalization rule contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

STAGES = (
    "scope", "special_classification", "manufacturer", "base_vehicle",
    "fuel", "bodywork", "parts_matching", "review",
)
OPERATORS = frozenset(
    {"equals", "not_equals", "in", "not_in", "contains", "starts_with", "regex", "exists", "is_null", "is_not_null", "between"}
)
ACTION_OPS = frozenset(
    {"set", "unset", "append_unique", "copy", "copy_if_present", "dictionary_lookup", "exclude", "add_review_reason", "remove_review_reason", "set_status", "stop_stage", "stop_processing"}
)
READABLE_FIELDS = frozenset(
    {
        "raw.brand", "raw.model", "raw.vin", "raw.fab_code", "raw.fuel1", "raw.fuel2",
        "raw.fuel3", "raw.fuel_combo", "raw.body_code", "raw.body_code2",
        "raw.body_code_extra", "raw.vehicle_class", "raw.base_manufacturer",
        "normalized.manufacturer", "normalized.special_purpose_type",
    }
)
WRITABLE_FIELDS = frozenset(
    {
        "normalized.manufacturer", "normalized.manufacturer_group",
        "normalized.base_vehicle_manufacturer", "normalized.base_model",
        "normalized.builder_converter_names", "normalized.parts_matching_policy",
        "normalized.status", "normalized.review_reasons", "normalized.record_route",
    }
)


def validate_rule_contract(contract: Mapping[str, Any]) -> tuple[str, ...]:
    """Return deterministic semantic errors not expressible safely in JSON Schema."""

    errors: list[str] = []
    stages = contract.get("processing_order")
    if not isinstance(stages, list) or not stages:
        errors.append("processing_order must be a non-empty list")
    else:
        if len(stages) != len(set(stages)):
            errors.append("processing_order contains duplicate stages")
        unknown = [stage for stage in stages if stage not in STAGES]
        if unknown:
            errors.append(f"processing_order contains unknown stages: {unknown}")
    dictionaries = contract.get("dictionaries")
    dictionary_names = set(dictionaries) if isinstance(dictionaries, dict) else set()
    rules = contract.get("rules")
    if not isinstance(rules, list) or not rules:
        return (*errors, "rules must be a non-empty list")
    ids: set[str] = set()
    ordered_slots: set[tuple[str, int]] = set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            errors.append(f"rules[{index}] must be an object")
            continue
        rule_id = rule.get("rule_id")
        if not isinstance(rule_id, str) or not rule_id:
            errors.append(f"rules[{index}] has no rule_id")
            rule_id = f"rules[{index}]"
        elif rule_id in ids:
            errors.append(f"duplicate rule_id: {rule_id}")
        ids.add(rule_id)
        stage = rule.get("stage")
        priority = rule.get("priority")
        if stage not in STAGES:
            errors.append(f"{rule_id}: unknown stage {stage!r}")
        if isinstance(priority, int) and isinstance(stage, str):
            slot = (stage, priority)
            if slot in ordered_slots:
                errors.append(f"{rule_id}: ambiguous duplicate stage/priority {slot}")
            ordered_slots.add(slot)
        match = rule.get("match")
        if not isinstance(match, dict) or not match:
            errors.append(f"{rule_id}: match must not be empty")
        else:
            for group_name, conditions in match.items():
                if not isinstance(conditions, list) or not conditions:
                    errors.append(f"{rule_id}: match.{group_name} must not be empty")
                    continue
                for condition in conditions:
                    _validate_condition(rule_id, condition, dictionary_names, errors)
        actions = rule.get("actions")
        if not isinstance(actions, list) or not actions:
            errors.append(f"{rule_id}: actions must not be empty")
            continue
        terminal_seen = False
        for action in actions:
            if terminal_seen:
                errors.append(f"{rule_id}: action follows a terminal action")
                break
            terminal_seen = _validate_action(rule_id, action, dictionary_names, errors)
    return tuple(errors)


def _validate_condition(
    rule_id: str, condition: object, dictionaries: set[str], errors: list[str]
) -> None:
    if not isinstance(condition, dict):
        errors.append(f"{rule_id}: condition must be an object")
        return
    field = condition.get("field")
    operator = condition.get("operator")
    if field not in READABLE_FIELDS:
        errors.append(f"{rule_id}: invalid readable field {field!r}")
    if operator not in OPERATORS:
        errors.append(f"{rule_id}: invalid operator {operator!r}")
    if operator == "regex" and isinstance(condition.get("value"), str):
        try:
            re.compile(condition["value"])
        except re.error as error:
            errors.append(f"{rule_id}: invalid regex: {error}")
    dictionary = condition.get("dictionary")
    if dictionary is not None and dictionary not in dictionaries:
        errors.append(f"{rule_id}: unknown dictionary {dictionary!r}")


def _validate_action(
    rule_id: str, action: object, dictionaries: set[str], errors: list[str]
) -> bool:
    if not isinstance(action, dict):
        errors.append(f"{rule_id}: action must be an object")
        return False
    operation = action.get("op")
    if operation not in ACTION_OPS:
        errors.append(f"{rule_id}: invalid action operation {operation!r}")
        return False
    field = action.get("field")
    if operation in {"set", "unset", "append_unique", "copy", "copy_if_present", "dictionary_lookup"} and field not in WRITABLE_FIELDS:
        errors.append(f"{rule_id}: invalid writable field {field!r}")
    if operation == "set" and "value" not in action:
        errors.append(f"{rule_id}: set requires value")
    if operation in {"copy", "copy_if_present"} and action.get("source_field") not in READABLE_FIELDS:
        errors.append(f"{rule_id}: copy requires a canonical source_field")
    if operation == "dictionary_lookup":
        if action.get("dictionary") not in dictionaries:
            errors.append(f"{rule_id}: dictionary_lookup references an unknown dictionary")
        if action.get("source_field") not in READABLE_FIELDS:
            errors.append(f"{rule_id}: dictionary_lookup requires a canonical source_field")
    return operation in {"exclude", "stop_processing"}

