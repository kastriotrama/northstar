"""The rules around recording a choice, with a fake store and a scripted lookup."""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from choice_test_support import VEHICLE_ID, candidate, inputs, lookup
from pydantic import ValidationError

from api.app.features.vehicle_ktype_choices import evidence, service
from api.app.features.vehicle_ktype_choices.schemas import KTypeChoiceRequest
from api.app.features.vehicle_ktype_choices.service import (
    ChoiceChangedError,
    EvidenceChangedError,
    InvalidVehicleIdError,
    KTypeChoiceService,
    KTypeNotACandidateError,
    NothingToWithdrawError,
    OperationReusedError,
)
from api.app.features.vehicle_matching.schemas import VehicleMatchLookup
from ingestion.vehicle_ktype_choices import NewChoice, StoredChoice


class _Store:
    """An in-memory chain for one vehicle, with the writer's own head rules."""

    def __init__(self) -> None:
        self.rows: list[StoredChoice] = []
        self.record_calls = 0

    def head(self) -> StoredChoice | None:
        return self.rows[-1] if self.rows else None

    def fetch(self, choice_id: UUID) -> StoredChoice | None:
        return next((row for row in self.rows if row.choice_id == choice_id), None)

    def current(self, vehicle_id: str) -> tuple[StoredChoice, int] | None:
        head = self.head()
        return (head, len(self.rows)) if head else None

    def chain(self, vehicle_id: str) -> list[StoredChoice]:
        return list(reversed(self.rows))

    def record(self, new: NewChoice) -> tuple[StoredChoice, bool, int]:
        self.record_calls += 1
        head = self.head()
        if (head.choice_id if head else None) != new.supersedes_choice_id:
            raise ChoiceChangedError("changed")
        if new.action == "withdraw" and (head is None or head.action == "withdraw"):
            raise NothingToWithdrawError("nothing")
        row = StoredChoice(
            created_at=datetime(2026, 10, 2, tzinfo=UTC),
            **{name: getattr(new, name) for name in NewChoice.__dataclass_fields__},
        )
        self.rows.append(row)
        return row, True, len(self.rows)


class _World:
    def __init__(self) -> None:
        self.store = _Store()
        self.today = lookup()
        self.lookups = 0
        self.service = KTypeChoiceService(self.store, self.lookup_vehicle, "abc1234")

    def lookup_vehicle(self, vehicle_id: str) -> VehicleMatchLookup:
        """What the matching service does: evaluate, then attach the head."""

        self.lookups += 1
        result = self.today.model_copy()
        found = self.store.current(vehicle_id)
        if found:
            result.choice = evidence.assess(found[0], result, True, found[1])
        result.effective_ktype, result.effective_source = evidence.effective(result.choice, result)
        return result

    def request(self, action: str = "choose", **body: Any) -> KTypeChoiceRequest:
        head = self.store.head()
        values: dict[str, Any] = {
            "operation_id": uuid4(), "action": action,
            "ktype": "A" if action == "choose" else None, "reviewer": "Ada",
            "supersedes_choice_id": head.choice_id if head else None,
            "evidence_fingerprint": self.today.evidence_fingerprint,
        }
        values.update(body)
        return KTypeChoiceRequest(**values)


@pytest.fixture
def world() -> _World:
    return _World()


