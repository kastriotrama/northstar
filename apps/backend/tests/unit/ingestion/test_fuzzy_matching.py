import pytest

from ingestion.fuzzy_matching import (
    FuzzyCandidateMatch,
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


def _combi_and_hatchback() -> tuple[VehicleCandidate, VehicleCandidate]:
    shared = {
        "manufacturer": "SKODA",
        "candidate_type": "TecDocKType",
        "year_from": 2014,
        "year_to": 2020,
        "fuels": frozenset({"petrol"}),
        "displacement_cc": 1395,
        "power_kw": 110,
        "drive_type": "fwd",
    }
    return (
        VehicleCandidate(
            candidate_reference="KTYPE-HATCH",
            model="OCTAVIA III (5E3, NL3, NR3)",
            model_aliases=("OCTAVIA", "OCTAVIA III"),
            bodyworks=frozenset({"hatchback"}),
            **shared,
        ),
        VehicleCandidate(
            candidate_reference="KTYPE-COMBI",
            model="OCTAVIA III Combi (5E5, 5E6)",
            model_aliases=("OCTAVIA COMBI", "OCTAVIA III Combi"),
            bodyworks=frozenset({"estate"}),
            **shared,
        ),
    )


def _octavia_query(bodywork: str | None) -> VehicleMatchQuery:
    return VehicleMatchQuery(
        manufacturer="Skoda",
        model="Octavia",
        year=2017,
        fuels=frozenset({"petrol"}),
        displacement_cc=1395,
        power_kw=110,
        drive_type="fwd",
        bodywork=bodywork,
    )


def test_registered_estate_resolves_to_the_combi_ktype_despite_its_body_word() -> None:
    result = FuzzyVehicleMatcher(ManufacturerCandidateIndex(_combi_and_hatchback())).match(
        _octavia_query("estate")
    )

    top = result.candidates[0]
    assert top.candidate_reference == "KTYPE-COMBI"
    assert top.text_score == 1.0
    assert "model_partial" not in top.matched_fields
    assert result.eligible_for_auto_resolution is True


def test_body_word_is_kept_when_the_car_body_is_not_the_ktype_body() -> None:
    hatch_result = FuzzyVehicleMatcher(ManufacturerCandidateIndex(_combi_and_hatchback())).match(
        _octavia_query("hatchback")
    )
    no_body_result = FuzzyVehicleMatcher(
        ManufacturerCandidateIndex(_combi_and_hatchback())
    ).match(_octavia_query(None))

    assert hatch_result.candidates[0].candidate_reference == "KTYPE-HATCH"
    combi = {c.candidate_reference: c for c in no_body_result.candidates}.get("KTYPE-COMBI")
    assert combi is None or combi.text_score < 1.0


def test_a_body_word_that_is_not_the_car_body_still_tells_models_apart() -> None:
    # TecDoc files the GLC Coupe as an SUV: "Coupe" names the model, not the body.
    shared = {
        "manufacturer": "MERCEDES-BENZ", "candidate_type": "TecDocKType",
        "year_from": 2016, "year_to": 2022, "fuels": frozenset({"diesel"}),
        "displacement_cc": 2143, "power_kw": 125, "bodyworks": frozenset({"suv"}),
    }
    catalog = (
        VehicleCandidate(candidate_reference="GLC", model="GLC (X253)", model_aliases=("GLC",), **shared),
        VehicleCandidate(candidate_reference="GLC-COUPE", model="GLC Coupe (C253)",
                         model_aliases=("GLC Coupe",), **shared),
    )
    result = FuzzyVehicleMatcher(ManufacturerCandidateIndex(catalog)).match(VehicleMatchQuery(
        manufacturer="Mercedes-Benz", model="GLC", year=2019, fuels=frozenset({"diesel"}),
        displacement_cc=2143, power_kw=125, bodywork="suv",
    ))

    assert result.candidates[0].candidate_reference == "GLC"
    assert result.eligible_for_auto_resolution is True


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


def _facelift_siblings(build_month: int | None, *, with_months: bool = True):  # type: ignore[no-untyped-def]
    """Two KTypes whose runs meet in 2008; only the build month can tell them apart."""

    def ktype(reference: str, year_from: int, year_to: int, month_from: int, month_to: int) -> VehicleCandidate:
        return VehicleCandidate(
            reference, "Volvo", "V70", year_from=year_from, year_to=year_to,
            month_from=month_from if with_months else None, month_to=month_to if with_months else None,
            power_kw=120,
        )

    matcher = FuzzyVehicleMatcher(
        ManufacturerCandidateIndex((ktype("PRE", 2000, 2008, 200001, 200804), ktype("FACELIFT", 2008, 2013, 200805, 201312)))
    )
    return matcher.match(
        VehicleMatchQuery("V70", manufacturer="Volvo", year=2008, build_month=build_month, power_kw=120)
    )


@pytest.mark.parametrize(("build_month", "winner"), [(200803, "PRE"), (200811, "FACELIFT")])
def test_the_build_month_decides_between_ktypes_whose_runs_meet_in_one_year(build_month: int, winner: str) -> None:
    result = _facelift_siblings(build_month)

    assert result.candidates[0].candidate_reference == winner
    assert result.reason != "candidate_margin_not_met"
    other = result.candidates[1]
    assert "year_month_outside_unverified" in other.missing_fields
    assert "year" not in other.conflicting_fields


@pytest.mark.parametrize(("build_month", "with_months"), [(None, True), (200803, False)])
def test_without_a_month_on_either_side_the_year_alone_still_ties(build_month: int | None, with_months: bool) -> None:
    result = _facelift_siblings(build_month, with_months=with_months)

    assert result.reason == "candidate_margin_not_met"
    assert all("year" in candidate.matched_fields for candidate in result.candidates)


def test_production_months_must_be_real_months() -> None:
    with pytest.raises(ValueError):
        VehicleCandidate("k", "Volvo", "V70", month_from=200813)
    with pytest.raises(ValueError):
        VehicleMatchQuery("V70", manufacturer="Volvo", build_month=2008)


@pytest.mark.parametrize(
    ("model", "body", "line"),
    [
        # Generations, chassis codes and the car's own body word leave one line.
        ("LEGACY IV Estate (BP)", "ESTATE", "LEGACY"),
        ("PASSAT B8 Variant (3G5, CB5)", "ESTATE", "PASSAT"),
        ("ASTRA J Sports Tourer (P10)", "ESTATE", "ASTRA"),
        ("INSIGNIA B Grand Sport (Z18)", "HATCHBACK", "INSIGNIA"),
        ("XC60 I SUV (156)", "SUV", "XC60"),
        # Makers' own estate names are bodies too, alone or as two words.
        ("E-CLASS T-Model (S210)", "ESTATE", "E CLASS"),
        ("MEGANE III Grandtour (KZ0/1)", "ESTATE", "MEGANE"),
        ("LEON ST (5F8)", "ESTATE", "LEON"),
        ("LANCER I Station Wagon (A7_V)", "ESTATE", "LANCER"),
        # A body registered only as closed contradicts no body name.
        ("E-CLASS T-Model (S210)", "COVERED BODY", "E CLASS"),
        ("E-CLASS T-Model (S210)", "SEDAN", "E CLASS MODEL"),
        # Another body's word stays: a registered SUV is not the GLC Coupe's line.
        ("GLC Coupe (C253)", "SUV", "GLC COUPE"),
        ("GLC Coupe (C253)", "COUPE", "GLC"),
        ("GLC Coupe (C253)", "", "GLC"),
        ("PASSAT B8 Variant (3G5, CB5)", "SEDAN", "PASSAT VARIANT"),
        # A name that tells models apart stays.
        ("PAJERO SPORT I (K7_, K9_)", "", "PAJERO SPORT"),
        ("IBIZA IV SC (6J1, 6P5)", "", "IBIZA SC"),
        ("PASSAT ALLTRACK B8 (3G5, CB5)", "ESTATE", "PASSAT ALLTRACK"),
        ("A3 Sportback (8PA)", "HATCHBACK", "A3 SPORTBACK"),
        # The model's own first word is never a body name.
        ("GRAND SPORT", "", "GRAND SPORT"),
        ("MODEL S (5YJS)", "", "MODEL S"),
    ],
)
def test_a_model_line_drops_generation_and_body_words_only(model: str, body: str, line: str) -> None:
    from ingestion.fuzzy_matching import _model_line

    assert _model_line(model, body) == line


def _built_in(build_month: int, *candidates: VehicleCandidate, model: str, body: str | None = None,
              config: FuzzyMatchConfig | None = None) -> dict[str, FuzzyCandidateMatch]:
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex(candidates), config=config)
    query = VehicleMatchQuery(model, manufacturer=candidates[0].manufacturer, year=build_month // 100,
                              build_month=build_month, power_kw=100, bodywork=body)
    return {match.candidate_reference: match for match in matcher.match(query).candidates}


