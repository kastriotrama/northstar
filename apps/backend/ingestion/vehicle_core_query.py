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
(`FROM core.vehicles AS v`).

Free text is resolved before it is compiled (`resolve_search`): the identifier
history and the manufacturer and model-family names are small lookups, and
their answers go into the SQL as values. A sub-select in their place leaves the
planner guessing that half the register matches, and it then walks all of
`core.vehicles` in NOR-ID order to find the one car a plate names.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from psycopg import Connection

from ingestion.vehicle_core_fields import (
    FIELDS_BY_NAME,
    FILTERABLE_FIELDS,
    INTEGER_TYPES,
    normalize_plate,
)
from ingestion.vehicle_core_migrations import VEHICLE_IDENTIFIERS_TABLE, VEHICLES_TABLE
from ingestion.vehicle_facts_query import (
    NUMERIC_OPERATORS,
    SUPPORTED_OPERATORS,
    CompiledPredicate,
    UnknownFieldError,
)

ALIAS = "v"
MAX_SEARCH_TOKENS = 6

#: A vehicle column that holds nothing: NULL, or for text the empty string. It
#: takes no values. The TS screen's filter (`vehicle_facts_query`) has no such
#: operator and is not changed.
IS_EMPTY = "is_empty"
VEHICLE_OPERATORS: frozenset[str] = SUPPORTED_OPERATORS | {IS_EMPTY}
_TEXT_TYPES = frozenset({"text"})

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
    """One clause. Values inside a clause are OR-ed; `is_empty` takes none."""

    if operator not in VEHICLE_OPERATORS:
        raise ValueError(f"unsupported operator: {operator}")
    terms = [str(value) for value in values if value is not None and str(value).strip()]
    if operator == IS_EMPTY:
        if terms:
            raise ValueError(f"{IS_EMPTY} takes no values")
        column, sql_type = _column(field)
        # Two plain comparisons of the column itself, so its index answers both.
        if sql_type in _TEXT_TYPES:
            return CompiledPredicate(f"({column} IS NULL OR {column} = '')", [])
        return CompiledPredicate(f"{column} IS NULL", [])
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


@dataclass(frozen=True)
class SearchWord:
    """One word of the search text, with what the database says it names.

    `code` is the word as an identifier (upper-case, a plate's spaces removed),
    matched by prefix against current plates and VINs. `pattern` finds it inside
    a manufacturer or model family. The rest is resolved: the vehicles that hold
    or ever held `code` as an identifier (and the vehicle it names, when it is a
    NOR ID), and the manufacturer and model-family values containing the word.
    """

    code: str
    pattern: str
    vehicle_ids: tuple[str, ...] = ()
    manufacturers: tuple[str, ...] = ()
    model_families: tuple[str, ...] = ()


@dataclass(frozen=True)
class VehicleSearch:
    """Free text, resolved. Every word must match -- or `joined`, the words
    run together as one identifier, because plates are written "ABC 123"."""

    words: tuple[SearchWord, ...]
    joined: SearchWord | None = None


def search_words(text: str) -> tuple[list[str], str | None]:
    """The words searched for, and their joined form when there are several."""

    words = [word for word in text.split() if word][:MAX_SEARCH_TOKENS]
    return words, "".join(words).upper() if len(words) > 1 else None


def _word(word: str) -> SearchWord:
    return SearchWord(normalize_plate(word) or word.upper(), f"%{_escape_like(word)}%")


def _holders(connection: Connection, codes: Sequence[str]) -> dict[str, tuple[str, ...]]:
    """Every vehicle that holds or held each code as a plate, VIN or chassis number."""

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT value, array_agg(DISTINCT vehicle_id ORDER BY vehicle_id) "
            f"FROM {VEHICLE_IDENTIFIERS_TABLE} "
            "WHERE kind IN ('plate', 'vin', 'chassis') AND value = ANY(%s) GROUP BY value",
            (list(codes),),
        )
        return {str(value): tuple(ids) for value, ids in cursor.fetchall()}


