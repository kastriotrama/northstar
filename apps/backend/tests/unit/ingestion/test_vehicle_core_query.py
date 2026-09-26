"""Vehicles-tab filters compile to SQL over `core.vehicles` -- whitelisted, values bound."""

from datetime import date

import pytest

from ingestion.vehicle_core_query import (
    compile_search_text,
    compile_term,
    compile_vehicle_filter,
    filterable_column,
    is_vehicle_id,
)
from ingestion.vehicle_facts_query import UnknownFieldError


def test_only_filterable_fields_reach_the_sql() -> None:
    assert filterable_column("manufacturer") == "v.manufacturer"
    for field in ("field_sources", "vehicle_id; DROP TABLE core.vehicles", "fuel_match_tokens"):
        with pytest.raises(UnknownFieldError):
            compile_term(field, "equals", ["x"])


def test_text_equals_binds_every_value() -> None:
    compiled = compile_term("fuel", "equals", ["diesel", "petrol", " "])

    assert compiled.sql == "v.fuel = ANY(%s)"
    assert compiled.parameters == [["diesel", "petrol"]]


def test_not_equals_keeps_vehicles_without_a_value() -> None:
    compiled = compile_term("vehicle_scope", "not_equals", ["motorhome"])

    assert compiled.sql == "(v.vehicle_scope IS NULL OR NOT (v.vehicle_scope = ANY(%s)))"


def test_integer_columns_compare_as_numbers() -> None:
    assert compile_term("production_year", "gte", ["2015"]).parameters == [2015]
    assert compile_term("power_kw", "equals", ["133", "x"]).parameters == [[133]]
    assert compile_term("power_kw", "equals", ["x"]).sql == "false"
    with pytest.raises(ValueError):
        compile_term("power_kw", "lte", ["lots"])


def test_date_columns_compare_as_dates() -> None:
    compiled = compile_term("first_registration_date", "lte", ["2015-03-12"])

    assert compiled.sql == "v.first_registration_date <= %s"
    assert compiled.parameters == [date(2015, 3, 12)]
    with pytest.raises(ValueError):
        compile_term("first_registration_date", "gte", ["March"])


def test_like_patterns_escape_wildcards() -> None:
    compiled = compile_term("engine_code", "starts_with", ["D4_%"])

    assert compiled.parameters == ["D4\\_\\%%"]


def test_text_search_reaches_previous_plates_and_the_nor_id() -> None:
    compiled = compile_search_text("volvo")

    assert compiled is not None
    assert "core.vehicle_identifiers" in compiled.sql
    assert compiled.parameters == ["VOLVO%", "VOLVO%", "VOLVO", "VOLVO", "%volvo%", "%volvo%"]
    assert compile_search_text("   ") is None


def test_a_plate_written_with_a_space_is_also_tried_whole() -> None:
    compiled = compile_search_text(" abc 123 ")

    assert compiled is not None
    # Every token must match -- or the text, spaces removed, is one identifier.
    assert compiled.sql.count("core.vehicle_identifiers") == 3
    assert compiled.parameters[-4:] == ["ABC123%", "ABC123%", "ABC123", "ABC123"]


def test_a_facet_lifts_its_own_clauses() -> None:
    terms = [("manufacturer", "equals", ("VOLVO",)), ("fuel", "equals", ("diesel",))]

    lifted = compile_vehicle_filter(terms, skip_field="manufacturer")

    assert lifted.sql == "v.fuel = ANY(%s)"
    assert compile_vehicle_filter([], "").sql == "true"


def test_vehicle_ids_are_recognised_by_shape() -> None:
    assert is_vehicle_id("NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7G")
    assert not is_vehicle_id("VEH-01J8Z3Y5W2QK4T7B9C1D3E5F7G")
    assert not is_vehicle_id("NOR-81J8Z3Y5W2QK4T7B9C1D3E5F7G")  # beyond 128 bits
    assert not is_vehicle_id("ABC123")