def test_choosing_stores_the_evaluation_and_answers_without_a_second_one(world: _World) -> None:
    request = world.request(reviewer="  Ada  ", reason="  ")

    answer, created = world.service.record(f" {VEHICLE_ID.lower()} ", request)

    assert created and world.lookups == 1
    (row,) = world.store.rows
    assert (row.choice_id, row.vehicle_id) == (request.operation_id, VEHICLE_ID)
    assert (row.action, row.ktype, row.reviewer, row.reason) == ("choose", "A", "Ada", None)
    assert (row.catalog_batch, row.automatic_terminal, row.automatic_ktype) == (
        "batch-1", "review_required", "A")
    assert row.code_version == "abc1234"
    assert row.evidence_fingerprint == world.today.evidence_fingerprint
    assert row.evidence == evidence.snapshot(world.today, "abc1234")
    assert answer.choice is not None
    assert (answer.choice.status, answer.choice.ktype, answer.choice.history_count) == (
        "chosen", "A", 1)
    assert answer.choice.needs_review is False
    assert (answer.effective_ktype, answer.effective_source) == ("A", "person")
    assert answer.terminal == "review_required" and answer.top_ktype == "A"
    assert answer == world.lookup_vehicle(VEHICLE_ID)


def test_none_change_keep_and_withdraw_each_append_one_row(world: _World) -> None:
    none, _ = world.service.record(VEHICLE_ID, world.request("none", reason="not listed"))
    assert none.choice is not None and none.choice.status == "none"
    assert (none.effective_ktype, none.effective_source) == (None, "person")

    changed, _ = world.service.record(VEHICLE_ID, world.request(ktype="B"))
    kept, _ = world.service.record(VEHICLE_ID, world.request(ktype="B"))
    withdrawn, _ = world.service.record(VEHICLE_ID, world.request("withdraw"))
    again, _ = world.service.record(VEHICLE_ID, world.request())

    assert changed.choice is not None and changed.choice.ktype == "B"
    assert kept.choice is not None and kept.choice.history_count == 3
    assert withdrawn.choice is not None and withdrawn.choice.status == "withdrawn"
    assert (withdrawn.effective_ktype, withdrawn.effective_source) == (None, None)
    assert again.choice is not None and again.choice.history_count == 5
    actions = [(row.action, row.ktype) for row in world.store.rows]
    assert actions == [("none", None), ("choose", "B"), ("choose", "B"), ("withdraw", None),
                       ("choose", "A")]
    supersedes = [row.supersedes_choice_id for row in world.store.rows]
    assert supersedes == [None, *[row.choice_id for row in world.store.rows[:-1]]]


def test_a_replay_returns_the_stored_choice_without_appending(world: _World) -> None:
    request = world.request(reason="because")
    world.service.record(VEHICLE_ID, request)
    # The car's matching has moved on since the write succeeded.
    world.today = lookup(inputs=inputs(power_kw=110))

    answer, created = world.service.record(VEHICLE_ID, request)

    assert not created
    assert world.store.record_calls == 1 and len(world.store.rows) == 1
    assert answer.choice is not None and answer.choice.choice_id == request.operation_id
    assert answer.choice.stale_reasons == ["evidence_changed"]


@pytest.mark.parametrize(
    "changed",
    [{"ktype": "B"}, {"reviewer": "Bob"}, {"reason": "other"}, {"action": "none", "ktype": None},
     {"supersedes_choice_id": uuid4()}],
)
def test_an_operation_id_cannot_carry_different_content(
    world: _World, changed: dict[str, Any]
) -> None:
    request = world.request()
    world.service.record(VEHICLE_ID, request)

    with pytest.raises(OperationReusedError):
        world.service.record(VEHICLE_ID, request.model_copy(update=changed))
    with pytest.raises(OperationReusedError):
        world.service.record("NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7H", request)
    assert len(world.store.rows) == 1


def test_a_replay_that_lands_inside_the_transaction_is_answered_too(world: _World) -> None:
    request = world.request()
    row = StoredChoice(
        choice_id=request.operation_id, vehicle_id=VEHICLE_ID, action="choose", ktype="A",
        supersedes_choice_id=None, reviewer="Ada", reason=None, catalog_batch="batch-1",
        automatic_terminal="review_required", automatic_ktype="A", code_version="abc1234",
        evidence_fingerprint=world.today.evidence_fingerprint,
        evidence=evidence.snapshot(world.today, "abc1234"),
        created_at=datetime(2026, 10, 2, tzinfo=UTC),
    )

    def raced(new: NewChoice) -> tuple[StoredChoice, bool, int]:
        world.store.rows.append(row)
        return row, False, 1

    world.store.record = raced  # type: ignore[method-assign]

    answer, created = world.service.record(VEHICLE_ID, request)

    assert not created
    assert answer.choice is not None and answer.choice.choice_id == request.operation_id


