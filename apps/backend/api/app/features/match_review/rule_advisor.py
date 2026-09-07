"""Suggests resolution rules for an unresolved population.

The division of labour is deliberate: statistics can find a *homogeneous block*
of cars, but they cannot say what that block means. `is_4wd = 0` narrowed to
`brand starts_with 'MERCEDES-BENZ 204'` is a clean 938-car block — whether it
is rear- or front-wheel drive is a fact about cars, not about the data, and
must come from evidence (OEM/TecDoc) or a human.

So an advisor proposes the *conditions* freely, and only fills in a
`target_value` when evidence actually supports one. `confident` says which of
the two happened.
"""

from __future__ import annotations

import logging

from dataclasses import dataclass, field
from typing import Any, Protocol

from api.app.features.match_review.field_resolution import (
    CONDITION_OPERATOR_VALUES,
    RESOLVABLE_TARGETS,
    TARGET_FIELD_PRIORS,
    suggest_value_patterns,
)
from api.app.features.match_review.integrations import JsonLlm

MIN_BLOCK_ROWS = 20


@dataclass(frozen=True)
class AdvisedCondition:
    layer: str
    field: str
    operator: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class RuleAdvice:
    advisor: str
    confident: bool
    conditions: list[AdvisedCondition]
    target_field: str
    target_value: str | None
    reasoning: str
    evidence: dict[str, Any] = field(default_factory=dict)


class RuleAdvisor(Protocol):
    @property
    def name(self) -> str: ...

    def advise(
        self,
        *,
        source_field: str,
        source_value: str,
        target_field: str,
        population: int,
        discriminators: list[dict[str, Any]],
        field_values: dict[str, list[tuple[str, int]]],
        oem_samples: list[dict[str, Any]],
        resolved_profile: dict[str, Any] | None = None,
    ) -> RuleAdvice: ...


def _oem_consensus(
    oem_samples: list[dict[str, Any]], target_field: str
) -> str | None:
    """A value only counts when every sample agrees on it."""

    keys = {
        "drive_type": ("drive", "drive_type", "driven_wheels"),
        "bodywork_form": ("body", "body_type"),
        "model_family": ("model", "model_name"),
    }.get(target_field, ())
    observed = set()
    for sample in oem_samples:
        for key in keys:
            value = sample.get(key)
            if value is not None and str(value).strip():
                observed.add(str(value).strip().casefold())
                break
    if len(observed) == 1:
        return observed.pop()
    return None


class PatternRuleAdvisor:
    """Deterministic advisor: finds the cleanest block, then asks for evidence."""

    @property
    def name(self) -> str:
        return "pattern-1"

    def advise(
        self,
        *,
        source_field: str,
        source_value: str,
        target_field: str,
        population: int,
        discriminators: list[dict[str, Any]],
        field_values: dict[str, list[tuple[str, int]]],
        oem_samples: list[dict[str, Any]],
        resolved_profile: dict[str, Any] | None = None,
    ) -> RuleAdvice:
        anchor = AdvisedCondition(
            layer="source",
            field=source_field,
            operator="equals",
            values=(source_value,),
        )
        usable = [item for item in discriminators if item.get("usable")]
        evidence: dict[str, Any] = {
            "population": population,
            "considered_fields": [item["field"] for item in usable[:5]],
        }
        if not usable:
            return RuleAdvice(
                advisor=self.name,
                confident=False,
                conditions=[anchor],
                target_field=target_field,
                target_value=None,
                reasoning=(
                    "No field separates this population — every candidate is "
                    "constant, absent, or near-unique. Fetch OEM evidence for a "
                    "few members before attempting a rule."
                ),
                evidence=evidence,
            )

        priors = TARGET_FIELD_PRIORS.get(target_field, ())
        preferred = [item for item in usable if item["field"] in priors]
        best = (
            max(preferred, key=lambda item: float(item.get("score", 0.0)))
            if preferred
            else usable[0]
        )
        evidence["chosen_by"] = "domain prior" if preferred else "score only"
        values = field_values.get(best["field"], [])
        patterns = suggest_value_patterns(values, population=population)
        narrowing: AdvisedCondition
        block_rows: int
        if patterns and patterns[0].row_count >= MIN_BLOCK_ROWS:
            pattern = patterns[0]
            narrowing = AdvisedCondition(
                layer="source",
                field=best["field"],
                operator="starts_with",
                values=(pattern.prefix,),
            )
            block_rows = pattern.row_count
            how = (
                f"the shared prefix '{pattern.prefix}' groups "
                f"{pattern.distinct_values} spellings"
            )
            evidence["pattern"] = {
                "prefix": pattern.prefix,
                "distinct_values": pattern.distinct_values,
                "score": pattern.score,
            }
        else:
            top = best["top_values"][0]
            narrowing = AdvisedCondition(
                layer="source",
                field=best["field"],
                operator="equals",
                values=(top["value"],),
            )
            block_rows = top["count"]
            how = f"the most common value '{top['value']}'"

        consensus = _oem_consensus(oem_samples, target_field)
        evidence["block_rows"] = block_rows
        evidence["oem_sample_count"] = len(oem_samples)
        if consensus is not None:
            return RuleAdvice(
                advisor=self.name,
                confident=True,
                conditions=[anchor, narrowing],
                target_field=target_field,
                target_value=consensus,
                reasoning=(
                    f"{best['field']} separates this population best; {how}, "
                    f"isolating {block_rows:,} cars. All {len(oem_samples)} OEM "
                    f"samples agree this block is {target_field} = {consensus}."
                ),
                evidence=evidence,
            )
        return RuleAdvice(
            advisor=self.name,
            confident=False,
            conditions=[anchor, narrowing],
            target_field=target_field,
            target_value=None,
            reasoning=(
                f"{best['field']} separates this population best; {how}, "
                f"isolating {block_rows:,} cars — a clean block to rule on. "
                f"What that block *means* is a fact about cars, not about the "
                f"data: confirm {target_field} with OEM or TecDoc evidence "
                f"before assigning a value."
            ),
            evidence=evidence,
        )


