from collections.abc import Mapping

import pytest

from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.match_run_service import MatchSourceRecord
from ingestion.tecdoc.match_run_adapters import (
    TecDocDryRunEvaluator,
    _flatten_strings,
    _integer,
    postgres_tecdoc_model_aliases,
    reviewed_candidate_context,
)
from ingestion.tecdoc.model_aliases import ReviewedModelAliasIndex
from ingestion.translation_dictionaries import TranslationRule, TranslationRuleSet


def test_evaluator_keeps_normalization_review_out_of_matching() -> None:
    evaluator = TecDocDryRunEvaluator((VehicleCandidate("1", "Volvo", "V60"),))
    assert evaluator(MatchSourceRecord(1, {"normalization_status": "review_required"})) == (
        "normalization_review"
    )


def test_evaluator_routes_exact_ktype_candidate() -> None:
    evaluator = TecDocDryRunEvaluator((VehicleCandidate("1", "Volvo", "V60"),))
    terminal = evaluator(
        MatchSourceRecord(
            1,
            {
                "normalization_status": "resolved",
                "normalized": {"manufacturer": "Volvo", "model_family": "V60"},
            },
        )
    )
    assert terminal == "resolved"
    assert (
        evaluator(
            MatchSourceRecord(
                2,
                {
                    "normalization_status": "resolved",
                    "normalized": {"manufacturer": "Volvo", "model_family": "V60"},
                },
            )
        )
        == "resolved"
    )
    assert evaluator.cache_size == 1


def test_evaluator_accounts_for_missing_scope_and_model() -> None:
    evaluator = TecDocDryRunEvaluator((VehicleCandidate("1", "Volvo", "V60"),))
    assert evaluator(MatchSourceRecord(1, {"normalized": {"model_family": "V60"}})) == ("unmatched")
    assert evaluator(MatchSourceRecord(2, {"normalized": {"manufacturer": "Volvo"}})) == (
        "review_required"
    )


def test_evaluator_routes_punctuation_only_model_to_review() -> None:
    evaluator = TecDocDryRunEvaluator((VehicleCandidate("1", "Volvo", "V60"),))

    result = evaluator(
        MatchSourceRecord(
            1,
            {
                "normalization_status": "provisional",
                "normalized": {"manufacturer": "Volvo", "model_family": "---"},
            },
        )
    )

    assert result == "review_required"


def test_evaluator_short_circuits_unknown_manufacturer_global_scope() -> None:
    evaluator = TecDocDryRunEvaluator((VehicleCandidate("1", "Volvo", "V60"),))

    result = evaluator(
        MatchSourceRecord(
            1,
            {
                "normalization_status": "provisional",
                "normalized": {
                    "manufacturer": "Unknown Motors",
                    "model_family": "V60",
                },
            },
        )
    )

    assert result == "review_required"
    assert evaluator.cache_size == 1


def test_evaluator_exposes_sanitized_reason_codes() -> None:
    evaluator = TecDocDryRunEvaluator((VehicleCandidate("1", "Volvo", "V60"),))

    evaluation = evaluator.evaluate(
        MatchSourceRecord(
            1,
            {
                "normalization_status": "review_required",
                "review_reasons": ["manufacturer_conflict"],
            },
        )
    )

    assert evaluation.terminal == "normalization_review"
    assert evaluation.reason_codes == ("normalization:manufacturer_conflict",)
    assert evaluation.top_candidate_reference is None


def test_evaluator_recovers_missing_model_from_exact_brand_tokens() -> None:
    evaluator = TecDocDryRunEvaluator((VehicleCandidate("1", "Chevrolet", "Corvette"),))

    evaluation = evaluator.evaluate(
        MatchSourceRecord(
            1,
            {
                "normalization_status": "provisional",
                "normalized": {"manufacturer": "Chevrolet"},
                "source_evidence": {"brand": "CHEVROLET CORVETTE"},
            },
        )
    )

    assert evaluation.terminal == "resolved"
    assert "model_recovered_from_brand" in evaluation.reason_codes
    assert "model_recovered_from_brand:resolved" in evaluation.reason_codes


def test_evaluator_recovers_missing_model_from_variant_tokens() -> None:
    evaluator = TecDocDryRunEvaluator((VehicleCandidate("1", "Volvo", "XC90"),))

    evaluation = evaluator.evaluate(
        MatchSourceRecord(
            1,
            {
                "normalization_status": "provisional",
                "normalized": {"manufacturer": "Volvo"},
                "source_evidence": {"variant": "XC90 T8"},
            },
        )
    )

    assert evaluation.terminal == "resolved"
    assert "model_recovered_from_variant" in evaluation.reason_codes


def test_evaluator_profiles_non_hard_bodywork_conflict() -> None:
    evaluator = TecDocDryRunEvaluator(
        (VehicleCandidate("1", "Volvo", "V70", bodyworks=frozenset({"estate"})),)
    )

    evaluation = evaluator.evaluate(
        MatchSourceRecord(
            1,
            {
                "normalization_status": "provisional",
                "normalized": {
                    "manufacturer": "Volvo",
                    "model_family": "V70",
                    "bodywork_form": "suv",
                },
            },
        )
    )

    assert evaluation.terminal == "review_required"
    assert "context_conflict:bodywork" in evaluation.reason_codes
    assert "route:non_hard_context_conflict" in evaluation.reason_codes


def test_evaluator_uses_stronger_raw_model_without_losing_normalized_evidence() -> None:
    evaluator = TecDocDryRunEvaluator((VehicleCandidate("1", "Volvo", "V60"),))

    evaluation = evaluator.evaluate(
        MatchSourceRecord(
            1,
            {
                "normalization_status": "resolved",
                "normalized": {"manufacturer": "Volvo", "model_family": "Unknown"},
                "source_evidence": {"model": "V60"},
            },
        )
    )

    assert evaluation.terminal == "resolved"
    assert "model_recovered_from_model" in evaluation.reason_codes


def test_evaluator_retains_tuple_fuel_evidence_from_live_normalization() -> None:
    evaluator = TecDocDryRunEvaluator(
        (
            VehicleCandidate("petrol", "Volvo", "V60", fuels=frozenset({"petrol"})),
            VehicleCandidate("diesel", "Volvo", "V60", fuels=frozenset({"diesel"})),
        )
    )

    evaluation = evaluator.evaluate(
        MatchSourceRecord(
            1,
            {
                "normalization_status": "resolved",
                "normalized": {
                    "manufacturer": "Volvo",
                    "model_family": "V60",
                    "energy_sources": ("petrol",),
                },
            },
        )
    )

    assert evaluation.terminal == "resolved"


def test_postgres_catalog_numeric_text_is_not_discarded() -> None:
    assert _integer("1969") == 1969
    assert _integer("140.0") == 140
    assert _integer(2020) == 2020
    assert _integer("") is None
    assert _integer("not-a-number") is None


def test_catalog_fuel_components_flatten_nested_graph_and_json_arrays() -> None:
    assert _flatten_strings([["petrol", "alcohol_unspecified"], None, ["petrol"]]) == frozenset(
        {"petrol", "alcohol_unspecified"}
    )
    assert _flatten_strings({"unknown": "object"}) == frozenset()


def test_evaluator_uses_reviewed_alias_without_degrading_base_route() -> None:
    aliases = ReviewedModelAliasIndex(
        TranslationRuleSet(
            version="rules-v1",
            rules=(
                TranslationRule(
                    rule_id="MOD-001",
                    area="model_family",
                    source_fields=("model",),
                    source_terms=("T ROC",),
                    canonical_field="model_family",
                    canonical_value="T-Roc",
                    decision="accepted",
                    manufacturers=("VW",),
                ),
            ),
        )
    )
    evaluator = TecDocDryRunEvaluator(
        (VehicleCandidate("1", "VW", "T-ROC (A11)"),),
        reviewed_model_aliases=aliases,
    )

    evaluation = evaluator.evaluate(
        MatchSourceRecord(
            1,
            {
                "normalization_status": "resolved",
                "normalized": {"manufacturer": "VW", "model_family": "T ROC"},
            },
        )
    )

    assert evaluation.terminal == "resolved"


def test_evaluator_attaches_a_rule_scoped_to_the_ts_manufacturer_name_to_the_catalogs_name() -> None:
    """Reviewed rules say "Volkswagen", TecDoc says "VW": the family name must still match exactly."""

    aliases = ReviewedModelAliasIndex(
        TranslationRuleSet(
            version="rules-v1",
            rules=(
                TranslationRule(
                    rule_id="MOD-001",
                    area="model_family",
                    source_fields=("model",),
                    source_terms=("PASSAT",),
                    canonical_field="model_family",
                    canonical_value="Passat",
                    decision="accepted",
                    manufacturers=("Volkswagen",),
                ),
            ),
        )
    )
    manufacturer_rules = {
        "MFR-VW": {
            "kind": "manufacturer_entity",
            "entity_role": "vehicle_manufacturer",
            "source_term": "VW",
            "canonical_name": "Volkswagen",
        }
    }
    evaluator = TecDocDryRunEvaluator(
        (VehicleCandidate("1", "VW", "PASSAT B8 Variant (3G5, CB5)"),),
        manufacturer_rules,
        reviewed_model_aliases=aliases,
    )

    evaluation = evaluator.evaluate(
        MatchSourceRecord(
            1,
            {
                "normalization_status": "resolved",
                "normalized": {"manufacturer": "Volkswagen", "model_family": "Passat"},
            },
        )
    )

    assert evaluation.terminal == "resolved"
    assert "match:phonetic_candidate_requires_review" not in evaluation.reason_codes


def test_evaluator_never_reports_candidate_only_ktype_as_resolved() -> None:
    evaluator = TecDocDryRunEvaluator(
        (
            VehicleCandidate(
                "candidate-only-1",
                "Volvo",
                "V60",
                candidate_type="TecDocKTypeCandidateOnly",
            ),
        )
    )

    evaluation = evaluator.evaluate(
        MatchSourceRecord(
            1,
            {
                "normalization_status": "resolved",
                "normalized": {"manufacturer": "Volvo", "model_family": "V60"},
            },
        )
    )

    assert evaluation.terminal == "provisional"
    assert "candidate_only_not_graph_safe" in evaluation.reason_codes


def _candidate_only_evaluation(engine_code: str | None, *, fingerprint: bool = False):  # type: ignore[no-untyped-def]
    from ingestion.tecdoc.engine_fingerprint_proposals import ReviewedEngineFingerprintIndex

    evaluator = TecDocDryRunEvaluator(
        (
            VehicleCandidate(
                "candidate-only-1", "Volvo", "V60",
                candidate_type="TecDocKTypeCandidateOnly",
                engine_codes=frozenset({"D 4204 T14", "D 4204 T8"}),
            ),
            VehicleCandidate("other", "Volvo", "XC90", engine_codes=frozenset({"B4204T"})),
        ),
        reviewed_engine_fingerprints=(
            _FixedFingerprints("D4204T14") if fingerprint else ReviewedEngineFingerprintIndex()
        ),
    )
    normalized: dict[str, object] = {"manufacturer": "Volvo", "model_family": "V60"}
    if engine_code:
        normalized["engine_code"] = engine_code
    return evaluator.evaluate(
        MatchSourceRecord(1, {"normalization_status": "resolved", "normalized": normalized})
    )


