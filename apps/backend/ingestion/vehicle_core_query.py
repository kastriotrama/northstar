"""Compile Vehicles-tab filters into SQL over `core.vehicles`.

The TS screen's compiler (`vehicle_facts_query`) reads a projection with two
layers -- the registry string and the canonical value -- and a rule overlay on
top. A NorthStar vehicle has one layer: each column already holds the value that
won the merge, from whichever source. So a condition here names a column and
nothing else.

Column names reach the SQL by interpolation, because a column cannot be a bind
parameter. `vehicle_core_fields.FILTERABLE_FIELDS` is therefore the security
boundary, and `_column` refuses anything not on it. Values are always bound.

Every fragment reads the vehicle through the alias `v`
(`FROM core.vehicles AS v`), so text search can reach the identifier history.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from typing import Any

from ingestion.vehicle_core_fields import (
    FIELDS_BY_NAME,
    FILTERABLE_FIELDS,
    INTEGER_TYPES,
    normalize_plate,
)
from ingestion.vehicle_core_migrations import VEHICLE_IDENTIFIERS_TABLE
from ingestion.vehicle_facts_query import (
    NUMERIC_OPERATORS,
    SUPPORTED_OPERATORS,
    CompiledPredicate,
    UnknownFieldError,
)

ALIAS = "v"
MAX_SEARCH_TOKENS = 6

#: (field, operator, values) -- structural, so this module does not depend on
#: the API's schemas.
VehicleTerm = tuple[str, str, Sequence[str]]

_FILTERABLE = frozenset(FILTERABLE_FIELDS)
_NOR_ID = re.compile(r"^NOR-[0-7][0-9A-HJKMNP-TV-Z]{25}$")


def _column(field: str) -> tuple[str, str]:
    """The column for one filterable field, and its SQL type."""

    if field not in _FILTERABLE:
        raise UnknownFieldError(f"{field!r} is not a filterable vehicle field")
    return f"{ALIAS}.{field}", FIELDS_BY_NAME[field].sql_type


def filterable_column(field: str) -> str:
    """The column expression for a filterable field; refuses anything else."""

    return _column(field)[0]


def _numbers(values: Sequence[str]) -> list[int]:
    numbers: list[int] = []
    for value in values:
        try:
            numbers.append(int(str(value).strip()))
        except (TypeError, ValueError):
            continue
    return numbers


def _dates(values: Sequence[str]) -> list[date]:
    found: list[date] = []
    for value in values:
        try:
            found.append(date.fromisoformat(str(value).strip()))
        except ValueError:
            continue
    return found


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def compile_term(field: str, operator: str, values: Sequence[str]) -> CompiledPredicate:
    """One clause. Values inside a clause are OR-ed."""

    if operator not in SUPPORTED_OPERATORS:
        raise ValueError(f"unsupported operator: {operator}")
    terms = [str(value) for value in values if value is not None and str(value).strip()]
    if not terms:
        raise ValueError(f"condition on {field!r} has no values")
    column, sql_type = _column(field)

    if operator in NUMERIC_OPERATORS:
        if len(terms) != 1:
            raise ValueError(f"{operator} takes exactly one value")
        comparison = ">=" if operator == "gte" else "<="
        if sql_type == "date":
            dates = _dates(terms)
            if not dates:
                raise ValueError(f"{operator} needs a YYYY-MM-DD date, got {terms[0]!r}")
            return CompiledPredicate(f"{column} {comparison} %s", [dates[0]])
        numbers = _numbers(terms)
        if not numbers:
            raise ValueError(f"{operator} needs a numeric value, got {terms[0]!r}")
        if sql_type in INTEGER_TYPES:
            return CompiledPredicate(f"{column} {comparison} %s", [numbers[0]])
        # A text column compared numerically: non-numeric rows go NULL and simply
        # fail to match, rather than aborting the query.
        guarded = f"(CASE WHEN {column} ~ '^-?[0-9]+$' THEN ({column})::bigint END)"
        return CompiledPredicate(f"{guarded} {comparison} %s", [numbers[0]])

    typed: list[Any]
    if sql_type in INTEGER_TYPES:
        typed = _numbers(terms)
    elif sql_type == "date":
        typed = _dates(terms)
    else:
        typed = terms
    if sql_type in INTEGER_TYPES or sql_type == "date":
        if operator == "equals":
            if not typed:
                return CompiledPredicate("false", [])
            return CompiledPredicate(f"{column} = ANY(%s)", [typed])
        if operator == "not_equals":
            if not typed:
                return CompiledPredicate("true", [])
            return CompiledPredicate(f"({column} IS NULL OR NOT ({column} = ANY(%s)))", [typed])
        # starts_with / contains against a number or date: compare its text form.
        column = f"({column})::text"

    if operator == "equals":
        return CompiledPredicate(f"{column} = ANY(%s)", [terms])
    if operator == "not_equals":
        return CompiledPredicate(f"({column} IS NULL OR NOT ({column} = ANY(%s)))", [terms])
    pattern = "{}%" if operator == "starts_with" else "%{}%"
    return CompiledPredicate(
        "(" + " OR ".join(f"{column} LIKE %s" for _ in terms) + ")",
        [pattern.format(_escape_like(term)) for term in terms],
    )


def _identifier_match(code: str) -> tuple[str, list[Any]]:
    """A current plate or VIN by prefix, the NOR ID, or any identifier ever held."""

    escaped = _escape_like(code)
    sql = (
        f"{ALIAS}.plate LIKE %s OR {ALIAS}.vin LIKE %s OR {ALIAS}.vehicle_id = %s "
        f"OR EXISTS (SELECT 1 FROM {VEHICLE_IDENTIFIERS_TABLE} AS identifier "
        f"WHERE identifier.vehicle_id = {ALIAS}.vehicle_id "
        "AND identifier.kind IN ('plate', 'vin', 'chassis') AND identifier.value = %s)"
    )
    return sql, [f"{escaped}%", f"{escaped}%", code, code]


def compile_search_text(text: str) -> CompiledPredicate | None:
    """Free text over identity and make/model. Every token must match (AND).

    A token matches a current plate or VIN by prefix, the vehicle's own NOR ID,
    any identifier it ever held (a previous plate still finds the car it
    belonged to), or appears in the manufacturer or model family. Text of
    several tokens is also tried whole with its spaces removed, because plates
    are written "ABC 123". Returns None when there is nothing to search for.
    """

    tokens = [token for token in text.split() if token][:MAX_SEARCH_TOKENS]
    if not tokens:
        return None
    fragments: list[str] = []
    parameters: list[Any] = []
    for token in tokens:
        identifier_sql, identifier_parameters = _identifier_match(
            normalize_plate(token) or token.upper()
        )
        like = f"%{_escape_like(token)}%"
        fragments.append(
            f"({identifier_sql} "
            f"OR {ALIAS}.manufacturer ILIKE %s OR {ALIAS}.model_family ILIKE %s)"
        )
        parameters.extend([*identifier_parameters, like, like])
    every_token = " AND ".join(fragments)
    if len(tokens) == 1:
        return CompiledPredicate(every_token, parameters)
    whole_sql, whole_parameters = _identifier_match("".join(tokens).upper())
    return CompiledPredicate(
        f"(({every_token}) OR ({whole_sql}))", [*parameters, *whole_parameters]
    )


def compile_vehicle_filter(
    terms: Sequence[VehicleTerm], text: str = "", *, skip_field: str | None = None
) -> CompiledPredicate:
    """AND the clauses and the text together; `true` when there is neither.

    `skip_field` lifts that field's own clauses -- a facet counts its values
    as if it were not filtered on itself, so choosing one never hides the rest.
    """

    fragments: list[str] = []
    parameters: list[Any] = []
    for field, operator, values in terms:
        if field == skip_field:
            continue
        compiled = compile_term(field, operator, values)
        fragments.append(compiled.sql)
        parameters.extend(compiled.parameters)
    search = compile_search_text(text)
    if search is not None:
        fragments.append(f"({search.sql})")
        parameters.extend(search.parameters)
    if not fragments:
        return CompiledPredicate("true", [])
    return CompiledPredicate(" AND ".join(fragments), parameters)


def is_vehicle_id(value: str) -> bool:
    """Shape only: `NOR-` and a ULID. Existence is the database's question."""

    return bool(_NOR_ID.match(value))
