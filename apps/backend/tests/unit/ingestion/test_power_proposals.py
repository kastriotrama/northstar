"""Power rulings proposed 2026-10-07: electric tolerance, reviewed pairs, veteran cars.

Each turns a power conflict into weaker evidence or a match, so each test pairs
the gain with the sibling or control car it must not reach.
"""

from dataclasses import replace

import pytest

from ingestion.fuzzy_matching import (
    FuzzyMatchConfig,
    FuzzyMatchResult,
    FuzzyVehicleMatcher,
    ManufacturerCandidateIndex,
    VehicleCandidate,
    VehicleMatchQuery,
)
from ingestion.tecdoc.power_equivalences import (
    REVIEWED_POWER_EQUIVALENCES,
    reviewed_power_equivalent,
)

_ELECTRIC = frozenset({"electric"})
_PETROL = frozenset({"petrol"})


def _match(catalog: tuple[VehicleCandidate, ...], query: VehicleMatchQuery,
           config: FuzzyMatchConfig | None = None) -> FuzzyMatchResult:
    return FuzzyVehicleMatcher(ManufacturerCandidateIndex(catalog), config).match(query)


def _ktype(reference: str, maker: str, model: str, power_kw: int, *, fuels: frozenset[str] = _ELECTRIC,
           **fields: object) -> VehicleCandidate:
    base: dict[str, object] = {"year_from": 2024, "fuels": fuels, "power_kw": power_kw}
    return VehicleCandidate(reference, maker, model, **{**base, **fields})  # type: ignore[arg-type]


def _car(maker: str, model: str, power_kw: int, *, fuels: frozenset[str] = _ELECTRIC,
         year: int | None = 2025) -> VehicleMatchQuery:
    return VehicleMatchQuery(model, manufacturer=maker, year=year, fuels=fuels, power_kw=power_kw)


def _power(result: FuzzyMatchResult, reference: str) -> str:
    """How one KType's power read: matched, conflict, or the unverified marker."""

    candidate = next(c for c in result.candidates if c.candidate_reference == reference)
    if "power_kw" in candidate.matched_fields:
        return "matched"
    if "power_kw" in candidate.conflicting_fields:
        return "conflict"
    return next(field for field in candidate.missing_fields if field.startswith("power_kw"))


# --- electric tolerance -----------------------------------------------------------


@pytest.mark.parametrize(("registered", "catalog"), [(168, 165), (255, 252), (475, 470), (162, 165)])
def test_an_electric_car_a_few_kw_from_its_models_only_figure_near_is_unverified(
    registered: int, catalog: int
) -> None:
    result = _match((_ktype("k", "Toyota", "bZ4X", catalog), _ktype("far", "Toyota", "bZ4X", 123)),
                    _car("Toyota", "bZ4X", registered))

    assert _power(result, "k") == "power_kw_electric_unverified"
    assert result.candidates[0].candidate_reference == "k"
    assert result.eligible_for_auto_resolution


def test_a_sibling_with_the_exact_figure_keeps_the_near_one_a_conflict() -> None:
    # Two drivetrains 3 kW apart (a Tesla Model Y at 255 and at 258 kW).
    result = _match((_ktype("exact", "Tesla", "MODEL Y", 255), _ktype("near", "Tesla", "MODEL Y", 258)),
                    _car("Tesla", "MODEL Y", 255))

    assert (_power(result, "exact"), _power(result, "near")) == ("matched", "conflict")


def test_a_car_between_two_figures_of_its_model_is_neithers() -> None:
    result = _match((_ktype("low", "Tesla", "MODEL Y", 252), _ktype("high", "Tesla", "MODEL Y", 258)),
                    _car("Tesla", "MODEL Y", 255))

    assert (_power(result, "low"), _power(result, "high")) == ("conflict", "conflict")
    assert not result.eligible_for_auto_resolution


@pytest.mark.parametrize(
    ("registered", "car_fuels", "ktype_fuels"),
    [
        (160, _ELECTRIC, _ELECTRIC),  # 3 % apart: another drivetrain
        (168, _PETROL, _PETROL),  # a combustion car
        (168, frozenset({"petrol", "electric", "hybrid petrol"}), frozenset({"hybrid petrol"})),  # a hybrid
        (168, _ELECTRIC, _PETROL),
    ],
)
def test_the_electric_tolerance_reaches_no_further(
    registered: int, car_fuels: frozenset[str], ktype_fuels: frozenset[str]
) -> None:
    result = _match((_ktype("k", "Toyota", "bZ4X", 165, fuels=ktype_fuels),),
                    _car("Toyota", "bZ4X", registered, fuels=car_fuels))

    assert "power_kw_electric_unverified" not in result.candidates[0].missing_fields
    assert _power(result, "k") == "conflict"


def test_the_electric_tolerance_can_be_switched_off() -> None:
    result = _match((_ktype("k", "Toyota", "bZ4X", 165),), _car("Toyota", "bZ4X", 168),
                    replace(FuzzyMatchConfig(), electric_power_tolerance=0.0))

    assert _power(result, "k") == "conflict"


# --- reviewed pairs ---------------------------------------------------------------


def _a6(*powers: int) -> tuple[VehicleCandidate, ...]:
    return tuple(_ktype(str(power), "AUDI", "A6 e-tron Avant (GH5)", power, model_aliases=("A6",))
                 for power in powers)


