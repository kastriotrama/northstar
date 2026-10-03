"""The reviewed drive layouts: which axle a two-wheel-drive car of a model drives."""

from __future__ import annotations

import pytest

from ingestion.vehicle_core_rules import DRIVE_LAYOUT_FAMILY, FAMILIES_BY_ID, key_sql
from ingestion.vehicle_drive_layouts import REVIEWED_DRIVE_LAYOUTS, Era, drive_layout


@pytest.mark.parametrize(
    ("make", "model", "year", "layout"),
    [
        ("Volkswagen", "Golf", 2015, "fwd"),
        ("Volvo", "V70", 2008, "fwd"),
        ("Volvo", "240", 1988, "rwd"),
        ("Volvo", "340", 1985, "rwd"),  # rear-wheel drive although a small hatchback
        ("BMW", "3 Series", 2012, "rwd"),
        ("Mercedes-Benz", "C-Class", 2010, "rwd"),
        ("Mercedes-Benz", "A-Class", 2010, "fwd"),
        ("Volkswagen", "ID.4", 2023, "rwd"),
        ("Tesla", "Model 3", 2021, "rwd"),
        ("Peugeot", "504", 1975, "rwd"),
        ("Saab", "9-5", 2004, "fwd"),
    ],
)
def test_a_model_with_one_layout_has_it_in_every_year(
    make: str, model: str, year: int, layout: str
) -> None:
    assert drive_layout(make, model, year, "petrol") == layout
    assert drive_layout(make, model, None, None) == layout  # the year is not needed


@pytest.mark.parametrize(
    ("make", "model", "year", "layout"),
    [
        # The 1 Series moved to a front-wheel-drive platform in 2019.
        ("BMW", "1 Series", 2012, "rwd"),
        ("BMW", "1 Series", 2021, "fwd"),
        ("BMW", "1 Series", 2019, None),  # the changeover year is left alone
        ("Opel", "Kadett", 1975, "rwd"),
        ("Opel", "Kadett", 1986, "fwd"),
        ("Toyota", "Corolla", 1978, "rwd"),
        ("Toyota", "Corolla", 1985, None),  # the rear-driven coupe ran beside the hatchback
        ("Toyota", "Corolla", 2010, "fwd"),
        ("Volvo", "S90", 1998, "rwd"),
        ("Volvo", "S90", 2018, "fwd"),
        ("Fiat", "500", 1968, "rwd"),
        ("Fiat", "500", 2015, "fwd"),
        ("Renault", "Twingo", 2010, "fwd"),
        ("Renault", "Twingo", 2018, "rwd"),
    ],
)
def test_a_model_that_changed_layout_is_decided_by_its_build_year(
    make: str, model: str, year: int, layout: str | None
) -> None:
    assert drive_layout(make, model, year, "petrol") == layout
    # Without a build year such a model cannot be decided.
    assert drive_layout(make, model, None, "petrol") is None


def test_the_electric_version_can_differ_from_the_combustion_one() -> None:
    assert drive_layout("Volvo", "XC40", 2025, "petrol") == "fwd"
    assert drive_layout("Volvo", "XC40", 2021, "electricity") == "fwd"
    assert drive_layout("Volvo", "XC40", 2025, "electricity") == "rwd"
    assert drive_layout("Volvo", "XC40", 2023, "electricity") is None  # both were built that year
    assert drive_layout("Audi", "A6", 2025, "electricity") == "rwd"
    assert drive_layout("Audi", "A6", 2015, "diesel") == "fwd"


def test_what_is_not_certain_is_left_alone() -> None:
    assert drive_layout("BMW", "2 Series", 2018, "petrol") is None  # coupe and tourer differ
    assert drive_layout("Ford", "Transit", 2015, "diesel") is None  # sold with either axle
    assert drive_layout("Volkswagen", None, 1970, "petrol") is None  # Beetle or K70?
    assert drive_layout("Nosuchmake", "X", 2000, "petrol") is None
    assert drive_layout(None, "Golf", 2000, "petrol") is None


