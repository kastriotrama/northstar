import pytest

from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.tecdoc.match_diagnostics import (
    BodyworkConflictDiagnostics,
    BodyworkConflictObservation,
    CandidateCatalogCoverage,
    UnresolvedCohortDiagnostics,
    UnresolvedMatchObservation,
    equivalent_technical_candidate_groups,
)


def observation(**overrides: object) -> UnresolvedMatchObservation:
    values = {
        "manufacturer": "Volvo",
        "normalized_model": "XC40",
        "raw_model": "XC40 RECHARGE",
        "production_year": 2022,
        "fuels": ("electricity",),
        "power_kw": 170,
        "reason_codes": ("no_candidate_above_threshold",),
        "top_candidate_reference": "000012345",
    }
    values.update(overrides)
    return UnresolvedMatchObservation(**values)


def test_cohort_key_excludes_vehicle_identity_and_is_deterministic() -> None:
    first = observation(fuels=("electricity", "petrol"))
    second = observation(fuels=("petrol", "electricity"))

    assert first.cohort_key() == second.cohort_key()
    assert "plate" not in first.cohort_evidence()
    assert "vin" not in first.cohort_evidence()
    assert "source_record_id" not in first.cohort_evidence()


def test_diagnostics_rank_and_aggregate_reasons_and_candidates() -> None:
    diagnostics = UnresolvedCohortDiagnostics()
    diagnostics.add(observation())
    diagnostics.add(observation(reason_codes=("hard_conflict:power_kw",)))
    diagnostics.add(
        observation(
            normalized_model="V60",
            raw_model="V60",
            top_candidate_reference=None,
        )
    )

    report = diagnostics.report(limit=2)

    assert report[0]["count"] == 2
    assert report[0]["reason_counts"] == {
        "hard_conflict:power_kw": 1,
        "no_candidate_above_threshold": 1,
    }
    assert report[0]["top_candidate_counts"] == {"000012345": 2}
    assert report[1]["count"] == 1


def test_diagnostics_reject_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="limit must be positive"):
        UnresolvedCohortDiagnostics().report(limit=0)


def test_bodywork_diagnostics_normalize_rank_and_exclude_identity() -> None:
    diagnostics = BodyworkConflictDiagnostics()
    diagnostics.add(BodyworkConflictObservation("Volvo", "V60", "AC", "Estate", ("Station Wagon",)))
    diagnostics.add(
        BodyworkConflictObservation(" VOLVO ", "V60", "ac", "estate", ("station  wagon",))
    )
    diagnostics.add(
        BodyworkConflictObservation("Volvo", "XC40", "AF", "suv", ("off-road vehicle",))
    )

    report = diagnostics.report(limit=2)

    assert report[0] == {
        "manufacturer": "volvo",
        "model": "v60",
        "ts_bodywork_code": "AC",
        "ts_bodywork": "estate",
        "tecdoc_bodyworks": ["station wagon"],
        "count": 2,
    }
    assert set(report[0]) == {
        "manufacturer",
        "model",
        "ts_bodywork_code",
        "ts_bodywork",
        "tecdoc_bodyworks",
        "count",
    }


def test_bodywork_diagnostics_reject_incomplete_evidence() -> None:
    diagnostics = BodyworkConflictDiagnostics()

    with pytest.raises(ValueError, match="must not be blank"):
        diagnostics.add(BodyworkConflictObservation("Volvo", "V60", "AC", "", ("estate",)))
    with pytest.raises(ValueError, match="limit must be positive"):
        diagnostics.report(limit=0)


def test_bodywork_compatibility_proposals_require_repetition_across_models() -> None:
    diagnostics = BodyworkConflictDiagnostics()
    for manufacturer, model, count in (
        ("Volvo", "XC40", 2),
        ("VW", "Tiguan", 2),
        ("Kia", "Niro", 1),
    ):
        for _ in range(count):
            diagnostics.add(
                BodyworkConflictObservation(manufacturer, model, "AC", "estate", ("suv",))
            )

    proposals = diagnostics.compatibility_proposals(minimum_occurrences=5, minimum_models=3)

    assert proposals == (
        {
            "ts_bodywork_code": "AC",
            "ts_bodywork": "estate",
            "tecdoc_bodyworks": ["suv"],
            "occurrence_count": 5,
            "distinct_manufacturer_model_count": 3,
            "status": "pending_review",
        },
    )


def test_bodywork_compatibility_proposals_reject_weak_or_invalid_thresholds() -> None:
    diagnostics = BodyworkConflictDiagnostics()
    diagnostics.add(BodyworkConflictObservation("Volvo", "XC40", "AC", "estate", ("suv",)))

    assert diagnostics.compatibility_proposals(minimum_occurrences=2) == ()
    with pytest.raises(ValueError, match="thresholds must be positive"):
        diagnostics.compatibility_proposals(minimum_models=0)


def test_catalog_coverage_reports_and_gates_missing_candidates() -> None:
    coverage = CandidateCatalogCoverage(
        active_ktype_count=72_570,
        candidate_ktype_count=55_808,
        promoted_ktype_count=55_808,
    )

    assert coverage.missing_candidate_count == 16_762
    assert coverage.candidate_coverage == 0.769023
    with pytest.raises(RuntimeError, match="missing=16762"):
        coverage.require_complete_candidates()


def test_complete_catalog_passes_gate() -> None:
    coverage = CandidateCatalogCoverage(72_570, 72_570, 55_808)

    coverage.require_complete_candidates()
    assert coverage.missing_candidate_count == 0


def test_equivalent_technical_groups_ignore_reference_and_catalog_status() -> None:
    shared = {
        "manufacturer": "Volvo",
        "model": "V70 III",
        "year_from": 2007,
        "year_to": 2016,
        "fuels": frozenset({"diesel"}),
        "engine_codes": frozenset({"D5244T"}),
        "displacement_cc": 2400,
        "power_kw": 136,
        "drive_type": "fwd",
        "bodyworks": frozenset({"estate"}),
    }
    candidates = (
        VehicleCandidate("KTYPE-1", candidate_type="TecDocKType", **shared),
        VehicleCandidate("KTYPE-2", candidate_type="TecDocKTypeCandidateOnly", **shared),
        VehicleCandidate(
            "KTYPE-3",
            candidate_type="TecDocKType",
            power_kw=151,
            **{key: value for key, value in shared.items() if key != "power_kw"},
        ),
    )

    assert equivalent_technical_candidate_groups(candidates) == (("KTYPE-1", "KTYPE-2"),)
