"""The reviewed drive layouts: which axle a two-wheel-drive car of a model drives."""

from __future__ import annotations

import pytest

from ingestion.vehicle_core_rules import (
    DRIVE_LAYOUT_FAMILY,
    DRIVE_VARIANT_FAMILY,
    FAMILIES_BY_ID,
    key_sql,
    learn_statement,
)
from ingestion.vehicle_drive_layouts import (
    _BY_TEXT,
    _VARIANTS,
    REVIEWED_DRIVE_LAYOUTS,
    Era,
    drive_layout,
    drive_variant,
)


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


# --- variants: what the model alone does not settle ------------------------------------

ELECTRIC = "electricity"


def _variant(
    make: str,
    model: str | None,
    year: int,
    fuel: str,
    power: int | None,
    *,
    body: str | None = None,
    text: str | None = None,
    four_wheel: bool | None = None,
) -> str | None:
    return drive_variant(make, model, year, fuel, None, power, body, text, four_wheel)


@pytest.mark.parametrize(
    ("make", "model", "year", "power", "drive"),
    [
        ("Tesla", "Model Y", 2024, 220, "rwd"),
        ("Tesla", "Model Y", 2024, 378, "awd"),
        ("Polestar", "2", 2023, 170, "fwd"),  # the single motor moved to the rear axle that year
        ("Polestar", "2", 2023, 220, "rwd"),
        ("Polestar", "2", 2024, 310, "awd"),
        ("Volvo", "EX30", 2026, 200, "rwd"),
        ("Volvo", "EX30", 2026, 315, "awd"),
        ("BMW", "iX1", 2026, 150, "fwd"),
        ("BMW", "iX1", 2026, 225, "awd"),
        ("Toyota", "bZ4X", 2023, 150, "fwd"),
        ("Toyota", "bZ4X", 2023, 160, "awd"),  # two 80 kW motors
        ("MG", "MG4", 2026, 110, "fwd"),  # the MG4 Urban
        ("MG", "MG4", 2023, 150, "rwd"),
        ("Kia", "EV2", 2026, 107, "fwd"),
    ],
)
def test_an_electric_cars_power_names_its_variant(
    make: str, model: str, year: int, power: int, drive: str
) -> None:
    assert _variant(make, model, year, ELECTRIC, power) == drive


def test_power_between_two_variants_or_missing_states_nothing() -> None:
    assert _variant("Tesla", "Model Y", 2024, ELECTRIC, 280) is None
    assert _variant("Tesla", "Model Y", 2024, ELECTRIC, None) is None
    assert _variant("Tesla", "Model Y", 2024, "petrol", 220) is None
    assert _variant("Toyota", "Yaris Cross", 2026, "petrol", 68) is None  # same power, either drive
    # A variant sold with four-wheel drive only: named without a statement, and silent
    # when the registry says the car is not four-wheel drive.
    assert _variant("Volvo", "XC60", 2023, "petrol", 220) == "awd"
    assert _variant("Volvo", "XC60", 2023, "petrol", 220, four_wheel=False) is None


def test_a_variant_never_contradicts_the_registrys_statement() -> None:
    # Marked as not four-wheel drive: a four-wheel-drive variant states nothing.
    assert _variant("Tesla", "Model Y", 2024, ELECTRIC, 378, four_wheel=False) is None
    assert _variant("Tesla", "Model Y", 2024, ELECTRIC, 220, four_wheel=False) == "rwd"
    # Marked as four-wheel drive: never ours.
    assert _variant("Tesla", "Model Y", 2024, ELECTRIC, 220, four_wheel=True) is None


def test_body_and_text_decide_only_for_a_car_marked_as_two_wheel_drive() -> None:
    tourer = {"body": "multi_purpose_vehicle", "text": "218D GRAN TOURER"}
    assert _variant("BMW", "2 Series", 2018, "diesel", 110, four_wheel=False, **tourer) == "fwd"
    assert _variant("BMW", "2 Series", 2018, "petrol", 272, four_wheel=False, body="coupe") == "rwd"
    # From 2020 a "coupe" may be the front-driven Gran Coupe; an M2 never is.
    assert _variant("BMW", "2 Series", 2021, "petrol", 100, four_wheel=False, body="coupe") is None
    assert _variant("BMW", "2 Series", 2021, "petrol", 302, four_wheel=False, body="coupe",
                    text="M2 COMPETITION") == "rwd"
    # Without the statement it could be the four-wheel-drive plug-in tourer.
    assert _variant("BMW", "2 Series", 2018, "petrol", 100, **tourer) is None
    assert _variant("Ford", "Transit", 2019, "diesel", 96, four_wheel=False,
                    text="TRANSIT CUSTOM") == "fwd"
    assert _variant("Ford", "Transit", 2019, "diesel", 96, four_wheel=False, text="TRANSIT") is None


