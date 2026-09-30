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
    # B20DTH is a real catalog engine (another KType carries it), so a car
    # claiming it contradicts this KType rather than being unverified.
    other = VehicleCandidate("other", "Opel", "Astra", engine_codes=frozenset({"B20DTH"}))
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((candidate, other)))

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


def test_engine_code_forms_and_family() -> None:
    from ingestion.fuzzy_matching import engine_code_family, engine_code_forms

    assert engine_code_forms("BHZ (DV6FC)") == {"BHZDV6FC", "BHZ", "DV6FC"}
    assert engine_code_forms("D 4204 T14") == {"D4204T14"}
    assert engine_code_family("K9K 276") == "K9K"
    assert engine_code_family("D4FB-H") == "D4FB"
    assert engine_code_family("BHZ (DV6FC)") is None
    assert engine_code_family("D4204T14") is None
    # One token: a single revision letter after a digit is dropped.
    assert engine_code_family("FB20C") == "FB20"
    assert engine_code_family("B47D20A") == "B47D20"
    # Ending in a digit, in several letters, or letters only: no family.
    assert engine_code_family("B5254T6") is None
    assert engine_code_family("Z16XER") is None
    assert engine_code_family("CFGB") is None
    # A head too short to name an engine alone is no family.
    assert engine_code_family("M 177.980") is None
    assert engine_code_family("OM 651.913") is None
    assert engine_code_family("20E") is None
    # Without revisions only the first-token family remains.
    assert engine_code_family("FB20C", revision=False) is None
    assert engine_code_family("K9K 276", revision=False) == "K9K"


def _engine_score(car_engine: str, *catalog_engines: str, others: tuple[str, ...] = ()):  # type: ignore[no-untyped-def]
    candidate = VehicleCandidate("k", "Renault", "Clio", engine_codes=frozenset(catalog_engines))
    other = VehicleCandidate("o", "Renault", "Megane", engine_codes=frozenset(others))
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((candidate, other)))
    return matcher._score(
        VehicleMatchQuery("Clio", manufacturer="Renault", engine_code=car_engine), candidate
    )


@pytest.mark.parametrize(
    ("car", "catalog"),
    [("BHZ", "BHZ (DV6FC)"), ("DV6FC", "BHZ (DV6FC)"), ("D4204T14", "D 4204 T14")],
)
def test_bracket_and_spacing_forms_are_the_same_engine(car: str, catalog: str) -> None:
    score = _engine_score(car, catalog)
    assert "engine_code" in score.matched_fields
    assert not score.conflicting_fields


@pytest.mark.parametrize(
    ("car", "catalog"),
    [("K9K", "K9K 276"), ("D4FB-H", "D4FB"), ("FB20", "FB20C"), ("FB20C", "FB20")],
)
def test_same_engine_family_is_compatible_with_a_smaller_bonus(car: str, catalog: str) -> None:
    family = _engine_score(car, catalog)
    exact = _engine_score(catalog, catalog)

    assert "engine_code_family" in family.matched_fields
    assert not family.conflicting_fields
    assert family.separation_score < exact.separation_score


def test_an_engine_code_no_ktype_carries_is_unverified_not_a_conflict() -> None:
    unknown = _engine_score("3DU", "K9K 276")
    known_other = _engine_score("F4R", "K9K 276", others=("F4R 870",))
    different_volvo = _engine_score("B5254T", "B 5254 T6", others=("B5254T",))

    assert "engine_code_unverified" in unknown.missing_fields
    assert not unknown.conflicting_fields
    assert "engine_code" in known_other.conflicting_fields
    # Two variants of one family are different engines (Renault D4F-742 / D4F 740).
    variants = _engine_score("D4F-742", "D4F 740", others=("D4F-742",))
    assert "engine_code" in variants.conflicting_fields
    # B5254T6 ends in a digit, so it names no family: the two stay different engines.
    assert "engine_code" in different_volvo.conflicting_fields


