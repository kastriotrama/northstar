from dataclasses import replace

from ingestion.confidence_routing import ConfidenceRouter
from ingestion.fuzzy_matching import (
    FuzzyVehicleMatcher,
    ManufacturerCandidateIndex,
    VehicleCandidate,
    VehicleMatchQuery,
)
from ingestion.match_run_service import MatchSourceRecord
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator
from ingestion.vocabulary_alignment import FuelAlignment


def test_partial_model_cannot_resolve_from_text_alone() -> None:
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((
        VehicleCandidate("1", "Volkswagen", "Golf Plus"),
    )))
    result = matcher.match(VehicleMatchQuery(manufacturer="Volkswagen", model="Golf"))
    assert result.candidates
    assert result.candidates[0].text_score < 1
    assert "model_partial" in result.candidates[0].matched_fields
    assert ConfidenceRouter().route(result).route == "review_required"


def test_shared_technical_specs_do_not_establish_model_identity() -> None:
    candidate = VehicleCandidate(
        "1", "Volkswagen", "Golf Plus", year_from=2010, year_to=2014,
        fuels=frozenset({"petrol"}), displacement_cc=1390, power_kw=90,
    )
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((candidate,)))
    query = VehicleMatchQuery(
        manufacturer="Volkswagen", model="Golf", year=2012, fuels=frozenset({"petrol"}),
        displacement_cc=1390, power_kw=90,
    )
    assert ConfidenceRouter().route(matcher.match(query)).route == "review_required"


def test_explicit_model_does_not_lose_to_longer_brand_label() -> None:
    index = ManufacturerCandidateIndex((
        VehicleCandidate("1", "Volkswagen", "Golf"),
        VehicleCandidate("2", "Volkswagen", "Passat"),
    ))
    assert index.recover_model_from_evidence(
        "Volkswagen", {"model": "Golf", "brand": "Volkswagen Passat"}
    ) == ("Golf", "model")
    assert index.recover_model_from_evidence(
        "Volkswagen", {"model": "Unknown", "brand": "Volkswagen Passat"}
    ) is None


def test_contradictory_source_models_require_review() -> None:
    evaluator = TecDocDryRunEvaluator((
        VehicleCandidate("1", "Volkswagen", "Golf"),
        VehicleCandidate("2", "Volkswagen", "Passat"),
    ))
    result = evaluator.evaluate(MatchSourceRecord(1, {
        "normalized": {"manufacturer": "Volkswagen", "model_family": "Golf"},
        "source_evidence": {"model": "Golf", "brand": "Volkswagen Passat"},
    }))
    assert result.terminal == "review_required"
    assert "model_source_evidence_conflict" in result.reason_codes


def test_specific_raw_model_does_not_fall_back_to_broader_normalization() -> None:
    evaluator = TecDocDryRunEvaluator((
        VehicleCandidate("1", "Toyota", "Yaris"),
        VehicleCandidate("2", "Toyota", "Yaris Cross"),
    ))
    result = evaluator.evaluate(MatchSourceRecord(1, {
        "normalized": {"manufacturer": "Toyota", "model_family": "Yaris"},
        "source_evidence": {"model": "Toyota Yaris Cross", "brand": "Toyota"},
    }))
    assert result.top_candidate_reference == "2"


def test_short_alphanumeric_model_recovery_is_catalog_scoped() -> None:
    index = ManufacturerCandidateIndex((
        VehicleCandidate("1", "Audi", "A4"), VehicleCandidate("2", "Audi", "Quattro"),
    ))
    assert index.recover_model_from_evidence("Audi", {"model": "A4"}) == ("A4", "model")
    assert index.recover_model_from_evidence("BMW", {"model": "A4"}) is None


def test_chassis_suffix_does_not_become_commercial_series_conflict() -> None:
    candidate = VehicleCandidate("1", "Mercedes-Benz", "AMG C 63 S (205.487)")
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((candidate,)))
    query = VehicleMatchQuery(manufacturer="Mercedes-Benz", model="AMG C 63 S")
    assert "model_series" not in matcher._score(query, candidate).conflicting_fields
    assert "model_series" in matcher._score(
        replace(query, model="AMG C 43 S"), candidate
    ).conflicting_fields
    assert "model_series" in matcher._score(
        query, replace(candidate, model="AMG C 63 S (43)")
    ).conflicting_fields


def test_approved_fuel_compatibility_is_neutral_and_directional() -> None:
    candidate = VehicleCandidate("1", "Test", "Model", fuels=frozenset({"petrol"}))
    matcher = FuzzyVehicleMatcher(
        ManufacturerCandidateIndex((candidate,)),
        fuel_compatible_pairs=frozenset({("ethanol", "petrol")}),
    )
    result = matcher._score(
        VehicleMatchQuery(manufacturer="Test", model="Model", fuels=frozenset({"ethanol"})), candidate
    )
    assert "fuels" not in result.matched_fields
    assert "fuels" not in result.conflicting_fields
    assert "fuels_compatible_not_confirmed" in result.missing_fields
    reverse = matcher._score(
        VehicleMatchQuery(manufacturer="Test", model="Model", fuels=frozenset({"petrol"})),
        replace(candidate, fuels=frozenset({"ethanol"})),
    )
    assert "fuels" in reverse.conflicting_fields


def test_evaluator_consumes_source_scoped_alignment() -> None:
    candidate = VehicleCandidate("1", "Test", "Model", fuels=frozenset({"catalog-token"}))
    rules = FuelAlignment(
        "test-v1", {"source-token": "petrol"}, {"catalog-token": "petrol"}, frozenset()
    )
    record = MatchSourceRecord(1, {
        "normalized": {
            "manufacturer": "Test", "model_family": "Model", "energy_sources": ["source-token"],
        },
    })
    assert TecDocDryRunEvaluator((candidate,)).evaluate(record).terminal == "hard_conflict"
    assert TecDocDryRunEvaluator((candidate,), fuel_alignment=rules).evaluate(record).terminal == "resolved"


def test_reviewed_fuel_corrections_refresh_comparison_tokens_and_identity() -> None:
    from ingestion.normalization_repository import normalization_uuid
    from ingestion.normalization_rules import normalize_ts_record
    outcome = normalize_ts_record(
        {"brand": "TOYOTA", "fuel1": "01"},
        manufacturer_entity_rules={"policy:test": {
            "kind": "reviewed_record_policy", "rule_id": "TEST",
            "match_fields": {"brand": "TOYOTA"},
            "normalized_updates": {"energy_sources": ["hydrogen"]},
        }},
    )
    assert outcome.normalized["fuel_match_tokens"] == ["hydrogen"]
    assert outcome.pipeline_version == "normalization-pipeline-v7"
    assert normalization_uuid(1, "map", "rule", outcome.pipeline_version) != normalization_uuid(
        1, "map", "rule", "normalization-pipeline-v5"
    )
