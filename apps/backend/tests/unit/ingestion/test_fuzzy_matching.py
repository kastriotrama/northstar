import pytest

from ingestion.fuzzy_matching import (
    FuzzyMatchConfig,
    FuzzyVehicleMatcher,
    ManufacturerCandidateIndex,
    VehicleCandidate,
    VehicleMatchQuery,
)


def test_similarity_functions_use_bounded_process_cache() -> None:
    from ingestion.fuzzy_matching import (
        _edit_similarity,
        _normalized_code,
        _normalized_text,
        _token_similarity,
    )

    _edit_similarity.cache_clear()
    _normalized_code.cache_clear()
    _normalized_text.cache_clear()
    _token_similarity.cache_clear()
    assert _normalized_text("V60 Cross Country") == "V60 CROSS COUNTRY"
    assert _normalized_text("V60 Cross Country") == "V60 CROSS COUNTRY"
    assert _normalized_code("B 4204-T") == "B4204T"
    assert _normalized_code("B 4204-T") == "B4204T"
    assert _edit_similarity("VOLVO", "VOLVO") == 1.0
    assert _edit_similarity("VOLVO", "VOLVO") == 1.0
    assert _token_similarity("V60 CROSS COUNTRY", "V60 CROSS COUNTRY") == 1.0
    assert _token_similarity("V60 CROSS COUNTRY", "V60 CROSS COUNTRY") == 1.0
    assert _edit_similarity.cache_info().hits == 1
    assert _normalized_code.cache_info().hits == 1
    assert _normalized_text.cache_info().hits == 1
    assert _token_similarity.cache_info().hits == 1


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
            displacement_cc=1969,
            power_kw=140,
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


def test_mixed_fuel_components_are_compatible_without_confirming_a_match() -> None:
    candidate = VehicleCandidate(
        "mixed", "Saab", "9-3", fuel_components=frozenset({"petrol", "alcohol_unspecified"}),
    )
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((candidate,)))

    score = matcher._score(
        VehicleMatchQuery("9-3", manufacturer="Saab", fuels=frozenset({"petrol"})),
        candidate,
    )

    assert "fuels_compatible_not_confirmed" in score.missing_fields
    assert "fuels" not in score.matched_fields
    assert "fuels" not in score.conflicting_fields


def test_mixed_fuel_components_reject_a_disjoint_observed_fuel() -> None:
    candidate = VehicleCandidate(
        "mixed", "Saab", "9-3", fuel_components=frozenset({"petrol", "alcohol_unspecified"}),
    )
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((candidate,)))

    score = matcher._score(
        VehicleMatchQuery("9-3", manufacturer="Saab", fuels=frozenset({"diesel"})),
        candidate,
    )

    assert "fuels" in score.conflicting_fields


def test_engine_code_is_compared_to_the_full_candidate_engine_set() -> None:
    candidate = VehicleCandidate(
        "multi-engine", "Opel", "Insignia", engine_codes=frozenset({"A19DTR", "Z19DTR"}),
    )
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((candidate,)))

    matched = matcher._score(
        VehicleMatchQuery("Insignia", manufacturer="Opel", engine_code="Z 19 DTR"),
        candidate,
    )
    conflict = matcher._score(
        VehicleMatchQuery("Insignia", manufacturer="Opel", engine_code="B20DTH"),
        candidate,
    )

    assert "engine_code" in matched.matched_fields
    assert "engine_code" not in matched.conflicting_fields
    assert "engine_code" in conflict.conflicting_fields


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


def test_index_recovers_unique_token_bounded_model_from_brand() -> None:
    index = ManufacturerCandidateIndex(_catalog())

    assert index.recover_model_from_brand("Volvo", "VOLVO S + XC90") == "XC90"
    assert index.recover_model_from_brand("Volvo", "VOLVO XC900") is None
    assert index.recover_model_from_brand("Unknown", "VOLVO XC90") is None


def test_index_recovers_model_from_alternative_scoped_evidence() -> None:
    index = ManufacturerCandidateIndex(_catalog())

    assert index.recover_model_from_evidence(
        "Volvo",
        {"variant": "XC90 T8", "version": "UNKNOWN"},
    ) == ("XC90", "variant")


def test_recovery_attribution_prefers_specific_evidence_over_brand_on_tie() -> None:
    index = ManufacturerCandidateIndex(_catalog())

    assert index.recover_model_from_evidence(
        "Volvo",
        {"brand": "VOLVO XC90", "variant": "XC90 T8"},
    ) == ("XC90", "variant")
    assert index.recover_model_from_evidence(
        "Volvo",
        {"brand": "VOLVO XC90", "eeg_type_approval": "XC90"},
    ) == ("XC90", "eeg_type_approval")
    assert index.recover_model_from_evidence(
        "Volvo",
        {"brand": "VOLVO XC90"},
    ) == ("XC90", "brand")


