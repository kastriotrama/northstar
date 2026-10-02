"""The HTTP surface: status codes, error codes and the routes themselves."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import psycopg
import pytest
from correction_test_support import VEHICLE_ID, lookup, row
from fastapi.testclient import TestClient

from api.app.features.vehicle_corrections.router import get_correction_service
from api.app.features.vehicle_corrections.schemas import (
    CorrectionFieldHistory,
    CorrectionHistory,
    CorrectionHistoryEntry,
    CorrectionRequest,
)
from api.app.features.vehicle_corrections.service import (
    CorrectionChangedError,
    CorrectionRejectedError,
    CorrectionVehicleNotFoundError,
    EvidenceChangedError,
    FieldNotCorrectableError,
    InvalidValueError,
    InvalidVehicleIdError,
    NothingToIgnoreError,
    NothingToWithdrawError,
    OperationReusedError,
    ReasonRequiredError,
    ValueUnchangedError,
    VehicleBusyError,
)
from api.app.features.vehicle_matching.schemas import VehicleMatchLookup
from api.app.features.vehicle_matching.service import NoCatalogError, VehicleNotFoundError
from ingestion.vehicle_fact_corrections import CorrectionHead

URL = f"/v1/vehicles/{VEHICLE_ID}/corrections"


class _Service:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.created = True
        self.requests: list[tuple[str, CorrectionRequest]] = []
        self.history_calls: list[str] = []

    def record(
        self, vehicle_id: str, request: CorrectionRequest
    ) -> tuple[VehicleMatchLookup, bool]:
        if self.error:
            raise self.error
        self.requests.append((vehicle_id, request))
        head = row(correction_id=request.operation_id)
        heads = {"engine_code": CorrectionHead("set", "DFGA", head.correction_id)}
        return lookup(heads=heads, details={"engine_code": (head, 1)}), self.created

    def history(self, vehicle_id: str) -> CorrectionHistory:
        if self.error:
            raise self.error
        self.history_calls.append(vehicle_id)
        head = row()
        entry = CorrectionHistoryEntry(
            correction_id=head.correction_id, action="set", value="DFGA", reviewer="Ada",
            reason=None, created_at=head.created_at, supersedes_correction_id=None,
            previous_value="DPCA", previous_source="review", group_id=None,
            catalog_batch="batch-1", automatic_terminal="review_required", automatic_ktype="A",
            code_version="abc1234",
        )
        return CorrectionHistory(
            vehicle_id=vehicle_id,
            fields=[
                CorrectionFieldHistory(
                    field="engine_code", current_correction_id=head.correction_id, entries=[entry]
                )
            ],
        )


@pytest.fixture
def fake(client: TestClient) -> Any:
    service = _Service()
    client.app.dependency_overrides[get_correction_service] = lambda: service  # type: ignore[attr-defined]
    yield service
    client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "operation_id": str(uuid4()), "field": "engine_code", "action": "set", "value": "DFGA",
        "reviewer": "Ada", "reason": None, "supersedes_correction_id": None,
        "evidence_fingerprint": "a" * 64,
    }
    body.update(overrides)
    return body


def test_a_recorded_correction_answers_201_with_the_refreshed_lookup(
    client: TestClient, fake: _Service
) -> None:
    body = _body()

    response = client.post(URL, json=body)

    assert response.status_code == 201
    payload = response.json()
    assert payload["vehicle_id"] == VEHICLE_ID
    assert len(payload["evidence_fingerprint"]) == 64
    assert payload["inputs"]["engine_code"] == "DFGA"
    assert payload["overlaid_fields"]["engine_code"] == "correction"
    (correction,) = payload["corrections"]
    assert set(correction) == {
        "field", "status", "correction_id", "value", "reviewer", "reason", "created_at",
        "previous_value", "previous_source", "group_id", "history_count",
    }
    assert correction["correction_id"] == body["operation_id"]
    assert (correction["field"], correction["status"], correction["value"]) == (
        "engine_code", "set", "DFGA")
    assert [item["field"] for item in payload["correctable_fields"]] == [
        "manufacturer", "model_family", "engine_code", "power_kw", "displacement_cc",
        "drive_type", "bodywork_form", "production_year", "production_month", "fuel",
        "electrification_type",
    ]
    assert payload["stop_reasons"] == []
    fuel = payload["correctable_fields"][9]
    assert (fuel["type"], fuel["current_value"], fuel["evidence_keys"]) == (
        "list", "petrol,electricity", ["fuels"])
    assert fuel["values"][:3] == ["petrol", "diesel", "electricity"]
    engine = payload["correctable_fields"][2]
    assert engine == {
        "field": "engine_code", "label": "Engine code", "type": "text", "values": [],
        "evidence_keys": ["engine_code"], "current_value": "DFGA",
        "current_source": "correction", "suggestions": ["DPCA", "DTSA"],
    }
    (sent,) = fake.requests
    assert sent[0] == VEHICLE_ID and str(sent[1].operation_id) == body["operation_id"]


def test_a_replay_answers_200(client: TestClient, fake: _Service) -> None:
    fake.created = False

    assert client.post(URL, json=_body()).status_code == 200


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (VehicleNotFoundError("no"), 404, "vehicle_not_found"),
        (CorrectionVehicleNotFoundError("no"), 404, "vehicle_not_found"),
        (OperationReusedError("x"), 409, "operation_id_reused"),
        (CorrectionChangedError("x"), 409, "correction_changed"),
        (EvidenceChangedError("x"), 409, "evidence_changed"),
        (InvalidVehicleIdError("x"), 422, "invalid_vehicle_id"),
        (FieldNotCorrectableError("x"), 422, "field_not_correctable"),
        (InvalidValueError("x"), 422, "invalid_value"),
        (ValueUnchangedError("x"), 422, "value_unchanged"),
        (ReasonRequiredError("x"), 422, "reason_required"),
        (NothingToIgnoreError("x"), 422, "nothing_to_ignore"),
        (NothingToWithdrawError("x"), 422, "nothing_to_withdraw"),
        # The database refused the content itself: the same request would fail again.
        (CorrectionRejectedError("x"), 422, "not_storable"),
        (psycopg.errors.DataError("connection details"), 422, "not_storable"),
        (psycopg.errors.CheckViolation("connection details"), 422, "not_storable"),
        # Only these are worth a retry.
        (VehicleBusyError("x"), 503, "vehicle_busy"),
        (NoCatalogError("x"), 503, "unavailable"),
        (psycopg.OperationalError("connection details"), 503, "unavailable"),
        # A database without the table (migration not run yet): loud, never a
        # car answered as if nobody had corrected it.
        (psycopg.errors.UndefinedTable("relation core.vehicle_fact_corrections"), 503,
         "unavailable"),
    ],
)
def test_each_refusal_has_its_status_and_code(
    client: TestClient, fake: _Service, error: Exception, status: int, code: str
) -> None:
    fake.error = error

    response = client.post(URL, json=_body())

    assert response.status_code == status
    detail = response.json()["detail"]
    assert detail["code"] == code and detail["message"]
    assert "connection details" not in detail["message"]
    assert "relation" not in detail["message"]


@pytest.mark.parametrize(
    "body",
    [
        _body(action="ignore"),  # a value with an action that takes none
        _body(action="withdraw", value="DFGA"),
        _body(action="ignore", value=None, evidence_fingerprint=None),
        _body(evidence_fingerprint=None),
        _body(value=150),
        _body(reviewer=" "),
        _body(operation_id="nope"),
        _body(action="replace"),
        # A control character cannot be stored: a 422, not a "try again".
        _body(value="DF\u0000GA"),
        _body(reviewer="A\u0000da"),
        _body(reason="bad\u0000byte"),
        {},
    ],
)
def test_a_malformed_body_is_a_422_and_reaches_no_service(
    client: TestClient, fake: _Service, body: dict[str, Any]
) -> None:
    assert client.post(URL, json=body).status_code == 422
    assert fake.requests == []


def test_a_value_the_service_must_judge_reaches_it(client: TestClient, fake: _Service) -> None:
    """Blank and overlong values are the service's to refuse, with a code the screen can read."""

    for value in (None, "", "x" * 5000):
        assert client.post(URL, json=_body(value=value)).status_code == 201
    assert [sent.value for _, sent in fake.requests] == [None, "", "x" * 5000]
    assert client.post(URL, json=_body(field="nonsense")).status_code == 201


