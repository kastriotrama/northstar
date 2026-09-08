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
    "bodywork_name": "bodywork_attributes->>'canonical_name'",
    "bodywork_status": "coalesce(variant_attributes->>'bodywork_link_status', 'code_missing')",
    "drive_type": "variant_attributes->>'drive_type'",
    "drive_status": (
        "coalesce(variant_attributes->>'drive_normalization_status', 'review_required')"
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
        "No canonical bodywork.",
        "bodywork_attributes->>'canonical_name' IS NULL",
    ),
    "drive_type": (
        "No canonical drive type.",
        "variant_attributes->>'drive_type' IS NULL",
    ),
    "engine": (
        "No engine allocation.",
        "coalesce(variant_attributes->>'engine_link_status', 'allocation_missing') <> 'linked'",
    ),
    "transmission": (
        "No transmission allocation.",
        (
            "coalesce(variant_attributes->>'transmission_link_status', 'allocation_missing') "
            "<> 'linked'"
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
        clauses.append(f"{expr} ILIKE %s")
        parameters.append(pattern.format(value))
    return CompiledCondition("(" + " OR ".join(clauses) + ")", parameters)


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