def _ktype(reference: str, model: str, months: tuple[int, int | None], *, make: str = "Make",
           alias: str | None = None, body: str | None = None) -> VehicleCandidate:
    month_from, month_to = months
    return VehicleCandidate(
        reference, make, model, model_aliases=(alias,) if alias else (),
        year_from=month_from // 100, year_to=month_to // 100 if month_to else None,
        month_from=month_from, month_to=month_to, power_kw=100,
        bodyworks=frozenset({body}) if body else frozenset(),
    )


def test_the_build_month_tolerance_is_zero_months_unless_set() -> None:
    early = _ktype("early", "V70 II (285)", (200001, 200804), alias="V70")
    late = _ktype("late", "V70 III (135)", (200805, 201312), alias="V70")

    assert FuzzyMatchConfig().production_month_tolerance == 0
    a_month_early = _built_in(200804, early, late, model="V70")["late"]
    assert "year_month_outside_unverified" in a_month_early.missing_fields
    widened = _built_in(200804, early, late, model="V70", config=FuzzyMatchConfig(production_month_tolerance=1))
    assert "year_month_outside_unverified" not in widened["late"].missing_fields
    with pytest.raises(ValueError):
        FuzzyMatchConfig(production_month_tolerance=-1)


def test_the_build_month_moves_a_car_to_its_lines_next_generation() -> None:
    # A Legacy estate built 08/2009, after TecDoc's Legacy IV ends and the V starts.
    old = _ktype("iv", "LEGACY IV Estate (BP)", (200309, 200904), alias="LEGACY", body="estate")
    new = _ktype("v", "LEGACY V Estate (BR)", (200905, 201412), alias="LEGACY", body="estate")

    scored = _built_in(200908, old, new, model="LEGACY", body="estate")

    assert "year_month_outside_unverified" in scored["iv"].missing_fields
    assert scored["v"].separation_score > scored["iv"].separation_score