def test_recovery_tolerates_an_evidence_field_outside_the_known_order() -> None:
    index = ManufacturerCandidateIndex(_catalog())

    # An unlisted field must still recover rather than raising.
    assert index.recover_model_from_evidence(
        "Volvo",
        {"trade_name": "VOLVO XC90"},
    ) == ("XC90", "trade_name")
    # Known fields still outrank unlisted ones on a tie.
    assert index.recover_model_from_evidence(
        "Volvo",
        {"trade_name": "XC90", "variant": "XC90"},
    ) == ("XC90", "variant")


def test_registry_model_text_outranks_incidental_evidence_fields() -> None:
    index = ManufacturerCandidateIndex(_catalog())

    # The model column states the model; brand merely happens to contain it.
    assert index.recover_model_from_evidence(
        "Volvo",
        {"model": "XC90", "brand": "VOLVO XC90"},
    ) == ("XC90", "model")


def test_year_fuel_and_engine_context_raise_a_supported_candidate() -> None:
    result = _matcher().match(
        VehicleMatchQuery(
            manufacturer="Volvo",
            model="XC 90",
            year=2022,
            fuels=frozenset({"petrol", "electricity"}),
            engine_code="b4204t",
            displacement_cc=1969,
            power_kw=140,
        )
    )

    candidate = result.candidates[0]
    assert candidate.candidate_reference == "KTYPE-100"
    assert candidate.confidence == 1.0
    assert candidate.matched_fields == (
        "model",
        "year",
        "fuels",
        "engine_code",
        "displacement_cc",
        "power_kw",
    )
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


def test_drive_and_bodywork_context_separate_candidates() -> None:
    awd_suv = VehicleCandidate(
        "KTYPE-AWD",
        "Volvo",
        "XC90",
        drive_type="awd",
        bodyworks=frozenset({"suv"}),
    )
    fwd_estate = VehicleCandidate(
        "KTYPE-FWD",
        "Volvo",
        "XC90",
        drive_type="fwd",
        bodyworks=frozenset({"estate"}),
    )
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((awd_suv, fwd_estate)))

    result = matcher.match(
        VehicleMatchQuery(
            manufacturer="Volvo",
            model="XC90",
            drive_type="awd",
            bodywork="suv",
        )
    )

    assert result.candidates[0].candidate_reference == "KTYPE-AWD"
    assert "drive_type" in result.candidates[0].matched_fields
    assert "bodywork" in result.candidates[0].matched_fields
    assert result.candidates[0].conflicting_fields == ()


