"""The HTTP surface: status codes, error codes and the routes themselves."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import psycopg
import pytest
from choice_test_support import VEHICLE_ID, lookup, stored
from fastapi.testclient import TestClient

from api.app.features.vehicle_ktype_choices import evidence
from api.app.features.vehicle_ktype_choices.router import get_choice_service
from api.app.features.vehicle_ktype_choices.schemas import (
    KTypeChoiceHistory,
    KTypeChoiceHistoryEntry,
    KTypeChoiceRequest,
)
from api.app.features.vehicle_ktype_choices.service import (
    ChoiceChangedError,
    ChoiceRejectedError,
    ChoiceVehicleNotFoundError,
    EvidenceChangedError,
    InvalidVehicleIdError,
    KTypeNotACandidateError,
    NothingToWithdrawError,
    OperationReusedError,
    VehicleBusyError,
)
from api.app.features.vehicle_matching.schemas import VehicleMatchLookup
from api.app.features.vehicle_matching.service import NoCatalogError, VehicleNotFoundError

URL = f"/v1/vehicles/{VEHICLE_ID}/ktype-choices"


class _Service:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.created = True
        self.requests: list[tuple[str, KTypeChoiceRequest]] = []
        self.history_calls: list[tuple[str, bool]] = []

    def record(
        self, vehicle_id: str, request: KTypeChoiceRequest
    ) -> tuple[VehicleMatchLookup, bool]:
        if self.error:
            raise self.error
        self.requests.append((vehicle_id, request))
        shown = lookup()
        row = stored(shown, choice_id=request.operation_id)
        state = evidence.assess(row, shown, True, 1)
        return (
            shown.model_copy(
                update={"choice": state, "effective_ktype": "A", "effective_source": "person"}
            ),
            self.created,
        )

    def history(self, vehicle_id: str, *, with_evidence: bool = False) -> KTypeChoiceHistory:
        if self.error:
            raise self.error
        self.history_calls.append((vehicle_id, with_evidence))
        row = stored(lookup())
        entry = KTypeChoiceHistoryEntry(
            choice_id=row.choice_id, action="choose", ktype="A", reviewer="Ada", reason=None,
            created_at=row.created_at, supersedes_choice_id=None, catalog_batch="batch-1",
            automatic_terminal="review_required", automatic_ktype="A", code_version="abc1234",
            evidence=row.evidence if with_evidence else None,
        )
        return KTypeChoiceHistory(
            vehicle_id=vehicle_id, current_choice_id=row.choice_id, entries=[entry]
        )


@pytest.fixture
def fake(client: TestClient) -> Any:
    service = _Service()
    client.app.dependency_overrides[get_choice_service] = lambda: service  # type: ignore[attr-defined]
    yield service
    client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "operation_id": str(uuid4()), "action": "choose", "ktype": "A", "reviewer": "Ada",
        "reason": None, "supersedes_choice_id": None, "evidence_fingerprint": "a" * 64,
    }
    body.update(overrides)
    return body


def test_a_recorded_choice_answers_201_with_the_refreshed_lookup(
    client: TestClient, fake: _Service
) -> None:
    body = _body()

    response = client.post(URL, json=body)

    assert response.status_code == 201
    payload = response.json()
    assert payload["vehicle_id"] == VEHICLE_ID
    assert payload["evidence_fingerprint"] == lookup().evidence_fingerprint
    assert (payload["effective_ktype"], payload["effective_source"]) == ("A", "person")
    assert set(payload["choice"]) == {
        "status", "choice_id", "ktype", "reviewer", "reason", "created_at", "catalog_batch",
        "automatic_terminal", "automatic_ktype", "chosen_candidate", "needs_review",
        "stale_reasons", "changed_inputs", "history_count",
    }
    assert payload["choice"]["choice_id"] == body["operation_id"]
    assert payload["choice"]["status"] == "chosen"
    assert payload["choice"]["chosen_candidate"]["ktype"] == "A"
    (sent,) = fake.requests
    assert sent[0] == VEHICLE_ID and str(sent[1].operation_id) == body["operation_id"]


def test_a_replay_answers_200(client: TestClient, fake: _Service) -> None:
    fake.created = False

    assert client.post(URL, json=_body()).status_code == 200


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (VehicleNotFoundError("no"), 404, "vehicle_not_found"),
        (ChoiceVehicleNotFoundError("no"), 404, "vehicle_not_found"),
        (OperationReusedError("x"), 409, "operation_id_reused"),
        (ChoiceChangedError("x"), 409, "choice_changed"),
        (EvidenceChangedError("x"), 409, "evidence_changed"),
        (InvalidVehicleIdError("x"), 422, "invalid_vehicle_id"),
        (KTypeNotACandidateError("x"), 422, "ktype_not_a_candidate"),
        (NothingToWithdrawError("x"), 422, "nothing_to_withdraw"),
        (VehicleBusyError("x"), 503, "vehicle_busy"),
        (NoCatalogError("x"), 503, "unavailable"),
        (psycopg.OperationalError("connection details"), 503, "unavailable"),
        (psycopg.errors.UndefinedTable("connection details"), 503, "unavailable"),
        # Permanent refusals are never "try again": the same request would fail again.
        (ChoiceRejectedError("That value cannot be stored."), 422, "not_storable"),
        (psycopg.DataError("connection details"), 422, "not_storable"),
        (psycopg.IntegrityError("connection details"), 422, "not_storable"),
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


@pytest.mark.parametrize(
    "body",
    [
        _body(ktype=None),
        _body(action="none"),
        _body(action="none", ktype=None, evidence_fingerprint=None),
        _body(reviewer=" "),
        # PostgreSQL text cannot hold NUL: refused as a bad request, not as "try again".
        _body(reviewer="Ada\x00"),
        _body(reviewer="Ada\nLovelace"),
        _body(reason="seen\x00on the car"),
        _body(ktype="A\x00"),
        _body(operation_id="nope"),
        {},
    ],
)
def test_a_malformed_body_is_a_422_and_reaches_no_service(
    client: TestClient, fake: _Service, body: dict[str, Any]
) -> None:
    assert client.post(URL, json=body).status_code == 422
    assert fake.requests == []


def test_a_reason_may_span_lines(client: TestClient, fake: _Service) -> None:
    assert client.post(URL, json=_body(reason="first line\nsecond\tline")).status_code == 201
    assert fake.requests[0][1].reason == "first line\nsecond\tline"


def test_history_is_returned_with_evidence_only_when_asked(
    client: TestClient, fake: _Service
) -> None:
    plain = client.get(URL)
    full = client.get(URL, params={"evidence": "true"})

    assert plain.status_code == full.status_code == 200
    assert fake.history_calls == [(VEHICLE_ID, False), (VEHICLE_ID, True)]
    payload = plain.json()
    assert set(payload) == {"vehicle_id", "current_choice_id", "entries"}
    assert set(payload["entries"][0]) == {
        "choice_id", "action", "ktype", "reviewer", "reason", "created_at",
        "supersedes_choice_id", "catalog_batch", "automatic_terminal", "automatic_ktype",
        "code_version", "evidence",
    }
    assert payload["entries"][0]["evidence"] is None
    assert full.json()["entries"][0]["evidence"]["schema"] == "manual-ktype-choice-evidence-v1"


def test_history_refusals(client: TestClient, fake: _Service) -> None:
    fake.error = InvalidVehicleIdError("x")
    assert client.get(URL).json()["detail"]["code"] == "invalid_vehicle_id"
    fake.error = psycopg.OperationalError("down")
    response = client.get(URL)
    assert (response.status_code, response.json()["detail"]["code"]) == (503, "unavailable")
    # Reading saves nothing, so the message does not talk about saving.
    assert "saved" not in response.json()["detail"]["message"]


def test_the_choice_routes_do_not_shadow_the_vehicle_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert set(paths["/v1/vehicles/{vehicle_id}/ktype-choices"]) == {"get", "post"}
    assert "/v1/vehicles/matching/lookup" in paths
    assert "/v1/vehicles/{vehicle_id}" in paths


def test_the_real_wiring_resolves_and_refuses_a_bad_id_before_any_database(
    client: TestClient,
) -> None:
    """No override: the choice service is built on the matching service's lookup."""

    response = client.post("/v1/vehicles/matching/ktype-choices", json=_body())

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_vehicle_id"
    assert client.get("/v1/vehicles/ABC123/ktype-choices").status_code == 422
