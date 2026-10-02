"""Vehicles-tab filters compile to SQL over `core.vehicles` -- whitelisted, values bound."""

from datetime import date

import pytest

from ingestion.vehicle_core_query import (
    SearchWord,
    VehicleSearch,
    compile_search,
    compile_term,
    compile_vehicle_filter,
    filterable_column,
    is_vehicle_id,
    search_words,
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


def test_search_words_are_capped_and_also_joined() -> None:
    assert search_words("  abc 123 ") == (["abc", "123"], "ABC123")
    assert search_words("volvo") == (["volvo"], None)
    assert search_words("   ") == ([], None)
    assert len(search_words("a b c d e f g h")[0]) == 6


def test_a_word_compiles_to_plate_and_vin_prefixes_and_only_what_it_resolved_to() -> None:
    bare = compile_search(VehicleSearch((SearchWord("LGF109", "%LGF109%"),)))

    # Nothing to guess at: no sub-select, so the planner can use the indexes.
    assert bare.sql == "(v.plate LIKE %s OR v.vin LIKE %s)"
    assert bare.parameters == ["LGF109%", "LGF109%"]

    volvo = SearchWord(
        "VOLVO",
        "%volvo%",
        vehicle_ids=("NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7G",),
        manufacturers=("VOLVO",),
        model_families=("VOLVO 240",),
    )
    resolved = compile_search(VehicleSearch((volvo,)))

    assert resolved.sql == (
        "(v.plate LIKE %s OR v.vin LIKE %s OR v.vehicle_id = ANY(%s) "
        "OR v.manufacturer = ANY(%s) OR v.model_family = ANY(%s))"
    )
    assert resolved.parameters == [
        "VOLVO%",
        "VOLVO%",
        ["NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7G"],
        ["VOLVO"],
        ["VOLVO 240"],
    ]


def test_prefixes_escape_like_wildcards() -> None:
    compiled = compile_search(VehicleSearch((SearchWord("A_B%", "%a\\_b\\%%"),)))

    assert compiled.parameters == ["A\\_B\\%%", "A\\_B\\%%"]


def test_a_plate_written_with_a_space_is_also_tried_whole() -> None:
    search = VehicleSearch(
        (SearchWord("ABC", "%abc%"), SearchWord("123", "%123%")),
        joined=SearchWord("ABC123", "%ABC123%", vehicle_ids=("NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7G",)),
    )

    compiled = compile_search(search)

    # Every word must match -- or the words, run together, are one identifier.
    assert compiled.sql == (
        "(((v.plate LIKE %s OR v.vin LIKE %s) AND (v.plate LIKE %s OR v.vin LIKE %s)) "
        "OR (v.plate LIKE %s OR v.vin LIKE %s OR v.vehicle_id = ANY(%s)))"
    )
    assert compiled.parameters[-3:] == [
        "ABC123%",
        "ABC123%",
        ["NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7G"],
    ]


def test_a_facet_lifts_its_own_clauses() -> None:
    terms = [("manufacturer", "equals", ("VOLVO",)), ("fuel", "equals", ("diesel",))]

    lifted = compile_vehicle_filter(terms, skip_field="manufacturer")

    assert lifted.sql == "v.fuel = ANY(%s)"
    assert compile_vehicle_filter([]).sql == "true"


def test_the_search_is_anded_with_the_conditions() -> None:
    search = VehicleSearch((SearchWord("ABC123", "%ABC123%"),))

    compiled = compile_vehicle_filter([("fuel", "equals", ("diesel",))], search)

    assert compiled.sql == "v.fuel = ANY(%s) AND ((v.plate LIKE %s OR v.vin LIKE %s))"
    assert compiled.parameters == [["diesel"], "ABC123%", "ABC123%"]


def test_vehicle_ids_are_recognised_by_shape() -> None:
    assert is_vehicle_id("NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7G")
    assert not is_vehicle_id("VEH-01J8Z3Y5W2QK4T7B9C1D3E5F7G")
    assert not is_vehicle_id("NOR-81J8Z3Y5W2QK4T7B9C1D3E5F7G")  # beyond 128 bits
    assert not is_vehicle_id("ABC123")