def test_a_model_never_sold_with_four_driven_wheels_needs_no_statement() -> None:
    assert _variant("Nissan", "Micra", 2025, ELECTRIC, 110) == "fwd"
    assert _variant("Cupra", "Born", 2024, ELECTRIC, 170) == "rwd"
    assert _variant("Volkswagen", "Polo", 2019, "petrol", 70) == "fwd"
    # A rally car built on one is four-wheel drive; its power gives it away.
    assert _variant("Volkswagen", "Polo", 2019, "petrol", 235) is None
    # A Golf came with four-wheel drive: without a statement nothing is said.
    assert _variant("Volkswagen", "Golf", 2019, "petrol", 110) is None


@pytest.mark.parametrize(
    ("make", "year", "text", "drive"),
    [
        ("Volkswagen", 1973, "VOLKSWAGEN 1303 S 135031", "rwd"),
        ("Volkswagen", 1976, "VOLKSWAGEN KLEINBUS 221", "rwd"),
        ("Volkswagen", 1995, "VOLKSWAGEN KOMBI 2,5", None),
        ("Ford", 2005, "FORD DM2 FOCUS C-MAX", "fwd"),
        ("Ford", 1966, "FORD 65 A MUSTANG", "rwd"),
        ("Ford", 1968, "FORD 17 M 1700", "rwd"),
        ("Renault", 1961, "RENAULT R 1090 DAUPHINE", "rwd"),
        ("Renault", 1994, "RENAULT B57B05 RT", "fwd"),
        ("Renault", 1994, "RENAULT MASTER", None),
        ("Renault", 1994, "RENAULT", None),
        ("Peugeot", 1970, "PEUGEOT 404", "rwd"),
        ("Peugeot", 1978, "PEUGEOT 104 SL 543705", "fwd"),
        ("Pontiac", 1992, "PONTIAC TRANS AM", "rwd"),
        ("Pontiac", 1992, "PONTIAC TRANS SPORT", "fwd"),
        ("BMC", 1965, "BMC 850 SALOON", "fwd"),
        ("Austin", 1962, "AUSTIN HEALEY SPRITE", "rwd"),
        ("Toyota", 2013, "86", "rwd"),
        ("Volvo", 1989, "VOLVO KX183E", "fwd"),
        ("Volvo", 1988, "VOLVO 360 GL", "rwd"),
    ],
)
def test_registry_text_names_the_model_of_a_car_without_one(
    make: str, year: int, text: str, drive: str | None
) -> None:
    assert _variant(make, None, year, "petrol", 50, text=text, four_wheel=False) == drive
    # Only for a car the registry marks as not four-wheel drive.
    assert _variant(make, None, year, "petrol", 50, text=text) is None


def test_the_variant_tables_are_well_formed() -> None:
    import re

    for (make, model), variants in _VARIANTS.items():
        assert make and model and variants
        for variant in variants:
            assert variant.kw is None or variant.kw[0] <= variant.kw[1], (make, model)
            if variant.text:
                re.compile(variant.text)
    for patterns in _BY_TEXT.values():
        for pattern, layout, first, last in patterns:
            re.compile(pattern)
            assert layout in ("fwd", "rwd")
            assert first is None or last is None or first <= last


def test_the_evidence_families_learn_from_real_drive_types_and_guard_their_fills() -> None:
    evidence = [FAMILIES_BY_ID[name] for name in ("DRV-EVP", "DRV-EME", "DRV-EMP", "DRV-EV")]
    order = [family.family for family in FAMILIES_BY_ID.values()]
    # The table first, then the variants, then the cars alike from the most specific key down.
    assert order.index(DRIVE_LAYOUT_FAMILY) < order.index(DRIVE_VARIANT_FAMILY)
    assert order.index(DRIVE_VARIANT_FAMILY) < order.index("DRV-EVP") < order.index("DRV-EME")
    assert order.index("DRV-EME") < order.index("DRV-EMP") < order.index("DRV-EV")
    for family in evidence:
        assert (family.target_field, family.learner, family.replaces) == (
            "drive_type", "drive_evidence", ("2wd",))
        assert family.min_agreement == 0.98
        # A model name and a power figure are reused across generations: the key that
        # names only those also names the build year (or the engine, or the VIN).
        assert {"vin_descriptor", "engine_code", "production_year"} & set(family.key_fields)
        statement = learn_statement(family)
        # The generic value is no evidence, and neither are these families' own fills.
        assert "drive_type::text IN ('fwd', 'rwd', 'awd')" in statement
        assert "<> 'rule:DRV-E'" in statement
        assert statement.count("%s") == 2  # support and agreement, no source
        # A fill never contradicts the registry's four-wheel-drive statement.
        assert "v.registry_all_wheel_drive = (r.value = 'awd')" in family.guard
    variant = FAMILIES_BY_ID[DRIVE_VARIANT_FAMILY]
    assert (variant.learner, variant.key_fields[-1], bool(variant.guard)) == (
        "reviewed", "drive_statement", True)
    assert "IS FALSE THEN 'no'" in key_sql("drive_statement", "v")