def test_the_build_month_never_sends_a_car_to_another_line() -> None:
    # A Pajero built 03/2000, a month before TecDoc's Pajero III starts, is no
    # Pajero Sport just because the Sport's run covers the month.
    pajero = _ktype("pajero", "PAJERO III (V7_W, V6_W)", (200004, 200701), alias="PAJERO")
    sport = _ktype("sport", "PAJERO SPORT I (K7_, K9_)", (199811, 200806), alias="PAJERO SPORT")

    scored = _built_in(200003, pajero, sport, model="PAJERO")

    assert "year_month_outside_unverified" not in scored["pajero"].missing_fields
    assert scored["pajero"].separation_score > scored["sport"].separation_score


def test_a_body_word_of_another_body_keeps_its_ktype_another_line() -> None:
    # An SUV GLC built 10/2022: TecDoc's GLC (X253) ends 06/2022, the GLC Coupe
    # (also an SUV in TecDoc) runs on. The Coupe is not the SUV's line.
    glc = _ktype("glc", "GLC (X253)", (201911, 202206), alias="GLC", body="suv")
    coupe = _ktype("coupe", "GLC Coupe (C253)", (201911, 202303), alias="GLC Coupe", body="suv")

    scored = _built_in(202210, glc, coupe, model="GLC", body="suv")

    assert "year_month_outside_unverified" not in scored["glc"].missing_fields
    assert scored["glc"].separation_score > scored["coupe"].separation_score