@pytest.mark.parametrize(
    ("car", "catalog"),
    [
        ("FB20C", "FB20D"), ("FB20D", "FB20C"),
        ("Z16XE", "Z16XER"), ("Z16XER", "Z16XE"),
        ("B5254T", "B5254T6"), ("B5254T6", "B5254T"),
    ],
)
def test_two_revisions_of_one_engine_still_conflict(car: str, catalog: str) -> None:
    score = _engine_score(car, catalog, others=(car,))

    assert "engine_code" in score.conflicting_fields
    assert "engine_code_family" not in score.matched_fields


@pytest.mark.parametrize(
    ("car", "catalog", "others"),
    [
        ("FB20X", "FB20C", ()),
        # V70 I: TS B5252S, TecDoc B 5252 FS; another KType carries the bare B 5252.
        ("B5252S", "B 5252 FS", ("B 5252",)),
    ],
)
def test_a_revision_no_ktype_carries_stays_unverified(
    car: str, catalog: str, others: tuple[str, ...]
) -> None:
    score = _engine_score(car, catalog, others=others)

    assert "engine_code_unverified" in score.missing_fields
    assert "engine_code" not in score.conflicting_fields


def test_an_exact_engine_code_clears_the_margin_over_a_family_sibling() -> None:
    exact = VehicleCandidate("k-fb25", "Subaru", "Legacy", engine_codes=frozenset({"FB25"}))
    sibling = VehicleCandidate("k-fb25b", "Subaru", "Legacy", engine_codes=frozenset({"FB25B"}))
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((exact, sibling)))

    result = matcher.match(VehicleMatchQuery("Legacy", manufacturer="Subaru", engine_code="FB25"))

    assert [c.candidate_reference for c in result.candidates] == ["k-fb25", "k-fb25b"]
    assert result.reason != "candidate_margin_not_met"
    assert result.eligible_for_auto_resolution is True


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
            # D4204T is the XC60's engine in this catalog: a known, different engine.
            VehicleMatchQuery(manufacturer="Volvo", model="XC90", engine_code="D4204T"),
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


def test_drive_compatible_pair_is_neutral_not_a_conflict() -> None:
    """Regression test: matching a query against a candidate that falls into
    `drive_compatible_pairs` used to raise `UnboundLocalError` on
    `drive_comparison` -- that branch never set it, but the shared return
    statement at the end of `_score` reads it unconditionally alongside
    `body_comparison`. Only surfaced once TS's undifferentiated `2wd` was
    actually compared against a real TecDoc `fwd`/`rwd` candidate."""

    fwd = VehicleCandidate("KTYPE-FWD", "Volvo", "XC90", drive_type="fwd")
    matcher = FuzzyVehicleMatcher(
        ManufacturerCandidateIndex((fwd,)),
        drive_compatible_pairs=frozenset({("2wd", "fwd")}),
    )

    result = matcher.match(
        VehicleMatchQuery(manufacturer="Volvo", model="XC90", drive_type="2wd")
    )

    candidate = result.candidates[0]
    assert "drive_type_compatible_not_confirmed" in candidate.missing_fields
    assert "drive_type" not in candidate.conflicting_fields
    assert "drive_type" not in candidate.matched_fields


def test_fuel_compatible_pair_is_neutral_not_a_conflict() -> None:
    petrol_only = VehicleCandidate(
        "KTYPE-PETROL", "Saab", "9-3", fuels=frozenset({"petrol"})
    )
    matcher = FuzzyVehicleMatcher(
        ManufacturerCandidateIndex((petrol_only,)),
        fuel_compatible_pairs=frozenset({("ethanol", "petrol")}),
    )

    result = matcher.match(
        VehicleMatchQuery("9-3", manufacturer="Saab", fuels=frozenset({"ethanol"}))
    )

    candidate = result.candidates[0]
    assert "fuels_compatible_not_confirmed" in candidate.missing_fields
    assert "fuels" not in candidate.conflicting_fields
    assert "fuels" not in candidate.matched_fields


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