class _FixedFingerprints:
    def __init__(self, code: str) -> None:
        self._code = code

    def resolve(self, **_: object) -> str:
        return self._code


def test_the_cars_own_engine_code_confirms_a_candidate_only_ktype() -> None:
    evaluation = _candidate_only_evaluation("D4204T14")

    assert evaluation.terminal == "resolved"
    assert "candidate_only_engine_confirmed" in evaluation.reason_codes
    assert "candidate_only_not_graph_safe" not in evaluation.reason_codes


@pytest.mark.parametrize(
    ("engine_code", "fingerprint"),
    [
        (None, False),  # no engine code: nothing confirms
        ("XYZ999", False),  # a code no KType carries: unverified
        (None, True),  # a reviewed fingerprint's inference is not the car's own code
    ],
)
def test_a_candidate_only_ktype_without_engine_confirmation_stays_provisional(
    engine_code: str | None, fingerprint: bool
) -> None:
    evaluation = _candidate_only_evaluation(engine_code, fingerprint=fingerprint)

    assert evaluation.terminal == "provisional"
    assert "candidate_only_not_graph_safe" in evaluation.reason_codes


def test_tecdoc_model_aliases_strip_chassis_code_and_generation_numeral() -> None:
    from ingestion.tecdoc.match_run_adapters import tecdoc_model_aliases

    # TS stores the bare marketing name; TecDoc decorates it.
    assert "V60" in tecdoc_model_aliases("V60 I (155)")
    assert "QASHQAI" in tecdoc_model_aliases("QASHQAI I (J10, NJ10)")
    assert "GOLF" in tecdoc_model_aliases("GOLF VII (5G1, BQ1)")
    assert tecdoc_model_aliases("ID.4 (E21)") == ("ID.4",)


def test_postgres_catalog_keeps_generated_and_source_model_aliases() -> None:
    assert postgres_tecdoc_model_aliases(
        "OCTAVIA III (5E3, NL3, NR3)", "OCTAVIA III"
    ) == ("OCTAVIA", "OCTAVIA III",)
    assert postgres_tecdoc_model_aliases("CTS", "CTS") == ()


def test_candidate_only_context_uses_reviewed_codes_without_overriding_canonical() -> None:
    reviewed = {"004": "awd"}

    assert reviewed_candidate_context("fwd", "004", reviewed) == "fwd"
    assert reviewed_candidate_context(None, "004", reviewed) == "awd"
    assert reviewed_candidate_context(None, "999", reviewed) is None


def test_evaluator_uses_technical_signature_to_separate_decorated_model_aliases() -> None:
    shared = {
        "manufacturer": "Skoda",
        "model_aliases": ("OCTAVIA",),
        "year_from": 2013,
        "year_to": 2020,
        "fuels": frozenset({"diesel"}),
        "displacement_cc": 1968,
    }
    evaluator = TecDocDryRunEvaluator(
        (
            VehicleCandidate(
                "ktype-exact",
                model="OCTAVIA III (5E3, NL3, NR3)",
                power_kw=110,
                **shared,
            ),
            VehicleCandidate(
                "ktype-rival",
                model="OCTAVIA III (5E3, NL3, NR3)",
                power_kw=135,
                **shared,
            ),
        )
    )

    evaluation = evaluator.evaluate(
        MatchSourceRecord(
            1,
            {
                "normalization_status": "provisional",
                "normalized": {
                    "manufacturer": "Skoda",
                    "model_family": "OCTAVIA",
                    "production_year": 2018,
                    "energy_sources": ("diesel",),
                    "displacement_cc": 1968,
                    "power_kw": 110,
                },
            },
        )
    )

    assert evaluation.terminal == "resolved"
    assert "route:resolved_threshold_met" in evaluation.reason_codes
    assert evaluation.top_candidate_reference == "ktype-exact"


def test_tecdoc_model_aliases_keep_meaningful_body_and_trim_words() -> None:
    from ingestion.tecdoc.match_run_adapters import tecdoc_model_aliases

    # "Cross Country" and "SUV" distinguish real models and must survive.
    assert "V60 Cross Country" in tecdoc_model_aliases("V60 I Cross Country (157)")
    assert "XC60 SUV" in tecdoc_model_aliases("XC60 I SUV (156)")


def test_tecdoc_model_aliases_never_strip_a_single_letter_model_name() -> None:
    from ingestion.tecdoc.match_run_adapters import tecdoc_model_aliases

    # Tesla's "X" is the model, not a generation; stripping it would collide
    # with Model S, Model 3 and Model Y.
    assert tecdoc_model_aliases("MODEL X") == ()
    assert tecdoc_model_aliases("MODEL S") == ()


def test_the_build_month_comes_from_the_vehicle_else_a_month_precise_normalization() -> None:
    from ingestion.tecdoc.match_run_adapters import _build_month

    assert _build_month({"production_month": 3}, 2008) == 200803
    assert _build_month({"production_date": "2008-03", "production_date_precision": "month"}, 2008) == 200803
    assert _build_month({"production_date": "2008-03-17", "production_date_precision": "day"}, 2008) == 200803
    # A year alone, or a year-precision date, states no month.
    assert _build_month({"production_date": "2008", "production_date_precision": "model_year"}, 2008) is None
    assert _build_month({}, 2008) is None
    assert _build_month({"production_month": 13}, 2008) is None


def test_cars_differing_only_by_build_month_are_not_served_one_cached_answer() -> None:
    evaluator = TecDocDryRunEvaluator(
        (
            VehicleCandidate("PRE", "Volvo", "V70", year_from=2000, year_to=2008, month_from=200001, month_to=200804, power_kw=120),
            VehicleCandidate("FACELIFT", "Volvo", "V70", year_from=2008, year_to=2013, month_from=200805, month_to=201312, power_kw=120),
        ),
    )

    def car(month: int) -> MatchSourceRecord:
        return MatchSourceRecord(
            month,
            {
                "normalization_status": "resolved",
                "normalized": {"manufacturer": "Volvo", "model_family": "V70", "production_year": 2008,
                               "production_month": month, "power_kw": 120},
            },
        )

    assert evaluator.evaluate(car(3)).top_candidate_reference == "PRE"
    assert evaluator.evaluate(car(11)).top_candidate_reference == "FACELIFT"


# --- reading guards (R1, R1b, R6a, F1b, F2r, K2) -----------------------------------------


def _aliases(make: str, *pairs: tuple[str, str]) -> ReviewedModelAliasIndex:
    return ReviewedModelAliasIndex(TranslationRuleSet(version="rules-v1", rules=tuple(
        TranslationRule(
            rule_id=f"MOD-{index}", area="model_family", source_fields=("model",), source_terms=(term,),
            canonical_field="model_family", canonical_value=canonical, decision="accepted", manufacturers=(make,),
        )
        for index, (term, canonical) in enumerate(pairs)
    )))


def _car(normalized: Mapping[str, object], *, candidates: Mapping[str, object] | None = None,
         evidence: Mapping[str, object] | None = None, record_id: int = 1) -> MatchSourceRecord:
    payload: dict[str, object] = {"normalization_status": "resolved", "normalized": dict(normalized)}
    if candidates:
        payload["candidates"] = dict(candidates)
    if evidence:
        payload["source_evidence"] = dict(evidence)
    return MatchSourceRecord(record_id, payload)


_HYBRID = ["petrol", "electric", "hybrid_petrol"]


def _hybrid(reference: str, model: str, power_kw: int, electrification: str | None, **fields: object) -> VehicleCandidate:
    return VehicleCandidate(reference, fields.pop("make", "Mercedes-Benz"), model,  # type: ignore[arg-type]
                            fuels=frozenset({"hybrid_petrol"}), power_kw=power_kw,
                            electrification=electrification, **fields)  # type: ignore[arg-type]


def _glc_catalog() -> tuple[VehicleCandidate, ...]:
    return (_hybrid("glc200", "GLC", 150, "mild_hybrid"), _hybrid("glc300e", "GLC", 230, "plug_in_hybrid"))


def _glc_car(electrification: str | None, record_id: int = 1) -> MatchSourceRecord:
    normalized: dict[str, object] = {"manufacturer": "Mercedes-Benz", "model_family": "GLC",
                                     "fuel_match_tokens": _HYBRID, "power_kw": 150}
    if electrification:
        normalized["electrification_type"] = electrification
    return _car(normalized, record_id=record_id)


def test_catalog_engine_types_map_to_electrification_and_anything_else_is_unknown() -> None:
    from ingestion.tecdoc.match_run_adapters import ktype_electrification

    assert ktype_electrification("046") == "plug_in_hybrid"
    assert ktype_electrification("047") == "range_extender"
    assert ktype_electrification("048") == "full_hybrid"
    assert ktype_electrification("049") == "mild_hybrid"
    assert ktype_electrification("040") == "battery_electric"
    # A known engine-only type is "combustion", which the checks may read as no
    # plug-in; a missing or unlisted code is unknown, which they never do.
    assert ktype_electrification("001") == "combustion"
    assert ktype_electrification("002") == "combustion"
    assert ktype_electrification(None) is None
    assert ktype_electrification("") is None
    assert ktype_electrification("041") is None


def test_the_graph_catalog_loads_electrification_too() -> None:
    from contextlib import contextmanager

    from ingestion.tecdoc.match_run_adapters import load_ktype_catalog

    row = {"ktype": "1", "manufacturer": "BMW", "model": "i8 (I12)", "year_from": 2014, "year_to": 2020,
           "fuel_type": "hybrid_petrol", "displacement_cc": 1499, "power_kw": 266, "drive_type": "awd",
           "engine_codes": ["B38K15A"], "engine_fuel_components": [], "bodyworks": ["coupe"]}

    class _Driver:
        def __init__(self, rows: list[dict[str, object]]) -> None:
            self._rows = rows

        @contextmanager
        def session(self):  # type: ignore[no-untyped-def]
            class _Session:
                def run(_, query: str) -> list[dict[str, object]]:
                    assert "tecdoc_engine_type_code" in query
                    return self._rows
            yield _Session()

    loaded = load_ktype_catalog(_Driver([{**row, "engine_type_code": "046"}]))  # type: ignore[arg-type]
    unknown = load_ktype_catalog(_Driver([row]))  # type: ignore[arg-type]

    assert loaded[0].electrification == "plug_in_hybrid"
    assert unknown[0].electrification is None


def test_cars_differing_only_in_electrification_are_not_served_one_cached_answer() -> None:
    evaluator = TecDocDryRunEvaluator(_glc_catalog())

    elhybrid = evaluator.evaluate(_glc_car("hybrid", 1))
    unstated = evaluator.evaluate(_glc_car(None, 2))

    assert evaluator.evaluation_key(_glc_car("hybrid")) != evaluator.evaluation_key(_glc_car(None))
    assert (elhybrid.terminal, elhybrid.top_candidate_reference) == ("resolved", "glc200")
    assert unstated.terminal == "review_required"
    assert "match_guard:plug_in_power_unverified" in unstated.reason_codes


