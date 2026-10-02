"""Displacement rounding, the doubled Wankel volume and the hp/PS unit gap.

All three turn a hard conflict into weaker evidence, so each test pairs the gain
with the sibling or control car that must not resolve because of it.
"""

import pytest

from ingestion.fuzzy_matching import (
    DISPLACEMENT_ROUNDING_SIBLING_GUARD,
    HORSEPOWER_UNIT_MAKERS,
    PLUG_IN_POWER_GUARD,
    TOLERATED_EVIDENCE_GUARD,
    FuzzyMatchConfig,
    FuzzyMatchResult,
    FuzzyVehicleMatcher,
    ManufacturerCandidateIndex,
    VehicleCandidate,
    VehicleMatchQuery,
)


def _match(catalog: tuple[VehicleCandidate, ...], query: VehicleMatchQuery,
           config: FuzzyMatchConfig | None = None) -> FuzzyMatchResult:
    return FuzzyVehicleMatcher(ManufacturerCandidateIndex(catalog), config).match(query)


# --- displacement rounding -------------------------------------------------

def _v70(reference: str, displacement_cc: int, **fields: object) -> VehicleCandidate:
    base: dict[str, object] = {
        "year_from": 2007, "year_to": 2016, "fuels": frozenset({"diesel"}), "power_kw": 136,
        "displacement_cc": displacement_cc,
    }
    return VehicleCandidate(reference, "Volvo", "V70", **{**base, **fields})  # type: ignore[arg-type]


def _v70_query(displacement_cc: int) -> VehicleMatchQuery:
    return VehicleMatchQuery("V70", manufacturer="Volvo", year=2008, fuels=frozenset({"diesel"}),
                             power_kw=136, displacement_cc=displacement_cc)


@pytest.mark.parametrize("registered", [2398, 2399, 2400, 2402, 2403, 2404])
def test_a_rounding_gap_of_up_to_three_cc_is_unverified_not_a_conflict(registered: int) -> None:
    result = _match((_v70("d5", 2401),), _v70_query(registered))

    top = result.candidates[0]
    assert "displacement_cc_rounding_unverified" in top.missing_fields
    assert "displacement_cc" not in top.matched_fields + top.conflicting_fields
    assert result.eligible_for_auto_resolution


@pytest.mark.parametrize("registered", [2397, 2405, 1984])
def test_a_gap_of_more_than_three_cc_stays_a_conflict(registered: int) -> None:
    result = _match((_v70("d5", 2401),), _v70_query(registered))

    assert "displacement_cc" in result.candidates[0].conflicting_fields
    assert result.reason == "context_conflict_requires_review"


def test_the_sibling_with_the_exact_cc_stays_ahead_by_more_than_the_margin() -> None:
    result = _match((_v70("rounded", 2401), _v70("exact", 2400)), _v70_query(2400))

    exact, rounded = result.candidates
    assert (exact.candidate_reference, rounded.candidate_reference) == ("exact", "rounded")
    # +0.05 for the exact cc, -0.05 for the rounded one.
    assert round(exact.separation_score - rounded.separation_score, 6) == 0.10
    assert result.eligible_for_auto_resolution


def test_two_rounded_siblings_stay_a_tie_for_the_user() -> None:
    # 2,400 registered; TecDoc lists 2,401 for both the 120 kW and the 136 kW
    # variant and the car has no power: the tolerance gives no new evidence.
    query = VehicleMatchQuery("V70", manufacturer="Volvo", year=2008, fuels=frozenset({"diesel"}),
                              displacement_cc=2400)
    result = _match((_v70("a", 2401), _v70("b", 2401, power_kw=120)), query)

    assert not result.eligible_for_auto_resolution
    assert result.reason == "candidate_margin_not_met"


def test_the_default_gap_between_exact_and_rounded_cc_exceeds_the_margin() -> None:
    config = FuzzyMatchConfig()

    assert config.displacement_tolerance_cc == 3
    assert (
        config.displacement_match_bonus + config.displacement_tolerance_penalty
        > config.automatic_margin
    )


def test_a_zero_tolerance_restores_the_exact_comparison() -> None:
    result = _match((_v70("d5", 2401),), _v70_query(2400), FuzzyMatchConfig(displacement_tolerance_cc=0))

    assert "displacement_cc" in result.candidates[0].conflicting_fields