def test_a_choice_made_on_a_stale_screen_is_refused(world: _World) -> None:
    request = world.request()
    world.today = lookup(inputs=inputs(power_kw=110))

    with pytest.raises(EvidenceChangedError):
        world.service.record(VEHICLE_ID, request)
    with pytest.raises(EvidenceChangedError):
        world.service.record(VEHICLE_ID, world.request("none", evidence_fingerprint="0" * 64))
    assert world.store.rows == []


def test_the_head_must_be_the_one_the_screen_showed(world: _World) -> None:
    stale = world.request()
    world.service.record(VEHICLE_ID, world.request())

    with pytest.raises(ChoiceChangedError):
        world.service.record(VEHICLE_ID, stale)
    with pytest.raises(ChoiceChangedError):
        world.service.record(VEHICLE_ID, world.request(supersedes_choice_id=uuid4()))
    assert len(world.store.rows) == 1


def test_only_a_listed_candidate_can_be_chosen(world: _World) -> None:
    with pytest.raises(KTypeNotACandidateError):
        world.service.record(VEHICLE_ID, world.request(ktype="Z"))
    assert world.store.record_calls == 0


def test_a_ruled_out_or_candidate_only_ktype_may_be_chosen(world: _World) -> None:
    world.today = lookup(candidates=[
        candidate("A"), candidate("B", conflicts=("power_kw",)), candidate("C", candidate_only=True),
    ])

    ruled_out, _ = world.service.record(VEHICLE_ID, world.request(ktype="B"))
    only, _ = world.service.record(VEHICLE_ID, world.request(ktype="C"))

    assert ruled_out.choice is not None and ruled_out.choice.chosen_candidate is not None
    assert ruled_out.choice.chosen_candidate["conflicting_fields"] == ["power_kw"]
    assert only.choice is not None and only.choice.needs_review is False


def test_a_resolved_car_can_be_overridden_and_the_matchers_result_stays(world: _World) -> None:
    world.today = lookup(terminal="resolved", top_ktype="A")

    answer, _ = world.service.record(VEHICLE_ID, world.request(ktype="B"))

    assert (answer.terminal, answer.top_ktype) == ("resolved", "A")
    assert (answer.effective_ktype, answer.effective_source) == ("B", "person")
    assert world.store.rows[0].automatic_ktype == "A"


def test_a_car_with_no_candidates_can_only_get_none_of_these(world: _World) -> None:
    world.today = lookup(inputs=None, candidates=[], top_ktype=None, terminal="policy_excluded")

    with pytest.raises(KTypeNotACandidateError):
        world.service.record(VEHICLE_ID, world.request())
    answer, created = world.service.record(VEHICLE_ID, world.request("none"))

    assert created and answer.choice is not None and answer.choice.status == "none"


def test_withdrawing_needs_a_choice_in_force_and_no_fingerprint(world: _World) -> None:
    with pytest.raises(NothingToWithdrawError):
        world.service.record(VEHICLE_ID, world.request("withdraw", supersedes_choice_id=None))
    world.service.record(VEHICLE_ID, world.request())
    world.today = lookup(inputs=inputs(power_kw=110))  # a stale choice can still be withdrawn

    world.service.record(VEHICLE_ID, world.request("withdraw", evidence_fingerprint=None))

    with pytest.raises(NothingToWithdrawError):
        world.service.record(VEHICLE_ID, world.request("withdraw"))
    assert [row.action for row in world.store.rows] == ["choose", "withdraw"]