def _year_score(car_year: int, year_from: int | None, year_to: int | None, **config: int):  # type: ignore[no-untyped-def]
    candidate = VehicleCandidate("k", "Opel", "Rekord", year_from=year_from, year_to=year_to)
    matcher = FuzzyVehicleMatcher(
        ManufacturerCandidateIndex((candidate,)), config=FuzzyMatchConfig(**config)
    )
    return matcher._score(VehicleMatchQuery("Rekord", manufacturer="Opel", year=car_year), candidate)


@pytest.mark.parametrize(
    ("car_year", "year_from", "year_to"),
    [
        # Registered the year after the run ended (Rekord E 1977-1984, car 1985).
        (1985, 1977, 1984),
        # A year before the run starts: a model year counted ahead of the build.
        (2012, 2013, 2020),
        (2012, 2013, None),
        (1985, None, 1984),
    ],
)
def test_a_year_just_outside_the_production_run_is_unverified(
    car_year: int, year_from: int | None, year_to: int | None
) -> None:
    score = _year_score(car_year, year_from, year_to)

    assert "year_adjacent_unverified" in score.missing_fields
    assert "year" not in score.conflicting_fields
    assert "year" not in score.matched_fields


@pytest.mark.parametrize(("car_year", "year_from", "year_to"), [(1986, 1977, 1984), (2011, 2013, 2020)])
def test_a_year_beyond_the_tolerance_still_conflicts(
    car_year: int, year_from: int, year_to: int
) -> None:
    assert "year" in _year_score(car_year, year_from, year_to).conflicting_fields


def test_a_zero_year_tolerance_keeps_the_strict_range() -> None:
    assert "year" in _year_score(1985, 1977, 1984, year_tolerance=0).conflicting_fields
    with pytest.raises(ValueError, match="year_tolerance"):
        FuzzyMatchConfig(year_tolerance=-1)


def test_a_ktype_whose_run_covers_the_year_outranks_an_adjacent_sibling() -> None:
    shared = {"manufacturer": "VW", "model": "Golf", "power_kw": 77}
    ending = VehicleCandidate("k-golf-vi", year_from=2008, year_to=2012, **shared)
    covering = VehicleCandidate("k-golf-vii", year_from=2012, year_to=2020, **shared)
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((ending, covering)))

    result = matcher.match(
        VehicleMatchQuery("Golf", manufacturer="VW", year=2013, power_kw=77)
    )

    assert [c.candidate_reference for c in result.candidates] == ["k-golf-vii", "k-golf-vi"]
    assert result.reason != "candidate_margin_not_met"
    assert result.eligible_for_auto_resolution is True


def test_reviewed_fuel_vocabulary_equivalents_match_once_pre_aligned() -> None:
    # Reconciling TS's "electricity" with TecDoc's "electric" is now the live
    # `tecdoc_resolution_rules` vocabulary rules' job (see
    # `ingestion/vocabulary_alignment.py`, exercised by
    # `tests/unit/ingestion/test_vocabulary_alignment.py`), applied to the
    # query and candidate catalog *before* either reaches the matcher -- the
    # matcher itself only ever compares already-canonicalized text.
    for shared_term in ("electric", "cng"):
        candidate = VehicleCandidate(
            "KTYPE-1", "Volvo", "XC40", fuels=frozenset({shared_term})
        )
        result = FuzzyVehicleMatcher(ManufacturerCandidateIndex((candidate,))).match(
            VehicleMatchQuery(
                manufacturer="Volvo", model="XC40", fuels=frozenset({shared_term})
            )
        )

        assert "fuels" in result.candidates[0].matched_fields
        assert "fuels" not in result.candidates[0].conflicting_fields


def test_hybrid_category_requires_both_underlying_ts_carriers() -> None:
    candidate = VehicleCandidate(
        "KTYPE-1", "Volvo", "XC60", fuels=frozenset({"hybrid_petrol"})
    )
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((candidate,)))

    # "electric" here is the canonical term the live vocabulary rules
    # normalize TS's "electricity" into -- this test exercises the matcher's
    # own hybrid-detection logic on already-canonicalized input, not the
    # alignment step itself.
    compatible = matcher.match(
        VehicleMatchQuery(
            manufacturer="Volvo",
            model="XC60",
            fuels=frozenset({"petrol", "electric"}),
        )
    ).candidates[0]
    incomplete = matcher.match(
        VehicleMatchQuery(
            manufacturer="Volvo", model="XC60", fuels=frozenset({"electric"})
        )
    ).candidates[0]

    assert "fuels" in compatible.matched_fields
    assert "fuels" in incomplete.conflicting_fields


