"""The catalog must list every transform, and must keep listing them.

These tests are the reason the Rules page can claim completeness. Two of them
fail when a lookup table or a pipeline stage is added without being registered,
which is the only way a hidden transformer gets in.
"""

from __future__ import annotations

import ast
import collections
import inspect
import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from ingestion import normalization_rules as rules
from ingestion import text_canonicalization as text
from ingestion.normalization_catalog import (
    _GRAMMARS,
    _STAGES,
    _TABLES,
    CODE_RULE_PREFIX,
    code_rules,
    transformer_stages,
)
from ingestion.normalization_rules import RULE_SET

#: Module constants that are not a lookup the pipeline consults, with the reason.
_NOT_A_LOOKUP = {
    "MAPPING_VERSION": "version string",
    "RULE_VERSION": "version string",
    "PIPELINE_VERSION": "version string",
    "RULE_SET": "the translation catalog itself, already listed",
    "NormalizationStatus": "type alias",
    "ManufacturerEntityRules": "type alias",
    "RuleHandler": "type alias",
    "DEFAULT_PIPELINE": "the pipeline, listed as transformer stages",
    "_MEASUREMENTS": "rendered separately as measurement_scale rules",
}


def _module_lookups() -> dict[str, object]:
    """Every module-level table or pattern that could steer a decision."""

    source = Path(inspect.getfile(rules)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names: list[str] = []
    for node in tree.body:
        targets = (
            [node.target] if isinstance(node, ast.AnnAssign) else getattr(node, "targets", [])
        )
        for target in targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
    lookups: dict[str, object] = {}
    for name in names:
        value = getattr(rules, name, None)
        is_table = isinstance(value, Mapping | frozenset | set | re.Pattern) or (
            isinstance(value, tuple) and bool(value) and not isinstance(value[0], type)
        )
        if is_table:
            lookups[name] = value
    return lookups


def test_every_hardcoded_lookup_is_registered() -> None:
    registered = {spec.name for spec in _TABLES}
    registered.update(grammar.constant for grammar in _GRAMMARS if grammar.constant)
    unregistered = {
        name
        for name in _module_lookups()
        if name not in registered and name not in _NOT_A_LOOKUP
    }
    assert not unregistered, (
        f"{sorted(unregistered)} steer normalization but are not in the rule catalog. "
        "Register them in normalization_catalog so the Rules page can show them, or add "
        "them to _NOT_A_LOOKUP with the reason they are not a transform."
    )


def test_every_pipeline_transformer_is_described() -> None:
    described = set(_STAGES)
    actual = {t.transformer_id for t in rules.DEFAULT_PIPELINE.transformers}
    assert actual <= described, (
        f"{sorted(actual - described)} run in the pipeline with no catalog entry. "
        "Every stage must be listed or it changes records invisibly."
    )
    assert described <= actual, f"{sorted(described - actual)} are described but never run"


def test_every_catalog_rule_area_is_claimed_by_a_transformer() -> None:
    claimed = {area for spec in _STAGES.values() for area in spec.rule_areas}
    actual = {rule.area for rule in RULE_SET.rules}
    assert actual <= claimed, (
        f"rule areas {sorted(actual - claimed)} are in the catalog but no transformer "
        "declares that it reads them"
    )


def test_declared_writes_cover_what_the_handlers_assign() -> None:
    """A stage may not quietly gain an output field.

    Derived from the source rather than hardcoded, so the assertion tracks the code.
    Fields written through ``rule.canonical_field`` are dynamic and are covered by the
    stage's declared rule areas instead.
    """

    source = Path(inspect.getfile(rules)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    calls: dict[str, set[str]] = collections.defaultdict(set)
    writes: dict[str, set[str]] = collections.defaultdict(set)
    for name, node in functions.items():
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id in functions
            ):
                calls[name].add(sub.func.id)
            if isinstance(sub, ast.Assign):
                for target in sub.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "normalized"
                        and isinstance(target.slice, ast.Constant)
                    ):
                        writes[name].add(str(target.slice.value))

    def reachable(name: str, seen: set[str] | None = None) -> set[str]:
        seen = seen if seen is not None else set()
        if name in seen:
            return seen
        seen.add(name)
        for callee in calls[name]:
            reachable(callee, seen)
        return seen

    handlers = {
        transformer.transformer_id: transformer.handler.__name__
        for transformer in rules.DEFAULT_PIPELINE.transformers
        if hasattr(transformer, "handler")
    }
    for transformer_id, handler in handlers.items():
        assigned = {field for name in reachable(handler) for field in writes[name]}
        declared = set(_STAGES[transformer_id].writes)
        assert assigned <= declared, (
            f"{transformer_id} writes {sorted(assigned - declared)} without declaring it. "
            "Add the field to its _StageSpec so the Rules page shows what it produces."
        )


