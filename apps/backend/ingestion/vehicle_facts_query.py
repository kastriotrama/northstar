"""Compile rule predicates into SQL over the flat vehicle projection.

One predicate, four uses: browsing matching cars, counting them, previewing what
a rule would resolve, and applying it. Those were four separate query builders
scoped to four different populations, which is why a filter built on one screen
could not become a rule on another. They are one expression here.

Field names reach the SQL by interpolation, because a column name cannot be a
bind parameter. The whitelists in `vehicle_facts_migrations` are therefore the
security boundary, and `_column` refuses anything not on them. Values are always
bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ingestion.vehicle_facts_migrations import (
    NORMALIZED_INTEGER_FIELDS,
    RESOLVABLE_FIELDS,
    SOURCE_INTEGER_COLUMNS,
    SOURCE_TEXT_COLUMNS,
    VEHICLE_FACTS_TABLE,
    unresolved_predicate,
)

SOURCE_IDENTITY_COLUMNS: frozenset[str] = frozenset({"plate", "vin"})

_SOURCE_COLUMNS: frozenset[str] = (
    frozenset(SOURCE_TEXT_COLUMNS) | frozenset(SOURCE_INTEGER_COLUMNS) | SOURCE_IDENTITY_COLUMNS
)
_INTEGER_COLUMNS: frozenset[str] = frozenset(SOURCE_INTEGER_COLUMNS)
_NORMALIZED_INTEGER_FIELDS: frozenset[str] = frozenset(NORMALIZED_INTEGER_FIELDS)

TEXT_OPERATORS: frozenset[str] = frozenset(
    {"equals", "not_equals", "starts_with", "contains"}
)
NUMERIC_OPERATORS: frozenset[str] = frozenset({"gte", "lte"})
SUPPORTED_OPERATORS: frozenset[str] = TEXT_OPERATORS | NUMERIC_OPERATORS


class UnknownFieldError(ValueError):
    """A predicate named a field the projection does not carry."""


@dataclass(frozen=True)
class CompiledPredicate:
    """A WHERE fragment and the values it binds, in matching order."""

    sql: str
    parameters: list[Any]


def _column(layer: str, field: str) -> tuple[str, bool]:
    """Resolve one term to a column expression, and whether it holds integers.

    A `normalized` term reads the *effective* value: what normalization derived,
    or failing that what an applied rule filled in. A rule must see the world as
    it stands, including the resolutions other rules already wrote -- otherwise
    two rules could each claim the same cars.
    """

    if layer == "source":
        if field not in _SOURCE_COLUMNS:
            raise UnknownFieldError(f"{field!r} is not a projected source column")
        return field, field in _INTEGER_COLUMNS
    if layer == "normalized":
        if field not in RESOLVABLE_FIELDS:
            raise UnknownFieldError(f"{field!r} is not a resolvable normalized field")
        return f"coalesce(n_{field}, r_{field})", field in _NORMALIZED_INTEGER_FIELDS
    raise UnknownFieldError(f"{layer!r} is not a known condition layer")


def _numeric_values(values: tuple[str, ...]) -> list[int]:
    """Registry text that is not a number simply cannot match a numeric column."""

    numbers: list[int] = []
    for value in values:
        try:
            numbers.append(int(str(value).strip()))
        except (TypeError, ValueError):
            continue
    return numbers


def compile_term(layer: str, field: str, operator: str, values: tuple[str, ...]) -> CompiledPredicate:
    """One clause. Values inside a clause are OR-ed."""

    if operator not in SUPPORTED_OPERATORS:
        raise ValueError(f"unsupported operator: {operator}")
    terms = tuple(value for value in values if value is not None and str(value).strip())
    if not terms:
        raise ValueError(f"condition on {field!r} has no values")

    column, is_integer = _column(layer, field)

    if operator in NUMERIC_OPERATORS:
        if len(terms) != 1:
            raise ValueError(f"{operator} takes exactly one value")
        numbers = _numeric_values(terms)
        if not numbers:
            raise ValueError(f"{operator} needs a numeric value, got {terms[0]!r}")
        comparison = ">=" if operator == "gte" else "<="
        if is_integer:
            return CompiledPredicate(f"{column} {comparison} %s", [numbers[0]])
        # A text column compared numerically: guard the cast so non-numeric rows
        # go NULL and simply fail to match, rather than aborting the query.
        guarded = (
            f"(CASE WHEN {column} ~ '^-?[0-9]+$' THEN ({column})::bigint END)"
        )
        return CompiledPredicate(f"{guarded} {comparison} %s", [numbers[0]])

    if is_integer:
        numbers = _numeric_values(terms)
        if operator == "equals":
            if not numbers:
                return CompiledPredicate("false", [])
            return CompiledPredicate(f"{column} = ANY(%s)", [numbers])
        if operator == "not_equals":
            if not numbers:
                return CompiledPredicate("true", [])
            return CompiledPredicate(
                f"({column} IS NULL OR NOT ({column} = ANY(%s)))", [numbers]
            )
        # starts_with / contains against a number: compare its text form.
        column = f"({column})::text"

    if operator == "equals":
        return CompiledPredicate(f"{column} = ANY(%s)", [list(terms)])
    if operator == "not_equals":
        return CompiledPredicate(
            f"({column} IS NULL OR NOT ({column} = ANY(%s)))", [list(terms)]
        )

    pattern = "{}%" if operator == "starts_with" else "%{}%"
    clauses = []
    parameters: list[Any] = []
    for value in terms:
        clauses.append(f"{column} LIKE %s")
        parameters.append(pattern.format(value))
    return CompiledPredicate("(" + " OR ".join(clauses) + ")", parameters)


def compile_predicate(
    conditions: list[tuple[str, str, str, tuple[str, ...]]],
    *,
    unresolved_field: str | None = None,
) -> CompiledPredicate:
    """AND the clauses together, optionally restricted to unresolved rows.

    `conditions` are (layer, field, operator, values) tuples -- the shape a
    `PredicateTerm` already has, kept structural so this module does not depend
    on the API's schemas.
    """

    fragments: list[str] = []
    parameters: list[Any] = []
    for layer, field, operator, values in conditions:
        compiled = compile_term(layer, field, operator, values)
        fragments.append(compiled.sql)
        parameters.extend(compiled.parameters)

    if unresolved_field is not None:
        fragments.append(f"({unresolved_predicate(unresolved_field)})")

    if not fragments:
        raise ValueError("a predicate needs at least one condition")
    return CompiledPredicate(" AND ".join(fragments), parameters)


def count_statement(predicate: CompiledPredicate) -> str:
    return f"SELECT count(*) FROM {VEHICLE_FACTS_TABLE} WHERE {predicate.sql}"


def facet_statement(predicate: CompiledPredicate, field: str, *, limit: int = 12) -> str:
    """Top values of one column inside the filtered set."""

    column, _ = _column("source", field) if field in _SOURCE_COLUMNS else _column(
        "normalized", field
    )
    return (
        f"SELECT {column} AS value, count(*) AS rows FROM {VEHICLE_FACTS_TABLE} "
        f"WHERE {predicate.sql} AND {column} IS NOT NULL "
        f"GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {int(limit)}"
    )


def page_statement(predicate: CompiledPredicate, *, limit: int = 100) -> str:
    """One keyset page of matching cars, for the browse list."""

    return (
        "SELECT source_record_id, plate, brand, model, variant, version, "
        "vehicle_year, kw, norm_status "
        f"FROM {VEHICLE_FACTS_TABLE} WHERE {predicate.sql} "
        "AND source_record_id > %s ORDER BY source_record_id LIMIT %s"
    )
