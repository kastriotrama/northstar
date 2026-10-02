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
    overlay_resolutions,
    overlay_vehicle,
    surrogate_record_id,
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
    verdict_of,
)
from api.app.features.vehicles.schemas import VehicleCondition, VehicleFilter
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


def test_a_vehicles_merged_values_replace_the_origin_derivation() -> None:
    """An engine code AIS supplied and a reviewer's correction both reach the matcher."""

    merged, overlaid = overlay_vehicle(
        {"bodywork_form": "estate", "power_kw": 140, "engine_code": None, "record_route": "x"},
        {"bodywork_form": "suv", "power_kw": 140, "engine_code": "D4204T14", "drive_type": None},
        {"bodywork_form": "review:rule-1", "engine_code": "ais:x@2026-09-19"},
        "transportstyrelsen",
    )

    assert merged["bodywork_form"] == "suv"
    assert merged["engine_code"] == "D4204T14"
    assert merged["record_route"] == "x"
    # The same value is not an overlay, and an empty vehicle field blanks nothing.
    assert overlaid == {"bodywork_form": "review", "engine_code": "ais"}
    assert "drive_type" not in merged


def test_a_vehicle_value_from_its_origin_record_is_labelled_with_the_origin() -> None:
    merged, overlaid = overlay_vehicle({}, {"manufacturer": "VOLVO"}, {}, "ais")

    assert merged == {"manufacturer": "VOLVO"}
    assert overlaid == {"manufacturer": "ais"}


def test_a_vehicle_without_a_ts_record_gets_a_stable_positive_record_id() -> None:
    first = surrogate_record_id("NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7G")

    assert first >= 1
    assert first == surrogate_record_id("NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7G")
    assert first != surrogate_record_id("NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7H")


# --------------------------------------------------------------------------- the service


class _Evaluator:
    """Stands in for TecDocDryRunEvaluator: one scripted outcome per car."""

    def __init__(self, outcomes: dict[int, MatchEvaluation]) -> None:
        self._outcomes = outcomes

    def evaluate(self, record: MatchSourceRecord) -> MatchEvaluation:
        return self._outcomes[record.source_record_id]

    def resolved_query(self, record: MatchSourceRecord) -> ResolvedMatchQuery:
        return _query()


def _car(rid: int, *, vehicle: bool = True) -> CarRecord:
    return CarRecord(
        source_record_id=rid,
        plate=f"P{rid}",
        vin=None,
        manufacturer="Volvo",
        model_family="XC60",
        record=MatchSourceRecord(rid, {}),
        rule_filled=(),
        vehicle_id=f"V{rid}" if vehicle else None,
        overlaid={"engine_code": "ais"} if vehicle else {},
    )


class _Repository:
    """Vehicles are `V<n>`; each is evaluated as record `n`."""

    def __init__(self, ids: list[int]) -> None:
        self._ids = [f"V{rid}" for rid in ids]

    def vehicles_for_identifier(self, identifier: str, *, limit: int = 10) -> list[str]:
        return ["V1", "V9"] if identifier == "ABC123" else []

    def vehicle_population(self, terms: Any, text: str, *, limit: int) -> tuple[int, list[str]]:
        return len(self._ids), self._ids[:limit]

    def vehicle_car_records(self, ids: list[str]) -> list[CarRecord]:
        return [_car(int(vehicle_id[1:])) for vehicle_id in ids if vehicle_id != "V404"]

    def car_records(self, ids: list[int]) -> list[CarRecord]:
        return [_car(rid, vehicle=False) for rid in ids]


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

    assert result.vehicle_id == "V1"
    assert result.source_record_id == 1
    assert result.overlaid_fields == {"engine_code": "ais"}
    assert result.bucket == "several"
    assert result.catalog_batch == "batch-1"
    assert [candidate.ktype for candidate in result.candidates] == ["A", "B"]
    assert result.candidates[0].engine_codes == ["D4204T14"]
    assert result.missing_separating_fields == ["engine_code"]
    assert result.other_vehicle_ids == ["V9"]


def test_lookup_by_vehicle_matches_that_vehicle() -> None:
    service = _service({1: _evaluation(_match("A")), 9: _evaluation()})

    result = service.lookup_vehicle(" v9 ")

    assert result.vehicle_id == "V9"
    assert result.bucket == "none"
    assert result.other_vehicle_ids == []
    with pytest.raises(VehicleNotFoundError):
        service.lookup_vehicle("V404")


