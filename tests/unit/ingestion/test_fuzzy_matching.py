import pytest

from ingestion.fuzzy_matching import (
    FuzzyMatchConfig,
    FuzzyVehicleMatcher,
    ManufacturerCandidateIndex,
    VehicleCandidate,
    VehicleMatchQuery,
)


def _catalog() -> tuple[VehicleCandidate, ...]:
    return (
        VehicleCandidate(
            candidate_reference="KTYPE-100",
            candidate_type="TecDocKType",
            manufacturer="Volvo",
            manufacturer_aliases=("Volvo Cars",),
            model="XC90",
            model_aliases=("XC 90",),
            year_from=2015,
            year_to=2024,
            fuels=frozenset({"petrol", "electricity"}),
            engine_codes=frozenset({"B4204T"}),
        ),
        VehicleCandidate(
            candidate_reference="KTYPE-101",
            candidate_type="TecDocKType",
            manufacturer="Volvo",
            model="XC60",
            year_from=2017,
            year_to=2025,
            fuels=frozenset({"diesel"}),
            engine_codes=frozenset({"D4204T"}),
        ),
        VehicleCandidate(
            candidate_reference="KTYPE-200",
            candidate_type="TecDocKType",
            manufacturer="Audi",
            model="XC90",
            year_from=2018,
            year_to=2023,
            fuels=frozenset({"petrol"}),
        ),
    )


def _matcher(config: FuzzyMatchConfig | None = None) -> FuzzyVehicleMatcher:
    return FuzzyVehicleMatcher(ManufacturerCandidateIndex(_catalog()), config)


def test_exact_manufacturer_scope_excludes_same_model_from_another_make() -> None:
    result = _matcher().match(VehicleMatchQuery(manufacturer="Volvo", model="XC90"))

    assert result.scope == "exact_manufacturer"
    assert [candidate.candidate_reference for candidate in result.candidates] == ["KTYPE-100"]
    assert result.eligible_for_auto_resolution is True


def test_recognized_manufacturer_alias_uses_exact_scope() -> None:
    result = _matcher().match(VehicleMatchQuery(manufacturer="Volvo Cars", model="XC90"))

    assert result.scope == "exact_manufacturer"
    assert result.candidates[0].candidate_reference == "KTYPE-100"


def test_noisy_manufacturer_is_scoped_but_never_auto_resolved() -> None:
    result = _matcher().match(VehicleMatchQuery(manufacturer="Volov", model="XC90"))

    assert result.scope == "fuzzy_manufacturer"
    assert result.candidates[0].candidate_reference == "KTYPE-100"
    assert result.eligible_for_auto_resolution is False
    assert result.reason == "manufacturer_scope_requires_review"


def test_missing_or_unknown_manufacturer_uses_review_only_global_candidates() -> None:
    missing = _matcher().match(VehicleMatchQuery(model="XC90"))
    unknown = _matcher().match(VehicleMatchQuery(manufacturer="Unknown Motors", model="XC90"))

    assert missing.scope == "global"
    assert unknown.scope == "global"
    assert missing.eligible_for_auto_resolution is False
    assert unknown.eligible_for_auto_resolution is False
    assert [candidate.candidate_reference for candidate in missing.candidates] == [
        "KTYPE-100",
        "KTYPE-200",
    ]


def test_year_fuel_and_engine_context_raise_a_supported_candidate() -> None:
    result = _matcher().match(
        VehicleMatchQuery(
            manufacturer="Volvo",
            model="XC 90",
            year=2022,
            fuels=frozenset({"petrol", "electricity"}),
            engine_code="b4204t",
        )
    )

    candidate = result.candidates[0]
    assert candidate.candidate_reference == "KTYPE-100"
    assert candidate.confidence == 1.0
    assert candidate.matched_fields == ("model", "year", "fuels", "engine_code")
    assert candidate.conflicting_fields == ()


@pytest.mark.parametrize(
    ("query", "conflicting_field"),
    [
        (VehicleMatchQuery(manufacturer="Volvo", model="XC90", year=2010), "year"),
        (
            VehicleMatchQuery(manufacturer="Volvo", model="XC90", fuels=frozenset({"hydrogen"})),
            "fuels",
        ),
        (
            VehicleMatchQuery(manufacturer="Volvo", model="XC90", engine_code="D5244T"),
            "engine_code",
        ),
    ],
)
def test_context_conflicts_prevent_automatic_resolution(
    query: VehicleMatchQuery,
    conflicting_field: str,
) -> None:
    result = _matcher().match(query)

    assert result.eligible_for_auto_resolution is False
    assert result.reason == "context_conflict_requires_review"
    assert conflicting_field in result.candidates[0].conflicting_fields


def test_known_false_model_match_stays_below_candidate_threshold() -> None:
    result = _matcher().match(VehicleMatchQuery(manufacturer="Volvo", model="XC40"))

    assert result.candidates == ()
    assert result.reason == "no_candidate_above_threshold"


def test_candidate_threshold_is_injected_and_configurable() -> None:
    default = _matcher().match(VehicleMatchQuery(manufacturer="Volvo", model="X90"))
    strict = _matcher(FuzzyMatchConfig(candidate_threshold=0.95, automatic_threshold=0.99)).match(
        VehicleMatchQuery(manufacturer="Volvo", model="X90")
    )

    assert default.candidates[0].candidate_reference == "KTYPE-100"
    assert default.eligible_for_auto_resolution is False
    assert strict.candidates == ()


