"""Matching diagnostics: how an evaluation is read back, and the job around it.

The matcher itself is the audit's and has its own tests; these cover what this
feature adds -- the one/several/none reading, the gap it names, the rule overlay
and the job lifecycle -- with a scripted evaluator standing in for the catalog.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.app.features.vehicle_matching.repository import (
    CarRecord,
    _population_predicate,
    overlay_resolutions,
)
from api.app.features.vehicle_matching.router import get_service
from api.app.features.vehicle_matching.service import (
    JobCapacityError,
    Matcher,
    SummaryJobNotFoundError,
    SummaryJobs,
    VehicleMatchingService,
    VehicleNotFoundError,
    bucket_for,
    missing_on_car,
    separating_fields,
)
from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.match_run_service import MatchSourceRecord
from ingestion.tecdoc.match_run_adapters import MatchEvaluation, ResolvedMatchQuery


def _match(reference: str, *, conflicts: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "candidate_reference": reference,
        "candidate_type": "TecDocKType",
        "confidence": 0.9,
        "evidence": {
            "manufacturer": "VOLVO",
            "model": "XC60",
            "matched_fields": ["model"],
            "missing_fields": [],
            "conflicting_fields": list(conflicts),
        },
    }


def _evaluation(*candidates: dict[str, Any], scored: bool = True) -> MatchEvaluation:
    reasons = ("match:automatic_candidate_threshold_met",) if scored else ("policy:x",)
    return MatchEvaluation(
        "resolved" if scored else "policy_excluded",
        reasons,
        candidate_matches=tuple(candidates),
    )


def _ktype(reference: str, *, engines: frozenset[str], power: int = 140) -> VehicleCandidate:
    return VehicleCandidate(
        candidate_reference=reference,
        manufacturer="VOLVO",
        model="XC60",
        year_from=2017,
        year_to=2022,
        fuels=frozenset({"diesel"}),
        engine_codes=engines,
        power_kw=power,
    )


def _query(**overrides: Any) -> ResolvedMatchQuery:
    values: dict[str, Any] = {
        "key": ("k",),
        "scope_manufacturer": "VOLVO",
        "model_values": ("XC60",),
        "year": 2018,
        "fuels": frozenset({"diesel"}),
        "engine_code": None,
        "displacement_cc": 1969,
        "power_kw": 140,
        "drive_type": "awd",
        "bodywork": "suv",
        "recovery_reason": None,
        "source_context": (),
        "source_model_resolution": None,
    }
    values.update(overrides)
    return ResolvedMatchQuery(**values)


# ------------------------------------------------------------------------- the reading


def test_a_car_stopped_before_scoring_is_not_matchable() -> None:
    assert bucket_for(_evaluation(scored=False)) == "not_matchable"


def test_buckets_count_only_candidates_that_conflict_on_nothing() -> None:
    """A KType the car contradicts on power is not a match, however it scored."""

    assert bucket_for(_evaluation()) == "none"
    assert bucket_for(_evaluation(_match("A"), _match("B", conflicts=("power_kw",)))) == "one"
    assert bucket_for(_evaluation(_match("A"), _match("B"))) == "several"
    assert bucket_for(_evaluation(_match("A", conflicts=("bodywork",)))) == "none"


def test_separating_fields_name_what_differs_among_compatible_candidates() -> None:
    catalog = {
        "A": _ktype("A", engines=frozenset({"D4204T14"})),
        "B": _ktype("B", engines=frozenset({"D4204T23"})),
        # Differs on power too, but conflicts with the car, so it must not count.
        "C": _ktype("C", engines=frozenset({"B4204T"}), power=187),
    }
    evaluation = _evaluation(_match("A"), _match("B"), _match("C", conflicts=("power_kw",)))

    assert separating_fields(evaluation, catalog) == ["engine_code"]


def test_the_gap_is_a_separating_field_the_car_has_no_value_for() -> None:
    assert missing_on_car(["engine_code", "power_kw"], _query()) == ["engine_code"]
    assert missing_on_car(["engine_code"], _query(engine_code="D4204T14")) == []
    assert missing_on_car(["engine_code"], None) == []


# ------------------------------------------------------------------------- the overlay


def test_a_rules_value_outranks_the_derivation_it_corrects() -> None:
    """Same precedence as effective_value: a corrected car is matched as corrected."""

    merged, applied = overlay_resolutions(
        {"bodywork_form": "estate", "power_kw": 140}, {"bodywork_form": "suv", "engine_code": ""}
    )

    assert merged["bodywork_form"] == "suv"
    assert merged["power_kw"] == 140
    assert "engine_code" not in merged
    assert applied == ("bodywork_form",)


def test_population_without_conditions_or_text_is_every_car() -> None:
    assert _population_predicate([], "").sql == "true"
    assert "ILIKE" in _population_predicate([], "volvo").sql


# --------------------------------------------------------------------------- the service


class _Evaluator:
    """Stands in for TecDocDryRunEvaluator: one scripted outcome per car."""

    def __init__(self, outcomes: dict[int, MatchEvaluation]) -> None:
        self._outcomes = outcomes

    def evaluate(self, record: MatchSourceRecord) -> MatchEvaluation:
        return self._outcomes[record.source_record_id]

    def resolved_query(self, record: MatchSourceRecord) -> ResolvedMatchQuery:
        return _query()


class _Repository:
    def __init__(self, ids: list[int]) -> None:
        self._ids = ids

    def ids_for_identifier(self, identifier: str, *, limit: int = 10) -> list[int]:
        return [1, 9] if identifier == "ABC123" else []

    def population(self, conditions: Any, text: str, *, limit: int) -> tuple[int, list[int]]:
        return len(self._ids), self._ids[:limit]

    def car_records(self, ids: list[int]) -> list[CarRecord]:
        return [
            CarRecord(
                source_record_id=rid,
                plate=f"P{rid}",
                vin=None,
                manufacturer="Volvo",
                model_family="XC60",
                record=MatchSourceRecord(rid, {}),
                rule_filled=(),
            )
            for rid in ids
        ]


def _service(outcomes: dict[int, MatchEvaluation], jobs: SummaryJobs | None = None) -> VehicleMatchingService:
    catalog = {
        "A": _ktype("A", engines=frozenset({"D4204T14"})),
        "B": _ktype("B", engines=frozenset({"D4204T23"})),
    }
    matcher = Matcher("batch-1", _Evaluator(outcomes), catalog)  # type: ignore[arg-type]
    return VehicleMatchingService(
        _Repository(sorted(outcomes)), lambda: matcher, jobs or SummaryJobs()  # type: ignore[arg-type]
    )


def test_lookup_normalizes_the_identifier_and_explains_the_gap() -> None:
    service = _service({1: _evaluation(_match("A"), _match("B")), 9: _evaluation()})

    result = service.lookup(" abc 123 ")

    assert result.source_record_id == 1
    assert result.bucket == "several"
    assert result.catalog_batch == "batch-1"
    assert [candidate.ktype for candidate in result.candidates] == ["A", "B"]
    assert result.candidates[0].engine_codes == ["D4204T14"]
    assert result.missing_separating_fields == ["engine_code"]
    assert result.other_source_record_ids == [9]


def test_lookup_of_an_unknown_plate_says_so() -> None:
    with pytest.raises(VehicleNotFoundError):
        _service({}).lookup("NOPE")


def test_summary_counts_buckets_and_names_the_gaps() -> None:
    outcomes = {
        1: _evaluation(_match("A")),
        2: _evaluation(_match("A"), _match("B")),
        3: _evaluation(_match("A", conflicts=("bodywork",))),
        4: _evaluation(),
        5: _evaluation(scored=False),
    }

    job = _service(outcomes).start_summary([], "", 10, run_in_background=False)

    assert job.status == "done"
    assert job.evaluated == 5
    summary = job.summary
    assert summary.buckets == {"one": 1, "several": 1, "none": 2, "not_matchable": 1}
    assert [(item.field, item.cars) for item in summary.none_conflicting_fields] == [("bodywork", 1)]
    assert summary.none_without_candidates == 1
    assert [(item.field, item.cars) for item in summary.several_missing_separating_fields] == [
        ("engine_code", 1)
    ]
    assert summary.not_matchable_reasons[0].reason == "policy:x"
    assert summary.examples["several"][0].plate == "P2"


def test_summary_reports_sampling_when_the_limit_cuts_the_population() -> None:
    outcomes = {rid: _evaluation(_match("A")) for rid in range(1, 6)}

    summary = _service(outcomes).start_summary([], "", 2, run_in_background=False).summary

    assert summary.population == 5
    assert summary.evaluated == 2
    assert summary.sampled is True


def test_a_cancelled_summary_keeps_what_it_counted() -> None:
    jobs = SummaryJobs()
    service = _service({1: _evaluation(_match("A"))}, jobs)
    job = jobs.create(population=1, target=1)
    job.cancel.set()

    service._run(job, [1])

    assert service.summary_job(job.job_id).status == "cancelled"


def test_a_failing_summary_reports_why_instead_of_dying_silently() -> None:
    jobs = SummaryJobs()
    service = _service({}, jobs)  # no scripted outcome: evaluate raises KeyError
    job = jobs.create(population=1, target=1)

    service._run(job, [42])

    snapshot = service.summary_job(job.job_id)
    assert snapshot.status == "failed"
    assert snapshot.error is not None and snapshot.error.startswith("KeyError")


def test_running_summaries_are_capped() -> None:
    jobs = SummaryJobs(max_running=1)
    jobs.create(population=1, target=1)

    with pytest.raises(JobCapacityError):
        jobs.create(population=1, target=1)


def test_an_unknown_job_is_reported_not_invented() -> None:
    with pytest.raises(SummaryJobNotFoundError):
        SummaryJobs().get("nope")


# ------------------------------------------------------------------------------ HTTP


def test_http_maps_the_services_errors(client: TestClient) -> None:
    client.app.dependency_overrides[get_service] = lambda: _service({1: _evaluation()})  # type: ignore[attr-defined]
    try:
        assert client.get("/v1/vehicles/matching/lookup", params={"q": "NOPE"}).status_code == 404
        assert client.get("/v1/vehicles/matching/summary/nope").status_code == 404
        started = client.post("/v1/vehicles/matching/summary", json={"conditions": [], "limit": 1})
        assert started.status_code == 202
        assert started.json()["target"] == 1
        too_many = client.post("/v1/vehicles/matching/summary", json={"conditions": [], "limit": 50_000})
        assert too_many.status_code == 422
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def test_matching_routes_are_not_read_as_a_vehicle_id(client: TestClient) -> None:
    """/v1/vehicles/{source_record_id} must not swallow /v1/vehicles/matching/..."""

    client.app.dependency_overrides[get_service] = lambda: _service({})  # type: ignore[attr-defined]
    try:
        response = client.get("/v1/vehicles/matching/lookup", params={"q": "NOPE"})
        assert response.json()["detail"].startswith("No vehicle with plate or VIN")
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]