def test_lookup_by_record_explains_that_exact_record() -> None:
    service = _service({1: _evaluation(_match("A")), 9: _evaluation()})

    result = service.lookup_record(9)

    assert result.source_record_id == 9
    assert result.vehicle_id is None
    assert result.bucket == "none"
    assert result.other_vehicle_ids == []


class _Choices:
    """Stands in for the choice repository: one head per vehicle, reads counted."""

    def __init__(self, heads: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.heads = heads or {}
        self.error = error
        self.reads: list[str] = []

    def current(self, vehicle_id: str) -> Any:
        self.reads.append(vehicle_id)
        if self.error:
            raise self.error
        return self.heads.get(vehicle_id)


def _with_choices(service: VehicleMatchingService, choices: _Choices) -> VehicleMatchingService:
    service._choices = choices
    return service


def _head(shown: Any, **overrides: Any) -> Any:
    from datetime import UTC, datetime
    from uuid import uuid4

    from api.app.features.vehicle_ktype_choices import evidence
    from ingestion.vehicle_ktype_choices import StoredChoice

    values: dict[str, Any] = {
        "choice_id": uuid4(), "vehicle_id": shown.vehicle_id, "action": "choose", "ktype": "B",
        "supersedes_choice_id": None, "reviewer": "Ada", "reason": None,
        "catalog_batch": shown.catalog_batch, "automatic_terminal": shown.terminal,
        "automatic_ktype": shown.top_ktype, "code_version": "v",
        "evidence_fingerprint": shown.evidence_fingerprint,
        "evidence": evidence.snapshot(shown, "v"), "created_at": datetime(2026, 10, 2, tzinfo=UTC),
    }
    values.update(overrides)
    return StoredChoice(**values)


def test_a_lookup_without_a_choice_reader_keeps_its_defaults() -> None:
    service = _service({1: _evaluation(_match("A"), _match("B"))})

    result = service.lookup_vehicle("V1")

    assert len(result.evidence_fingerprint) == 64
    assert result.evidence_fingerprint == service.lookup_vehicle("V1").evidence_fingerprint
    assert result.choice is None
    # The scripted evaluation is "resolved" with no top candidate: nothing effective.
    assert (result.effective_ktype, result.effective_source) == (None, None)
    assert result.inputs is not None
    assert (result.inputs.build_month, result.inputs.electrification) == (None, None)


def test_a_resolved_match_is_the_effective_ktype_until_a_person_chooses() -> None:
    resolved = MatchEvaluation(
        "resolved", ("match:x",), top_candidate_reference="A",
        candidate_matches=(_match("A"), _match("B")),
    )
    choices = _Choices()
    service = _with_choices(_service({1: resolved}), choices)

    shown = service.lookup_vehicle("V1")
    assert (shown.effective_ktype, shown.effective_source, shown.choice) == ("A", "matcher", None)

    choices.heads["V1"] = (_head(shown), 2)
    chosen = service.lookup_vehicle("V1")

    assert chosen.choice is not None
    assert (chosen.choice.status, chosen.choice.ktype, chosen.choice.history_count) == (
        "chosen", "B", 2)
    assert chosen.choice.needs_review is False
    assert (chosen.effective_ktype, chosen.effective_source) == ("B", "person")
    # The matcher's own result is untouched.
    assert (chosen.terminal, chosen.top_ktype) == ("resolved", "A")
    assert chosen.evidence_fingerprint == shown.evidence_fingerprint


def test_a_lookup_flags_a_choice_that_no_longer_fits_and_asks_the_catalog() -> None:
    choices = _Choices()
    service = _with_choices(_service({1: _evaluation(_match("A"), _match("B"))}), choices)
    shown = service.lookup_vehicle("V1")
    choices.heads["V1"] = (_head(shown, ktype="B", catalog_batch="older-batch"), 1)

    stale = service.lookup_vehicle("V1").choice
    assert stale is not None and stale.stale_reasons == ["catalog_batch_changed"]

    # "A" and "B" are the scripted catalog; a KType outside it is "not in the catalog".
    gone_evidence = dict(shown.model_dump(mode="json"), candidates=[{"ktype": "Z"}])
    choices.heads["V1"] = (_head(shown, ktype="Z", evidence=gone_evidence), 1)
    gone = service.lookup_vehicle("V1").choice
    assert gone is not None and "ktype_not_in_catalog" in gone.stale_reasons


def test_a_vehicle_lookup_reads_the_choice_once_and_a_record_lookup_never() -> None:
    choices = _Choices()
    service = _with_choices(
        _service({1: _evaluation(_match("A")), 2: _evaluation(_match("A")), 9: _evaluation()}),
        choices,
    )

    service.lookup_vehicle("V1")
    service.lookup("ABC123")
    assert choices.reads == ["V1", "V1"]

    by_record = service.lookup_record(9)
    assert by_record.choice is None and len(by_record.evidence_fingerprint) == 64
    job = service.start_summary([], "", 10, run_in_background=False)
    assert job.evaluated == 3
    assert choices.reads == ["V1", "V1"]


def test_a_failing_choice_read_fails_the_lookup() -> None:
    """A choice is never silently hidden: the router answers 503."""

    import psycopg

    service = _with_choices(
        _service({1: _evaluation(_match("A"))}), _Choices(error=psycopg.OperationalError("down"))
    )

    with pytest.raises(psycopg.OperationalError):
        service.lookup_vehicle("V1")


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
    assert summary.examples["several"][0].vehicle_id == "V2"


def test_the_verdict_is_the_routing_gates_own_explanation() -> None:
    gate = {"signal": "routing_gate", "value": "review_required",
            "explanation": "The top candidates are too close to separate safely."}
    routed = MatchEvaluation(
        "review_required", ("route:candidate_margin_below_gate",),
        decision_trace=({"signal": "text_similarity", "explanation": "Stage 2"}, gate),
    )

    assert verdict_of(routed) == "The top candidates are too close to separate safely."
    assert verdict_of(_evaluation(scored=False)) is None


def test_summary_lists_every_car_of_a_bucket_with_what_stopped_it() -> None:
    outcomes = {
        1: _evaluation(_match("A")),
        2: _evaluation(_match("A"), _match("B")),
        3: _evaluation(_match("A", conflicts=("bodywork",))),
        4: _evaluation(),
        5: _evaluation(scored=False),
        6: _evaluation(_match("A"), _match("B")),
    }
    service = _service(outcomes)
    job = service.start_summary([], "", 10, run_in_background=False)

    several = service.summary_cars(job.job_id, "several", offset=0, limit=50)
    assert several.total == 2
    assert [car.plate for car in several.cars] == ["P2", "P6"]
    first = several.cars[0]
    assert (first.vehicle_id, first.candidates, first.terminal) == ("V2", 2, "resolved")
    assert "engine_code" in first.separating_fields
    assert first.missing_fields == ["engine_code"]

    none = service.summary_cars(job.job_id, "none", offset=0, limit=50)
    assert [(car.plate, car.conflicting_fields) for car in none.cars] == [
        ("P3", ["bodywork"]), ("P4", []),
    ]
    stopped = service.summary_cars(job.job_id, "not_matchable", offset=0, limit=50).cars[0]
    assert (stopped.reason_codes, stopped.verdict) == (["policy:x"], None)

    page = service.summary_cars(job.job_id, "several", offset=1, limit=1)
    assert (page.total, page.offset, [car.plate for car in page.cars]) == (2, 1, ["P6"])


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

    service._run(job, ["V1"])

    assert service.summary_job(job.job_id).status == "cancelled"


def test_a_failing_summary_reports_why_instead_of_dying_silently() -> None:
    jobs = SummaryJobs()
    service = _service({}, jobs)  # no scripted outcome: evaluate raises KeyError
    job = jobs.create(population=1, target=1)

    service._run(job, ["V42"])

    snapshot = service.summary_job(job.job_id)
    assert snapshot.status == "failed"
    assert snapshot.error is not None and snapshot.error.startswith("KeyError")


def test_running_summaries_are_capped() -> None:
    jobs = SummaryJobs(max_running=1)
    jobs.create(population=1, target=1)

    with pytest.raises(JobCapacityError):
        jobs.create(population=1, target=1)


def test_jobs_are_listed_newest_first_with_the_filter_they_ran_on() -> None:
    """A job outlives its screen; the list is how a reopened view finds it again."""

    jobs = SummaryJobs()
    service = _service({1: _evaluation(_match("A"))}, jobs)
    volvo = VehicleFilter(conditions=[VehicleCondition(field="manufacturer", values=["Volvo"])])
    finished = service.start_summary([], "", 1, vehicle_filter=volvo, run_in_background=False)
    running = jobs.create(population=1, target=1)

    listed = service.summary_jobs()

    assert [(job.job_id, job.status) for job in listed] == [
        (running.job_id, "running"),
        (finished.job_id, "done"),
    ]
    assert listed[1].filter == volvo
    assert listed[0].filter is None


def test_an_unknown_job_is_reported_not_invented() -> None:
    with pytest.raises(SummaryJobNotFoundError):
        SummaryJobs().get("nope")


# ------------------------------------------------------------------------------ HTTP


def test_http_maps_the_services_errors(client: TestClient) -> None:
    jobs = SummaryJobs()  # one per process in the app, as `_jobs()` caches it
    client.app.dependency_overrides[get_service] = lambda: _service({1: _evaluation()}, jobs)  # type: ignore[attr-defined]
    try:
        assert client.get("/v1/vehicles/matching/lookup", params={"q": "NOPE"}).status_code == 404
        assert client.get("/v1/vehicles/matching/lookup").status_code == 422
        both = {"q": "ABC123", "source_record_id": 1}
        assert client.get("/v1/vehicles/matching/lookup", params=both).status_code == 422
        by_record = client.get("/v1/vehicles/matching/lookup", params={"source_record_id": 1})
        assert by_record.status_code == 200
        by_vehicle = client.get("/v1/vehicles/matching/lookup", params={"vehicle_id": "V1"})
        assert by_vehicle.json()["vehicle_id"] == "V1"
        unknown = client.get("/v1/vehicles/matching/lookup", params={"vehicle_id": "V404"})
        assert unknown.status_code == 404
        assert {"evidence_fingerprint", "choice", "effective_ktype", "effective_source"} <= set(
            by_vehicle.json()
        )
        layered = {"conditions": [{"field": "fuel", "operator": "gte", "values": ["1", "2"]}]}
        assert client.post("/v1/vehicles/matching/summary", json=layered).status_code == 422
        assert client.get("/v1/vehicles/matching/summary/nope").status_code == 404
        started = client.post("/v1/vehicles/matching/summary", json={"conditions": [], "limit": 1})
        assert started.status_code == 202
        assert started.json()["target"] == 1
        assert started.json()["filter"] == {"conditions": [], "text": ""}
        listed = client.get("/v1/vehicles/matching/summary")
        assert listed.status_code == 200
        assert [job["job_id"] for job in listed.json()] == [started.json()["job_id"]]
        too_many = client.post("/v1/vehicles/matching/summary", json={"conditions": [], "limit": 50_000})
        assert too_many.status_code == 422
        cars_url = f"/v1/vehicles/matching/summary/{started.json()['job_id']}/cars"
        cars = client.get(cars_url, params={"bucket": "none"})
        assert cars.status_code == 200
        assert cars.json()["total"] == 1
        assert cars.json()["cars"][0]["vehicle_id"] == "V1"
        assert client.get(cars_url, params={"bucket": "maybe"}).status_code == 422
        assert client.get(cars_url).status_code == 422
        assert client.get("/v1/vehicles/matching/summary/nope/cars",
                          params={"bucket": "one"}).status_code == 404
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


def test_a_car_without_a_ts_record_offers_its_registry_text_as_evidence() -> None:
    from api.app.features.vehicle_matching.repository import (
        EVIDENCE_FALLBACK,
        MATCHER_FIELDS,
        _vehicle_car_record,
    )

    def row(raw: dict[str, Any] | None) -> tuple[Any, ...]:
        matcher_values = [None] * len(MATCHER_FIELDS)
        matcher_values[MATCHER_FIELDS.index("manufacturer")] = "Volvo"
        registry = {"registry_brand_text": "VOLVO XC40 RECHARGE", "registry_model_text": "XC40",
                    "variant_code": "XK", "version_code": None, "registry_type_code": "X"}
        return (
            "NOR-01ARZ3NDEKTSV4RRFFQ69G5FAV", "ABC123", "YV1XZ", None, "ais", "resolved", {},
            *matcher_values, None, None, None, raw,
            *(registry[name] for name in EVIDENCE_FALLBACK.values()),
        )

    ais_only = _vehicle_car_record(row(None)).record.payload["source_evidence"]
    with_ts = _vehicle_car_record(row({"brand": "VOLVO", "model": ""})).record.payload["source_evidence"]

    assert (ais_only["brand"], ais_only["model"], ais_only["variant"]) == (
        "VOLVO XC40 RECHARGE", "XC40", "XK"
    )
    assert ais_only["version"] is None
    # A TS record's own text wins; an empty TS field still falls back.
    assert (with_ts["brand"], with_ts["model"]) == ("VOLVO", "XC40")