def test_a_car_with_neither_electrification_nor_registry_family_keeps_its_key() -> None:
    evaluator = TecDocDryRunEvaluator((VehicleCandidate("1", "Volvo", "V60"),))

    key = evaluator.evaluation_key(_car({"manufacturer": "Volvo"}, candidates={"model_family": "V60"}))

    assert key is not None
    assert not any(isinstance(part, tuple) and part[:1] in {("electrification",), ("registry_family",)}
                   for part in key)


# R1 through the evaluator, and R1b: the registry's electrification type against the KType's.


@pytest.mark.parametrize("registered", [None, "plug_in_hybrid"])
def test_a_plug_in_or_unstated_hybrid_ties_with_its_plug_in_sibling_and_says_why(registered: str | None) -> None:
    from api.app.features.vehicle_matching.service import verdict_of

    evaluation = TecDocDryRunEvaluator(_glc_catalog()).evaluate(_glc_car(registered))

    assert evaluation.terminal == "review_required"
    assert evaluation.top_candidate_reference == "glc200"
    assert "route:candidate_margin_below_gate" in evaluation.reason_codes
    assert "match_guard:plug_in_power_unverified" in evaluation.reason_codes
    assert evaluation.decision_trace[-1]["rule_id"] == "ROUTE-REVIEW-PLUG-IN-POWER-V1"
    assert "plug-in sibling" in str(verdict_of(evaluation))


@pytest.mark.parametrize("ktype", ["combustion", "mild_hybrid", "full_hybrid", "battery_electric"])
def test_a_car_registered_as_a_plug_in_goes_to_review_on_a_ktype_that_is_none(ktype: str) -> None:
    # 330e cars resolved to the 330i of the 3 Touring (F31), which has no plug-in KType.
    evaluator = TecDocDryRunEvaluator((VehicleCandidate("330i", "BMW", "3 Touring (F31)", model_aliases=("330I",),
                                                        fuels=frozenset({"petrol"}), power_kw=135,
                                                        electrification=ktype),))

    evaluation = evaluator.evaluate(_car({"manufacturer": "BMW", "model_family": "330I", "fuel_match_tokens": _HYBRID,
                                          "power_kw": 135, "electrification_type": "plug_in_hybrid"}))

    assert evaluation.terminal == "review_required"
    assert evaluation.top_candidate_reference == "330i"
    assert "electrification_conflict" in evaluation.reason_codes
    assert evaluation.decision_trace[-1]["rule_id"] == "ROUTE-REVIEW-ELECTRIFICATION-CONFLICT-V1"


def _terramar(registered: str, *catalog: VehicleCandidate):  # type: ignore[no-untyped-def]
    return TecDocDryRunEvaluator(catalog).evaluate(_car({
        "manufacturer": "Cupra", "model_family": "TERRAMAR", "fuel_match_tokens": _HYBRID,
        "power_kw": 150, "electrification_type": registered,
    }))


def test_a_car_registered_as_a_non_plug_in_hybrid_goes_to_review_on_a_plug_in_ktype() -> None:
    # A Terramar registered ELHYBRID (13 kW electric: a mild hybrid) on the eHybrid.
    evaluation = _terramar(
        "hybrid",
        _hybrid("ehybrid", "TERRAMAR", 150, "plug_in_hybrid", make="Cupra"),
        _hybrid("etsi", "TERRAMAR", 110, "mild_hybrid", make="Cupra"),
    )

    assert evaluation.terminal == "review_required"
    assert evaluation.top_candidate_reference == "ehybrid"
    assert "electrification_conflict" in evaluation.reason_codes


def _range_rover_evaluator() -> TecDocDryRunEvaluator:
    # The reviewed alias "RANGE ROVER" reads the Evoque, whose P300 MHEV ties on power with
    # nothing but a plug-in; the base reading reaches the L405 plug-in by its own name.
    return TecDocDryRunEvaluator(
        (
            _hybrid("rr4", "RANGE ROVER IV (L405)", 297, "plug_in_hybrid", make="Land Rover",
                    model_aliases=("RANGE ROVER", "RANGE ROVER IV"), year_from=2017, year_to=2021),
            _hybrid("evq", "RANGE ROVER EVOQUE (L551)", 221, "mild_hybrid", make="Land Rover",
                    model_aliases=("RANGE ROVER EVOQUE",), year_from=2018),
            _hybrid("evq-phev", "RANGE ROVER EVOQUE (L551)", 227, "plug_in_hybrid", make="Land Rover",
                    model_aliases=("RANGE ROVER EVOQUE",), year_from=2018),
        ),
        reviewed_model_aliases=_aliases("Land Rover", ("RANGE ROVER", "RANGE ROVER EVOQUE")),
    )


_RANGE_ROVER_CAR = _car({"manufacturer": "Land Rover", "model_family": "Range Rover", "production_year": 2021,
                         "power_kw": 221, "fuel_match_tokens": _HYBRID})


def test_a_reading_the_plug_in_guard_held_back_is_not_resolved_around() -> None:
    # A 2021 plug-in Range Rover: unguarded, the alias reading resolved the Evoque P300
    # MHEV; with that reading held back the base reading resolved the L405 instead.
    evaluation = _range_rover_evaluator().evaluate(_RANGE_ROVER_CAR)

    assert evaluation.terminal == "review_required"
    assert evaluation.top_candidate_reference == "evq"
    assert "match_guard:plug_in_power_unverified" in evaluation.reason_codes
    assert evaluation.decision_trace[-1]["rule_id"] == "ROUTE-REVIEW-PLUG-IN-POWER-V1"


def test_a_non_plug_in_hybrid_keeps_a_plug_in_ktype_whose_family_has_no_other_hybrid() -> None:
    # Every i8 KType is a plug-in; TecDoc files the 2011 Panamera S Hybrid as one too.
    evaluation = _terramar("hybrid", _hybrid("ehybrid", "TERRAMAR", 150, "plug_in_hybrid", make="Cupra"),
                           _hybrid("tsi", "TERRAMAR", 110, "combustion", make="Cupra"))

    assert evaluation.terminal == "resolved"
    assert "electrification_conflict" not in evaluation.reason_codes


@pytest.mark.parametrize(
    ("registered", "ktype"),
    [
        ("hybrid", "range_extender"),  # a range extender is no conflict for ELHYBRID
        ("hybrid", "mild_hybrid"),
        ("plug_in_hybrid", "plug_in_hybrid"),
        ("plug_in_hybrid", "range_extender"),
        ("plug_in_hybrid", None),  # unknown: a Neo4j catalog without engine types
        ("hybrid", None),
        ("battery_electric", "combustion"),  # other registry types are not read
    ],
)
def test_an_agreeing_or_unknown_electrification_changes_nothing(registered: str, ktype: str | None) -> None:
    evaluation = _terramar(registered, _hybrid("k", "TERRAMAR", 150, ktype, make="Cupra"),
                           _hybrid("etsi", "TERRAMAR", 110, "mild_hybrid", make="Cupra"))

    assert evaluation.terminal == "resolved"
    assert evaluation.top_candidate_reference == "k"


def test_the_electrification_check_sends_a_provisional_car_to_review_too() -> None:
    evaluator = TecDocDryRunEvaluator((VehicleCandidate("330i", "BMW", "330I", fuels=frozenset({"petrol"}),
                                                        candidate_type="TecDocKTypeCandidateOnly",
                                                        electrification="combustion"),))
    car = {"manufacturer": "BMW", "model_family": "330I", "fuel_match_tokens": _HYBRID}

    assert evaluator.evaluate(_car(car)).terminal == "provisional"
    assert evaluator.evaluate(_car({**car, "electrification_type": "plug_in_hybrid"})).terminal == "review_required"


# R6a: a reading that contradicts nothing over a hard conflict, inside the family.

_V70 = {"fuels": frozenset({"diesel"}), "power_kw": 120, "engine_codes": frozenset({"D5244T"})}


def _v70_catalog(*, body: str | None = None, siblings: int = 2) -> tuple[VehicleCandidate, ...]:
    # A 2005 V70 reads the V70 III (from 2007: a year conflict) on "V70", and the V70 II
    # that fits on "V70 II"; two identical V70 II KTypes tie.
    bodies = {"bodyworks": frozenset({body})} if body else {}
    return (
        VehicleCandidate("v70iii", "Volvo", "V70 III (135)", model_aliases=("V70",), year_from=2007,
                         year_to=2016, **({"bodyworks": frozenset({"estate"})} if body else {}), **_V70),  # type: ignore[arg-type]
        *(VehicleCandidate(f"v70ii-{index}", "Volvo", "V70 II (285)", model_aliases=("V70 II",), year_from=2000,
                           year_to=2007, **bodies, **_V70) for index in range(siblings)),  # type: ignore[arg-type]
    )


def _v70_car(second_value: str, **normalized: object) -> MatchSourceRecord:
    return _car({"manufacturer": "Volvo", "model_family": "V70", "production_year": 2005, "energy_sources": ["diesel"],
                 "power_kw": 120, "engine_code": "D5244T", **normalized},
                candidates={"model_family": second_value})


@pytest.mark.parametrize(
    ("second_value", "aliases"),
    [("V70 II", None), ("V70 MK2", _aliases("Volvo", ("V70 MK2", "V70 II")))],  # base, then alias path
)
def test_a_conflict_free_tie_replaces_a_hard_conflict_of_higher_confidence(
    second_value: str, aliases: ReviewedModelAliasIndex | None
) -> None:
    from api.app.features.vehicle_matching.service import verdict_of

    evaluation = TecDocDryRunEvaluator(_v70_catalog(), reviewed_model_aliases=aliases).evaluate(_v70_car(second_value))

    assert evaluation.terminal == "review_required"
    assert evaluation.top_candidate_reference == "v70ii-0"
    assert "hard_conflict_replaced:year" in evaluation.reason_codes
    assert "conflict:year" not in evaluation.reason_codes
    assert evaluation.decision_trace[-1]["rule_id"] == "ROUTE-REVIEW-HARD-CONFLICT-REPLACED-V1"
    assert "stays in review" in str(verdict_of(evaluation))


def test_a_hard_conflict_stays_when_the_other_readings_find_nothing() -> None:
    evaluation = TecDocDryRunEvaluator(_v70_catalog()).evaluate(_v70_car("ZZZ"))

    assert (evaluation.terminal, evaluation.top_candidate_reference) == ("hard_conflict", "v70iii")
    assert "conflict:year" in evaluation.reason_codes


def test_a_reading_with_a_body_conflict_never_replaces_a_hard_conflict() -> None:
    evaluation = TecDocDryRunEvaluator(_v70_catalog(body="sedan", siblings=1)).evaluate(
        _v70_car("V70 II", bodywork_form="estate")
    )

    assert (evaluation.terminal, evaluation.top_candidate_reference) == ("hard_conflict", "v70iii")


@pytest.mark.parametrize(
    ("engine_code", "terminal"),
    [("D5244T", "resolved"), ("XYZ999", "provisional")],  # a code no KType carries: provisional
)
def test_a_resolved_or_provisional_reading_wins_over_a_hard_conflict_as_before(
    engine_code: str, terminal: str
) -> None:
    evaluation = TecDocDryRunEvaluator(_v70_catalog(siblings=1)).evaluate(_v70_car("V70 II", engine_code=engine_code))

    assert (evaluation.terminal, evaluation.top_candidate_reference) == (terminal, "v70ii-0")
    assert not any(reason.startswith("hard_conflict_replaced") for reason in evaluation.reason_codes)