def test_a_path_that_is_not_a_nor_id_is_refused_before_anything_runs(world: _World) -> None:
    with pytest.raises(InvalidVehicleIdError):
        world.service.record("matching", world.request())
    with pytest.raises(InvalidVehicleIdError):
        world.service.history("ABC123")
    assert world.lookups == 0 and world.store.rows == []


def test_history_runs_from_the_current_choice_backwards(world: _World) -> None:
    assert world.service.history(VEHICLE_ID).entries == []
    assert world.service.history(VEHICLE_ID).current_choice_id is None
    world.service.record(VEHICLE_ID, world.request(reason="first"))
    world.service.record(VEHICLE_ID, world.request("none"))
    lookups = world.lookups

    history = world.service.history(VEHICLE_ID)
    full = world.service.history(VEHICLE_ID, with_evidence=True)

    assert world.lookups == lookups  # no matcher run
    assert history.vehicle_id == VEHICLE_ID
    assert history.current_choice_id == world.store.rows[-1].choice_id
    assert [(entry.action, entry.ktype, entry.reason) for entry in history.entries] == [
        ("none", None, None), ("choose", "A", "first")]
    assert history.entries[0].supersedes_choice_id == history.entries[1].choice_id
    assert history.entries[1].code_version == "abc1234"
    assert all(entry.evidence is None for entry in history.entries)
    assert full.entries[1].evidence == world.store.rows[0].evidence


def test_a_blank_code_version_is_recorded_as_unknown(world: _World) -> None:
    blank = KTypeChoiceService(world.store, world.lookup_vehicle, "  ")

    blank.record(VEHICLE_ID, world.request())

    assert world.store.rows[0].code_version == "unknown"


@pytest.mark.parametrize(
    "body",
    [
        {"action": "choose", "ktype": None},
        {"action": "choose", "ktype": "  "},
        {"action": "none", "ktype": "A"},
        {"action": "withdraw", "ktype": "A"},
        {"action": "none", "evidence_fingerprint": None},
        {"action": "choose", "evidence_fingerprint": ""},
        {"reviewer": "   "},
        {"reviewer": "x" * 121},
        {"reason": "x" * 1001},
        {"ktype": "x" * 161},
        {"action": "pick"},
        {"operation_id": "not-a-uuid"},
    ],
)
def test_the_request_refuses_what_the_table_would_refuse(body: dict[str, Any]) -> None:
    values: dict[str, Any] = {
        "operation_id": str(uuid4()), "action": "choose", "ktype": "A", "reviewer": "Ada",
        "evidence_fingerprint": "a" * 64,
    }
    values.update(body)
    if values["action"] != "choose" and "ktype" not in body:
        values["ktype"] = None

    with pytest.raises(ValidationError):
        KTypeChoiceRequest(**values)


def test_the_request_trims_who_and_why() -> None:
    request = KTypeChoiceRequest(
        operation_id=uuid4(), action="withdraw", reviewer=" Ada ", reason="  typo \n")

    assert (request.reviewer, request.reason, request.evidence_fingerprint) == ("Ada", "typo", None)


def test_the_service_raises_no_http_errors() -> None:
    assert "HTTPException" not in inspect.getsource(service)
    assert "fastapi" not in inspect.getsource(service)
    assert "fastapi" not in inspect.getsource(evidence)


def test_a_replay_is_judged_on_the_request_not_on_the_evidence(world: _World) -> None:
    request = world.request()
    world.service.record(VEHICLE_ID, request)
    row = world.store.rows[0]

    assert row.same_request(vehicle_id=VEHICLE_ID, action="choose", ktype="A", reviewer="Ada",
                            reason=None, supersedes_choice_id=None)
    assert replace(row, evidence={}).same_request(
        vehicle_id=VEHICLE_ID, action="choose", ktype="A", reviewer="Ada", reason=None,
        supersedes_choice_id=None)
