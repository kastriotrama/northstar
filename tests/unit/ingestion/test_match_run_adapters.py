from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.match_run_service import MatchSourceRecord
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator


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


def test_tecdoc_model_aliases_strip_chassis_code_and_generation_numeral() -> None:
    from ingestion.tecdoc.match_run_adapters import tecdoc_model_aliases

    # TS stores the bare marketing name; TecDoc decorates it.
    assert "V60" in tecdoc_model_aliases("V60 I (155)")
    assert "QASHQAI" in tecdoc_model_aliases("QASHQAI I (J10, NJ10)")
    assert "GOLF" in tecdoc_model_aliases("GOLF VII (5G1, BQ1)")
    assert tecdoc_model_aliases("ID.4 (E21)") == ("ID.4",)


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