def test_token_similarity_handles_reordered_multi_word_model_text() -> None:
    candidate = VehicleCandidate(
        "KTYPE-300",
        "Volvo",
        "V60 Cross Country",
    )
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((candidate,)))

    result = matcher.match(VehicleMatchQuery(manufacturer="Volvo", model="Cross Country V60"))

    assert result.candidates[0].candidate_reference == "KTYPE-300"
    assert result.candidates[0].text_score >= 0.70
    assert result.eligible_for_auto_resolution is False


def test_equal_scores_use_reference_as_deterministic_tie_breaker() -> None:
    first = VehicleCandidate("KTYPE-B", "Volvo", "V60")
    second = VehicleCandidate("KTYPE-A", "Volvo", "V60")
    query = VehicleMatchQuery(manufacturer="Volvo", model="V60")

    forward = FuzzyVehicleMatcher(ManufacturerCandidateIndex((first, second))).match(query)
    reverse = FuzzyVehicleMatcher(ManufacturerCandidateIndex((second, first))).match(query)

    assert [candidate.candidate_reference for candidate in forward.candidates] == [
        "KTYPE-A",
        "KTYPE-B",
    ]
    assert forward == reverse
    assert forward.eligible_for_auto_resolution is False
    assert forward.reason == "candidate_margin_not_met"


def test_candidate_limit_does_not_hide_an_ambiguous_runner_up() -> None:
    candidates = (
        VehicleCandidate("KTYPE-A", "Volvo", "V60"),
        VehicleCandidate("KTYPE-B", "Volvo", "V60"),
    )
    matcher = FuzzyVehicleMatcher(
        ManufacturerCandidateIndex(candidates),
        FuzzyMatchConfig(max_candidates=1),
    )

    result = matcher.match(VehicleMatchQuery(manufacturer="Volvo", model="V60"))

    assert len(result.candidates) == 1
    assert result.eligible_for_auto_resolution is False
    assert result.reason == "candidate_margin_not_met"


def test_review_payload_matches_review_queue_candidate_contract() -> None:
    result = _matcher().match(VehicleMatchQuery(manufacturer="Volov", model="XC90"))

    payload = result.review_candidates()[0]
    assert payload["candidate_reference"] == "KTYPE-100"
    assert payload["candidate_type"] == "TecDocKType"
    assert 0.0 <= payload["confidence"] <= 1.0
    assert payload["evidence"]["matched_fields"] == ["model"]
    assert payload["evidence"]["phonetic_match"] is False


def test_phonetic_manufacturer_scope_recovers_candidate_for_review_only() -> None:
    candidate = VehicleCandidate(
        "KTYPE-400",
        "Mercedes-Benz",
        "C-Class",
        manufacturer_aliases=("Mercedes",),
    )
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((candidate,)))

    result = matcher.match(VehicleMatchQuery(manufacturer="Mersedez", model="C-Class"))

    assert result.scope == "phonetic_manufacturer"
    assert result.candidates[0].candidate_reference == "KTYPE-400"
    assert result.eligible_for_auto_resolution is False
    assert result.reason == "manufacturer_scope_requires_review"
    assert result.phonetic_version == "northstar-phonetic-v1"
    evidence = result.review_candidates()[0]["evidence"]
    assert evidence["match_scope"] == "phonetic_manufacturer"
    assert evidence["phonetic_version"] == "northstar-phonetic-v1"


def test_phonetic_model_signal_makes_misspelling_reviewable_but_not_automatic() -> None:
    candidate = VehicleCandidate(
        "KTYPE-500",
        "Toyota",
        "Camry",
        year_from=2018,
        year_to=2025,
    )
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((candidate,)))

    result = matcher.match(VehicleMatchQuery(manufacturer="Toyota", model="Kamri", year=2022))

    match = result.candidates[0]
    assert match.phonetic_match is True
    assert "model_phonetic" in match.matched_fields
    assert result.eligible_for_auto_resolution is False
    assert result.reason == "phonetic_candidate_requires_review"
    assert result.phonetic_version == "northstar-phonetic-v1"
    assert result.review_candidates()[0]["evidence"]["phonetic_version"] == (
        "northstar-phonetic-v1"
    )


def test_phonetic_signal_cannot_bypass_hard_year_conflict() -> None:
    candidate = VehicleCandidate(
        "KTYPE-500",
        "Toyota",
        "Camry",
        year_from=2018,
        year_to=2025,
    )
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((candidate,)))

    result = matcher.match(VehicleMatchQuery(manufacturer="Toyota", model="Camri", year=2010))

    assert result.eligible_for_auto_resolution is False
    assert result.reason == "context_conflict_requires_review"
    assert "year" in result.candidates[0].conflicting_fields


def test_duplicate_references_and_invalid_config_are_rejected() -> None:
    duplicate = VehicleCandidate("KTYPE-100", "Volvo", "V60")
    with pytest.raises(ValueError, match="duplicate candidate_reference"):
        ManufacturerCandidateIndex((duplicate, duplicate))
    with pytest.raises(ValueError, match="sum to 1.0"):
        FuzzyMatchConfig(edit_weight=0.8, token_weight=0.3)
    with pytest.raises(ValueError, match="between 0.0 and 1.0"):
        FuzzyMatchConfig(engine_conflict_penalty=-0.1)