def _names(connection: Connection, patterns: Sequence[str]) -> dict[str, dict[int, list[str]]]:
    """Manufacturer and model-family values containing each pattern, by word position.

    The distinct values come from a skip scan of each column's index -- a few
    hundred index probes -- rather than from reading the table.
    """

    found: dict[str, dict[int, list[str]]] = {
        "manufacturer": defaultdict(list),
        "model_family": defaultdict(list),
    }
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH RECURSIVE make(value) AS (
                SELECT min(manufacturer) FROM {VEHICLES_TABLE}
                UNION ALL
                SELECT (SELECT min(manufacturer) FROM {VEHICLES_TABLE}
                        WHERE manufacturer > make.value)
                FROM make WHERE make.value IS NOT NULL
            ), family(value) AS (
                SELECT min(model_family) FROM {VEHICLES_TABLE}
                UNION ALL
                SELECT (SELECT min(model_family) FROM {VEHICLES_TABLE}
                        WHERE model_family > family.value)
                FROM family WHERE family.value IS NOT NULL
            ), word AS (
                SELECT pattern, position::int AS position
                FROM unnest(%s::text[]) WITH ORDINALITY AS item(pattern, position)
            )
            SELECT 'manufacturer', word.position, make.value
            FROM make JOIN word ON make.value ILIKE word.pattern
            UNION ALL
            SELECT 'model_family', word.position, family.value
            FROM family JOIN word ON family.value ILIKE word.pattern
            ORDER BY 1, 2, 3
            """,
            (list(patterns),),
        )
        for column, position, value in cursor.fetchall():
            found[str(column)][int(position) - 1].append(str(value))
    return found


def resolve_search(connection: Connection, text: str) -> VehicleSearch | None:
    """Look the search text up; None when there is nothing to search for."""

    raw_words, joined_code = search_words(text)
    if not raw_words:
        return None
    words = [_word(word) for word in raw_words]
    codes = [word.code for word in words] + ([joined_code] if joined_code else [])
    holders = _holders(connection, codes)
    names = _names(connection, [word.pattern for word in words])

    def vehicles(code: str) -> tuple[str, ...]:
        own = (code,) if is_vehicle_id(code) else ()
        return tuple(sorted({*own, *holders.get(code, ())}))

    resolved = tuple(
        SearchWord(
            word.code,
            word.pattern,
            vehicles(word.code),
            tuple(names["manufacturer"].get(position, ())),
            tuple(names["model_family"].get(position, ())),
        )
        for position, word in enumerate(words)
    )
    joined = (
        SearchWord(joined_code, f"%{_escape_like(joined_code)}%", vehicles(joined_code))
        if joined_code
        else None
    )
    return VehicleSearch(resolved, joined)


def _identifier_match(word: SearchWord) -> tuple[list[str], list[Any]]:
    """A current plate or VIN by prefix, or a vehicle the code resolved to."""

    prefix = f"{_escape_like(word.code)}%"
    branches = [f"{ALIAS}.plate LIKE %s", f"{ALIAS}.vin LIKE %s"]
    parameters: list[Any] = [prefix, prefix]
    if word.vehicle_ids:
        branches.append(f"{ALIAS}.vehicle_id = ANY(%s)")
        parameters.append(list(word.vehicle_ids))
    return branches, parameters


def compile_search(search: VehicleSearch) -> CompiledPredicate:
    """Free text over identity and make/model. Every word must match (AND).

    A word matches a current plate or VIN by prefix, the vehicle's own NOR ID,
    any identifier it ever held (a previous plate still finds the car it
    belonged to), or appears in the manufacturer or model family. Several words
    are also tried joined, as one identifier.
    """

    fragments: list[str] = []
    parameters: list[Any] = []
    for word in search.words:
        branches, word_parameters = _identifier_match(word)
        for column, values in (
            ("manufacturer", word.manufacturers),
            ("model_family", word.model_families),
        ):
            if values:
                branches.append(f"{ALIAS}.{column} = ANY(%s)")
                word_parameters.append(list(values))
        fragments.append("(" + " OR ".join(branches) + ")")
        parameters.extend(word_parameters)
    every_word = " AND ".join(fragments)
    if search.joined is None:
        return CompiledPredicate(every_word, parameters)
    joined_branches, joined_parameters = _identifier_match(search.joined)
    return CompiledPredicate(
        f"(({every_word}) OR ({' OR '.join(joined_branches)}))",
        [*parameters, *joined_parameters],
    )


def compile_vehicle_filter(
    terms: Sequence[VehicleTerm],
    search: VehicleSearch | None = None,
    *,
    skip_field: str | None = None,
) -> CompiledPredicate:
    """AND the clauses and the resolved text together; `true` when there is neither.

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
    if search is not None:
        compiled = compile_search(search)
        fragments.append(f"({compiled.sql})")
        parameters.extend(compiled.parameters)
    if not fragments:
        return CompiledPredicate("true", [])
    return CompiledPredicate(" AND ".join(fragments), parameters)


def is_vehicle_id(value: str) -> bool:
    """Shape only: `NOR-` and a ULID. Existence is the database's question."""

    return bool(_NOR_ID.match(value))
