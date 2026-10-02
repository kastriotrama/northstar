"""The HTTP surface of one correction for many cars: routes, status codes, error codes."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import psycopg
import pytest
from correction_test_support import VEHICLE_ID
from fastapi.testclient import TestClient
from many_cars_test_support import ScriptedMatcher, car, plan

from api.app.features.vehicle_corrections.decision_service import (
    DecisionChangedError,
    DecisionNotFoundError,
    DecisionRejectedError,
    EvidenceChangedError,
    HarmsMoreThanItFixesError,
    InvalidScopeError,
    NothingToApplyError,
    NothingToWithdrawError,
    OperationReusedError,
    PreviewBusyError,
    PreviewExpiredError,
    PreviewIncompleteError,
    PreviewNotFoundError,
    ScopeNotOfferedError,
    ScopeTooBroadError,
    VehiclesBusyError,
)
from api.app.features.vehicle_corrections.preview import PreviewJobs, run_check
from api.app.features.vehicle_corrections.router import get_decision_service
from api.app.features.vehicle_corrections.schemas import (
    DecisionList,
    DecisionResult,
    DecisionWithdrawal,
    PreviewCounts,
    ScopeOption,
    ScopeOptions,
    SkippedCars,
)
from api.app.features.vehicle_corrections.service import (
    CorrectionVehicleNotFoundError,
    FieldNotCorrectableError,
    InvalidValueError,
    InvalidVehicleIdError,
    NothingToIgnoreError,
    ReasonRequiredError,
    ValueUnchangedError,
)
from api.app.features.vehicle_matching.service import NoCatalogError, VehicleNotFoundError
from api.app.main import create_app

SCOPES = f"/v1/vehicles/{VEHICLE_ID}/corrections/scopes"
PREVIEW = f"/v1/vehicles/{VEHICLE_ID}/corrections/preview"
BASE = "/v1/vehicle-corrections"


class _Service:
    """Stands in for the decision service: scripted answers, or one scripted error."""

    def __init__(self) -> None:
        self.error: Exception | None = None
        self.created = True
        self.calls: list[tuple[Any, ...]] = []
        self.jobs = PreviewJobs()
        self.job = self.jobs.create(plan(), affected=2, cap=500)
        cars = {str(item.vehicle_id): item for item in (car(1), car(2, drive_type="rwd"))}
        run_check(
            self.job, sorted(cars),
            read_page=lambda ids: type("P", (), {"cars": [cars[i] for i in ids], "choices": {}})(),
            matcher=lambda: ScriptedMatcher(), max_seconds=60,
        )

    def _call(self, *call: Any) -> None:
        if self.error:
            raise self.error
        self.calls.append(call)

    def scopes(self, vehicle_id: str, request: Any) -> ScopeOptions:
        self._call("scopes", vehicle_id, request)
        return ScopeOptions(scopes=[ScopeOption(kind="this_car", label="Only this car", count=1)])

    def start_preview(self, vehicle_id: str, request: Any) -> Any:
        self._call("start", vehicle_id, request)
        return self.jobs.snapshot(self.job)

    def preview(self, preview_id: str) -> Any:
        self._call("preview", preview_id)
        return self.jobs.snapshot(self.job)

    def preview_cars(self, preview_id: str, outcome: Any, *, offset: int, limit: int) -> Any:
        self._call("cars", preview_id, outcome, offset, limit)
        return self.jobs.cars(self.job, outcome, offset=offset, limit=limit)

    def stop_preview(self, preview_id: str) -> Any:
        self._call("stop", preview_id)
        return self.jobs.snapshot(self.job)

    def decide(self, request: Any) -> tuple[DecisionResult, bool]:
        self._call("decide", request)
        return DecisionResult(
            decision_id=request.operation_id, status="applied", written=1,
            skipped=SkippedCars(changed_since_check=1), counts=PreviewCounts(gained=1),
            scope_label="All VOLVO V70 cars with no drive type",
            written_by_outcome={"gained": 1},
        ), self.created

    def withdraw(self, decision_id: Any, request: Any) -> tuple[DecisionWithdrawal, bool]:
        self._call("withdraw", decision_id, request)
        return DecisionWithdrawal(
            decision_id=decision_id, status="withdrawn", withdrawn=1, left_changed=0,
            member_count=1, scope_label="x", skipped={"changed_by_person": 0},
        ), self.created

    def decisions(self, limit: int) -> DecisionList:
        self._call("list", limit)
        return DecisionList(decisions=[])

    def decision(self, decision_id: Any) -> Any:
        self._call("one", decision_id)
        raise DecisionNotFoundError(str(decision_id))


@pytest.fixture
def fake() -> _Service:
    return _Service()


@pytest.fixture
def client(fake: _Service) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_decision_service] = lambda: fake
    return TestClient(app)


def _code(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["detail"]["code"]


_CORRECTION = {"field": "drive_type", "action": "set", "value": "fwd"}
_CHECK = {**_CORRECTION, "scope": {"kind": "like_this", "rung": 1, "narrow": []},
          "evidence_fingerprint": "a" * 64}


def _decision(**overrides: Any) -> dict[str, Any]:
    return {"operation_id": str(uuid4()), "preview_id": "abc", "event": "apply",
            "include_changed": False, "reviewer": "Ada", "reason": "checked", **overrides}


def test_the_routes_and_what_they_answer(client: TestClient, fake: _Service) -> None:
    scopes = client.post(SCOPES, json=_CORRECTION)
    assert scopes.status_code == 200
    assert scopes.json() == {"scopes": [{
        "kind": "this_car", "label": "Only this car", "count": 1, "too_broad": False,
        "rung": None, "conditions": [], "narrowable": [],
    }]}

    started = client.post(PREVIEW, json=_CHECK)
    assert started.status_code == 202
    job = started.json()
    assert set(job) == {
        "preview_id", "status", "field", "action", "value", "scope", "affected", "cap",
        "checked", "complete", "stopped_by", "counts", "engine_check", "would_write",
        "can_apply", "blocked_by", "catalog_batch", "seconds_elapsed", "error",
    }
    assert set(job["counts"]) == {
        "gained", "lost", "moved", "worse", "same", "still_unresolved", "no_effect",
        "already_corrected", "not_like_this", "with_choice", "choice_would_disagree",
        "still_unresolved_by_terminal",
    }
    assert (job["status"], job["checked"], job["complete"], job["would_write"]) == (
        "done", 2, True, 1)
    assert job["scope"]["label"] == "All VOLVO V70 cars with no drive type"
    preview = f"{BASE}/previews/{job['preview_id']}"
    assert client.get(preview).json() == job
    assert client.delete(preview).json() == job

    cars = client.get(f"{preview}/cars", params={"outcome": "gained", "limit": 5}).json()
    assert set(cars) == {"preview_id", "outcome", "total", "offset", "cars"}
    assert (cars["total"], cars["outcome"]) == (1, "gained")
    assert cars["cars"][0] == {
        "vehicle_id": str(car(1).vehicle_id), "plate": "TST001", "outcome": "gained",
        "before": {"terminal": "review_required", "ktype": None},
        "after": {"terminal": "resolved", "ktype": "K1"},
    }
    assert client.get(f"{preview}/cars").json()["total"] == 2
    assert client.get(f"{preview}/cars", params={"outcome": "nonsense"}).status_code == 422
    assert client.get(f"{preview}/cars", params={"limit": 0}).status_code == 422

    body = _decision()
    decided = client.post(f"{BASE}/decisions", json=body)
    assert decided.status_code == 201
    answer = decided.json()
    assert set(answer) == {"decision_id", "status", "written", "skipped", "counts",
                           "scope_label", "written_by_outcome"}
    assert (answer["decision_id"], answer["status"], answer["written"], answer["skipped"]) == (
        body["operation_id"], "applied", 1,
        {"changed_since_check": 1, "corrected_meanwhile": 0})

    decision_id = body["operation_id"]
    undone = client.post(f"{BASE}/decisions/{decision_id}/withdraw", json={
        "operation_id": str(uuid4()), "reviewer": "Bo", "reason": "wrong group"})
    assert undone.status_code == 201
    assert undone.json() == {
        "decision_id": decision_id, "status": "withdrawn", "withdrawn": 1, "left_changed": 0,
        "member_count": 1, "scope_label": "x", "skipped": {"changed_by_person": 0},
    }
    assert client.get(f"{BASE}/decisions", params={"limit": 5}).json() == {"decisions": []}
    assert _code(client.get(f"{BASE}/decisions/{decision_id}")) == (404, "decision_not_found")
    assert client.get(f"{BASE}/decisions/not-a-uuid").status_code == 422

    fake.created = False
    assert client.post(f"{BASE}/decisions", json=_decision()).status_code == 200
    assert client.post(f"{BASE}/decisions/{decision_id}/withdraw", json={
        "operation_id": str(uuid4()), "reviewer": "Bo", "reason": "again"}).status_code == 200


_ANCHOR_REFUSALS = [
    (InvalidVehicleIdError("x"), 422, "invalid_vehicle_id"),
    (VehicleNotFoundError("x"), 404, "vehicle_not_found"),
    (CorrectionVehicleNotFoundError("x"), 404, "vehicle_not_found"),
    (FieldNotCorrectableError("x"), 422, "field_not_correctable"),
    (InvalidValueError("x"), 422, "invalid_value"),
    (ValueUnchangedError("x"), 422, "value_unchanged"),
    (NothingToIgnoreError("x"), 422, "nothing_to_ignore"),
    (NoCatalogError("x"), 503, "unavailable"),
    (psycopg.OperationalError("connection details"), 503, "unavailable"),
]


@pytest.mark.parametrize(("error", "status", "code"), _ANCHOR_REFUSALS)
def test_the_scopes_call_refuses_as_one_cars_correction_does(
    client: TestClient, fake: _Service, error: Exception, status: int, code: str
) -> None:
    fake.error = error

    response = client.post(SCOPES, json=_CORRECTION)

    assert _code(response) == (status, code)
    assert "connection details" not in response.text


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        *_ANCHOR_REFUSALS,
        (PreviewBusyError("x"), 429, "busy"),
        (EvidenceChangedError("x"), 409, "evidence_changed"),
        (ScopeTooBroadError("x"), 422, "scope_too_broad"),
        (ScopeNotOfferedError("x"), 422, "scope_not_offered"),
        (InvalidScopeError("x"), 422, "invalid_scope"),
    ],
)
def test_each_refusal_of_a_check_has_its_status_and_code(
    client: TestClient, fake: _Service, error: Exception, status: int, code: str
) -> None:
    fake.error = error

    assert _code(client.post(PREVIEW, json=_CHECK)) == (status, code)


def test_an_unknown_check_is_a_404_on_every_route(client: TestClient, fake: _Service) -> None:
    fake.error = PreviewNotFoundError("gone")

    assert _code(client.get(f"{BASE}/previews/x")) == (404, "preview_not_found")
    assert _code(client.get(f"{BASE}/previews/x/cars")) == (404, "preview_not_found")
    assert _code(client.delete(f"{BASE}/previews/x")) == (404, "preview_not_found")
    assert _code(client.post(f"{BASE}/decisions", json=_decision())) == (404, "preview_not_found")


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (PreviewExpiredError("x"), 409, "preview_expired"),
        (PreviewIncompleteError("x"), 409, "preview_incomplete"),
        (OperationReusedError("x"), 409, "operation_id_reused"),
        (ReasonRequiredError("x"), 422, "reason_required"),
        (HarmsMoreThanItFixesError("x"), 422, "harms_more_than_it_fixes"),
        (NothingToApplyError("x"), 422, "nothing_to_apply"),
        (DecisionRejectedError("x"), 422, "not_storable"),
        (VehiclesBusyError("x"), 503, "vehicles_busy"),
        (psycopg.OperationalError("connection details"), 503, "unavailable"),
    ],
)
def test_each_refusal_of_a_decision_has_its_status_and_code(
    client: TestClient, fake: _Service, error: Exception, status: int, code: str
) -> None:
    fake.error = error

    response = client.post(f"{BASE}/decisions", json=_decision())

    assert _code(response) == (status, code)
    assert "connection details" not in response.text


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (DecisionNotFoundError("x"), 404, "decision_not_found"),
        (OperationReusedError("x"), 409, "operation_id_reused"),
        (DecisionChangedError("x"), 409, "decision_changed"),
        (ReasonRequiredError("x"), 422, "reason_required"),
        (NothingToWithdrawError("x"), 422, "nothing_to_withdraw"),
        (DecisionRejectedError("x"), 422, "not_storable"),
        (VehiclesBusyError("x"), 503, "vehicles_busy"),
        (psycopg.OperationalError("down"), 503, "unavailable"),
    ],
)
def test_each_refusal_of_a_withdrawal_has_its_status_and_code(
    client: TestClient, fake: _Service, error: Exception, status: int, code: str
) -> None:
    fake.error = error

    response = client.post(f"{BASE}/decisions/{uuid4()}/withdraw", json={
        "operation_id": str(uuid4()), "reviewer": "Bo", "reason": "why"})

    assert _code(response) == (status, code)


def test_malformed_bodies_reach_no_service(client: TestClient, fake: _Service) -> None:
    assert client.post(SCOPES, json={**_CORRECTION, "action": "withdraw"}).status_code == 422
    assert client.post(PREVIEW, json={**_CHECK, "scope": {"kind": "this_car"}}).status_code == 422
    assert client.post(f"{BASE}/decisions", json=_decision(event="withdraw")).status_code == 422
    assert client.post(f"{BASE}/decisions", json=_decision(reviewer=" ")).status_code == 422
    assert fake.calls == []


def test_the_real_wiring_builds_the_service_from_the_settings() -> None:
    from api.app.core.settings import Settings
    from api.app.features.vehicle_corrections import router

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (settings.correction_preview_max_cars, settings.correction_preview_max_seconds) == (
        500, 240)
    paths = create_app().openapi()["paths"]
    assert {"/v1/vehicles/{vehicle_id}/corrections/scopes",
            "/v1/vehicles/{vehicle_id}/corrections/preview",
            "/v1/vehicle-corrections/previews/{preview_id}",
            "/v1/vehicle-corrections/previews/{preview_id}/cars",
            "/v1/vehicle-corrections/decisions",
            "/v1/vehicle-corrections/decisions/{decision_id}",
            "/v1/vehicle-corrections/decisions/{decision_id}/withdraw"} <= set(paths)
    assert set(paths["/v1/vehicle-corrections/previews/{preview_id}"]) == {"get", "delete"}
    assert router._previews() is router._previews()  # one set of checks per process