@pytest.mark.parametrize(
    ("query", "conflicting_field"),
    [
        (
            VehicleMatchQuery(manufacturer="Volvo", model="XC90", displacement_cc=2000),
            "displacement_cc",
        ),
        (
            # Beyond the PS/kW rounding tolerance, so a genuine contradiction
            # rather than measurement noise (the catalog entry is 140 kW).
            VehicleMatchQuery(manufacturer="Volvo", model="XC90", power_kw=160),
            "power_kw",
        ),
    ],
)
def test_numeric_technical_conflicts_prevent_automatic_resolution(
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


def _sibling_ktypes(
    *,
    rival_power_kw: int,
    rival_bodyworks: frozenset[str] = frozenset({"estate"}),
) -> tuple[VehicleCandidate, VehicleCandidate]:
    """Two k-types of one model family, separable only by technical evidence."""

    shared = {
        "manufacturer": "Volvo",
        "model": "V70",
        "year_from": 2008,
        "year_to": 2016,
        "fuels": frozenset({"diesel"}),
        "displacement_cc": 1984,
        "drive_type": "fwd",
    }
    return (
        VehicleCandidate(
            candidate_reference="KTYPE-EXACT",
            power_kw=120,
            bodyworks=frozenset({"estate"}),
            **shared,
        ),
        VehicleCandidate(
            candidate_reference="KTYPE-RIVAL",
            power_kw=rival_power_kw,
            bodyworks=rival_bodyworks,
            **shared,
        ),
    )


_FULL_EVIDENCE_QUERY = VehicleMatchQuery(
    manufacturer="Volvo",
    model="V70",
    year=2012,
    fuels=frozenset({"diesel"}),
    displacement_cc=1984,
    power_kw=120,
    drive_type="fwd",
    bodywork="estate",
)


def test_separation_score_is_unclamped_so_saturated_candidates_stay_comparable() -> None:
    exact, rival = _sibling_ktypes(rival_power_kw=136)
    result = FuzzyVehicleMatcher(ManufacturerCandidateIndex((exact, rival))).match(
        _FULL_EVIDENCE_QUERY
    )

    top, second = result.candidates[0], result.candidates[1]
    # Both saturate at the reported confidence ceiling...
    assert top.confidence == second.confidence == 1.0
    # ...but the separation score still reflects the real evidence gap.
    assert top.separation_score > second.separation_score
    assert top.separation_score - second.separation_score >= 0.08


def test_fully_matched_candidate_outranks_a_conflicting_sibling_ktype() -> None:
    exact, rival = _sibling_ktypes(rival_power_kw=136)
    result = FuzzyVehicleMatcher(ManufacturerCandidateIndex((exact, rival))).match(
        _FULL_EVIDENCE_QUERY
    )

    assert result.candidates[0].candidate_reference == "KTYPE-EXACT"
    assert result.eligible_for_auto_resolution is True
    assert result.reason == "automatic_candidate_threshold_met"


def test_identical_sibling_ktypes_remain_ambiguous() -> None:
    exact, rival = _sibling_ktypes(rival_power_kw=120)
    result = FuzzyVehicleMatcher(ManufacturerCandidateIndex((exact, rival))).match(
        _FULL_EVIDENCE_QUERY
    )

    assert result.eligible_for_auto_resolution is False
    assert result.reason == "candidate_margin_not_met"


def test_sibling_separated_only_by_missing_evidence_remains_ambiguous() -> None:
    exact, rival = _sibling_ktypes(rival_power_kw=120, rival_bodyworks=frozenset())
    result = FuzzyVehicleMatcher(ManufacturerCandidateIndex((exact, rival))).match(
        _FULL_EVIDENCE_QUERY
    )

    # A missing field is weaker evidence than a matched one, but the gap is
    # below the automatic margin, so the pair stays ambiguous.
    assert result.eligible_for_auto_resolution is False
    assert result.reason == "candidate_margin_not_met"


def _power_candidates() -> tuple[VehicleCandidate, VehicleCandidate]:
    shared = {"manufacturer": "Volvo", "model": "V60", "year_from": 2010, "year_to": 2018}
    return (
        VehicleCandidate(candidate_reference="KTYPE-EXACT", power_kw=110, **shared),
        VehicleCandidate(candidate_reference="KTYPE-NEAR", power_kw=112, **shared),
    )


def test_power_within_rounding_tolerance_is_not_a_conflict() -> None:
    _, near = _power_candidates()
    result = FuzzyVehicleMatcher(ManufacturerCandidateIndex((near,))).match(
        VehicleMatchQuery(manufacturer="Volvo", model="V60", power_kw=110)
    )

    candidate = result.candidates[0]
    # 2 kW apart is PS/kW rounding, so it must not read as a contradiction...
    assert "power_kw" not in candidate.conflicting_fields
    # ...but it is not proof of a match either.
    assert "power_kw" not in candidate.matched_fields
    assert "power_kw" in candidate.missing_fields


def test_power_beyond_tolerance_remains_a_conflict() -> None:
    far = VehicleCandidate(
        candidate_reference="KTYPE-FAR", manufacturer="Volvo", model="V60", power_kw=140
    )
    result = FuzzyVehicleMatcher(ManufacturerCandidateIndex((far,))).match(
        VehicleMatchQuery(manufacturer="Volvo", model="V60", power_kw=110)
    )

    assert "power_kw" in result.candidates[0].conflicting_fields


def test_exact_power_still_outranks_a_within_tolerance_sibling() -> None:
    exact, near = _power_candidates()
    result = FuzzyVehicleMatcher(ManufacturerCandidateIndex((exact, near))).match(
        VehicleMatchQuery(manufacturer="Volvo", model="V60", power_kw=110)
    )

    assert result.candidates[0].candidate_reference == "KTYPE-EXACT"


def test_reviewed_fuel_vocabulary_equivalents_match_exactly() -> None:
    for ts_fuel, tecdoc_fuel in (("electricity", "electric"), ("methane", "cng")):
        candidate = VehicleCandidate(
            "KTYPE-1", "Volvo", "XC40", fuels=frozenset({tecdoc_fuel})
        )
        result = FuzzyVehicleMatcher(ManufacturerCandidateIndex((candidate,))).match(
            VehicleMatchQuery(
                manufacturer="Volvo", model="XC40", fuels=frozenset({ts_fuel})
            )
        )

        assert "fuels" in result.candidates[0].matched_fields
        assert "fuels" not in result.candidates[0].conflicting_fields


def test_hybrid_category_requires_both_underlying_ts_carriers() -> None:
    candidate = VehicleCandidate(
        "KTYPE-1", "Volvo", "XC60", fuels=frozenset({"hybrid_petrol"})
    )
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((candidate,)))

    compatible = matcher.match(
        VehicleMatchQuery(
            manufacturer="Volvo",
            model="XC60",
            fuels=frozenset({"petrol", "electricity"}),
        )
    ).candidates[0]
    incomplete = matcher.match(
        VehicleMatchQuery(
            manufacturer="Volvo", model="XC60", fuels=frozenset({"electricity"})
        )
    ).candidates[0]

    assert "fuels" in compatible.matched_fields
    assert "fuels" in incomplete.conflicting_fields