logger = logging.getLogger(__name__)


class NoSuggestion(ValueError):
    """The model answered and proposed nothing.

    Distinct from a reply that was malformed or out of vocabulary, and distinct
    again from the model being unreachable. A population with nothing to
    separate it is a real answer, and reporting it as a failure sends a reviewer
    looking for a broken key.
    """

ADVISOR_INSTRUCTIONS = """\
You propose resolution rules over Swedish vehicle-register data. A rule is a
conjunction of conditions (AND); values inside one condition are OR-ed.

Rules:
1. Use only `allowed_condition_fields` and `allowed_operators`.
2. Prefer fields listed in `semantically_relevant_fields_in_order`: a
   statistically strong field can still be meaningless for the target (a model
   year says nothing about which wheels are driven).
3. `value_distributions` shows real values; shared prefixes often encode model
   or chassis identity worth a `starts_with`.
4. Every condition needs a `layer` from `allowed_layers`. Use `source` unless
   you deliberately mean the canonical normalized value: the fields and
   distributions above are all source-layer registry strings.
5. `already_established_for_all_of_these_cars` holds what normalization has
   already derived and verified for every car in the block. Reason from it: it
   identifies the vehicle far better than the raw registry spellings do.
6. Propose a `target_value` whenever the block is identified well enough to name
   one, and it is in `allowed_target_values` when that list is closed. Set
   `confident` true ONLY when `oem_evidence` supports the value. A proposal
   resting on the established identity rather than on evidence is wanted: it
   reaches a reviewer marked as unverified, and a value they can check beats a
   blank they cannot. Say in `reasoning` which the value rests on. Narrowing
   without naming a value remains a valid answer when the block is genuinely
   ambiguous.

Reply with one JSON object: conditions (list of {field, operator, values,
layer}), target_value, confident (bool), reasoning.
"""


