"""Filter compilation over the promoted TecDoc vehicle CTE.

`FILTERABLE_FIELDS` is the security boundary: a field name is interpolated into
SQL because a column name cannot be bound, so the allowlist is what stands
between a request and arbitrary SQL. Values must never be interpolated. Both
properties are asserted here rather than assumed, because nothing else in the
stack re-checks them.
"""

from __future__ import annotations

import pytest

from api.app.features.tecdoc_review.predicate import (
    FILTERABLE_FIELDS,
    GAP_FIELDS,
    NUMERIC_OPERATORS,
    SUPPORTED_OPERATORS,
    UnknownTecDocFieldError,
    compile_condition,
    compile_conditions,
    gap_predicate,
)


class TestTheAllowlistIsTheBoundary:
    def test_an_unknown_field_is_refused(self) -> None:
        with pytest.raises(UnknownTecDocFieldError, match="not a filterable"):
            compile_condition("dropped_table", "equals", ("x",))

    def test_a_field_name_carrying_sql_is_refused(self) -> None:
        with pytest.raises(UnknownTecDocFieldError):
            compile_condition("manufacturer'; DROP TABLE core.vehicles; --", "equals", ("x",))

    def test_an_unknown_operator_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unsupported operator"):
            compile_condition("manufacturer", "regex", ("x",))

    @pytest.mark.parametrize("field", sorted(FILTERABLE_FIELDS))
    def test_every_allowlisted_field_compiles(self, field: str) -> None:
        compiled = compile_condition(field, "equals", ("value",))

        assert compiled.parameters == [["value"]]

    @pytest.mark.parametrize("field", sorted(FILTERABLE_FIELDS))
    def test_no_field_expression_can_close_a_quote_or_comment(self, field: str) -> None:
        """The expressions are hand-written; a stray quote would be injectable."""

        expression = FILTERABLE_FIELDS[field]

        assert expression.count("'") % 2 == 0
        assert "--" not in expression
        assert ";" not in expression


class TestValuesAreAlwaysBound:
    @pytest.mark.parametrize("operator", sorted(SUPPORTED_OPERATORS - NUMERIC_OPERATORS))
    def test_a_value_never_reaches_the_sql(self, operator: str) -> None:
        # No `%` or `_` in the payload: a LIKE operator escapes those, and this
        # test is about the value staying out of the SQL, not about escaping.
        hostile = "'; DROP TABLE vehicles; --"

        compiled = compile_condition("manufacturer", operator, (hostile,))

        assert "DROP TABLE" not in compiled.sql
        assert any(hostile in str(parameter) for parameter in compiled.parameters)

    def test_the_placeholder_count_matches_the_parameter_count(self) -> None:
        compiled = compile_conditions(
            [
                ("manufacturer", "equals", ("VOLVO", "BMW")),
                ("model_family", "contains", ("XC", "V")),
                ("year_from", "gte", ("2015",)),
            ]
        )

        assert compiled.sql.count("%s") == len(compiled.parameters)


class TestLikeWildcardsAreNeutralised:
    """A reviewer typing a literal code expects it to stay literal."""

    @pytest.mark.parametrize("operator", ["contains", "starts_with"])
    @pytest.mark.parametrize(
        ("typed", "expected"),
        [
            ("50%", "50\\%"),
            ("BMW_3", "BMW\\_3"),
            ("back\\slash", "back\\\\slash"),
        ],
    )
    def test_a_wildcard_in_the_value_is_escaped(
        self, operator: str, typed: str, expected: str
    ) -> None:
        compiled = compile_condition("manufacturer", operator, (typed,))

        assert expected in compiled.parameters[0]

    @pytest.mark.parametrize("operator", ["contains", "starts_with"])
    def test_the_clause_declares_its_escape_character(self, operator: str) -> None:
        compiled = compile_condition("manufacturer", operator, ("x",))

        assert "ESCAPE" in compiled.sql

    def test_the_surrounding_wildcards_survive_escaping(self) -> None:
        contains = compile_condition("manufacturer", "contains", ("VOL",))
        starts = compile_condition("manufacturer", "starts_with", ("VOL",))

        assert contains.parameters == ["%VOL%"]
        assert starts.parameters == ["VOL%"]