def test_without_a_registry_family_a_reading_in_the_conflicts_own_family_replaces_it() -> None:
    # A reviewed alias puts "V70" on the V70 II too: the alias matcher's V70 II tie
    # stands in for the base matcher's V70 III, the same family.
    evaluator = TecDocDryRunEvaluator(_v70_catalog(), reviewed_model_aliases=_aliases("Volvo", ("V70", "V70 II")))

    evaluation = evaluator.evaluate(_car({"manufacturer": "Volvo", "production_year": 2005, "energy_sources": ["diesel"],
                                          "power_kw": 120, "engine_code": "D5244T"}, evidence={"model": "V70"}))

    assert evaluation.terminal == "review_required"
    assert evaluation.top_candidate_reference == "v70ii-0"


def _gle_eqe() -> tuple[VehicleCandidate, ...]:
    return (
        VehicleCandidate("gle", "Mercedes-Benz", "GLE (V167)", model_aliases=("GLE",), year_from=2019, power_kw=200),
        *(VehicleCandidate(f"eqe-{index}", "Mercedes-Benz", "EQE SUV (X294)", model_aliases=("EQE SUV",),
                           year_from=2022, power_kw=270) for index in range(2)),
    )


def test_a_conflict_free_reading_never_leaves_the_registry_family() -> None:
    # A GLE 53 whose GLE conflicts on power is not shown the electric EQE SUV tie.
    evaluation = TecDocDryRunEvaluator(_gle_eqe()).evaluate(_car(
        {"manufacturer": "Mercedes-Benz", "model_family": "GLE", "production_year": 2023, "power_kw": 270},
        candidates={"model_family": "EQE SUV"},
    ))

    assert (evaluation.terminal, evaluation.top_candidate_reference) == ("hard_conflict", "gle")
    assert "conflict:power_kw" in evaluation.reason_codes


def test_without_a_registry_family_a_reading_never_leaves_the_conflicts_own_family() -> None:
    evaluator = TecDocDryRunEvaluator(_gle_eqe(), reviewed_model_aliases=_aliases("Mercedes-Benz", ("GLE", "EQE SUV")))

    evaluation = evaluator.evaluate(_car({"manufacturer": "Mercedes-Benz", "production_year": 2023, "power_kw": 270},
                                         evidence={"model": "GLE"}))

    assert (evaluation.terminal, evaluation.top_candidate_reference) == ("hard_conflict", "gle")


# F1b: an alternative value's winner outside the registry family.


def _ioniq(normalized: Mapping[str, object]) -> tuple[TecDocDryRunEvaluator, MatchSourceRecord]:
    # The car's own "IONIQ5" text reads the IONIQ 5, which conflicts on power; a
    # reviewed alias "IONIQ" lets the alias matcher resolve the IONIQ 6 on it.
    from ingestion.tecdoc.match_run_adapters import tecdoc_model_aliases

    evaluator = TecDocDryRunEvaluator(
        tuple(VehicleCandidate(reference, "Hyundai", model, model_aliases=tecdoc_model_aliases(model), power_kw=power)
              for reference, model, power in (("ioniq5", "IONIQ 5 (NE)", 160), ("ioniq6", "IONIQ 6 (CE)", 125))),
        reviewed_model_aliases=_aliases("Hyundai", ("IONIQ", "IONIQ 6")),
    )
    record = _car({"manufacturer": "Hyundai", "power_kw": 125, **normalized}, evidence={"model": "IONIQ5"})
    return evaluator, record


def _ioniq_evaluation(normalized: Mapping[str, object]):  # type: ignore[no-untyped-def]
    evaluator, record = _ioniq(normalized)
    return evaluator.evaluate(record)


def test_an_alternative_values_ktype_outside_the_registry_family_goes_to_review() -> None:
    evaluator, record = _ioniq({"model_family": "Ioniq 5"})

    evaluation = evaluator.evaluate(record)

    assert evaluation.terminal == "review_required"
    assert evaluation.top_candidate_reference == "ioniq6"
    assert "reading_disagreement:outside_registry_family" in evaluation.reason_codes
    assert evaluation.decision_trace[-1]["rule_id"] == "ROUTE-REVIEW-OUTSIDE-REGISTRY-FAMILY-V1"


@pytest.mark.parametrize("normalized", [{}, {"model_family": "Kona"}])  # no family; one the catalog lacks
def test_without_a_registry_family_reading_the_alternative_values_ktype_stands(normalized: dict[str, object]) -> None:
    evaluator, record = _ioniq(normalized)

    evaluation = evaluator.evaluate(record)

    assert (evaluation.terminal, evaluation.top_candidate_reference) == ("resolved", "ioniq6")


def test_cars_differing_only_in_registry_family_are_not_served_one_cached_answer() -> None:
    evaluator, with_family = _ioniq({"model_family": "Ioniq 5"})
    _, without = _ioniq({})

    assert evaluator.resolved_query(with_family).model_values == evaluator.resolved_query(without).model_values  # type: ignore[union-attr]
    assert evaluator.evaluation_key(with_family) != evaluator.evaluation_key(without)
    assert evaluator.evaluate(with_family).terminal == "review_required"
    assert evaluator.evaluate(without).terminal == "resolved"


def test_a_primary_values_ktype_outside_the_registry_family_is_not_checked() -> None:
    # A rule filled "Ioniq 5"; the registry's own text reads the IONIQ 6 first. Only
    # an alternative value's winner is held to the registry family.
    evaluator, _ = _ioniq({})
    record = MatchSourceRecord(1, {
        "normalization_status": "resolved", "inferred_fields": ["model_family"],
        "normalized": {"manufacturer": "Hyundai", "power_kw": 125, "model_family": "Ioniq 5"},
        "candidates": {"model_family": "IONIQ 6"},
    })

    evaluation = evaluator.evaluate(record)

    assert evaluator.resolved_query(record).model_values == ("IONIQ 6", "Ioniq 5")  # type: ignore[union-attr]
    assert (evaluation.terminal, evaluation.top_candidate_reference) == ("resolved", "ioniq6")


def test_an_ix1_the_registry_calls_x1_reaches_no_x1_reading_and_stays() -> None:
    evaluator = TecDocDryRunEvaluator((
        VehicleCandidate("ix1", "BMW", "iX1 (U11)", model_aliases=("IX1",), power_kw=150),
        VehicleCandidate("x3", "BMW", "X3 (G45)", model_aliases=("X3",), power_kw=150),
    ))

    evaluation = evaluator.evaluate(_car({"manufacturer": "BMW", "model_family": "X1", "power_kw": 150},
                                         candidates={"model_family": "IX1"}))

    assert (evaluation.terminal, evaluation.top_candidate_reference) == ("resolved", "ix1")


# F2r: the other matcher resolves another family on the winning value.


def _caravelle_evaluator(transporter_label: str) -> TecDocDryRunEvaluator:
    # Base reads "CARAVELLE" through the Transporter's own name; a reviewed alias puts
    # it on the Multivan, which the alias matcher resolves on power and body.
    return TecDocDryRunEvaluator(
        (
            VehicleCandidate("transporter", "VW", "TRANSPORTER T6 Bus (SGB)", model_aliases=(transporter_label,),
                             year_from=2015, power_kw=108),
            VehicleCandidate("multivan", "VW", "MULTIVAN T6 (SGF)", model_aliases=("MULTIVAN",),
                             bodyworks=frozenset({"bus"}), year_from=2015, power_kw=110),
        ),
        reviewed_model_aliases=_aliases("VW", ("CARAVELLE", "MULTIVAN")),
    )


_CARAVELLE_CAR = _car({"manufacturer": "VW", "model_family": "CARAVELLE", "production_year": 2018,
                       "power_kw": 110, "bodywork_form": "bus"})


def _caravelle(transporter_label: str):  # type: ignore[no-untyped-def]
    return _caravelle_evaluator(transporter_label).evaluate(_CARAVELLE_CAR)


def test_base_and_alias_resolving_different_families_go_to_review() -> None:
    evaluation = _caravelle("CARAVELLE")

    assert evaluation.terminal == "review_required"
    assert evaluation.top_candidate_reference == "multivan"
    assert "reading_disagreement:other_matcher_family" in evaluation.reason_codes


def test_a_base_phonetic_reading_in_another_family_changes_nothing() -> None:
    evaluation = _caravelle("KARAVELLE")

    assert (evaluation.terminal, evaluation.top_candidate_reference) == ("resolved", "multivan")


# K2: another model value resolves another KType.


def _v60(*extra: VehicleCandidate, cross_country_engine: frozenset[str] = frozenset()) -> TecDocDryRunEvaluator:
    return TecDocDryRunEvaluator((
        VehicleCandidate("v60", "Volvo", "V60 I (155)", model_aliases=("V60",)),
        VehicleCandidate("v60cc", "Volvo", "V60 I Cross Country (157)", model_aliases=("V60 Cross Country",),
                         engine_codes=cross_country_engine),
        *extra,
    ))


def test_v60_and_v60_cross_country_resolving_different_ktypes_go_to_review() -> None:
    evaluation = _v60().evaluate(_car({"manufacturer": "Volvo", "model_family": "V60"},
                                      candidates={"model_family": "V60 CROSS COUNTRY"}))

    assert evaluation.terminal == "review_required"
    assert evaluation.top_candidate_reference == "v60"
    assert "reading_disagreement:other_value_ktype" in evaluation.reason_codes
    assert evaluation.decision_trace[-1]["rule_id"] == "ROUTE-REVIEW-MODEL-VALUES-DISAGREE-V1"


def test_a_cross_country_right_on_its_own_value_goes_to_review_too_the_accepted_cost() -> None:
    # A 2024 V60 CC B5 that "V60 CROSS COUNTRY" got right: the plain "V60" resolves too.
    evaluation = _v60().evaluate(_car({"manufacturer": "Volvo", "model_family": "V60 CROSS COUNTRY"},
                                      candidates={"model_family": "V60"}))

    assert (evaluation.terminal, evaluation.top_candidate_reference) == ("review_required", "v60cc")


def test_another_value_that_only_ties_or_is_provisional_changes_nothing() -> None:
    car = {"manufacturer": "Volvo", "model_family": "V60"}
    tie = _v60(VehicleCandidate("v60cc-b", "Volvo", "V60 I Cross Country (157)", model_aliases=("V60 Cross Country",)))
    # The car's engine code is unknown to the catalog: the Cross Country, which carries
    # codes, is only provisional (C 400 4MATIC against the E-Class).
    provisional = _v60(cross_country_engine=frozenset({"B4204T"}))

    for evaluator, normalized in ((tie, car), (provisional, {**car, "engine_code": "XYZ999"})):
        evaluation = evaluator.evaluate(_car(normalized, candidates={"model_family": "V60 CROSS COUNTRY"}))

        assert (evaluation.terminal, evaluation.top_candidate_reference) == ("resolved", "v60")


