import pytest

from ingestion.vehicle_facts_query import (
    UnknownFieldError,
    compile_predicate,
    compile_term,
    count_statement,
    page_statement,
)


def test_values_are_bound_never_interpolated() -> None:
    """The whole injection surface is field names; values must stay parameters."""

    compiled = compile_term("source", "brand", "equals", ("TOYOTA'; DROP TABLE x --",))

    assert "DROP TABLE" not in compiled.sql
    assert compiled.parameters == [["TOYOTA'; DROP TABLE x --"]]


def test_unknown_source_field_is_refused() -> None:
    """Field names are interpolated, so the whitelist is the security boundary."""

    with pytest.raises(UnknownFieldError):
        compile_term("source", "brand); DROP TABLE core.vehicle_facts --", "equals", ("x",))


def test_unknown_normalized_field_is_refused() -> None:
    with pytest.raises(UnknownFieldError):
        compile_term("normalized", "not_a_field", "equals", ("x",))


def test_unknown_layer_is_refused() -> None:
    with pytest.raises(UnknownFieldError):
        compile_term("sql", "brand", "equals", ("x",))


def test_normalized_terms_read_the_effective_value() -> None:
    """A rule must see resolutions other rules already wrote, or two rules
    could each claim the same cars."""

    compiled = compile_term("normalized", "drive_type", "equals", ("fwd",))

    assert compiled.sql == "coalesce(n_drive_type, r_drive_type) = ANY(%s)"


def test_values_within_a_term_are_or_ed() -> None:
    compiled = compile_term("source", "brand", "equals", ("VOLVO", "SAAB"))

    assert compiled.parameters == [["VOLVO", "SAAB"]]


def test_not_equals_keeps_null_rows() -> None:
    """`brand != VOLVO` must still match a car whose brand is absent."""

    compiled = compile_term("source", "brand", "not_equals", ("VOLVO",))

    assert "IS NULL OR NOT" in compiled.sql


def test_integer_columns_bind_numbers_so_the_index_stays_usable() -> None:
    compiled = compile_term("source", "vehicle_year", "equals", ("2019",))

    assert compiled.sql == "vehicle_year = ANY(%s)"
    assert compiled.parameters == [[2019]]


def test_non_numeric_value_against_an_integer_column_matches_nothing() -> None:
    """Registry junk cannot equal a number; it must not abort the query either."""

    compiled = compile_term("source", "vehicle_year", "equals", ("not-a-year",))

    assert compiled.sql == "false"
    assert compiled.parameters == []


def test_numeric_comparison_takes_exactly_one_value() -> None:
    with pytest.raises(ValueError):
        compile_term("source", "vehicle_year", "gte", ("2010", "2011"))


def test_numeric_comparison_on_a_text_column_guards_the_cast() -> None:
    compiled = compile_term("source", "type_text", "gte", ("100",))

    assert "~ '^-?[0-9]+$'" in compiled.sql


def test_empty_values_are_refused() -> None:
    with pytest.raises(ValueError):
        compile_term("source", "brand", "equals", ("", "   "))


def test_unsupported_operator_is_refused() -> None:
    with pytest.raises(ValueError):
        compile_term("source", "brand", "regex", ("^V",))


def test_clauses_are_and_ed_and_parameters_stay_in_order() -> None:
    compiled = compile_predicate(
        [
            ("source", "brand", "equals", ("TOYOTA",)),
            ("source", "vehicle_year", "gte", ("2015",)),
        ]
    )

    assert compiled.sql == "brand = ANY(%s) AND vehicle_year >= %s"
    assert compiled.parameters == [["TOYOTA"], 2015]


def test_unresolved_restriction_requires_both_halves_absent() -> None:
    compiled = compile_predicate(
        [("source", "brand", "equals", ("TOYOTA",))], unresolved_field="drive_type"
    )

    assert "n_drive_type IS NULL AND r_drive_type IS NULL" in compiled.sql


def test_a_predicate_needs_at_least_one_condition() -> None:
    """An empty predicate would match every car in the database."""

    with pytest.raises(ValueError):
        compile_predicate([])


def test_statements_embed_the_compiled_predicate() -> None:
    compiled = compile_predicate([("source", "brand", "equals", ("TOYOTA",))])

    assert compiled.sql in count_statement(compiled)
    assert compiled.sql in page_statement(compiled)
    assert "source_record_id > %s" in page_statement(compiled)