def _fuel_siblings(car_fuels: set[str], variant: set[str]):  # type: ignore[no-untyped-def]
    """Two KTypes identical but for fuel: the one carrying the car's exact fuel must clear the margin."""

    def ktype(reference: str, fuels: set[str]) -> VehicleCandidate:
        return VehicleCandidate(
            reference, "Saab", "9-5", year_from=2006, year_to=2009, fuels=frozenset(fuels), power_kw=110,
        )

    matcher = FuzzyVehicleMatcher(
        ManufacturerCandidateIndex((ktype("PETROL", {"petrol"}), ktype("VARIANT", variant))),
        fuel_compatible_pairs=frozenset({("petrol", "lpg")}),
    )
    return matcher.match(
        VehicleMatchQuery("9-5", manufacturer="Saab", year=2007, fuels=frozenset(car_fuels), power_kw=110)
    )


@pytest.mark.parametrize(
    ("car_fuels", "variant", "winner"),
    [
        # Flex-fuel registration (petrol + ethanol): the BioPower KType, not the petrol one.
        ({"petrol", "ethanol"}, {"ethanol"}, "VARIANT"),
        # Registered hybrid: the hybrid KType, not its petrol sibling.
        ({"petrol", "electric", "hybrid_petrol"}, {"hybrid_petrol"}, "VARIANT"),
        # Registered petrol only: the petrol KType, not the factory LPG one.
        ({"petrol"}, {"lpg"}, "PETROL"),
    ],
)
def test_the_ktype_with_the_cars_exact_fuel_clears_the_margin_over_its_fuel_sibling(
    car_fuels: set[str], variant: set[str], winner: str
) -> None:
    result = _fuel_siblings(car_fuels, variant)

    assert result.candidates[0].candidate_reference == winner
    assert result.reason != "candidate_margin_not_met"
    loser = result.candidates[1]
    assert "fuels" not in loser.matched_fields
    assert "fuels" not in loser.conflicting_fields
    assert {"fuels_variant_not_confirmed", "fuels_compatible_not_confirmed"} & set(loser.missing_fields)


def test_a_base_fuel_ktype_is_unverified_not_rejected_when_it_is_the_only_one() -> None:
    petrol = VehicleCandidate("PETROL", "BMW", "X5", fuels=frozenset({"petrol"}))
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((petrol,)))

    # Registered as a hybrid, catalogued by its combustion fuel (BMW X5 45e).
    score = matcher._score(
        VehicleMatchQuery("X5", manufacturer="BMW", fuels=frozenset({"petrol", "electric", "hybrid_petrol"})),
        petrol,
    )

    assert "fuels_variant_not_confirmed" in score.missing_fields
    assert "fuels" not in score.conflicting_fields


def _power_score(car_kw: int, car_fuels: set[str], ktype_kw: int, ktype_fuels: set[str]):  # type: ignore[no-untyped-def]
    candidate = VehicleCandidate(
        "k", "Kia", "Ceed", fuels=frozenset(ktype_fuels), power_kw=ktype_kw,
    )
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((candidate,)))
    return matcher._score(
        VehicleMatchQuery("Ceed", manufacturer="Kia", fuels=frozenset(car_fuels), power_kw=car_kw),
        candidate,
    )


@pytest.mark.parametrize(
    ("car_fuels", "ktype_fuels"),
    [
        ({"petrol", "electricity", "hybrid_petrol"}, {"hybrid_petrol"}),
        # Registered as petrol only, catalogued as a hybrid (Toyota).
        ({"petrol"}, {"hybrid_petrol"}),
        # Registered as a hybrid, catalogued by its combustion fuel (BMW X5 45e).
        ({"petrol", "electricity", "hybrid_petrol"}, {"petrol"}),
    ],
)
def test_a_hybrids_combustion_power_below_system_power_is_unverified(
    car_fuels: set[str], ktype_fuels: set[str]
) -> None:
    score = _power_score(77, car_fuels, 104, ktype_fuels)

    assert "power_kw_hybrid_unverified" in score.missing_fields
    assert "power_kw" not in score.conflicting_fields


