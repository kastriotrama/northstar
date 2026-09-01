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