def test_a_make_without_a_model_is_decided_only_where_the_whole_make_agrees() -> None:
    assert drive_layout("Mercedes-Benz", None, 1985, "petrol") == "rwd"
    assert drive_layout("Mercedes-Benz", None, 2005, "petrol") is None  # an A-Class or a C-Class
    assert drive_layout("Porsche", None, None, None) == "rwd"
    assert drive_layout("Saab", None, 1999, "petrol") == "fwd"
    assert drive_layout("Volkswagen", None, 1965, "petrol") == "rwd"  # before the K70
    assert drive_layout("Volvo", None, 1980, "petrol") == "rwd"
    assert drive_layout("Volvo", None, 1990, "petrol") is None  # a 240 or a 480
    assert drive_layout("Chevrolet", None, 1968, "petrol") == "rwd"
    assert drive_layout("Chevrolet", None, 1985, "petrol") is None
    # The electric iOn drives the rear wheels, so an electric Peugeot without a model stays open.
    assert drive_layout("Peugeot", None, 2012, "diesel") == "fwd"
    assert drive_layout("Peugeot", None, 2012, "electricity") is None
    assert drive_layout("Fiat", None, 2005, "diesel") == "fwd"
    assert drive_layout("Fiat", None, 2017, "petrol") is None  # the 124 Spider years


def test_every_entry_is_a_well_formed_statement() -> None:
    assert len(REVIEWED_DRIVE_LAYOUTS) > 700
    for (make, model), eras in REVIEWED_DRIVE_LAYOUTS.items():
        assert make and model and eras, (make, model)
        for era in eras:
            assert isinstance(era, Era) and era.layout in ("fwd", "rwd")
            if era.year_from is not None and era.year_to is not None:
                assert era.year_from <= era.year_to, (make, model)
        # Two eras of one model never claim the same year and fuel.
        for year in range(1930, 2031):
            for fuel in ("petrol", "electricity"):
                assert sum(era.covers(year, fuel) for era in eras) <= 1, (make, model, year, fuel)


def test_the_rule_family_keys_on_the_registrys_two_wheel_drive_mark() -> None:
    family = FAMILIES_BY_ID[DRIVE_LAYOUT_FAMILY]
    assert (family.target_field, family.learner, family.replaces) == ("drive_type", "reviewed", ("2wd",))
    assert family.key_fields[-1] == "two_wheel_drive"
    # The key is NULL, so no rule matches, unless the registry says "not four-wheel drive".
    assert "registry_all_wheel_drive IS FALSE" in key_sql("two_wheel_drive", "v")


def test_years_in_which_a_model_was_four_wheel_drive_only_are_left_alone() -> None:
    # A registry "not four-wheel drive" on such a car contradicts the model: no value is chosen.
    assert drive_layout("Volvo", "XC70", 2008, "diesel") is None
    assert drive_layout("Volvo", "XC70", 2012, "diesel") == "fwd"
    assert drive_layout("BMW", "X3", 2006, "diesel") is None
    assert drive_layout("BMW", "X3", 2015, "diesel") == "rwd"
    assert drive_layout("Porsche", "Macan", 2020, "petrol") is None
    assert drive_layout("Porsche", "Macan", 2025, "electricity") == "rwd"


def test_a_name_reused_on_another_layout_is_decided_by_year() -> None:
    assert drive_layout("Volkswagen", "Transporter", 1985, "petrol") == "rwd"  # engine in the back
    assert drive_layout("Volkswagen", "Transporter", 2005, "diesel") == "fwd"
    assert drive_layout("Volkswagen", "Transporter", 1991, "diesel") is None  # T3 and T4 overlap
    assert drive_layout("Jeep", "Cherokee", 1998, "petrol") == "rwd"
    assert drive_layout("Jeep", "Cherokee", 2016, "diesel") == "fwd"
    assert drive_layout("Dodge", "Dart", 1970, "petrol") == "rwd"
    assert drive_layout("Dodge", "Dart", 2014, "petrol") == "fwd"