def test_a_repeated_model_value_is_one_value() -> None:
    evaluator = TecDocDryRunEvaluator((VehicleCandidate("ibiza", "Seat", "IBIZA"),))
    record = _car({"manufacturer": "Seat"}, evidence={"model": "Ibiza"})

    assert evaluator.resolved_query(record).model_values == ("IBIZA", "Ibiza")  # type: ignore[union-attr]
    assert evaluator.evaluate(record).terminal == "resolved"


def _guard_cases() -> list[object]:
    v60_car = _car({"manufacturer": "Volvo", "model_family": "V60"}, candidates={"model_family": "V60 CROSS COUNTRY"})
    return [
        pytest.param(lambda: TecDocDryRunEvaluator(_glc_catalog()).evaluate(_glc_car(None)),
                     "match_guard:plug_in_power_unverified", "ROUTE-REVIEW-PLUG-IN-POWER-V1", id="R1"),
        pytest.param(lambda: _terramar("hybrid", _hybrid("ehybrid", "TERRAMAR", 150, "plug_in_hybrid", make="Cupra"),
                                       _hybrid("etsi", "TERRAMAR", 110, "mild_hybrid", make="Cupra")),
                     "electrification_conflict", "ROUTE-REVIEW-ELECTRIFICATION-CONFLICT-V1", id="R1b"),
        pytest.param(lambda: TecDocDryRunEvaluator(_v70_catalog()).evaluate(_v70_car("V70 II")),
                     "hard_conflict_replaced:year", "ROUTE-REVIEW-HARD-CONFLICT-REPLACED-V1", id="R6a"),
        pytest.param(lambda: _ioniq_evaluation({"model_family": "Ioniq 5"}),
                     "reading_disagreement:outside_registry_family", "ROUTE-REVIEW-OUTSIDE-REGISTRY-FAMILY-V1",
                     id="F1b"),
        pytest.param(lambda: _caravelle("CARAVELLE"),
                     "reading_disagreement:other_matcher_family", "ROUTE-REVIEW-MATCHERS-DISAGREE-V1", id="F2r"),
        pytest.param(lambda: _v60().evaluate(v60_car),
                     "reading_disagreement:other_value_ktype", "ROUTE-REVIEW-MODEL-VALUES-DISAGREE-V1", id="K2"),
    ]


@pytest.mark.parametrize(("evaluate", "reason", "rule_id"), _guard_cases())
def test_each_guard_gives_its_reason_and_the_verdict_explains_it(evaluate: object, reason: str, rule_id: str) -> None:
    from api.app.features.vehicle_matching.service import verdict_of

    evaluation = evaluate()  # type: ignore[operator]
    gate = evaluation.decision_trace[-1]

    assert evaluation.terminal == "review_required"
    assert reason in evaluation.reason_codes
    assert (gate["rule_id"], gate["signal"], gate["value"]) == (rule_id, "routing_gate", "review_required")
    assert [entry["sequence"] for entry in evaluation.decision_trace] == list(
        range(1, len(evaluation.decision_trace) + 1)
    )
    assert verdict_of(evaluation) == gate["explanation"]
    assert verdict_of(evaluation) != "Composite confidence meets the resolved threshold."


# The package: no guard raises a route or changes a resolved KType, and a tie stays one.


def _scenarios() -> list[tuple[str, object, MatchSourceRecord]]:
    """(name, evaluator factory, car) for every guard's cases, both ways."""

    v60_values = ({"model_family": "V60"}, {"model_family": "V60 CROSS COUNTRY"})
    scenarios: list[tuple[str, object, MatchSourceRecord]] = []
    for registered in (None, "hybrid", "plug_in_hybrid"):
        scenarios.append((f"glc-{registered}", lambda: TecDocDryRunEvaluator(_glc_catalog()), _glc_car(registered)))
        for ktype in ("plug_in_hybrid", "mild_hybrid", "combustion", None):
            scenarios.append((
                f"terramar-{registered}-{ktype}",
                lambda ktype=ktype: TecDocDryRunEvaluator((
                    _hybrid("k", "TERRAMAR", 150, ktype, make="Cupra"),
                    _hybrid("etsi", "TERRAMAR", 110, "mild_hybrid", make="Cupra"),
                )),
                _car({"manufacturer": "Cupra", "model_family": "TERRAMAR", "fuel_match_tokens": _HYBRID,
                      "power_kw": 150, **({"electrification_type": registered} if registered else {})}),
            ))
    for second in ("V70 II", "ZZZ"):
        for siblings in (1, 2):
            scenarios.append((f"v70-{second}-{siblings}", lambda siblings=siblings: TecDocDryRunEvaluator(
                _v70_catalog(siblings=siblings)), _v70_car(second)))
    scenarios.append(("gle", lambda: TecDocDryRunEvaluator(_gle_eqe()), _car(
        {"manufacturer": "Mercedes-Benz", "model_family": "GLE", "production_year": 2023, "power_kw": 270},
        candidates={"model_family": "EQE SUV"})))
    for normalized in ({"model_family": "Ioniq 5"}, {}):
        scenarios.append((f"ioniq-{normalized}", lambda: _ioniq({})[0], _ioniq(normalized)[1]))
    scenarios += [(f"caravelle-{label}", lambda label=label: _caravelle_evaluator(label), _CARAVELLE_CAR)
                  for label in ("CARAVELLE", "KARAVELLE")]
    scenarios.append(("range-rover-fall-through", _range_rover_evaluator, _RANGE_ROVER_CAR))
    for primary, alternative in (v60_values, v60_values[::-1]):
        scenarios.append((f"v60-{primary['model_family']}", _v60,
                          _car({"manufacturer": "Volvo", **primary}, candidates=alternative)))
    return scenarios


def test_the_package_scenarios_exercise_every_guard() -> None:
    reasons = {
        reason
        for _, factory, record in _scenarios()
        for reason in factory().evaluate(record).reason_codes  # type: ignore[operator]
    }

    assert {
        "match_guard:plug_in_power_unverified", "electrification_conflict", "hard_conflict_replaced:year",
        "reading_disagreement:outside_registry_family", "reading_disagreement:other_matcher_family",
        "reading_disagreement:other_value_ktype",
    } <= reasons


