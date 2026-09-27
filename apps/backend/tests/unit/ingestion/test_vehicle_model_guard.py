"""The model guard: a learned model never contradicts the model word in the car's own text."""

import pytest

from ingestion.fuzzy_matching import VehicleCandidate, same_model_family
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator
from ingestion.vehicle_model_guard import ModelGuard, count_verdicts

CATALOG = (
    VehicleCandidate("golf", "VW", "GOLF IV (1J1)", model_aliases=("GOLF",), year_from=1997, year_to=2005),
    VehicleCandidate("bora", "VW", "BORA I (1J2)", model_aliases=("BORA",), year_from=1998, year_to=2005),
    VehicleCandidate("e30", "BMW", "3 (E30)", model_aliases=("3",), year_from=1982, year_to=1994),
    VehicleCandidate("w116", "MERCEDES-BENZ", "S-CLASS (W116)", model_aliases=("S-CLASS",),
                     year_from=1972, year_to=1980),
    VehicleCandidate("s123", "MERCEDES-BENZ", "123 T-Model (S123)", model_aliases=("200 T",),
                     year_from=1977, year_to=1986),
    VehicleCandidate("ceed", "KIA", "CEE'D (ED)", year_from=2006, year_to=2012),
    VehicleCandidate("bk", "MAZDA", "3 (BK)", year_from=2003, year_to=2009),
)


@pytest.fixture(scope="module")
def guard() -> ModelGuard:
    return ModelGuard(TecDocDryRunEvaluator(CATALOG))


@pytest.mark.parametrize(
    ("value", "catalog_model", "manufacturer", "expected"),
    [
        ("Golf", "GOLF IV (1J1)", "VW", True),
        ("Golf", "BORA I (1J2)", "VW", False),
        ("3 Series", "3 (E46)", "BMW", True),
        ("5 Series", "3 (E46)", "BMW", False),
        ("C-Class", "C-CLASS Coupe (CL203)", "MERCEDES-BENZ", True),
        ("Ceed", "CEE'D (ED)", "KIA", True),
        ("Mazda3", "3 (BK)", "MAZDA", True),
        ("XC40", "EX40 (536)", "VOLVO", False),
        ("V70", "XC70 I Cross Country (295)", "VOLVO", False),
        ("307", "307 SW (3H)", "PEUGEOT", True),
        ("3 Series", "320", "BMW", True),
        ("3 Series", "520", "BMW", False),
        ("SLK", "170", "MERCEDES-BENZ", False),
    ],
)
def test_same_family(value: str, catalog_model: str, manufacturer: str, expected: bool) -> None:
    assert same_model_family(value, catalog_model, manufacturer) is expected


def test_text_that_names_another_model_refuses_the_fill(guard: ModelGuard) -> None:
    verdict = guard.verdict(manufacturer="VW", model_family="Golf", evidence={"brand": "VW BORA 1,6"})

    assert verdict is not None
    assert (verdict.reason, verdict.detail) == ("text_names_another_model", "BORA I (1J2)")


@pytest.mark.parametrize(
    ("manufacturer", "model", "brand"),
    [
        ("VW", "Golf", "VW GOLF 1,6"),  # the text agrees
        ("VW", "Golf", "VW"),  # the text names no model
        ("BMW", "3 Series", "BMW 318I"),  # same family, spelled differently
        ("KIA", "Ceed", "KIA CEED 1,6"),
        ("MAZDA", "Mazda3", "MAZDA MAZDA3"),
        # A trim word is weaker evidence than the rule: "200 T" is on the 123
        # T-Model, but "C" is the model word and names no catalog model here.
        ("MERCEDES-BENZ", "C-Class", "MERCEDES-BENZ C 200 T"),
        # A model a catalog lacks for the car's era is not contradicted by it.
        ("MERCEDES-BENZ", "S-Class", "MERCEDES-BENZ"),
    ],
)
def test_a_fill_the_model_word_does_not_contradict_is_kept(
    guard: ModelGuard, manufacturer: str, model: str, brand: str
) -> None:
    assert guard.verdict(manufacturer=manufacturer, model_family=model, evidence={"brand": brand}) is None


def test_the_registry_model_field_counts_as_the_model_word(guard: ModelGuard) -> None:
    verdict = guard.verdict(manufacturer="VW", model_family="Golf", evidence={"brand": "VW", "model": "BORA"})

    assert verdict is not None and verdict.detail == "BORA I (1J2)"


def test_verdicts_are_counted_by_reason(guard: ModelGuard) -> None:
    verdicts = [
        guard.verdict(manufacturer="VW", model_family="Golf", evidence={"brand": b})
        for b in ("VW BORA", "VW BORA 1,6", "VW GOLF")
    ]
    assert count_verdicts(verdicts) == {"text_names_another_model": 2}
