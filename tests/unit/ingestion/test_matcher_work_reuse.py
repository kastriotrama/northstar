from dataclasses import replace
from unittest.mock import patch

import pytest

from ingestion.fuzzy_matching import (
    FuzzyMatchConfig,
    FuzzyVehicleMatcher,
    ManufacturerCandidateIndex,
    VehicleCandidate,
    VehicleMatchQuery,
    _best_model_label,
)


def _matcher(weight: float = 1.0) -> FuzzyVehicleMatcher:
    return FuzzyVehicleMatcher(ManufacturerCandidateIndex((
        VehicleCandidate("estate", "Test", "Model", bodyworks=frozenset({"estate"})),
        VehicleCandidate("suv", "Test", "Model", bodyworks=frozenset({"suv"})),
    )), FuzzyMatchConfig(bodywork_discriminating_weight=weight))


@pytest.mark.parametrize(("weight", "bodywork", "expected_calls"), [
    (1.0, "estate", 2), (1.0, None, 2), (2.0, None, 2), (2.0, "estate", 4),
])
def test_only_rescore_when_weight_can_change_result(weight, bodywork, expected_calls):
    matcher = _matcher(weight)
    with patch.object(matcher, "_score", wraps=matcher._score) as score:
        result = matcher.match(VehicleMatchQuery(model="Model", manufacturer="Test", bodywork=bodywork))
    assert score.call_count == expected_calls
    if bodywork:
        by_reference = {row.candidate_reference: row for row in result.candidates}
        assert "bodywork" in by_reference["suv"].conflicting_fields
        assert "bodywork" in by_reference["estate"].matched_fields


@pytest.mark.parametrize("weight", [1.0, 2.0])
@pytest.mark.parametrize("bodywork", [None, "estate", "suv", "van"])
def test_cached_and_uncached_results_are_identical(weight, bodywork):
    matcher = _matcher(weight)
    query = VehicleMatchQuery(model="Model", manufacturer="Test", bodywork=bodywork)
    cached = matcher.match(query)
    with patch("ingestion.fuzzy_matching._best_model_label", _best_model_label.__wrapped__):
        assert matcher.match(query) == cached


def test_text_cache_does_not_reuse_technical_evidence():
    _best_model_label.cache_clear()
    matcher = _matcher()
    candidate = VehicleCandidate("same", "Test", "Model", power_kw=100)
    query = VehicleMatchQuery(model="Model", manufacturer="Test", power_kw=100)
    accepted = matcher._score(query, candidate)
    conflict = matcher._score(query, replace(candidate, power_kw=200))
    assert "power_kw" in accepted.matched_fields
    assert "power_kw" in conflict.conflicting_fields
    assert _best_model_label.cache_info().hits == 1
    assert _best_model_label.cache_info().maxsize == 100_000


def test_text_cache_keys_include_catalog_labels_and_policy():
    _best_model_label.cache_clear()
    args = ("GOLF", "Golf Plus", (), 0.7, 0.3)
    low = _best_model_label(*args, 0.5)
    high = _best_model_label(*args, 0.9)
    alias = _best_model_label("GOLF", "Golf Plus", ("Golf",), 0.7, 0.3, 0.9)
    assert high[0] > low[0]
    assert alias == (1.0, "GOLF")
    assert _best_model_label.cache_info().misses == 3


@pytest.mark.parametrize("bodywork", [None, "estate", "suv", "van"])
@pytest.mark.parametrize("power", [None, 100, 200])
def test_skipped_unit_weight_pass_would_produce_identical_evidence(bodywork, power):
    matcher = _matcher()
    candidate = VehicleCandidate(
        "1", "Test", "Model", bodyworks=frozenset({"estate"}), power_kw=100,
    )
    query = VehicleMatchQuery(
        model="Model", manufacturer="Test", bodywork=bodywork, power_kw=power,
    )
    assert matcher._score(query, candidate, bodywork_discriminates=False) == matcher._score(
        query, candidate, bodywork_discriminates=True,
    )