class TestOperators:
    def test_equals_ors_its_values_in_one_array_bind(self) -> None:
        compiled = compile_condition("manufacturer", "equals", ("VOLVO", "BMW"))

        assert compiled.sql.endswith("= ANY(%s)")
        assert compiled.parameters == [["VOLVO", "BMW"]]

    def test_not_equals_keeps_null_rows(self) -> None:
        """A NULL is not equal to anything, so `NOT (x = ANY)` would drop it."""

        compiled = compile_condition("manufacturer", "not_equals", ("VOLVO",))

        assert "IS NULL OR NOT" in compiled.sql

    def test_a_numeric_operator_on_a_text_field_is_refused(self) -> None:
        with pytest.raises(UnknownTecDocFieldError, match="does not support"):
            compile_condition("manufacturer", "gte", ("2015",))

    def test_a_numeric_comparison_guards_against_non_numeric_rows(self) -> None:
        compiled = compile_condition("year_from", "gte", ("2015",))

        assert "CASE WHEN" in compiled.sql
        assert "::bigint" in compiled.sql

    def test_a_numeric_operator_takes_exactly_one_value(self) -> None:
        with pytest.raises(ValueError, match="exactly one value"):
            compile_condition("year_from", "gte", ("2015", "2016"))

    def test_blank_values_are_refused_rather_than_matching_everything(self) -> None:
        with pytest.raises(ValueError, match="no values"):
            compile_condition("manufacturer", "equals", ("", "   "))

    def test_blank_values_are_dropped_from_a_mixed_list(self) -> None:
        compiled = compile_condition("manufacturer", "equals", ("VOLVO", "  "))

        assert compiled.parameters == [["VOLVO"]]


class TestCombining:
    def test_no_conditions_matches_every_row(self) -> None:
        compiled = compile_conditions([])

        assert compiled.sql == "true"
        assert compiled.parameters == []

    def test_clauses_are_anded(self) -> None:
        compiled = compile_conditions(
            [("manufacturer", "equals", ("VOLVO",)), ("model_family", "equals", ("XC90",))]
        )

        assert " AND " in compiled.sql

    def test_an_unresolved_field_restricts_to_that_gap(self) -> None:
        compiled = compile_conditions([], unresolved_field="bodywork_form")

        assert gap_predicate("bodywork_form") in compiled.sql

    def test_an_unresolved_field_survives_alongside_conditions(self) -> None:
        compiled = compile_conditions(
            [("manufacturer", "equals", ("VOLVO",))], unresolved_field="drive_type"
        )

        assert compiled.sql.count("%s") == len(compiled.parameters)
        assert gap_predicate("drive_type") in compiled.sql

    def test_an_unknown_gap_is_refused(self) -> None:
        with pytest.raises(UnknownTecDocFieldError, match="not a known TecDoc gap"):
            compile_conditions([], unresolved_field="nonsense")


class TestGapFields:
    @pytest.mark.parametrize("field", sorted(GAP_FIELDS))
    def test_every_gap_has_a_description_and_a_predicate(self, field: str) -> None:
        description, predicate = GAP_FIELDS[field]

        assert description.strip()
        assert predicate.strip()

    def test_the_three_resolvable_gaps_share_their_names_with_canonical_fields(self) -> None:
        """A gap counted here and a rule proposed there must be one question."""

        from ingestion.tecdoc.canonical_rule_proposals import FIELD_SOURCES

        canonical_fields = {source.canonical_field for source in FIELD_SOURCES}

        assert {"energy_sources", "bodywork_form", "drive_type"} <= canonical_fields
        assert {"energy_sources", "bodywork_form", "drive_type"} <= set(GAP_FIELDS)