def test_code_rules_are_unique_and_prefixed() -> None:
    entries = code_rules()
    ids = [entry.rule_id for entry in entries]
    assert len(ids) == len(set(ids)), "code rule IDs must be unique"
    assert all(rule_id.startswith(f"{CODE_RULE_PREFIX}:") for rule_id in ids)
    catalog_ids = {rule.rule_id for rule in RULE_SET.rules}
    assert not catalog_ids & set(ids), "a code rule ID must never shadow a catalog rule"


def test_code_rules_carry_the_table_contents() -> None:
    """Spot-check that entries read the live table, not a copy of it."""

    by_id = {entry.rule_id: entry for entry in code_rules()}
    for term, expected in rules._MANUFACTURER_ALIASES.items():
        assert by_id[f"{CODE_RULE_PREFIX}:MFA:{term}"].canonical_value == expected
    for code, (classification, english, swedish) in (
        (code, (value[2], value[1], value[0]))
        for code, value in rules._TEXT_CODE_DEFINITIONS.items()
    ):
        entry = by_id[f"{CODE_RULE_PREFIX}:TXC:{code}"]
        assert entry.canonical_value == classification
        assert entry.display_value == english
        assert swedish in (entry.notes or "")
    for field_name in text.CODE_FIELDS:
        assert field_name in by_id[f"{CODE_RULE_PREFIX}:TXT:TXT-CASE-CODE-V1"].source_fields


def test_grammar_rules_show_the_live_pattern() -> None:
    by_id = {entry.rule_id: entry for entry in code_rules()}
    entry = by_id[f"{CODE_RULE_PREFIX}:GRM:TYPE-APPROVAL"]
    assert entry.source_terms == (rules._TYPE_APPROVAL.pattern,)


def test_stages_are_ordered_and_count_their_rules() -> None:
    stages = transformer_stages(RULE_SET)
    assert [stage.order for stage in stages] == sorted(stage.order for stage in stages)
    model_family = next(s for s in stages if s.transformer_id == "ts.model-family")
    assert model_family.catalog_rule_count == sum(
        1 for rule in RULE_SET.rules if rule.area == "model_family"
    )
    assert model_family.catalog_rule_count > 1000
    tyres = next(s for s in stages if s.transformer_id == "ts.tyres")
    assert tyres.catalog_rule_count == 0
    assert tyres.code_rule_count > 0, "the tyre parser must be visible as code rules"


def test_an_undescribed_transformer_is_rejected() -> None:
    class _Ghost:
        transformer_id = "ts.ghost"
        order = 999
        default_rule_id = "GHOST-V1"
        source_fields = ()

    original = rules.DEFAULT_PIPELINE.transformers
    rules.DEFAULT_PIPELINE.transformers = (*original, _Ghost())
    try:
        with pytest.raises(KeyError, match="ts.ghost"):
            transformer_stages(RULE_SET)
    finally:
        rules.DEFAULT_PIPELINE.transformers = original