@pytest.mark.parametrize(("name", "factory", "record"), [pytest.param(*case, id=case[0]) for case in _scenarios()])
def test_no_guard_raises_a_route_changes_a_resolved_ktype_or_resolves_a_tie(
    name: str, factory: object, record: MatchSourceRecord, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingestion.fuzzy_matching import FuzzyVehicleMatcher

    guarded = factory().evaluate(record)  # type: ignore[operator]
    with monkeypatch.context() as unguarded:
        unguarded.setattr(FuzzyVehicleMatcher, "_plug_in_power_lead", lambda *_: False)
        unguarded.setattr(TecDocDryRunEvaluator, "_reading_guards", lambda *_: ())
        unguarded.setattr(TecDocDryRunEvaluator, "_conflict_free_reading", lambda *_: None)
        baseline = factory().evaluate(record)  # type: ignore[operator]

    rank = {"hard_conflict": 0, "review_required": 0, "provisional": 1, "resolved": 2}
    assert rank[guarded.terminal] <= rank[baseline.terminal], name
    if guarded.terminal != "review_required" or baseline.terminal != "hard_conflict":
        # Only a hard conflict's suggestion may change, and only to a reading in review.
        assert guarded.top_candidate_reference == baseline.top_candidate_reference, name
    if "route:candidate_margin_below_gate" in baseline.reason_codes:
        assert guarded.terminal != "resolved", name


# Catalog names in the registry's spelling: apostrophes and accents.


def _ktype(reference: str, maker: str, model: str, **fields: object) -> VehicleCandidate:
    """A KType with the model aliases the catalog loaders give it."""
    from ingestion.tecdoc.match_run_adapters import reviewed_export_names, tecdoc_model_aliases

    aliases = tuple(dict.fromkeys((*tecdoc_model_aliases(model), *reviewed_export_names(maker, model))))
    return VehicleCandidate(reference, maker, model, model_aliases=aliases, **fields)  # type: ignore[arg-type]


def _outcome(evaluator: TecDocDryRunEvaluator, record: MatchSourceRecord) -> tuple[str, str | None]:
    evaluation = evaluator.evaluate(record)
    return evaluation.terminal, evaluation.top_candidate_reference


def test_tecdoc_model_aliases_spell_an_apostrophe_name_as_the_registry_does() -> None:
    from ingestion.tecdoc.match_run_adapters import tecdoc_model_aliases

    # TecDoc writes "CEE'D"; the registry writes "CEED" for every generation.
    assert tecdoc_model_aliases("CEE'D (JD)") == ("CEE'D", "CEED")
    assert "PRO CEED" in tecdoc_model_aliases("PRO CEE'D (JD)")
    assert "CEED Sportswagon" in tecdoc_model_aliases("CEE'D Sportswagon (JD)")
    # The typographic apostrophe is the same sign.
    assert "CEED" in tecdoc_model_aliases("CEE\u2019D (JD)")
    # A name without one gains nothing.
    assert tecdoc_model_aliases("CEED (CD)") == ("CEED",)


def test_tecdoc_model_aliases_fold_accents_but_never_a_swedish_letter() -> None:
    from ingestion.tecdoc.match_run_adapters import registry_spelling, tecdoc_model_aliases

    assert tecdoc_model_aliases("SANTA FÉ III (DM, DMA)") == ("SANTA FE", "SANTA FE III", "SANTA FÉ", "SANTA FÉ III")
    assert tecdoc_model_aliases("GRAND SANTA FÉ") == ("GRAND SANTA FE",)
    assert tecdoc_model_aliases("MURCIÉLAGO") == ("MURCIELAGO",)
    assert "SCENIC" in tecdoc_model_aliases("SCÉNIC IV (J9_)")
    # Å, Ä and Ö are letters of the registry's alphabet: Opel's Kapitän is no "KAPITAN".
    assert tecdoc_model_aliases("KAPITÄN A") == ()
    assert registry_spelling("Å Ä Ö å ä ö") == "Å Ä Ö å ä ö"
    # The same letters written as a base letter and a combining mark.
    assert registry_spelling("SANTA FÉ") == "SANTA FE"
    assert registry_spelling("KAPITÄN") == "KAPITÄN"
    # No other sign is touched: DS's "N°4" keeps its degree sign.
    assert registry_spelling("N°4") == "N°4"


def _kia(*, cd_power_kw: int = 88) -> TecDocDryRunEvaluator:
    petrol = frozenset({"petrol"})
    return TecDocDryRunEvaluator(
        (
            _ktype("jd", "KIA", "CEE'D (JD)", year_from=2012, year_to=2018, month_from=201205, month_to=201807,
                   fuels=petrol, power_kw=88),
            _ktype("cd", "KIA", "CEED (CD)", year_from=2018, month_from=201803, fuels=petrol, power_kw=cd_power_kw),
            _ktype("cd-sw", "KIA", "CEED Sportswagon (CD)", year_from=2018, month_from=201804,
                   bodyworks=frozenset({"estate"}), fuels=petrol, power_kw=cd_power_kw),
        ),
        # The reviewed rule the live rule set has: it names the 2018+ "CEED" families.
        reviewed_model_aliases=_aliases("Kia", ("CEED", "Ceed")),
    )


@pytest.mark.parametrize(
    ("year", "month", "expected"),
    [
        # Before the change every "CEED" was read as the 2018+ CEED (CD), the only
        # exact name; the 2012-2018 CEE'D (JD) scored 0.65 and stayed provisional.
        (2015, 6, ("resolved", "jd")),
        (2017, 11, ("resolved", "jd")),
        (2017, None, ("resolved", "jd")),
        # 2018 is both generations' year. Built in months both cover, or with no
        # month, the car is a tie for a person to choose: never the JD by its name.
        (2018, 6, ("review_required", "cd")),
        (2018, None, ("review_required", "cd")),
        # Built after the JD ended (07/2018), or in a later year: the CD.
        (2018, 9, ("resolved", "cd")),
        (2019, 2, ("resolved", "cd")),
    ],
)
def test_a_registry_ceed_reaches_the_ceed_generation_its_years_name(
    year: int, month: int | None, expected: tuple[str, str]
) -> None:
    normalized = {"manufacturer": "Kia", "model_family": "Ceed", "production_year": year,
                  "fuel_match_tokens": ["petrol"], "power_kw": 88,
                  **({"production_month": month} if month else {})}

    assert _outcome(_kia(), _car(normalized, evidence={"brand": "KIA", "model": "CEED"})) == expected


def test_a_ceed_built_in_2018_before_the_cd_started_needs_more_than_its_month() -> None:
    # 01/2018 is inside the JD's months and before the CD's first (03/2018). To the
    # month rule "CEE'D" and "CEED" are two model lines, so the month does not demote
    # the CD: on equal evidence the car ties, and only other evidence makes it a JD
    # (here the JD's exact power against a CD one kilowatt away).
    normalized = {"manufacturer": "Kia", "model_family": "Ceed", "production_year": 2018, "production_month": 1,
                  "fuel_match_tokens": ["petrol"], "power_kw": 88}
    record = _car(normalized, evidence={"brand": "KIA", "model": "CEED"})

    tie = _kia().evaluate(record)
    assert tie.terminal == "review_required"
    assert "match:candidate_margin_not_met" in tie.reason_codes
    assert _outcome(_kia(cd_power_kw=87), record) == ("resolved", "jd")


def _santa_fe() -> TecDocDryRunEvaluator:
    diesel = {"fuels": frozenset({"diesel"}), "power_kw": 147}
    return TecDocDryRunEvaluator(
        (
            _ktype("dm", "HYUNDAI", "SANTA FÉ III (DM, DMA)", year_from=2012, year_to=2018, month_from=201209,
                   month_to=201812, **diesel),
            _ktype("tm", "HYUNDAI", "SANTA FE IV (TM, TMA)", year_from=2018, month_from=201802, **diesel),
            _ktype("mx5", "HYUNDAI", "SANTA FE V (MX5)", year_from=2024, month_from=202402,
                   fuels=frozenset({"hybrid_petrol"}), power_kw=158),
            _ktype("grand", "HYUNDAI", "GRAND SANTA FÉ", year_from=2013, year_to=2019, month_from=201301,
                   month_to=201912, **diesel),
        ),
        reviewed_model_aliases=_aliases("Hyundai", ("SANTA FE", "Santa Fe")),
    )


@pytest.mark.parametrize(
    ("text", "year", "month", "expected"),
    [
        # TecDoc accents the first three generations and not the fourth, so every
        # registry "SANTA FE" used to read as the 2018+ SANTA FE IV (TM) alone.
        ("SANTA FE", 2015, 6, ("resolved", "dm")),
        # A 2017 car was resolved to the TM, a year before the TM existed.
        ("SANTA FE", 2017, 6, ("resolved", "dm")),
        ("GRAND SANTA FE", 2017, 3, ("resolved", "grand")),
        # 2018 is the year both were built: a tie where nothing else separates them.
        ("SANTA FE", 2018, 6, ("review_required", "dm")),
        ("SANTA FE", 2018, None, ("review_required", "dm")),
        ("SANTA FE", 2019, 4, ("resolved", "tm")),
    ],
)
def test_an_unaccented_registry_santa_fe_reaches_the_accented_generations(
    text: str, year: int, month: int | None, expected: tuple[str, str]
) -> None:
    normalized = {"manufacturer": "Hyundai", "model_family": "Santa Fe", "production_year": year,
                  "fuel_match_tokens": ["diesel"], "power_kw": 147,
                  **({"production_month": month} if month else {})}

    assert _outcome(_santa_fe(), _car(normalized, evidence={"brand": "HYUNDAI", "model": text})) == expected


def test_an_unaccented_brand_text_names_an_accented_catalog_model() -> None:
    evaluator = TecDocDryRunEvaluator((
        _ktype("murcielago", "LAMBORGHINI", "MURCIÉLAGO", year_from=2001, power_kw=426),
        _ktype("gallardo", "LAMBORGHINI", "GALLARDO", year_from=2003, power_kw=368),
    ))
    record = _car({"manufacturer": "Lamborghini", "production_year": 2003, "power_kw": 426},
                  evidence={"brand": "LAMBORGHINI MURCIELAGO"})

    assert _outcome(evaluator, record) == ("resolved", "murcielago")


# Reviewed export names: one test per name.


@pytest.mark.parametrize(
    ("maker", "model", "names"),
    [
        ("VW", "GOLF PLUS V (5M1, 521)", ("GOLF PLUS",)),
        ("VW", "NEW BEETLE (9C1, 1C1)", ("BEETLE",)),
        ("VW", "NEW BEETLE Convertible (1Y7)", ("BEETLE",)),
        ("VW", "ID. Buzz Bus (EBB, EBJ)", ("ID. BUZZ",)),
        ("VW", "PASSAT CC B6 (357)", ("CC",)),
        ("VW", "CC B7 (358)", ("CC",)),
        ("SKODA", "E-CITIGO (NE1)", ("CITIGO",)),
        ("MERCEDES-BENZ", "PAGODE (W113)", ("SL",)),
        ("JAGUAR", "XJ (XJ40, XJ81)", ("SOVEREIGN",)),
        ("JAGUAR", "XJ", ("SOVEREIGN",)),
        ("VOLVO", "P 210 DUETT (P211, P212)", ("DUETT",)),
        ("VOLVO", "PV 445 DUETT (P445)", ("DUETT",)),
        ("RENAULT", "SCENIC E-TECH PHASE I", ("SCENIC E-TECH",)),
        ("MAZDA", "3 (BM, BN)", ("MAZDA3",)),
        ("MAZDA", "3 Saloon (BP_)", ("MAZDA3 SALOON",)),
        ("MAZDA", "2 Hatchback (DL, DJ)", ("MAZDA2 HATCHBACK",)),
        ("MAZDA", "6 Estate (GJ, GL)", ("MAZDA6 ESTATE",)),
        # Families that keep their TecDoc name only.
        ("VW", "ID. Buzz Cargo (EBA)", ()),         # the van
        ("VW", "GOLF PLUS Van (521)", ()),
        ("VW", "KAEFER", ()),                       # Beetle or Käfer: a naming question on hold
        ("VW", "PASSAT B7 (362)", ()),
        ("JAGUAR", "XJ Coupe", ()),                 # no Sovereign was a coupe
        ("VOLVO", "P 121", ()),                     # Amazon -> P 121 is not reviewed (1966 builds)
        ("SKODA", "ENYAQ iV SUV (5AZ)", ()),        # SUV or Coupé stays a person's choice
        ("MAZDA", "5 (CR)", ()),
        ("MAZDA", "323 F V (BA)", ()),
        ("MAZDA", "6e (GN)", ()),
        ("MAZDA", "CX-3 (DK)", ()),
        # A name belongs to its own manufacturer.
        ("DAIMLER", "XJ 40, 81", ()),
        ("BMW", "3 (E90, E91)", ()),
        ("SEAT", "CITIGO", ()),
    ],
)
def test_reviewed_export_names_by_family(maker: str, model: str, names: tuple[str, ...]) -> None:
    from ingestion.tecdoc.match_run_adapters import reviewed_export_names

    assert reviewed_export_names(maker, model) == names


_PETROL = frozenset({"petrol"})
_ELECTRIC = frozenset({"electric"})


def _named(make: str, text: str, *, inferred: bool = False, **normalized: object) -> MatchSourceRecord:
    """A car whose registry model field reads `text`; `inferred` marks a rule-filled family."""
    record = _car({"manufacturer": make, **normalized}, evidence={"brand": make, "model": text})
    if inferred:
        record.payload["inferred_fields"] = ["model_family"]
    return record


def _vw() -> TecDocDryRunEvaluator:
    hatch, coupe = frozenset({"hatchback"}), frozenset({"coupe"})
    return TecDocDryRunEvaluator((
        _ktype("golf-plus", "VW", "GOLF PLUS V (5M1, 521)", year_from=2004, year_to=2013, fuels=_PETROL,
               power_kw=75, bodyworks=hatch),
        _ktype("golf-v", "VW", "GOLF V (1K1)", year_from=2003, year_to=2009, fuels=_PETROL, power_kw=75,
               bodyworks=hatch),
        _ktype("new-beetle", "VW", "NEW BEETLE (9C1, 1C1)", year_from=1998, year_to=2010, fuels=_PETROL,
               power_kw=85, bodyworks=hatch),
        _ktype("new-beetle-cabrio", "VW", "NEW BEETLE Convertible (1Y7)", year_from=2002, year_to=2010,
               fuels=_PETROL, power_kw=85, bodyworks=frozenset({"convertible"})),
        _ktype("beetle", "VW", "BEETLE (5C1, 5C2)", year_from=2011, year_to=2019, fuels=_PETROL, power_kw=77,
               bodyworks=hatch),
        _ktype("beetle-cabrio", "VW", "BEETLE Convertible (5C7, 5C8)", year_from=2011, year_to=2019,
               fuels=_PETROL, power_kw=77, bodyworks=frozenset({"convertible"})),
        _ktype("buzz", "VW", "ID. Buzz Bus (EBB, EBJ)", year_from=2022, fuels=_ELECTRIC, power_kw=150,
               bodyworks=frozenset({"bus"})),
        _ktype("buzz-cargo", "VW", "ID. Buzz Cargo (EBA)", year_from=2022, fuels=_ELECTRIC, power_kw=150,
               bodyworks=frozenset({"van"})),
        _ktype("cc-b6", "VW", "PASSAT CC B6 (357)", year_from=2008, year_to=2012, month_from=200802,
               month_to=201201, fuels=_PETROL, power_kw=118, bodyworks=coupe),
        _ktype("cc-b7", "VW", "CC B7 (358)", year_from=2011, year_to=2016, month_from=201111, month_to=201612,
               fuels=_PETROL, power_kw=118, bodyworks=coupe),
        _ktype("passat-b7", "VW", "PASSAT B7 (362)", year_from=2010, year_to=2014, fuels=_PETROL, power_kw=118,
               bodyworks=frozenset({"sedan"})),
    ), reviewed_model_aliases=_aliases("VW", ("BEETLE", "Beetle")))


def test_a_registry_golf_plus_is_the_golf_plus_not_the_golf() -> None:
    record = _named("VW", "GOLF PLUS", model_family="Golf", production_year=2007, power_kw=75,
                    fuel_match_tokens=["petrol"], bodywork_form="hatchback")

    assert _outcome(_vw(), record) == ("resolved", "golf-plus")


@pytest.mark.parametrize(
    ("year", "body", "expected"),
    [
        # TecDoc's "BEETLE (5C1, 5C2)" starts 2011; the registry's earlier "BEETLE" is the New Beetle.
        (2003, "hatchback", "new-beetle"),
        (2005, "convertible", "new-beetle-cabrio"),
        (2014, "hatchback", "beetle"),
        (2014, "convertible", "beetle-cabrio"),
    ],
)
def test_a_registry_beetle_is_the_new_beetle_or_the_beetle_by_its_year(year: int, body: str, expected: str) -> None:
    record = _named("VW", "BEETLE", model_family="Beetle", production_year=year, power_kw=85 if year < 2011 else 77,
                    fuel_match_tokens=["petrol"], bodywork_form=body)

    assert _outcome(_vw(), record) == ("resolved", expected)


def test_a_registry_id_buzz_is_the_bus_never_the_cargo_van() -> None:
    record = _named("VW", "ID. BUZZ PRO", model_family="ID. Buzz", production_year=2024, power_kw=150,
                    fuel_match_tokens=["electric"])

    assert _outcome(_vw(), record) == ("resolved", "buzz")


@pytest.mark.parametrize(
    ("year", "month", "expected"),
    [
        # A rule fills these cars "Passat"; the registry's model field reads "CC".
        (2010, None, ("resolved", "cc-b6")),
        (2014, None, ("resolved", "cc-b7")),
        (2012, 6, ("resolved", "cc-b7")),
        # Built while TecDoc lists both (11/2011-01/2012): a tie, for a person.
        (2011, 12, ("review_required", "cc-b6")),
    ],
)
def test_a_registry_cc_is_the_passat_cc_or_the_cc_by_its_years(
    year: int, month: int | None, expected: tuple[str, str]
) -> None:
    record = _named("VW", "CC", inferred=True, model_family="Passat", production_year=year, power_kw=118,
                    fuel_match_tokens=["petrol"], bodywork_form="coupe",
                    **({"production_month": month} if month else {}))

    assert _outcome(_vw(), record) == expected


def test_the_fuel_tells_the_electric_citigo_from_the_petrol_one() -> None:
    evaluator = TecDocDryRunEvaluator((
        _ktype("citigo", "SKODA", "CITIGO (NF1)", year_from=2011, year_to=2019, fuels=_PETROL, power_kw=44),
        _ktype("e-citigo", "SKODA", "E-CITIGO (NE1)", year_from=2019, year_to=2021, fuels=_ELECTRIC, power_kw=61),
    ))
    electric = _named("Skoda", "CITIGO", model_family="Citigo", production_year=2020, power_kw=61,
                      fuel_match_tokens=["electric"])
    petrol = _named("Skoda", "CITIGO", model_family="Citigo", production_year=2019, power_kw=44,
                    fuel_match_tokens=["petrol"])

    assert _outcome(evaluator, electric) == ("resolved", "e-citigo")
    assert _outcome(evaluator, petrol) == ("resolved", "citigo")


@pytest.mark.parametrize(
    ("year", "power_kw", "expected"),
    [
        (1966, 110, ("resolved", "pagode")),
        # Only the W113's own years reach it: its neighbours keep their cars...
        (1960, 77, ("resolved", "w121")),
        (1975, 147, ("resolved", "r107")),
        # ...and a 1975 car with the Pagode's power is a conflict, not a Pagode.
        (1975, 110, ("hard_conflict", "pagode")),
    ],
)
def test_a_registry_sl_of_the_w113_years_is_the_pagode(year: int, power_kw: int, expected: tuple[str, str]) -> None:
    evaluator = TecDocDryRunEvaluator((
        _ktype("w121", "MERCEDES-BENZ", "SL (W121)", year_from=1955, year_to=1963, fuels=_PETROL, power_kw=77),
        _ktype("pagode", "MERCEDES-BENZ", "PAGODE (W113)", year_from=1963, year_to=1971, fuels=_PETROL, power_kw=110),
        _ktype("r107", "MERCEDES-BENZ", "SL (R107)", year_from=1971, year_to=1989, fuels=_PETROL, power_kw=147),
    ))
    record = _car({"manufacturer": "Mercedes-Benz", "model_family": "SL", "production_year": year,
                   "power_kw": power_kw, "fuel_match_tokens": ["petrol"]})

    assert _outcome(evaluator, record) == expected


def test_a_registry_sovereign_is_the_xj_saloon_of_its_years() -> None:
    evaluator = TecDocDryRunEvaluator((
        _ktype("xj40", "JAGUAR", "XJ (XJ40, XJ81)", year_from=1986, year_to=1994, fuels=_PETROL, power_kw=166),
        _ktype("x300", "JAGUAR", "XJ (X300, X330)", year_from=1994, year_to=1997, fuels=_PETROL, power_kw=177),
        # The XJ-S, with the XJ40's power: not a Sovereign, so it never ties one.
        _ktype("xjs", "JAGUAR", "XJ Coupe", year_from=1973, year_to=1996, fuels=_PETROL, power_kw=166),
    ))

    def sovereign(year: int, power_kw: int) -> MatchSourceRecord:
        return _car({"manufacturer": "Jaguar", "model_family": "Sovereign", "production_year": year,
                     "power_kw": power_kw, "fuel_match_tokens": ["petrol"]})

    assert _outcome(evaluator, sovereign(1992, 166)) == ("resolved", "xj40")
    assert _outcome(evaluator, sovereign(1996, 177)) == ("resolved", "x300")


def test_a_registry_duett_is_the_duett_its_year_and_power_name() -> None:
    evaluator = TecDocDryRunEvaluator((
        _ktype("p445", "VOLVO", "PV 445 DUETT (P445)", year_from=1957, year_to=1960, fuels=_PETROL, power_kw=44),
        _ktype("p210-b16", "VOLVO", "P 210 DUETT (P211, P212)", year_from=1960, year_to=1962, fuels=_PETROL,
               power_kw=44),
        _ktype("p210-b18", "VOLVO", "P 210 DUETT (P211, P212)", year_from=1961, year_to=1967, fuels=_PETROL,
               power_kw=55),
    ))

    def duett(year: int, power_kw: int) -> MatchSourceRecord:
        record = _car({"manufacturer": "Volvo", "model_family": "Duett", "production_year": year,
                       "power_kw": power_kw, "fuel_match_tokens": ["petrol"]}, evidence={"brand": "VOLVO 21134 E"})
        record.payload["inferred_fields"] = ["model_family"]
        return record

    assert _outcome(evaluator, duett(1965, 55)) == ("resolved", "p210-b18")
    assert _outcome(evaluator, duett(1958, 44)) == ("resolved", "p445")
    # The name adds no tolerance: a 50 kW car still contradicts the 55 kW KType.
    assert _outcome(evaluator, duett(1965, 50))[0] == "hard_conflict"
    # 1960 is both Duetts' year, with the same 44 kW: a tie, never the P 210 by its name.
    tie = evaluator.evaluate(duett(1960, 44))
    assert tie.terminal == "review_required"
    assert "match:candidate_margin_not_met" in tie.reason_codes


def test_a_registry_scenic_e_tech_is_tecdocs_phase_i() -> None:
    evaluator = TecDocDryRunEvaluator((
        _ktype("scenic-e-tech", "RENAULT", "SCENIC E-TECH PHASE I", year_from=2023, fuels=_ELECTRIC, power_kw=160),
        _ktype("scenic-iv", "RENAULT", "SCÉNIC IV (J9_)", year_from=2016, year_to=2022, fuels=_PETROL, power_kw=103),
    ))
    electric = _named("Renault", "SCENIC E-TECH ELECTRIC", model_family="Scenic E-Tech", production_year=2024,
                      power_kw=160, fuel_match_tokens=["electric"])
    petrol = _named("Renault", "SCENIC", model_family="Scenic", production_year=2018, power_kw=103,
                    fuel_match_tokens=["petrol"])

    assert _outcome(evaluator, electric) == ("resolved", "scenic-e-tech")
    # The unaccented "SCENIC" of an earlier car is the accented SCÉNIC IV.
    assert _outcome(evaluator, petrol) == ("resolved", "scenic-iv")


def _mazda() -> TecDocDryRunEvaluator:
    return TecDocDryRunEvaluator((
        _ktype("3", "MAZDA", "3 (BM, BN)", year_from=2013, year_to=2019, fuels=_PETROL, power_kw=88,
               bodyworks=frozenset({"hatchback"})),
        _ktype("3-saloon", "MAZDA", "3 Saloon (BM_, BN_)", year_from=2013, year_to=2019, fuels=_PETROL,
               power_kw=88, bodyworks=frozenset({"sedan"})),
        _ktype("2", "MAZDA", "2 Hatchback (DL, DJ)", year_from=2014, fuels=_PETROL, power_kw=66,
               bodyworks=frozenset({"hatchback"})),
        _ktype("6-estate", "MAZDA", "6 Estate (GJ, GL)", year_from=2012, fuels=_PETROL, power_kw=107,
               bodyworks=frozenset({"estate"})),
        _ktype("cx-3", "MAZDA", "CX-3 (DK)", year_from=2015, fuels=_PETROL, power_kw=88,
               bodyworks=frozenset({"suv"})),
        _ktype("cx-5", "MAZDA", "CX-5 (KE, GH)", year_from=2011, year_to=2017, fuels=_PETROL, power_kw=121,
               bodyworks=frozenset({"suv"})),
    ))


@pytest.mark.parametrize(
    ("text", "family", "inferred", "power_kw", "body", "expected"),
    [
        ("MAZDA3", "Mazda3", False, 88, "hatchback", "3"),
        # The body word stays in the glued name, so the saloon is the saloon.
        ("MAZDA3", "Mazda3", False, 88, "sedan", "3-saloon"),
        # No rule knows "MAZDA2" or "MAZDA6", and a learned rule fills another model:
        # the car's own text is read first and now names its family.
        ("MAZDA2", "CX-3", True, 66, "hatchback", "2"),
        ("MAZDA6", "CX-5", True, 107, "estate", "6-estate"),
    ],
)
def test_a_make_glued_to_a_mazda_number_names_the_numbered_family(
    text: str, family: str, inferred: bool, power_kw: int, body: str, expected: str
) -> None:
    record = _named("Mazda", text, inferred=inferred, model_family=family, production_year=2016,
                    power_kw=power_kw, fuel_match_tokens=["petrol"], bodywork_form=body)

    assert _outcome(_mazda(), record) == ("resolved", expected)


# Model field against brand text, where one label sits on several generations.


def _kia_families() -> TecDocDryRunEvaluator:
    estate = frozenset({"estate"})
    return TecDocDryRunEvaluator(
        (
            _ktype("jd", "KIA", "CEE'D (JD)", year_from=2012, year_to=2018, fuels=_PETROL, power_kw=150),
            _ktype("cd", "KIA", "CEED (CD)", year_from=2018, fuels=_PETROL, power_kw=103),
            _ktype("ed-sw", "KIA", "CEE'D SW (ED)", year_from=2007, year_to=2012, fuels=_PETROL, power_kw=90,
                   bodyworks=estate),
            _ktype("jd-sw", "KIA", "CEE'D Sportswagon (JD)", year_from=2012, year_to=2018, fuels=_PETROL,
                   power_kw=99, bodyworks=estate),
            # TecDoc's own family for the three-door, with a 150 kW KType of its own.
            _ktype("pro-jd", "KIA", "PRO CEE'D (JD)", year_from=2013, year_to=2018, fuels=_PETROL, power_kw=150),
        ),
        reviewed_model_aliases=_aliases("Kia", ("CEED", "Ceed")),
    )


def _ceed(brand: str, year: int, power_kw: int) -> MatchSourceRecord:
    return _car({"manufacturer": "Kia", "model_family": "Ceed", "production_year": year,
                 "fuel_match_tokens": ["petrol"], "power_kw": power_kw}, evidence={"brand": brand, "model": "CEED"})


@pytest.mark.parametrize("brand", ["KIA PRO_CEE'D GT", "KIA PRO CEED GT"])
def test_a_ceed_whose_brand_text_names_the_pro_ceed_is_never_the_five_door(brand: str) -> None:
    # "CEED" sits on CEE'D (JD) and CEED (CD). While that shared label read as no
    # model at all, the check against the brand text was skipped and the car
    # resolved to the five-door CEE'D (JD) on its 150 kW.
    evaluation = _kia_families().evaluate(_ceed(brand, 2013, 150))

    assert evaluation.terminal == "review_required"
    assert evaluation.reason_codes == ("model_source_evidence_conflict",)
    assert evaluation.top_candidate_reference is None


@pytest.mark.parametrize(("brand", "year", "power_kw"), [("KIA CEED SW", 2010, 90), ("KIA CEED", 2014, 150)])
def test_a_ceed_whose_brand_text_names_a_ceed_is_one_family_named_twice(brand: str, year: int, power_kw: int) -> None:
    evaluation = _kia_families().evaluate(_ceed(brand, year, power_kw))

    assert "model_source_evidence_conflict" not in evaluation.reason_codes
    assert evaluation.top_candidate_reference in {"ed-sw", "jd"}


def _mini() -> TecDocDryRunEvaluator:
    # The catalog calls a model by its make, and "MINI" is a label on every generation.
    def ktype(reference: str, model: str, name: str, type_name: str, **fields: object) -> VehicleCandidate:
        return VehicleCandidate(reference, "MINI", model, model_aliases=(name, type_name), **fields)  # type: ignore[arg-type]

    return TecDocDryRunEvaluator((
        ktype("r56", "MINI (R56)", "MINI", "Cooper", year_from=2006, year_to=2013, fuels=_PETROL, power_kw=90),
        ktype("f56", "MINI (F56)", "MINI", "Cooper", year_from=2013, year_to=2024, fuels=_PETROL, power_kw=100),
        ktype("j01-e", "MINI COOPER (J01)", "MINI COOPER", "Cooper E", year_from=2023, fuels=_ELECTRIC,
              power_kw=135),
        ktype("u25", "MINI COUNTRYMAN (U25)", "MINI COUNTRYMAN", "Countryman E", year_from=2023,
              fuels=_ELECTRIC, power_kw=150),
        # The classic Mini is a catalog model called exactly "MINI", under another make.
        VehicleCandidate("classic", "ROVER", "MINI", model_aliases=("MINI",), year_from=1986, year_to=2000,
                         fuels=_PETROL, power_kw=46),
    ))


def _cooper_e(brand: str) -> MatchSourceRecord:
    return _car({"manufacturer": "MINI", "model_family": "MINI", "production_year": 2024,
                 "fuel_match_tokens": ["electric"], "power_kw": 135},
                evidence={"brand": brand, "model": "COOPER E"})


def test_a_brand_text_that_is_only_the_makes_name_names_no_model() -> None:
    # "MINI" beside the model text "COOPER E" is the make. Read as the catalog's
    # MINI it disagreed with the Cooper the model text names, and stopped the car.
    evaluation = _mini().evaluate(_cooper_e("MINI"))

    assert "model_source_evidence_conflict" not in evaluation.reason_codes
    assert (evaluation.terminal, evaluation.top_candidate_reference) == ("resolved", "j01-e")


def test_a_brand_text_that_names_another_model_still_disagrees() -> None:
    evaluation = _mini().evaluate(_cooper_e("MINI COUNTRYMAN"))

    assert evaluation.terminal == "review_required"
    assert evaluation.reason_codes == ("model_source_evidence_conflict",)


def _santa_fe_with_brand(brand: str) -> tuple[str, str | None]:
    evaluator = TecDocDryRunEvaluator((
        _ktype("cm", "HYUNDAI", "SANTA FÉ II (CM)", year_from=2005, year_to=2012, fuels=_PETROL, power_kw=128),
        _ktype("dm", "HYUNDAI", "SANTA FÉ III (DM, DMA)", year_from=2012, year_to=2018, fuels=_PETROL, power_kw=141),
        _ktype("grand", "HYUNDAI", "GRAND SANTA FÉ", year_from=2013, year_to=2018, fuels=_PETROL, power_kw=141),
    ))
    return _outcome(evaluator, _car(
        {"manufacturer": "Hyundai", "model_family": "Santa Fe", "production_year": 2015,
         "fuel_match_tokens": ["petrol"], "power_kw": 141}, evidence={"brand": brand, "model": "SANTA FE"}))


def test_a_santa_fe_whose_brand_text_names_the_grand_santa_fe_is_never_the_dm() -> None:
    assert _santa_fe_with_brand("HYUNDAI GRAND SANTA FE") == ("review_required", None)
    assert _santa_fe_with_brand("HYUNDAI SANTA FE") == ("resolved", "dm")


# A rule-inferred model reached only through a reviewed export name.


def _export_name_guard(evaluation: object) -> bool:
    codes = "export_name_without_engine_evidence" in evaluation.reason_codes  # type: ignore[attr-defined]
    gate = any(
        entry["rule_id"] == "ROUTE-REVIEW-EXPORT-NAME-WITHOUT-ENGINE-EVIDENCE-V1" and entry["signal"] == "routing_gate"
        for entry in evaluation.decision_trace  # type: ignore[attr-defined]
    )
    assert codes == gate
    return codes


def _inferred(make: str, family: str, brand: str, year: int, **normalized: object) -> MatchSourceRecord:
    record = _car({"manufacturer": make, "model_family": family, "production_year": year,
                   "fuel_match_tokens": ["petrol"], **normalized}, evidence={"brand": brand})
    record.payload["inferred_fields"] = ["model_family"]
    return record


def test_an_inferred_duett_needs_power_or_an_engine_code_not_its_name_and_year() -> None:
    evaluator = TecDocDryRunEvaluator((
        _ktype("p445", "VOLVO", "PV 445 DUETT (P445)", year_from=1957, year_to=1960, fuels=_PETROL, power_kw=44),
        _ktype("p210-b18", "VOLVO", "P 210 DUETT (P211, P212)", year_from=1961, year_to=1967, fuels=_PETROL,
               power_kw=55, engine_codes=("B 18 A",)),
    ))

    bare = evaluator.evaluate(_inferred("Volvo", "Duett", "VOLVO 21134 E", 1965))
    assert (bare.terminal, bare.top_candidate_reference) == ("review_required", "p210-b18")
    assert _export_name_guard(bare)

    for evidence in ({"power_kw": 55}, {"engine_code": "B 18 A"}):
        matched = evaluator.evaluate(_inferred("Volvo", "Duett", "VOLVO 21134 E", 1965, **evidence))
        assert matched.terminal in {"resolved", "provisional"}
        assert matched.top_candidate_reference == "p210-b18"
        assert not _export_name_guard(matched)


def test_an_inferred_sl_without_power_is_no_pagode_by_its_year() -> None:
    evaluator = TecDocDryRunEvaluator((
        _ktype("pagode", "MERCEDES-BENZ", "PAGODE (W113)", year_from=1963, year_to=1971, fuels=_PETROL, power_kw=110),
        _ktype("r107", "MERCEDES-BENZ", "SL (R107)", year_from=1971, year_to=1989, fuels=_PETROL, power_kw=147),
    ))

    # A plain "230" saloon a rule filled "SL": the name and 1964 alone reach the Pagode.
    bare = evaluator.evaluate(_inferred("Mercedes-Benz", "SL", "MB 230", 1964))
    assert (bare.terminal, bare.top_candidate_reference) == ("review_required", "pagode")
    assert _export_name_guard(bare)
    assert not _export_name_guard(evaluator.evaluate(_inferred("Mercedes-Benz", "SL", "MB 230", 1964, power_kw=110)))
    # "SL" is the R107's own name, and a model the registry states is not inferred.
    assert not _export_name_guard(evaluator.evaluate(_inferred("Mercedes-Benz", "SL", "MB 230", 1980)))
    stated = _car({"manufacturer": "Mercedes-Benz", "model_family": "SL", "production_year": 1964,
                   "fuel_match_tokens": ["petrol"]}, evidence={"brand": "MB 230 SL"})
    assert not _export_name_guard(evaluator.evaluate(stated))


# --- evaluating without remembering (a check over many cars) ------------------------------


def _what_if_records() -> tuple[MatchSourceRecord, ...]:
    """A car for every way an evaluation ends, so every branch that writes the memo runs."""

    return (
        _glc_car(None),
        _glc_car("plug_in_hybrid", 2),
        _glc_car("hybrid", 3),
        _car({"manufacturer": "Volvo", "model_family": "V60"}, record_id=4),
        # No catalog manufacturer, and a model no query can be built from.
        _car({"manufacturer": "Unknown Motors", "model_family": "GLC"}, record_id=5),
        _car({"manufacturer": "Mercedes-Benz", "model_family": "---"}, record_id=6),
        # Stopped before matching: nothing is scored, nothing is remembered either way.
        _car({"manufacturer": "Mercedes-Benz"}, record_id=7),
        MatchSourceRecord(8, {"normalization_status": "review_required"}),
    )


def test_an_evaluation_that_is_not_remembered_is_the_same_evaluation() -> None:
    catalog = (*_glc_catalog(), VehicleCandidate("v60", "Volvo", "V60"))
    records = _what_if_records()
    remembering, forgetting = TecDocDryRunEvaluator(catalog), TecDocDryRunEvaluator(catalog)

    remembered = [remembering.evaluate(record) for record in records]
    forgotten = [forgetting.evaluate(record, remember=False) for record in records]

    assert forgotten == remembered
    assert [evaluation.reason_codes for evaluation in remembered[4:6]] == [
        ("manufacturer_global_scope",), ("invalid_match_query_evidence",)]
    assert {evaluation.terminal for evaluation in remembered} == {
        "resolved", "review_required", "normalization_review"}
    # However many cars were checked, the memo did not grow ...
    assert (forgetting.cache_size, remembering.cache_size) == (0, 6)
    # ... a remembered answer is read rather than computed again, and stays what it was ...
    for record, evaluation in zip(records[:6], remembered[:6], strict=True):
        assert remembering.evaluate(record, remember=False) is evaluation
    assert remembering.cache_size == 6
    # ... and remembering afterwards gives the same answers once more.
    assert [forgetting.evaluate(record) for record in records] == remembered
    assert forgetting.cache_size == 6
