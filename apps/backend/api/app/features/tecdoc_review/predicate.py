"""Filter conditions and gap definitions over the promoted TecDoc vehicle CTE.

`TecDocReviewRepository.fetch_vehicles` builds a `vehicles` CTE with one row per
promoted KType, joining in the manufacturer/model_family/engine/transmission/
bodywork attribute JSONB each carries. There is no flat projection table the way
`core.vehicle_facts` is for Transportstyrelsen, so a condition here compiles to an
expression over that CTE's already-joined columns rather than a real column name.

Field names reach the SQL by interpolation, exactly as `vehicle_facts_query`
does it for TS: a column name cannot be a bind parameter, so `FILTERABLE_FIELDS`
is the security boundary and `compile_condition` refuses anything not on it.
Values are always bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TEXT_OPERATORS: frozenset[str] = frozenset({"equals", "not_equals", "starts_with", "contains"})
NUMERIC_OPERATORS: frozenset[str] = frozenset({"gte", "lte"})
SUPPORTED_OPERATORS: frozenset[str] = TEXT_OPERATORS | NUMERIC_OPERATORS

#: Every field a filter condition or facet can name, and the SQL expression it
#: reads inside the `vehicles` CTE. Adding a field here is what makes it
#: filterable and facetable; nothing else needs to change.
FILTERABLE_FIELDS: dict[str, str] = {
    "manufacturer": "manufacturer_attributes->>'canonical_name'",
    "model_family": "family_attributes->>'canonical_name'",
    "fuel_type": "coalesce(engine_attributes->>'fuel_type', variant_attributes->>'fuel_type')",
    # Falls back to a live ruling from `core.tecdoc_resolution_rules` (joined
    # into the CTE as `bodywork_resolution_value`/`drive_resolution_value`)
    # before the promoted batch's own value -- the Tier 1 promotion-loop
    # closer. A KType a reviewer has since resolved reads as resolved here
    # without the batch itself ever being rewritten.
    "bodywork_name": "coalesce(bodywork_attributes->>'canonical_name', bodywork_resolution_value)",
    "bodywork_status": (
        "CASE WHEN coalesce(bodywork_attributes->>'canonical_name', bodywork_resolution_value) "
        "IS NOT NULL THEN 'linked' "
        "ELSE coalesce(variant_attributes->>'bodywork_link_status', 'code_missing') END"
    ),
    "drive_type": "coalesce(variant_attributes->>'drive_type', drive_resolution_value)",
    "drive_status": (
        "CASE WHEN coalesce(variant_attributes->>'drive_type', drive_resolution_value) "
        "IS NOT NULL THEN 'mapped' "
        "ELSE coalesce(variant_attributes->>'drive_normalization_status', 'review_required') END"
    ),
    "transmission_type_name": "transmission_attributes->>'transmission_type_name'",
    "transmission_link_status": (
        "coalesce(variant_attributes->>'transmission_link_status', 'allocation_missing')"
    ),
    "engine_link_status": (
        "coalesce(variant_attributes->>'engine_link_status', 'allocation_missing')"
    ),
    "year_from": "variant_attributes->>'year_from'",
    "year_to": "variant_attributes->>'year_to'",
}

#: Numeric fields get a guarded cast rather than a plain `::numeric`, so a row
#: whose value is not actually a number goes NULL and fails to match instead of
#: aborting the whole query -- the same guard `vehicle_facts_query` uses for a
#: text column compared numerically.
_NUMERIC_FIELDS: frozenset[str] = frozenset({"year_from", "year_to"})

#: The row-level gaps a reviewer can restrict the list to or count across a
#: filter, mirroring `unresolved_field` on the TS vehicle population. The first
#: three share their name with a `canonical_field` in
#: `ingestion.tecdoc.canonical_rule_proposals.FIELD_SOURCES`, so a gap counted
#: here and a rule proposed there are provably the same question. `engine` and
#: `transmission` have no canonical vocabulary yet -- they report whether the
#: KType links to one at all, not whether that link resolved to a term.
GAP_FIELDS: dict[str, tuple[str, str]] = {
    "energy_sources": (
        "No canonical fuel.",
        "coalesce(engine_attributes->>'fuel_type', variant_attributes->>'fuel_type') IS NULL",
    ),
    "bodywork_form": (
        "Has a body type code; canonical mapping needs review.",
        "coalesce(bodywork_attributes->>'canonical_name', bodywork_resolution_value) IS NULL",
    ),
    "drive_type": (
        "No canonical drive type.",
        "coalesce(variant_attributes->>'drive_type', drive_resolution_value) IS NULL",
    ),
    # Split from one "engine" gap for the same reason "transmission" was: a
    # ktype set aside for `engine_ambiguous` has 2+ real, named Table 155
    # engines, and one set aside for `fuel_unresolved`/`displacement_unresolved`
    # typically has exactly one -- neither is "no engine allocation", the label
    # this used to report for both alongside genuine zero-engine ktypes.
    # `review_required` (a real single engine, excluded for a reason that has
    # nothing to do with the engine) is deliberately excluded, same as
    # `type_known` is for transmission below: it is not an engine gap.
    # `ambiguous` (2+ named engines, none selected) is deliberately not its own
    # tile here either -- it is not "missing" anything, and the breakdown
    # already reachable from this tile's own button reports it, from the
    # same `engine_link_status` facet, without a second top-level number for
    # what is really one underlying question.
    "engine_missing": (
        "No engine data in TecDoc at all.",
        "coalesce(variant_attributes->>'engine_link_status', 'allocation_missing') = 'allocation_missing'",
    ),
    # Split from one "transmission" gap: a ktype with zero Table 547 rows and a
    # ktype with two is not the same fact, and folding both into "no
    # transmission allocation" hid the second group's real, named options from
    # a reviewer entirely. `type_known` -- a category read off the ktype's own
    # Table 120 field rather than an allocation -- is deliberately excluded:
    # it is not an allocation gap, it already answers the question this
    # screen asks. `linked_multiple` is likewise not its own tile, for the
    # same reason `ambiguous` is not above: its own breakdown already reports
    # it from the same `transmission_link_status` facet.
    "transmission_missing": (
        "No transmission data in TecDoc at all.",
        (
            "coalesce(variant_attributes->>'transmission_link_status', 'allocation_missing') "
            "= 'allocation_missing'"
        ),
    ),
}


class UnknownTecDocFieldError(ValueError):
    """A condition, facet or gap named a field this screen does not project."""


@dataclass(frozen=True)
class CompiledCondition:
    """A WHERE fragment and the values it binds, in matching order."""

    sql: str
    parameters: list[Any]


def gap_predicate(field: str) -> str:
    if field not in GAP_FIELDS:
        raise UnknownTecDocFieldError(f"{field!r} is not a known TecDoc gap")
    return GAP_FIELDS[field][1]


def compile_condition(field: str, operator: str, values: tuple[str, ...]) -> CompiledCondition:
    """One clause. Values inside a clause are OR-ed, exactly as TS conditions are."""

    if field not in FILTERABLE_FIELDS:
        raise UnknownTecDocFieldError(f"{field!r} is not a filterable TecDoc field")
    if operator not in SUPPORTED_OPERATORS:
        raise ValueError(f"unsupported operator: {operator}")
    terms = tuple(value for value in values if value is not None and str(value).strip())
    if not terms:
        raise ValueError(f"condition on {field!r} has no values")

    expr = FILTERABLE_FIELDS[field]

    if operator in NUMERIC_OPERATORS:
        if len(terms) != 1:
            raise ValueError(f"{operator} takes exactly one value")
        if field not in _NUMERIC_FIELDS:
            raise UnknownTecDocFieldError(f"{field!r} does not support {operator}")
        comparison = ">=" if operator == "gte" else "<="
        guarded = f"(CASE WHEN {expr} ~ '^-?[0-9]+$' THEN ({expr})::bigint END)"
        return CompiledCondition(f"{guarded} {comparison} %s", [terms[0]])

    if operator == "equals":
        return CompiledCondition(f"{expr} = ANY(%s)", [list(terms)])
    if operator == "not_equals":
        return CompiledCondition(f"({expr} IS NULL OR NOT ({expr} = ANY(%s)))", [list(terms)])

    pattern = "{}%" if operator == "starts_with" else "%{}%"
    clauses: list[str] = []
    parameters: list[Any] = []
    for value in terms:
        clauses.append(f"{expr} ILIKE %s ESCAPE '\\'")
        parameters.append(pattern.format(_escape_like(value)))
    return CompiledCondition("(" + " OR ".join(clauses) + ")", parameters)


def _escape_like(value: str) -> str:
    """Neutralise LIKE wildcards inside a value the reviewer typed.

    Binding the value protects against injection but not against meaning: `%`
    and `_` stay wildcards inside the bound string, so searching for `50%` matched
    every row and `BMW_3` matched `BMW 3` and `BMWX3` alike. A reviewer typing a
    literal code expects it to be literal, so the two wildcards and the escape
    character itself are escaped, and the clause declares the escape explicitly
    rather than relying on the backslash default.
    """

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def compile_conditions(
    conditions: list[tuple[str, str, tuple[str, ...]]],
    *,
    unresolved_field: str | None = None,
) -> CompiledCondition:
    """AND every clause together, optionally restricted to one named gap."""

    fragments: list[str] = []
    parameters: list[Any] = []
    for field, operator, values in conditions:
        compiled = compile_condition(field, operator, values)
        fragments.append(compiled.sql)
        parameters.extend(compiled.parameters)
    if unresolved_field is not None:
        fragments.append(f"({gap_predicate(unresolved_field)})")
    if not fragments:
        return CompiledCondition("true", [])
    return CompiledCondition(" AND ".join(fragments), parameters)
