"""A registry make's models TecDoc files under a sister maker ("FORD USA")."""

from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.match_run_service import MatchSourceRecord
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator

COMMON = {"fuels": frozenset({"petrol"}), "bodyworks": frozenset({"coupe"})}
CATALOG = (
    VehicleCandidate("focus", "FORD", "FOCUS II (DA_)", model_aliases=("FOCUS",), year_from=2004, year_to=2012,
                     power_kw=74, **COMMON),
    VehicleCandidate("mustang", "FORD USA", "MUSTANG Coupe", model_aliases=("MUSTANG",), year_from=2004,
                     year_to=2010, power_kw=224, **COMMON),
    VehicleCandidate("mustang-au", "FORD AUSTRALIA", "MUSTANG Coupe", model_aliases=("MUSTANG",),
                     year_from=2004, year_to=2010, power_kw=224, **COMMON),
    VehicleCandidate("focus-us", "FORD USA", "FOCUS", model_aliases=("FOCUS",), year_from=2004, year_to=2012,
                     power_kw=74, **COMMON),
)


def record(model: str, power_kw: int) -> MatchSourceRecord:
    return MatchSourceRecord(1, {
        "normalized": {"manufacturer": "Ford", "model_family": model, "production_year": 2007,
                       "fuel_match_tokens": ["petrol"], "power_kw": power_kw, "bodywork_form": "coupe"},
        "source_evidence": {"brand": f"FORD {model.upper()}"},
    })


def test_a_model_the_make_lacks_is_found_under_its_sister_maker() -> None:
    evaluation = TecDocDryRunEvaluator(CATALOG).evaluate(record("Mustang", 224))

    # FORD USA comes before FORD AUSTRALIA, so the US Mustang is the one matched.
    assert evaluation.top_candidate_reference == "mustang"


def test_a_model_the_make_has_stays_with_the_make() -> None:
    evaluation = TecDocDryRunEvaluator(CATALOG).evaluate(record("Focus", 74))

    # FORD USA also lists a Focus, but the European Focus is the make's own family.
    assert evaluation.top_candidate_reference == "focus"


# SEAT -> CUPRA. ORA is no sister of GREAT WALL until the data owner decides.

ELECTRIC = {"fuels": frozenset({"electric"}), "bodyworks": frozenset({"hatchback"})}
SISTERS = (
    VehicleCandidate("seat-leon", "SEAT", "LEON (KL1, KLG)", model_aliases=("LEON",), year_from=2019, power_kw=110,
                     **COMMON),
    VehicleCandidate("cupra-leon", "CUPRA", "LEON (KL1, KU1, KUG)", model_aliases=("LEON",), year_from=2020,
                     power_kw=110, **COMMON),
    VehicleCandidate("born", "CUPRA", "BORN (K11)", model_aliases=("BORN",), year_from=2021, power_kw=150,
                     **ELECTRIC),
    VehicleCandidate("ora-es11", "ORA", "ES11", year_from=2022, power_kw=126, **ELECTRIC),
    # TecDoc lists the same car under both makers.
    VehicleCandidate("ora-own-07", "ORA", "07 EV (EC24)", model_aliases=("07 EV",), year_from=2024, power_kw=150,
                     **ELECTRIC),
    VehicleCandidate("ora-07", "GREAT WALL", "Ora 07", year_from=2023, power_kw=150, **ELECTRIC),
)


def sister_record(make: str, model: str, fuel: str, power_kw: int, body: str) -> MatchSourceRecord:
    return MatchSourceRecord(1, {
        "normalized": {"manufacturer": make, "production_year": 2023, "fuel_match_tokens": [fuel],
                       "power_kw": power_kw, "bodywork_form": body},
        "source_evidence": {"brand": make.upper(), "model": model},
    })


def test_a_seat_that_tecdoc_files_under_cupra_is_found_there() -> None:
    evaluator = TecDocDryRunEvaluator(SISTERS)

    # The registry's first Cupra Borns are SEATs; TecDoc has the Born under CUPRA only.
    assert evaluator.evaluate(sister_record("SEAT", "BORN 150 KW 58/62 KWH", "electric", 150, "hatchback")
                              ).top_candidate_reference == "born"
    # A Leon is a SEAT family, so a SEAT Leon never becomes the Cupra Leon.
    assert evaluator.evaluate(sister_record("SEAT", "LEON", "petrol", 110, "coupe")
                              ).top_candidate_reference == "seat-leon"


def test_an_ora_is_never_sent_to_great_wall() -> None:
    from ingestion.tecdoc.match_run_adapters import REVIEWED_SISTER_MAKERS

    # TecDoc has the Ora 07 under ORA ("07 EV (EC24)") and under GREAT WALL ("Ora 07").
    # Scoping to one maker would hide the other's KType, so no tie would be shown:
    # which maker is canonical is the data owner's decision.
    assert "ORA" not in REVIEWED_SISTER_MAKERS
    for model in ("ORA 07", "ORA FUNKY CAT"):
        evaluation = TecDocDryRunEvaluator(SISTERS).evaluate(sister_record("ORA", model, "electric", 150, "hatchback"))

        assert evaluation.top_candidate_reference != "ora-07"
        assert all(match["candidate_reference"] != "ora-07" for match in evaluation.candidate_matches)


# A model a rule inferred names the sister maker when the car's own text names nothing.


def inferred_record(model: str, power_kw: int, brand: str) -> MatchSourceRecord:
    car = record(model, power_kw)
    car.payload["source_evidence"] = {"brand": brand}
    car.payload["inferred_fields"] = ["model_family"]
    return car


def test_an_inferred_model_the_make_lacks_is_found_under_its_sister_maker() -> None:
    # Brand text "FORD" names no model; the rule-filled "Mustang" is all there is.
    evaluation = TecDocDryRunEvaluator(CATALOG).evaluate(inferred_record("Mustang", 224, "FORD"))

    assert (evaluation.terminal, evaluation.top_candidate_reference) == ("resolved", "mustang")
    assert "model_inferred_by_rule" in evaluation.reason_codes


def test_an_inferred_model_the_make_has_stays_with_the_make() -> None:
    evaluation = TecDocDryRunEvaluator(CATALOG).evaluate(inferred_record("Focus", 74, "FORD"))

    assert evaluation.top_candidate_reference == "focus"


def test_the_cars_own_text_outranks_the_inferred_model_for_the_sister_maker() -> None:
    evaluator = TecDocDryRunEvaluator(CATALOG)

    # The text names the sister's Mustang; a rule's "Focus" does not keep the car a FORD.
    assert evaluator.evaluate(inferred_record("Focus", 224, "FORD MUSTANG GT")).top_candidate_reference == "mustang"
    # The text names the make's own Focus; a rule's "Mustang" does not send it to FORD USA.
    assert evaluator.evaluate(inferred_record("Mustang", 74, "FORD FOCUS 1,6")).top_candidate_reference == "focus"