@pytest.mark.parametrize(
    "fields",
    [
        {"displacement_tolerance_cc": -1},
        {"displacement_tolerance_penalty": 1.5},
        {"displacement_tolerance_penalty": -0.1},
        {"horsepower_unit_slack_kw": -0.5},
        {"power_tolerance_kw": -1},
        {"power_tolerance_penalty": 1.5},
    ],
)
def test_tolerance_settings_are_validated(fields: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        FuzzyMatchConfig(**fields)  # type: ignore[arg-type]


# --- displacement rounding against a plug-in sibling ------------------------

_DIESEL_PLUG_IN_FUELS = frozenset({"diesel", "electric", "hybrid_diesel"})


def _e_class(reference: str, model_alias: str, power_kw: int, electrification: str) -> VehicleCandidate:
    return VehicleCandidate(
        reference, "Mercedes-Benz", "E-CLASS T-Model (S214)", model_aliases=("E-CLASS", model_alias),
        year_from=2023, year_to=2026, fuels=frozenset({"hybrid_diesel"}), displacement_cc=1993,
        power_kw=power_kw, bodyworks=frozenset({"estate"}), electrification=electrification,
    )


_E_220_D = _e_class("220d", "E 220 d 4-matic", 145, "mild_hybrid")
_E_300_DE = _e_class("300de", "E 300 de 4-matic", 230, "plug_in_hybrid")


def _e_class_query(displacement_cc: int) -> VehicleMatchQuery:
    # Registered diesel + electricity at the combustion engine's 145 kW: that is
    # the 220 d's power exactly and also what a 300 de plug-in is registered with.
    return VehicleMatchQuery("E-CLASS", manufacturer="Mercedes-Benz", year=2024, fuels=_DIESEL_PLUG_IN_FUELS,
                             power_kw=145, displacement_cc=displacement_cc, bodywork="estate")


def test_a_rounded_cc_never_resolves_a_plug_in_diesel_to_its_non_plug_in_sibling() -> None:
    # Before the tolerance both KTypes conflicted on 1,992 against 1,993 cc and the
    # car went to review. Tolerated, the 220 d leads by its exact power alone,
    # which says nothing against the 300 de: the two tie for the user.
    result = _match((_E_220_D, _E_300_DE), _e_class_query(1992))

    assert [candidate.candidate_reference for candidate in result.candidates] == ["220d", "300de"]
    assert all(
        "displacement_cc_rounding_unverified" in candidate.missing_fields
        and not candidate.conflicting_fields
        for candidate in result.candidates
    )
    assert not result.eligible_for_auto_resolution
    assert result.reason == "candidate_margin_not_met"
    assert result.guards == (PLUG_IN_POWER_GUARD,)


def test_the_plug_in_tie_is_the_same_with_the_exact_cc() -> None:
    rounded = _match((_E_220_D, _E_300_DE), _e_class_query(1992))
    exact = _match((_E_220_D, _E_300_DE), _e_class_query(1993))

    assert not exact.eligible_for_auto_resolution
    assert (exact.reason, exact.guards) == (rounded.reason, rounded.guards)


# --- Wankel rotary: the registry doubles the chamber volume -----------------

def _rx8(reference: str, power_kw: int, engine_codes: frozenset[str], **fields: object) -> VehicleCandidate:
    base: dict[str, object] = {
        "year_from": 2003, "year_to": 2012, "fuels": frozenset({"petrol"}), "displacement_cc": 1308,
        "power_kw": power_kw, "engine_codes": engine_codes,
    }
    return VehicleCandidate(reference, "Mazda", "RX-8 (SE, FE)", model_aliases=("RX-8",),
                            **{**base, **fields})  # type: ignore[arg-type]


def _rx8_query(displacement_cc: int, manufacturer: str = "Mazda") -> VehicleMatchQuery:
    return VehicleMatchQuery("RX-8", manufacturer=manufacturer, year=2005, fuels=frozenset({"petrol"}),
                             power_kw=170, displacement_cc=displacement_cc)


@pytest.mark.parametrize("code", ["13B-MSP", "13B", "RE13B", "13B-REW", "13BAP", "12A", "12AN2", "RENESIS"])
def test_a_mazda_rotary_registered_with_exactly_double_the_cc_matches(code: str) -> None:
    result = _match(
        (_rx8("170", 170, frozenset({code})), _rx8("141", 141, frozenset({code}))), _rx8_query(2616)
    )

    top = result.candidates[0]
    assert top.candidate_reference == "170"
    assert "displacement_cc" in top.matched_fields
    assert not top.conflicting_fields
    assert result.eligible_for_auto_resolution


@pytest.mark.parametrize("registered", [2615, 2617, 2613, 3924, 654])
def test_only_exactly_double_counts_for_a_rotary(registered: int) -> None:
    result = _match((_rx8("170", 170, frozenset({"13B-MSP"})),), _rx8_query(registered))

    assert "displacement_cc" in result.candidates[0].conflicting_fields


@pytest.mark.parametrize("codes", [frozenset(), frozenset({"L3-VE"}), frozenset({"N307"}), frozenset({"B13B"})])
def test_double_the_cc_on_a_mazda_without_a_rotary_code_stays_a_conflict(codes: frozenset[str]) -> None:
    result = _match((_rx8("170", 170, codes),), _rx8_query(2616))

    assert "displacement_cc" in result.candidates[0].conflicting_fields
    assert not result.eligible_for_auto_resolution


def test_double_the_cc_on_another_maker_stays_a_conflict() -> None:
    # The rule is Mazda's alone, whatever the engine code reads.
    other = VehicleCandidate("ro80", "NSU", "RX-8", year_from=2003, year_to=2012, fuels=frozenset({"petrol"}),
                             displacement_cc=1308, power_kw=170, engine_codes=frozenset({"13B-MSP"}))
    result = _match((other,), _rx8_query(2616, manufacturer="NSU"))

    assert "displacement_cc" in result.candidates[0].conflicting_fields


# --- US-spec power: hp read as PS, or PS read as hp -------------------------

def _us_car(reference: str, manufacturer: str, power_kw: int, **fields: object) -> VehicleCandidate:
    base: dict[str, object] = {
        "year_from": 2004, "year_to": 2010, "fuels": frozenset({"petrol"}), "power_kw": power_kw,
    }
    return VehicleCandidate(reference, manufacturer, "MUSTANG Coupe", model_aliases=("MUSTANG",),
                            **{**base, **fields})  # type: ignore[arg-type]


def _us_query(manufacturer: str, power_kw: int, fuels: frozenset[str] = frozenset({"petrol"})) -> VehicleMatchQuery:
    return VehicleMatchQuery("MUSTANG", manufacturer=manufacturer, year=2007, fuels=fuels, power_kw=power_kw)


def test_the_reviewed_us_makers_are_exactly_these() -> None:
    assert HORSEPOWER_UNIT_MAKERS == {
        "FORD USA", "CHEVROLET", "DODGE", "CHRYSLER", "JEEP", "CADILLAC", "GMC", "BUICK", "LINCOLN", "PONTIAC",
    }


@pytest.mark.parametrize(
    ("registered", "catalog"),
    [
        (221, 224),  # 300 hp read as 300 PS (Mustang 4.6 V8)
        (154, 157),  # 210 hp (Mustang 4.0 V6)
        (313, 317),  # 425 hp (Charger SRT8)
        (368, 373),  # 500 hp (Viper)
        (224, 221),  # the other way round: PS read as hp
    ],
)
@pytest.mark.parametrize("manufacturer", sorted(HORSEPOWER_UNIT_MAKERS))
def test_an_hp_ps_gap_on_a_us_maker_is_unverified_not_a_conflict(
    manufacturer: str, registered: int, catalog: int
) -> None:
    result = _match((_us_car("v8", manufacturer, catalog),), _us_query(manufacturer, registered))

    top = result.candidates[0]
    assert "power_kw_horsepower_unit_unverified" in top.missing_fields
    assert "power_kw" not in top.matched_fields + top.conflicting_fields
    # No cc and no engine code beside it: a suggestion for review, not a match.
    assert result.reason == "candidate_margin_not_met"
    assert result.guards == (TOLERATED_EVIDENCE_GUARD,)


@pytest.mark.parametrize("manufacturer", sorted(HORSEPOWER_UNIT_MAKERS))
def test_an_hp_ps_gap_beside_the_exact_cc_resolves(manufacturer: str) -> None:
    query = VehicleMatchQuery("MUSTANG", manufacturer=manufacturer, year=2007, fuels=frozenset({"petrol"}),
                              power_kw=221, displacement_cc=4601)
    result = _match((_us_car("v8", manufacturer, 224, displacement_cc=4601),), query)

    assert "power_kw_horsepower_unit_unverified" in result.candidates[0].missing_fields
    assert result.eligible_for_auto_resolution
    assert result.guards == ()


@pytest.mark.parametrize("manufacturer", ["FORD", "FORD AUSTRALIA", "BMW", "Nissan", "Volvo", "Tesla", "HUMMER"])
def test_the_same_gap_on_any_other_maker_stays_a_conflict(manufacturer: str) -> None:
    result = _match((_us_car("v8", manufacturer, 224),), _us_query(manufacturer, 221))

    assert "power_kw" in result.candidates[0].conflicting_fields
    assert result.reason == "context_conflict_requires_review"


@pytest.mark.parametrize(
    ("car_fuels", "ktype_fuels"),
    [
        (frozenset({"electric"}), frozenset({"electric"})),  # Mustang Mach-E style
        (frozenset({"electricity"}), frozenset({"electricity"})),  # unaligned registry spelling
        (frozenset({"petrol", "electric"}), frozenset({"petrol"})),
        (frozenset({"petrol"}), frozenset({"hybrid_petrol"})),
        (frozenset({"petrol", "electric", "hybrid_petrol"}), frozenset({"hybrid_petrol"})),
    ],
)
def test_the_hp_ps_gap_never_counts_with_electricity(car_fuels: frozenset[str], ktype_fuels: frozenset[str]) -> None:
    # Registered above the KType, so the hybrid system-power rule is not what decides.
    result = _match((_us_car("ev", "Chevrolet", 221, fuels=ktype_fuels),), _us_query("Chevrolet", 224, car_fuels))

    top = result.candidates[0]
    assert "power_kw_horsepower_unit_unverified" not in top.missing_fields
    assert "power_kw" in top.conflicting_fields
    assert not result.eligible_for_auto_resolution


@pytest.mark.parametrize(
    ("registered", "catalog"),
    [
        (221, 228),  # 3.2% apart: another engine
        (221, 226),  # 2.3%
        (150, 154),  # 2.7%: at 150 kW the unit gap is 2 kW, inside the plain tolerance
        (368, 380),
        (100, 224),
    ],
)
def test_a_catalog_gap_on_a_us_maker_stays_a_conflict(registered: int, catalog: int) -> None:
    # Control (catalog gap): TecDoc has no KType with the car's power. A gap that
    # is not the 1.4% unit ratio must never resolve.
    result = _match((_us_car("v8", "FORD USA", catalog),), _us_query("FORD USA", registered))

    assert "power_kw" in result.candidates[0].conflicting_fields
    assert not result.eligible_for_auto_resolution


def test_the_sibling_with_the_exact_power_stays_ahead_of_the_unit_gap_sibling() -> None:
    result = _match(
        (_us_car("unit", "Dodge", 224), _us_car("exact", "Dodge", 221)), _us_query("Dodge", 221)
    )

    exact, unit = result.candidates
    assert (exact.candidate_reference, unit.candidate_reference) == ("exact", "unit")
    assert round(exact.separation_score - unit.separation_score, 6) == 0.10
    assert result.eligible_for_auto_resolution


def test_a_unit_gap_sibling_and_a_rounding_sibling_tie_for_the_user() -> None:
    # 221 kW registered; 224 (unit gap) and 222 (plain +-2 kW rounding) are both
    # unverified with the same penalty: no evidence separates them.
    result = _match(
        (_us_car("unit", "Jeep", 224), _us_car("rounded", "Jeep", 222)), _us_query("Jeep", 221)
    )

    assert not result.eligible_for_auto_resolution
    assert result.reason == "candidate_margin_not_met"


def test_two_unit_gap_siblings_tie_for_the_user() -> None:
    result = _match(
        (_us_car("a", "Cadillac", 224), _us_car("b", "Cadillac", 224, drive_type="awd")),
        _us_query("Cadillac", 221),
    )

    assert not result.eligible_for_auto_resolution
    assert result.reason == "candidate_margin_not_met"


def test_a_zero_slack_only_accepts_an_exact_conversion() -> None:
    result = _match(
        (_us_car("v8", "FORD USA", 224),), _us_query("FORD USA", 221),
        FuzzyMatchConfig(horsepower_unit_slack_kw=0.0),
    )

    assert "power_kw" in result.candidates[0].conflicting_fields


def test_the_mx30_range_extender_registered_with_double_its_830_cc_matches() -> None:
    # The measured car: MX-30 R-EV, 830 cc in TecDoc (code N8Y1), 1,660 in the
    # registry, with the generator engine's 55 kW against 125 kW system power.
    fuels = frozenset({"petrol", "electric", "hybrid_petrol"})
    r_ev = VehicleCandidate("r-ev", "Mazda", "MX-30 (DR)", model_aliases=("MX-30",), year_from=2023,
                            fuels=frozenset({"hybrid_petrol"}), displacement_cc=830, power_kw=125,
                            engine_codes=frozenset({"N8Y1"}), electrification="range_extender")
    battery = VehicleCandidate("ev", "Mazda", "MX-30 (DR)", model_aliases=("MX-30",), year_from=2020,
                               fuels=frozenset({"electric"}), power_kw=107,
                               engine_codes=frozenset({"MH01"}), electrification="battery_electric")
    query = VehicleMatchQuery("MX-30", manufacturer="Mazda", year=2023, fuels=fuels, engine_code="N8Y1",
                              displacement_cc=1660, power_kw=55)
    result = _match((r_ev, battery), query)

    top = result.candidates[0]
    assert top.candidate_reference == "r-ev"
    assert "displacement_cc" in top.matched_fields
    assert not top.conflicting_fields
    assert result.eligible_for_auto_resolution


# --- a rounded cc against an exact-cc sibling held back by its engine code ---

def _xe(reference: str, displacement_cc: int, code: str, year_from: int) -> VehicleCandidate:
    return VehicleCandidate(reference, "Jaguar", "XE (X760)", model_aliases=("XE",), year_from=year_from,
                            fuels=frozenset({"petrol"}), displacement_cc=displacement_cc, power_kw=147,
                            engine_codes=frozenset({code}))


def _xe_query(code: str) -> VehicleMatchQuery:
    return VehicleMatchQuery("XE", manufacturer="Jaguar", year=2019, fuels=frozenset({"petrol"}),
                             engine_code=code, displacement_cc=1997, power_kw=147)


def test_a_rounded_cc_never_resolves_past_the_exact_cc_sibling_blocked_by_its_code() -> None:
    # Synthetic codes, so no reviewed alias can ever join them: the top carries
    # the car's code at 1,999 cc, the sibling the car's exact 1,997 cc and
    # power under another code. 2 cc is not proof of rounding here.
    result = _match((_xe("rounded", 1999, "AAA1", 2015), _xe("exact", 1997, "BBB2", 2017)), _xe_query("AAA1"))

    rounded, exact = result.candidates
    assert (rounded.candidate_reference, exact.candidate_reference) == ("rounded", "exact")
    assert "displacement_cc_rounding_unverified" in rounded.missing_fields
    assert "displacement_cc" in exact.matched_fields
    assert exact.conflicting_fields == ("engine_code",)
    assert not result.eligible_for_auto_resolution
    assert result.reason == "candidate_margin_not_met"
    assert result.guards == (DISPLACEMENT_ROUNDING_SIBLING_GUARD,)


def test_a_jaguar_xe_ingenium_never_resolves_to_the_gtdi_ktype() -> None:
    # The measured cars: built 2019, 1,997 cc, 147 kW, registered `204PT`. The
    # 1,999 cc KType is the Ford-derived GTDi, the 1,997 cc one the Ingenium the
    # car has; the registry writes `204PT` for both. Until a reviewed alias
    # joins `204PT` and `PT204` the two tie; with it the Ingenium wins on exact cc.
    result = _match(
        (_xe("gtdi", 1999, "204PT(GTDI)", 2015), _xe("ingenium", 1997, "PT204(AJ20P4)", 2017)),
        _xe_query("204PT"),
    )

    if result.candidates[0].candidate_reference == "gtdi":
        assert not result.eligible_for_auto_resolution
        assert result.reason == "candidate_margin_not_met"
        assert result.guards == (DISPLACEMENT_ROUNDING_SIBLING_GUARD,)
    else:
        assert result.candidates[0].candidate_reference == "ingenium"


def test_a_rounded_cc_still_resolves_when_the_exact_cc_sibling_conflicts_on_more() -> None:
    # Control: the exact-cc sibling also has another power, so it is another variant.
    other = VehicleCandidate("exact", "Jaguar", "XE (X760)", model_aliases=("XE",), year_from=2017,
                             fuels=frozenset({"petrol"}), displacement_cc=1997, power_kw=184,
                             engine_codes=frozenset({"BBB2"}))
    result = _match((_xe("rounded", 1999, "AAA1", 2015), other), _xe_query("AAA1"))

    assert result.candidates[0].candidate_reference == "rounded"
    assert result.eligible_for_auto_resolution
    assert result.guards == ()


# --- the hp/PS slack is the two roundings, no more ---------------------------

def test_the_default_slack_is_the_two_integer_roundings() -> None:
    # 0.5 kW on one figure, 0.5 kW on the other scaled by hp/PS.
    assert 0.5 + 0.5 * 0.7457 / 0.7355 < FuzzyMatchConfig().horsepower_unit_slack_kw == 1.01


@pytest.mark.parametrize(
    ("registered", "catalog"),
    [
        (132, 135),  # Jeep Cherokee: 1987-1990 against 1991-2001, another engine version
        (139, 142),  # Camaro 1987
        (295, 298),  # Corvette 2006
    ],
)
@pytest.mark.parametrize("manufacturer", ["JEEP", "CHEVROLET"])
def test_a_plain_three_kw_gap_on_a_us_maker_stays_a_conflict(manufacturer: str, registered: int, catalog: int) -> None:
    # No integer horsepower figure converts to both numbers: 2.0-2.3% apart, not 1.4%.
    result = _match((_us_car("v8", manufacturer, catalog),), _us_query(manufacturer, registered))

    top = result.candidates[0]
    assert "power_kw_horsepower_unit_unverified" not in top.missing_fields
    assert "power_kw" in top.conflicting_fields
    assert not result.eligible_for_auto_resolution


# --- a tolerated cc or power needs one exact technical field -----------------

def _d5(**fields: object) -> VehicleCandidate:
    base: dict[str, object] = {"year_from": 2007, "year_to": 2016, "engine_codes": frozenset({"D5244T4"})}
    return _v70("d5", 2401, **{**base, **fields})


def _d5_query(year: int, power_kw: int | None, displacement_cc: int | None,
              engine_code: str | None = None) -> VehicleMatchQuery:
    return VehicleMatchQuery("V70", manufacturer="Volvo", year=year, fuels=frozenset({"diesel"}),
                             power_kw=power_kw, displacement_cc=displacement_cc, engine_code=engine_code)


def test_stacked_tolerances_without_an_exact_technical_field_go_to_review() -> None:
    # Year adjacent (-0.05), cc rounded (-0.05), power 2 kW off (-0.05), fuel
    # (+0.05): confidence 0.90 reaches the automatic threshold on no exact figure.
    result = _match((_d5(),), _d5_query(2006, 134, 2400))

    top = result.candidates[0]
    assert {"year_adjacent_unverified", "displacement_cc_rounding_unverified", "power_kw"} <= set(top.missing_fields)
    assert not top.conflicting_fields
    assert top.confidence == 0.90
    assert not result.eligible_for_auto_resolution
    assert result.reason == "candidate_margin_not_met"
    assert result.guards == (TOLERATED_EVIDENCE_GUARD,)


@pytest.mark.parametrize(
    ("power_kw", "displacement_cc"),
    [
        (None, 2400),  # cc rounded, nothing else
        (134, 2400),  # cc rounded beside a power only within tolerance
    ],
)
def test_a_rounded_cc_without_an_exact_figure_goes_to_review(power_kw: int | None, displacement_cc: int) -> None:
    result = _match((_d5(),), _d5_query(2008, power_kw, displacement_cc))

    assert not result.candidates[0].conflicting_fields
    assert not result.eligible_for_auto_resolution
    assert result.guards == (TOLERATED_EVIDENCE_GUARD,)


def test_a_power_within_the_older_tolerance_alone_still_resolves() -> None:
    # The +-2 kW tolerance predates the guard: 35 cars of the 20k resolve on it alone.
    result = _match((_d5(),), _d5_query(2008, 134, None))

    assert result.eligible_for_auto_resolution
    assert result.guards == ()


def test_a_unit_gap_alone_goes_to_review() -> None:
    # Buick Wildcat style: no cc, no code, the power only a unit gap.
    result = _match((_us_car("v8", "Buick", 254),), _us_query("Buick", 250))

    assert "power_kw_horsepower_unit_unverified" in result.candidates[0].missing_fields
    assert not result.eligible_for_auto_resolution
    assert result.reason == "candidate_margin_not_met"
    assert result.guards == (TOLERATED_EVIDENCE_GUARD,)


@pytest.mark.parametrize(
    ("power_kw", "displacement_cc", "engine_code"),
    [
        (136, 2400, None),  # exact power beside the rounded cc
        (134, 2401, None),  # exact cc beside the tolerated power
        (134, 2400, "D5244T4"),  # exact engine code beside both
    ],
)
def test_one_exact_technical_field_keeps_a_tolerated_figure_automatic(
    power_kw: int, displacement_cc: int, engine_code: str | None
) -> None:
    result = _match((_d5(),), _d5_query(2008, power_kw, displacement_cc, engine_code))

    assert result.eligible_for_auto_resolution
    assert result.guards == ()


def test_a_ktype_without_power_is_not_a_tolerated_power() -> None:
    # Missing on the KType is not "close": the guard is about tolerated figures only.
    result = _match((_d5(power_kw=None),), _d5_query(2008, 136, None))

    assert result.eligible_for_auto_resolution
    assert result.guards == ()


# --- the plug-in guard and the exact-against-rounded cc swing ----------------

def _leon(reference: str, alias: str, displacement_cc: int, power_kw: int, electrification: str) -> VehicleCandidate:
    return VehicleCandidate(
        reference, "Seat", "LEON (KL1)", model_aliases=("LEON", alias), year_from=2020,
        fuels=frozenset({"hybrid_petrol"}), displacement_cc=displacement_cc, power_kw=power_kw,
        electrification=electrification,
    )


def test_an_exact_cc_is_no_evidence_against_a_plug_in_whose_cc_is_only_rounded() -> None:
    # Mild hybrid 1,495 cc / 110 kW against the plug-in at 1,498 cc / 150 kW system
    # power, the car registered with electricity at 1,495 cc and the engine's
    # 110 kW. Exact power and exact cc both favour the mild hybrid and neither
    # says anything against the plug-in: the two tie for the user.
    catalog = (
        _leon("mild", "LEON 1.5 eTSI", 1495, 110, "mild_hybrid"),
        _leon("plug-in", "LEON 1.4 e-Hybrid", 1498, 150, "plug_in_hybrid"),
    )
    query = VehicleMatchQuery("LEON", manufacturer="Seat", year=2022,
                              fuels=frozenset({"petrol", "electric", "hybrid_petrol"}),
                              power_kw=110, displacement_cc=1495)
    result = _match(catalog, query)

    mild, plug_in = result.candidates
    assert (mild.candidate_reference, plug_in.candidate_reference) == ("mild", "plug-in")
    assert "displacement_cc_rounding_unverified" in plug_in.missing_fields
    assert round(mild.separation_score - plug_in.separation_score, 6) == 0.20
    assert not result.eligible_for_auto_resolution
    assert result.reason == "candidate_margin_not_met"
    assert result.guards == (PLUG_IN_POWER_GUARD,)


# --- V70 II against V70 III: 2,401 against 2,400 cc --------------------------

def test_a_v70_built_before_the_v70_iii_months_ties_between_the_generations() -> None:
    # Registered 2,400 cc, built 2007-02. The V70 III (2,400 cc) starts 2007-04,
    # so its exact cc is offset by the month outside its run; the V70 II
    # (2,401 cc, rounded) covers the month. Neither leads: the user decides.
    second = VehicleCandidate("v70-ii", "Volvo", "V70 II (285)", model_aliases=("V70",), year_from=2000,
                              year_to=2007, month_from=200003, month_to=200708, fuels=frozenset({"diesel"}),
                              displacement_cc=2401, power_kw=136)
    third = VehicleCandidate("v70-iii", "Volvo", "V70 III (135)", model_aliases=("V70",), year_from=2007,
                             year_to=2016, month_from=200704, month_to=201612, fuels=frozenset({"diesel"}),
                             displacement_cc=2400, power_kw=136)
    query = VehicleMatchQuery("V70", manufacturer="Volvo", year=2007, build_month=200702,
                              fuels=frozenset({"diesel"}), power_kw=136, displacement_cc=2400)
    result = _match((second, third), query)

    by_reference = {candidate.candidate_reference: candidate for candidate in result.candidates}
    assert "displacement_cc_rounding_unverified" in by_reference["v70-ii"].missing_fields
    assert "year_month_outside_unverified" in by_reference["v70-iii"].missing_fields
    assert by_reference["v70-ii"].separation_score == by_reference["v70-iii"].separation_score
    assert not result.eligible_for_auto_resolution
    assert result.reason == "candidate_margin_not_met"