def test_history_is_returned_by_field(client: TestClient, fake: _Service) -> None:
    response = client.get(URL)

    assert response.status_code == 200
    assert fake.history_calls == [VEHICLE_ID]
    payload = response.json()
    assert set(payload) == {"vehicle_id", "fields"}
    (field,) = payload["fields"]
    assert set(field) == {"field", "current_correction_id", "entries"}
    assert field["current_correction_id"] == field["entries"][0]["correction_id"]
    assert set(field["entries"][0]) == {
        "correction_id", "action", "value", "reviewer", "reason", "created_at",
        "supersedes_correction_id", "previous_value", "previous_source", "group_id",
        "catalog_batch", "automatic_terminal", "automatic_ktype", "code_version",
    }


def test_history_refusals(client: TestClient, fake: _Service) -> None:
    fake.error = InvalidVehicleIdError("x")
    response = client.get(URL)
    assert (response.status_code, response.json()["detail"]["code"]) == (422, "invalid_vehicle_id")
    fake.error = psycopg.OperationalError("down")
    response = client.get(URL)
    assert (response.status_code, response.json()["detail"]["code"]) == (503, "unavailable")


def test_the_correction_routes_do_not_shadow_the_vehicle_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert set(paths["/v1/vehicles/{vehicle_id}/corrections"]) == {"get", "post"}
    assert set(paths["/v1/vehicles/{vehicle_id}/ktype-choices"]) == {"get", "post"}
    assert "/v1/vehicles/matching/lookup" in paths
    assert "/v1/vehicles/{vehicle_id}" in paths
    lookup_schema = client.get("/openapi.json").json()["components"]["schemas"]["VehicleMatchLookup"]
    added = {"corrections", "correctable_fields", "stop_reasons"}
    assert added <= set(lookup_schema["properties"])
    assert not added & set(lookup_schema["required"])


def test_the_real_wiring_resolves_and_refuses_a_bad_id_before_any_database(
    client: TestClient,
) -> None:
    """No override: the correction service is built on the matching service's lookup."""

    response = client.post("/v1/vehicles/matching/corrections", json=_body())

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_vehicle_id"
    assert client.get("/v1/vehicles/ABC123/corrections").status_code == 422