def test_another_lines_ktype_built_outside_the_month_is_demoted() -> None:
    # A Passat estate built 01/2015 is no Passat Alltrack, which TecDoc starts later.
    passat = _ktype("passat", "PASSAT B8 Variant (3G5, CB5)", (201408, 202310), alias="PASSAT", body="estate")
    alltrack = _ktype("alltrack", "PASSAT ALLTRACK B8 (3G5, CB5)", (201503, 202310), alias="PASSAT",
                      body="estate")

    scored = _built_in(201501, passat, alltrack, model="PASSAT", body="estate")

    assert "year_month_outside_unverified" in scored["alltrack"].missing_fields
    assert scored["passat"].separation_score > scored["alltrack"].separation_score


@pytest.mark.parametrize(
    ("brand", "model"),
    [
        # "2.0" is the engine size, not the Qashqai +2.
        ("NISSAN QASHQAI 2.0 ACENT", "QASHQAI"),
        ("NISSAN QASHQAI 1,6", "QASHQAI"),
        ("NISSAN QASHQAI+2 2.0", "QASHQAI +2 (JJ10E)"),
    ],
)
def test_an_engine_size_in_registry_text_is_no_model_number(brand: str, model: str) -> None:
    index = ManufacturerCandidateIndex((
        VehicleCandidate("one", "Nissan", "QASHQAI I (J10, NJ10)", model_aliases=("QASHQAI",)),
        VehicleCandidate("plus", "Nissan", "QASHQAI +2 (JJ10E)", model_aliases=("QASHQAI +2",)),
    ))

    assert index.recover_model_from_evidence("Nissan", {"brand": brand}) == (model, "brand")


def test_a_label_holding_the_whole_decimal_still_reads_it() -> None:
    from ingestion.fuzzy_matching import _decimal_points, _reads_without_cutting_a_number

    text, points = " VW GOLF 1 6 FSI ", _decimal_points("VW GOLF 1.6 FSI")

    assert _reads_without_cutting_a_number("1 6 FSI", text, points)
    assert not _reads_without_cutting_a_number("GOLF 1", text, points)
    assert not _reads_without_cutting_a_number("6 FSI", text, points)


@pytest.mark.parametrize(("j01_fuel", "demoted"), [("electric", True), ("petrol", False)])
def test_a_conflicting_ktype_gets_no_shelter_from_the_cars_own_line(j01_fuel: str, demoted: bool) -> None:
    # "MINI COOPER S" built 10/2023: the text names the line of TecDoc's "MINI COOPER
    # (J01)", which starts 11/2023. An electric J01 conflicts with a petrol car, so it
    # is demoted as any KType outside the month; a petrol one keeps its line's shelter.
    f56 = VehicleCandidate("f56", "MINI", "MINI (F56)", model_aliases=("MINI",), year_from=2020,
                           month_from=202011, power_kw=131, fuels=frozenset({"petrol"}))
    j01 = VehicleCandidate("j01", "MINI", "MINI COOPER (J01)", model_aliases=("MINI COOPER",), year_from=2023,
                           month_from=202311, power_kw=131, fuels=frozenset({j01_fuel}))
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((f56, j01)))

    result = matcher.match(VehicleMatchQuery("MINI COOPER", manufacturer="MINI", year=2023, build_month=202310,
                                             power_kw=131, fuels=frozenset({"petrol"})))
    scored = {match.candidate_reference: match for match in result.candidates}

    assert ("fuels" in scored["j01"].conflicting_fields) is demoted
    assert ("year_month_outside_unverified" in scored["j01"].missing_fields) is demoted