def test_hybrid_power_above_system_power_or_a_non_hybrid_gap_still_conflicts() -> None:
    above = _power_score(130, {"hybrid_petrol"}, 104, {"hybrid_petrol"})
    petrol = _power_score(77, {"petrol"}, 104, {"petrol"})

    assert "power_kw" in above.conflicting_fields
    assert "power_kw" in petrol.conflicting_fields


def test_an_exact_hybrid_power_match_still_separates_from_a_higher_sibling() -> None:
    exact = VehicleCandidate("k72", "Toyota", "Corolla", fuels=frozenset({"hybrid_petrol"}), power_kw=72)
    higher = VehicleCandidate("k103", "Toyota", "Corolla", fuels=frozenset({"hybrid_petrol"}), power_kw=103)
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((exact, higher)))

    result = matcher.match(
        VehicleMatchQuery("Corolla", manufacturer="Toyota",
                          fuels=frozenset({"hybrid_petrol"}), power_kw=72)
    )

    assert [c.candidate_reference for c in result.candidates] == ["k72", "k103"]
    assert result.reason != "candidate_margin_not_met"


def _body_score(car_body: str, *ktype_bodies: str, pairs: frozenset[tuple[str, str]]):  # type: ignore[no-untyped-def]
    candidate = VehicleCandidate("k", "Volvo", "S60", bodyworks=frozenset(ktype_bodies))
    matcher = FuzzyVehicleMatcher(
        ManufacturerCandidateIndex((candidate,)), bodywork_compatible_pairs=pairs
    )
    return matcher._score(VehicleMatchQuery("S60", manufacturer="Volvo", bodywork=car_body), candidate)


COVERED = frozenset({("covered_body", "sedan"), ("multi_purpose_vehicle", "bus")})


@pytest.mark.parametrize(("car", "ktype"), [("covered_body", "sedan"), ("multi_purpose_vehicle", "bus")])
def test_a_reviewed_broader_body_is_compatible_not_a_conflict(car: str, ktype: str) -> None:
    score = _body_score(car, ktype, pairs=COVERED)

    assert "bodywork_compatible_not_confirmed" in score.missing_fields
    assert "bodywork" not in score.conflicting_fields


def test_body_pairs_are_directional_and_never_beat_an_exact_body() -> None:
    reverse = _body_score("sedan", "covered_body", pairs=COVERED)
    unpaired = _body_score("covered_body", "convertible", pairs=COVERED)
    exact = _body_score("multi_purpose_vehicle", "multi_purpose_vehicle", "bus", pairs=COVERED)
    without_rulings = _body_score("covered_body", "sedan", pairs=frozenset())

    assert "bodywork" in reverse.conflicting_fields
    assert "bodywork" in unpaired.conflicting_fields
    assert "bodywork" in exact.matched_fields
    assert "bodywork" in without_rulings.conflicting_fields


def test_an_exact_body_still_separates_from_a_compatible_sibling() -> None:
    mpv = VehicleCandidate("mpv", "Citroen", "Berlingo", bodyworks=frozenset({"multi_purpose_vehicle"}))
    van = VehicleCandidate("van", "Citroen", "Berlingo", bodyworks=frozenset({"van"}))
    matcher = FuzzyVehicleMatcher(
        ManufacturerCandidateIndex((mpv, van)), bodywork_compatible_pairs=COVERED | {("multi_purpose_vehicle", "van")}
    )

    result = matcher.match(
        VehicleMatchQuery("Berlingo", manufacturer="Citroen", bodywork="multi_purpose_vehicle")
    )

    assert [c.candidate_reference for c in result.candidates] == ["mpv", "van"]
    assert result.reason != "candidate_margin_not_met"