@pytest.mark.parametrize(("registered", "catalog"), [(210, 240), (270, 280), (315, 340), (370, 405)])
def test_a_reviewed_pair_is_the_same_power(registered: int, catalog: int) -> None:
    result = _match(_a6(240, 280, 340, 405), _car("Audi", "A6", registered))

    assert result.candidates[0].candidate_reference == str(catalog)
    assert _power(result, str(catalog)) == "matched"
    assert all(_power(result, c.candidate_reference) == "conflict" for c in result.candidates[1:])
    assert result.eligible_for_auto_resolution


def test_a_pair_holds_for_its_model_and_for_electric_cars_only() -> None:
    # The Q6 e-tron has no 270 / 280 pair, and a petrol A6 none at all.
    q6 = (_ktype("q6", "AUDI", "Q6 E-TRON (GFB)", 280, model_aliases=("Q6",)),)
    assert _power(_match(q6, _car("Audi", "Q6", 270)), "q6") == "conflict"
    petrol = (_ktype("a6", "AUDI", "A6 e-tron Avant (GH5)", 280, fuels=_PETROL, model_aliases=("A6",)),)
    assert _power(_match(petrol, _car("Audi", "A6", 270, fuels=_PETROL)), "a6") == "conflict"


def test_a_figure_with_a_ktype_of_its_own_is_that_ktypes() -> None:
    # 270 kW exists on the model: the exact KType, not the pair's 280.
    result = _match(_a6(270, 280), _car("Audi", "A6", 270))

    assert result.candidates[0].candidate_reference == "270"
    assert _power(result, "270") == "matched"


def test_the_reviewed_pairs_can_be_switched_off() -> None:
    result = _match(_a6(280), _car("Audi", "A6", 270),
                    replace(FuzzyMatchConfig(), reviewed_power_equivalences=False))

    assert _power(result, "280") == "conflict"


def test_every_reviewed_pair_goes_up_and_never_onto_another_registry_figure() -> None:
    for equivalence in REVIEWED_POWER_EQUIVALENCES:
        assert all(registry < tecdoc for registry, tecdoc in equivalence.pairs.items())
        # One to one, in ascending order: a pair never crosses another.
        assert sorted(equivalence.pairs.values()) == [equivalence.pairs[k] for k in sorted(equivalence.pairs)]
        assert not set(equivalence.pairs) & set(equivalence.pairs.values())
        assert equivalence.evidence
    assert reviewed_power_equivalent("Porsche", "MACAN (XAB)", 285) == 300
    assert reviewed_power_equivalent("Porsche", "MACAN (95B)", 285) is None
    assert reviewed_power_equivalent("POLESTAR", "POLESTAR 2 (534)", 150) == 300
    assert reviewed_power_equivalent("MINI", "MINI COUNTRYMAN (U25)", 225) == 230
    assert reviewed_power_equivalent("MINI", "MINI COUNTRYMAN (F60)", 225) is None


# --- veteran cars -----------------------------------------------------------------


def _amazon(reference: str, power_kw: int) -> VehicleCandidate:
    return VehicleCandidate(reference, "Volvo", "P 122 S AMAZON", model_aliases=("AMAZON",), year_from=1959,
                            year_to=1971, fuels=_PETROL, power_kw=power_kw)


@pytest.mark.parametrize("year", [1958, 1965, 1974])
def test_a_veteran_cars_power_contradicts_no_ktype_when_none_carries_it(year: int) -> None:
    # Registered at 55 kW; TecDoc knows the Amazon at 59 and 74.
    result = _match((_amazon("b18", 59), _amazon("b20", 74)), _car("Volvo", "AMAZON", 55, fuels=_PETROL, year=year))

    assert {_power(result, "b18"), _power(result, "b20")} == {"power_kw_veteran_unverified"}
    # Nothing tells the two apart: left to a person.
    assert not result.eligible_for_auto_resolution


def test_a_veteran_with_one_ktype_left_is_not_stopped_by_its_power() -> None:
    result = _match((_amazon("b18", 59),), _car("Volvo", "AMAZON", 55, fuels=_PETROL, year=1965))

    assert _power(result, "b18") == "power_kw_veteran_unverified"
    assert not result.candidates[0].conflicting_fields


@pytest.mark.parametrize(("registered", "expected"), [(44, "matched"), (45, "power_kw")])
def test_where_a_ktype_carries_the_veterans_power_the_others_contradict_as_before(
    registered: int, expected: str
) -> None:
    # 44 kW is the B16's figure (45 is the same within rounding): power decides, and
    # the 59 kW KType is a conflict, not a rival.
    result = _match((_amazon("b18", 59), _amazon("b16", 44)),
                    _car("Volvo", "AMAZON", registered, fuels=_PETROL, year=1962))

    assert result.candidates[0].candidate_reference == "b16"
    assert (_power(result, "b16"), _power(result, "b18")) == (expected, "conflict")
    assert result.eligible_for_auto_resolution


@pytest.mark.parametrize("year", [1975, 1990, None])
def test_from_1975_on_and_without_a_year_power_contradicts_as_before(year: int | None) -> None:
    ktype = VehicleCandidate("k", "Volvo", "240", year_from=1974, year_to=1993, fuels=_PETROL, power_kw=83)

    result = _match((ktype,), _car("Volvo", "240", 90, fuels=_PETROL, year=year))

    assert _power(result, "k") == "conflict"


def test_the_veteran_rule_can_be_switched_off() -> None:
    result = _match((_amazon("b18", 59),), _car("Volvo", "AMAZON", 55, fuels=_PETROL, year=1965),
                    replace(FuzzyMatchConfig(), veteran_power_before_year=0))

    assert _power(result, "b18") == "conflict"