# --- plug-in power guard ------------------------------------------------------------------
# The registry gives a hybrid's combustion power (150 kW), TecDoc a plug-in's system
# power (230 kW). Exact power alone must not set a mild hybrid apart from its plug-in
# sibling: GLC 300 e cars resolved to the GLC 200 4MATIC mild hybrid that way.

_HYBRID_FUELS = frozenset({"petrol", "electric", "hybrid_petrol"})


def _glc(reference: str, power_kw: int, electrification: str | None, **fields: object) -> VehicleCandidate:
    return VehicleCandidate(
        reference, "Mercedes-Benz", "GLC", fuels=frozenset({"hybrid_petrol"}), power_kw=power_kw,
        electrification=electrification, **fields,  # type: ignore[arg-type]
    )


def _glc_match(*catalog: VehicleCandidate, model: str = "GLC", **query: object):  # type: ignore[no-untyped-def]
    fields: dict[str, object] = {"fuels": _HYBRID_FUELS, "power_kw": 150, **query}
    return FuzzyVehicleMatcher(ManufacturerCandidateIndex(catalog)).match(
        VehicleMatchQuery(model, manufacturer="Mercedes-Benz", **fields)  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("registered", [None, "plug_in_hybrid"])  # an AIS car states none
def test_exact_power_alone_never_sets_a_hybrid_apart_from_its_plug_in_sibling(registered: str | None) -> None:
    from ingestion.fuzzy_matching import PLUG_IN_POWER_GUARD

    result = _glc_match(
        _glc("glc200", 150, "mild_hybrid"), _glc("glc300e", 230, "plug_in_hybrid"), electrification=registered
    )

    assert result.reason == "candidate_margin_not_met"
    assert not result.eligible_for_auto_resolution
    assert result.guards == (PLUG_IN_POWER_GUARD,)
    # The mild hybrid stays the suggestion, with the plug-in in front of the reviewer.
    assert [match.candidate_reference for match in result.candidates] == ["glc200", "glc300e"]


def test_without_the_plug_in_sibling_exact_power_still_resolves() -> None:
    result = _glc_match(_glc("glc200", 150, "mild_hybrid"), _glc("glc300", 190, "mild_hybrid"))

    assert result.reason == "automatic_candidate_threshold_met"
    assert result.guards == ()


def test_a_car_registered_as_a_non_plug_in_hybrid_keeps_its_exact_power() -> None:
    # ELHYBRID (or a word such as "48V"): a 540i xDrive or X5 xDrive40i mild hybrid is
    # right on its exact power, whatever plug-in sibling TecDoc has.
    result = _glc_match(
        _glc("glc200", 150, "mild_hybrid"), _glc("glc300e", 230, "plug_in_hybrid"), electrification="hybrid"
    )

    assert result.reason == "automatic_candidate_threshold_met"
    assert result.guards == ()


def test_a_plug_in_rival_with_another_engine_code_never_holds_the_top_back() -> None:
    result = _glc_match(
        _glc("glc200", 150, "mild_hybrid", engine_codes=frozenset({"M 254.920"})),
        _glc("glc300e", 230, "plug_in_hybrid", engine_codes=frozenset({"M 264.920"})),
        engine_code="M 254.920",
    )

    assert "engine_code" in result.candidates[1].conflicting_fields
    assert result.reason == "automatic_candidate_threshold_met"
    assert result.guards == ()


def test_an_exact_engine_code_a_margin_apart_keeps_the_car_resolved() -> None:
    # The plug-in carries no engine code: unverified, not a conflict. The exact code
    # alone (0.12) clears the margin once the power swing is taken out.
    result = _glc_match(
        _glc("glc200", 150, "mild_hybrid", engine_codes=frozenset({"M 254.920"})),
        _glc("glc300e", 230, "plug_in_hybrid"),
        engine_code="M 254.920",
    )

    assert "power_kw_hybrid_unverified" in result.candidates[1].missing_fields
    assert result.reason == "automatic_candidate_threshold_met"


def test_an_engine_family_match_is_too_little_to_keep_them_apart() -> None:
    # Same engine family (0.03) on the mild hybrid only: with the 0.10 power swing
    # taken out the two are 0.03 apart, under the margin.
    result = _glc_match(
        _glc("glc200", 150, "mild_hybrid", engine_codes=frozenset({"B47D20"})),
        _glc("glc300e", 230, "plug_in_hybrid"),
        engine_code="B47D20A",
    )

    assert "engine_code_family" in result.candidates[0].matched_fields
    assert result.reason == "candidate_margin_not_met"


def test_a_plug_in_rival_contradicting_the_car_never_holds_the_top_back() -> None:
    # A plug-in GLC Coupe for a registered SUV: its body conflicts. The mild hybrid's
    # year is just outside its run (and its body unknown) while the Coupe's year fits,
    # so but for the conflict the two would be 0.05 apart once the power swing is out.
    result = _glc_match(
        _glc("suv200", 150, "mild_hybrid", year_from=2016, year_to=2022),
        _glc("coupe300e", 230, "plug_in_hybrid", year_from=2016, year_to=2024, bodyworks=frozenset({"coupe"})),
        year=2023, bodywork="suv",
    )

    assert "bodywork" in result.candidates[1].conflicting_fields
    assert result.reason == "automatic_candidate_threshold_met"
    assert result.guards == ()


def test_a_higher_power_mild_or_combustion_sibling_is_no_plug_in_rival() -> None:
    # Captur TCe 140 (103 kW) against TCe 160 (116 kW): both mild hybrids.
    def captur(reference: str, power_kw: int, electrification: str) -> VehicleCandidate:
        return VehicleCandidate(reference, "Renault", "CAPTUR", fuels=frozenset({"hybrid_petrol"}),
                                power_kw=power_kw, electrification=electrification)

    for rival in ("mild_hybrid", "combustion"):
        result = FuzzyVehicleMatcher(
            ManufacturerCandidateIndex((captur("tce140", 103, "mild_hybrid"), captur("tce160", 116, rival)))
        ).match(VehicleMatchQuery("CAPTUR", manufacturer="Renault", fuels=_HYBRID_FUELS, power_kw=103))

        assert result.reason == "automatic_candidate_threshold_met", rival


def test_a_car_without_electricity_is_never_guarded() -> None:
    # A petrol-only car against hybrid KTypes, reviewed as compatible fuels.
    matcher = FuzzyVehicleMatcher(
        ManufacturerCandidateIndex((_glc("glc200", 150, "mild_hybrid"), _glc("glc300e", 230, "plug_in_hybrid"))),
        fuel_compatible_pairs=frozenset({("petrol", "hybrid_petrol")}),
    )

    result = matcher.match(
        VehicleMatchQuery("GLC", manufacturer="Mercedes-Benz", fuels=frozenset({"petrol"}), power_kw=150)
    )

    assert "power_kw_hybrid_unverified" in result.candidates[1].missing_fields
    assert result.reason == "automatic_candidate_threshold_met"
    assert result.guards == ()


def test_a_top_below_the_automatic_threshold_ties_with_its_plug_in_sibling_too() -> None:
    from ingestion.fuzzy_matching import PLUG_IN_POWER_GUARD

    # Text that only nearly matches ("CLA" against "CLAX": 0.75) keeps the top at
    # 0.85: it would have been routed on its own score, provisional at best.
    result = _glc_match(
        VehicleCandidate("cla200", "Mercedes-Benz", "CLAX", fuels=frozenset({"hybrid_petrol"}), power_kw=150,
                         electrification="mild_hybrid"),
        VehicleCandidate("cla250e", "Mercedes-Benz", "CLAX", fuels=frozenset({"hybrid_petrol"}), power_kw=160,
                         electrification="plug_in_hybrid"),
        model="CLA",
    )

    assert result.candidates[0].confidence < FuzzyMatchConfig().automatic_threshold
    assert result.reason == "candidate_margin_not_met"
    assert result.guards == (PLUG_IN_POWER_GUARD,)


@pytest.mark.parametrize(
    ("top", "rival"),
    [
        (None, "plug_in_hybrid"),  # the top's engine type was never loaded
        ("mild_hybrid", None),  # nor the rival's
        ("plug_in_hybrid", "plug_in_hybrid"),  # a plug-in top is the plug-in reading
        ("range_extender", "plug_in_hybrid"),
    ],
)
def test_unknown_electrification_or_a_plug_in_top_is_never_guarded(top: str | None, rival: str | None) -> None:
    result = _glc_match(_glc("glc200", 150, top), _glc("glc300e", 230, rival))

    assert result.reason == "automatic_candidate_threshold_met"
    assert result.guards == ()


def test_a_top_already_tied_with_its_runner_up_carries_no_guard() -> None:
    result = _glc_match(
        _glc("glc200", 150, "mild_hybrid"), _glc("glc200-b", 150, "mild_hybrid"), _glc("glc300e", 230, "plug_in_hybrid")
    )

    assert result.reason == "candidate_margin_not_met"
    assert result.guards == ()


def test_a_range_extender_counts_as_a_plug_in_rival() -> None:
    result = _glc_match(_glc("glc200", 150, "mild_hybrid"), _glc("glc300e", 230, "range_extender"))

    assert result.reason == "candidate_margin_not_met"


def test_reviewed_aliases_keep_electrification_so_the_alias_matcher_is_guarded_too() -> None:
    from ingestion.tecdoc.model_aliases import ReviewedModelAliasIndex
    from ingestion.translation_dictionaries import TranslationRule, TranslationRuleSet

    aliases = ReviewedModelAliasIndex(TranslationRuleSet(version="rules-v1", rules=(TranslationRule(
        rule_id="MOD-001", area="model_family", source_fields=("model",), source_terms=("GLC KUPE",),
        canonical_field="model_family", canonical_value="GLC", decision="accepted",
        manufacturers=("Mercedes-Benz",),
    ),)))
    catalog = (_glc("glc200", 150, "mild_hybrid"), _glc("glc300e", 230, "plug_in_hybrid"))
    expanded = tuple(aliases.expand(candidate) for candidate in catalog)

    assert [candidate.electrification for candidate in expanded] == ["mild_hybrid", "plug_in_hybrid"]
    assert "GLC KUPE" in expanded[0].model_aliases
    assert _glc_match(*expanded, model="GLC KUPE").reason == "candidate_margin_not_met"


def test_matching_text_is_compared_as_written_only_catalog_aliases_are_respelled() -> None:
    from ingestion.fuzzy_matching import _normalized_text

    # "CEE'D" and "SANTA FÉ" meet the registry's "CEED" and "SANTA FE" through catalog
    # aliases (`match_run_adapters.registry_spelling`), never by folding text here:
    # that would also change manufacturer keys and the registry's own Å, Ä and Ö.
    assert _normalized_text("Cee'd") == "CEE D"
    assert _normalized_text("Santa Fé") == "SANTA FÉ"
    assert _normalized_text("Kapitän") == "KAPITÄN"


def test_a_label_shared_by_generations_is_their_family_in_the_registrys_spelling_too() -> None:
    from ingestion.fuzzy_matching import _unique_or_family

    # "CEE'D (JD)" normalizes to "CEE D JD", which never starts with "CEED": read as
    # written only, the shared label recovered nothing and the model went unread.
    assert _unique_or_family({("CEED", "CEE'D (JD)", "model"), ("CEED", "CEED (CD)", "model")}) == ("CEED", "model")
    assert _unique_or_family(
        {("SANTA FE", "SANTA FÉ II (CM)", "model"), ("SANTA FE", "SANTA FÉ III (DM, DMA)", "model")}
    ) == ("SANTA FE", "model")
    # A label that is no family name of every canonical still recovers nothing.
    assert _unique_or_family({("CEED", "CEE'D (JD)", "model"), ("CEED", "PRO CEE'D (JD)", "model")}) is None
    assert _unique_or_family({("KAPITAN", "KAPITÄN A", "model"), ("KAPITAN", "KAPITÄN B", "model")}) is None