class LlmRuleAdvisor:
    """Optional LLM adapter, used only when a model is configured.

    Sends the same evidence bundle the deterministic advisor sees and expects
    one JSON object back. It never writes anything: the result is a proposal
    that still goes through preview and human approval. Falls back to the
    deterministic advisor on any transport, parsing, or validation failure so
    the screen degrades rather than breaks.
    """

    def __init__(
        self,
        *,
        llm: JsonLlm,
        fallback: RuleAdvisor | None = None,
    ) -> None:
        self._llm = llm
        self._fallback = fallback or PatternRuleAdvisor()

    @property
    def name(self) -> str:
        return f"llm:{self._llm.model}"

    def advise(
        self,
        *,
        source_field: str,
        source_value: str,
        target_field: str,
        population: int,
        discriminators: list[dict[str, Any]],
        field_values: dict[str, list[tuple[str, int]]],
        oem_samples: list[dict[str, Any]],
        resolved_profile: dict[str, Any] | None = None,
    ) -> RuleAdvice:
        allowed_values = RESOLVABLE_TARGETS.get(target_field, ())
        allowed_fields = sorted(
            {item["field"] for item in discriminators} | set(field_values)
        )
        prompt = {
            "task": (
                f"Transportstyrelsen records {source_field} = {source_value!r} "
                f"for {population:,} cars, and NorthStar cannot derive "
                f"{target_field} from it. Propose conditions that isolate a "
                f"block of cars that all mean the same thing."
            ),
            "unresolved": {"field": source_field, "value": source_value},
            "target_field": target_field,
            "allowed_target_values": list(allowed_values) or "open vocabulary",
            "population": population,
            "semantically_relevant_fields_in_order": list(
                TARGET_FIELD_PRIORS.get(target_field, ())
            ),
            "allowed_condition_fields": allowed_fields,
            "allowed_layers": ["source", "normalized"],
            "allowed_operators": [
                "equals",
                "not_equals",
                "starts_with",
                "contains",
                "gte",
                "lte",
            ],
            "discriminating_fields": discriminators[:6],
            "value_distributions": {
                name: [
                    {"value": value, "count": count} for value, count in values[:40]
                ]
                for name, values in field_values.items()
            },
            "oem_evidence": oem_samples[:5],
            # What normalization already settled for every car in this block. The
            # model was previously left to infer the vehicle from registry
            # spellings while its derived identity sat unused.
            "already_established_for_all_of_these_cars": resolved_profile or {},
        }
        try:
            payload = self._llm.complete_json(
                instructions=ADVISOR_INSTRUCTIONS, payload=prompt
            )
            conditions = [
                AdvisedCondition(
                    layer=str(item.get("layer", "source")),
                    field=str(item["field"]),
                    operator=str(item.get("operator", "equals")),
                    values=tuple(
                        str(value)
                        for value in item.get("values", [item.get("value")])
                        if value
                    ),
                )
                for item in payload["conditions"]
            ]
            # The model is untrusted input: reject anything outside the
            # allowlists rather than letting an invented field or a
            # non-canonical value reach the preview.
            if not conditions or any(not c.values for c in conditions):
                raise NoSuggestion("model proposed no conditions")
            for condition in conditions:
                if condition.field not in allowed_fields:
                    raise ValueError(f"unknown field {condition.field!r}")
                if condition.operator not in CONDITION_OPERATOR_VALUES:
                    raise ValueError(f"unknown operator {condition.operator!r}")
                if condition.layer not in {"source", "normalized"}:
                    raise ValueError(f"unknown layer {condition.layer!r}")
            target_value = payload.get("target_value")
            if (
                target_value
                and allowed_values
                and str(target_value) not in allowed_values
            ):
                raise ValueError(f"non-canonical target value {target_value!r}")
            unverified = bool(target_value) and not oem_samples
            return RuleAdvice(
                advisor=self.name,
                # Confidence means evidence, not eloquence: only OEM samples can
                # justify a value. An unverified proposal still reaches the
                # reviewer, marked, because Preview and Save are the real gate and
                # a suggestion they can check beats a blank they cannot.
                confident=(
                    bool(payload.get("confident"))
                    and target_value is not None
                    and not unverified
                ),
                conditions=conditions,
                target_field=target_field,
                target_value=str(target_value) if target_value else None,
                reasoning=str(payload.get("reasoning", "")).strip()
                or "Model returned no reasoning.",
                evidence={
                    "population": population,
                    "source": self.name,
                    "value_unverified": unverified,
                    "resolved_profile": resolved_profile or {},
                },
            )
        except Exception as error:  # noqa: BLE001 - any failure degrades to heuristics
            # Why it fell back matters to whoever is looking at the screen. A
            # model that answered and had nothing to propose is not the same as
            # a model that could not be reached, and reporting both as
            # "unavailable" sent a reviewer looking for a broken API key when the
            # population simply had nothing to separate it.
            if isinstance(error, NoSuggestion):
                reason = "no suggestion"
            elif isinstance(error, (ValueError, KeyError, TypeError)):
                # The model replied, but with a field, operator, layer or value
                # outside the allowlists. That is a model problem worth naming
                # separately from an unreachable one.
                reason = "rejected reply"
            else:
                reason = "llm unavailable"
            logger.info(
                "Rule advisor fell back to heuristics",
                extra={
                    "reason": reason,
                    "error_code": type(error).__name__,
                    "source_field": source_field,
                },
            )
            advice = self._fallback.advise(
                source_field=source_field,
                source_value=source_value,
                target_field=target_field,
                population=population,
                discriminators=discriminators,
                field_values=field_values,
                oem_samples=oem_samples,
            )
            return RuleAdvice(
                advisor=f"{advice.advisor} ({reason})",
                confident=advice.confident,
                conditions=advice.conditions,
                target_field=advice.target_field,
                target_value=advice.target_value,
                reasoning=advice.reasoning,
                evidence={**advice.evidence, "llm_fallback": True},
            )
