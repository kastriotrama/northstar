"""One correction for many cars: the scopes call, the check and the decision's own rules.

A fake store and a scripted matcher; what the database guarantees is tested in
`tests/integration/test_vehicle_correction_decisions.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from correction_test_support import VEHICLE_ID, lookup
from many_cars_test_support import (
    ANCHOR,
    Page,
    ScriptedMatcher,
    by_drive,
    car,
    corrected,
    vehicle_id,
)

from api.app.features.vehicle_corrections.decision_repository import (
    PlannedMember,
    ScopeTimeoutError,
)
from api.app.features.vehicle_corrections.decision_service import (
    MEMBER_EVIDENCE_SCHEMA,
    DecisionNotFoundError,
    DecisionService,
    EvidenceChangedError,
    HarmsMoreThanItFixesError,
    InvalidScopeError,
    NothingToApplyError,
    OperationReusedError,
    PreviewBusyError,
    PreviewExpiredError,
    PreviewIncompleteError,
    PreviewNotFoundError,
    ScopeNotOfferedError,
    ScopeTooBroadError,
)
from api.app.features.vehicle_corrections.preview import PreviewJobs
from api.app.features.vehicle_corrections.schemas import (
    DecisionRequest,
    DecisionWithdrawRequest,
    PreviewRequest,
    ScopeCondition,
    ScopesRequest,
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
from api.app.features.vehicle_matching.repository import CarRecord, matcher_input_hash
from api.app.features.vehicle_matching.schemas import VehicleMatchLookup
from ingestion.vehicle_correction_decisions import (
    DecisionRecord,
    NewDecisionEvent,
    StoredDecisionEvent,
    member_correction_id,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 5_000.0

    def __call__(self) -> float:
        return self.now


def _stored(event: NewDecisionEvent, position: int = 0) -> StoredDecisionEvent:
    return StoredDecisionEvent(
        chain_position=position,
        created_at=datetime(2026, 10, 3, 9, 0, tzinfo=UTC),
        **{name: getattr(event, name) for name in NewDecisionEvent.__dataclass_fields__},
    )


class _Store:
    """Stands in for the decision repository: the scope's cars, and what was stored."""

    def __init__(self, cars: Sequence[CarRecord]) -> None:
        self.cars = {str(item.vehicle_id): item for item in cars}
        self.anchor_values: dict[str, Any] | None = dict(ANCHOR)
        #: The count each scope gets, by how many conditions it has; None: too slow.
        self.counts: dict[int, int | None] = {}
        self.affected: int | None = None
        self.timeout = False
        self.limits: list[int] = []
        self.terms: list[list[Any]] = []
        self.events: dict[UUID, StoredDecisionEvent] = {}
        self.applied: list[tuple[NewDecisionEvent, list[PlannedMember]]] = []
        self.withdrawals: list[dict[str, Any]] = []

    def anchor(self, vehicle_id: str) -> dict[str, Any] | None:
        return self.anchor_values

    def count_scope(self, terms: Sequence[Any]) -> int | None:
        return self.counts.get(len(terms), 7)

    def scope_population(self, terms: Sequence[Any], *, limit: int) -> tuple[int, list[str]]:
        if self.timeout:
            raise ScopeTimeoutError("too slow")
        self.limits.append(limit)
        self.terms.append(list(terms))
        ids = sorted(self.cars)
        return (len(ids) if self.affected is None else self.affected), ids[:limit]

    def check_page(self, vehicle_ids: Sequence[str]) -> Page:
        return Page([self.cars[item] for item in vehicle_ids])

    def fetch_event(self, event_id: UUID) -> StoredDecisionEvent | None:
        return self.events.get(event_id)

    def decision(self, decision_id: UUID) -> DecisionRecord | None:
        chain = sorted(
            (event for event in self.events.values() if event.decision_id == decision_id),
            key=lambda event: event.chain_position,
        )
        return DecisionRecord(tuple(chain), 3) if chain else None

    def recent(self, limit: int) -> list[DecisionRecord]:
        roots = [event for event in self.events.values() if event.chain_position == 0]
        found = [self.decision(root.decision_id) for root in roots[:limit]]
        return [record for record in found if record is not None]

    def propose(self, event: NewDecisionEvent) -> tuple[StoredDecisionEvent, bool]:
        self.events[event.event_id] = _stored(event)
        return self.events[event.event_id], True

    def apply(
        self, event: NewDecisionEvent, members: Sequence[PlannedMember]
    ) -> tuple[StoredDecisionEvent, bool]:
        self.applied.append((event, list(members)))
        measured = {
            **event.measurement, "written": len(members),
            "skipped": {"changed_since_check": 0, "corrected_meanwhile": 0},
        }
        stored = _stored(event)
        self.events[event.event_id] = StoredDecisionEvent(
            **{**stored.__dict__, "measurement": measured})
        return self.events[event.event_id], True

    def withdraw(self, decision_id: UUID, *, operation_id: UUID, reviewer: str,
                 reason: str | None, code_version: str) -> tuple[StoredDecisionEvent, bool]:
        self.withdrawals.append({"decision_id": decision_id, "reviewer": reviewer,
                                 "reason": reason, "code_version": code_version})
        root = self.events[decision_id]
        event = NewDecisionEvent(
            event_id=operation_id, decision_id=decision_id, event="withdraw",
            supersedes_event_id=root.event_id, field=None, action=None, value=None, scope=None,
            scope_label=None, manufacturer=None, model_family=None, reviewer=reviewer,
            reason=reason, catalog_batch=root.catalog_batch, code_version=code_version,
            measurement={**root.measurement,
                         "withdrawal": {"members": 3, "withdrawn": 2, "left_changed": 1}},
        )
        self.events[operation_id] = _stored(event, 1)
        return self.events[operation_id], True


def _by_power(values: Any) -> tuple[str, str | None]:
    """Cars with 90 kW resolve today and lose it; 150 kW ones tie and become a conflict."""

    drive = values.get("drive_type")
    if values.get("power_kw") == 90:
        return ("review_required", None) if drive else ("resolved", "K1")
    if values.get("power_kw") == 150:
        return ("hard_conflict", None) if drive else ("review_required", None)
    if values.get("power_kw") == 70:
        return ("resolved", "K3")
    return by_drive(values)


class _World:
    def __init__(self, cars: Sequence[CarRecord] | None = None, *, max_cars: int = 500) -> None:
        self.clock = _Clock()
        self.store = _Store(cars if cars is not None else [
            car(1), car(2), car(3, power_kw=70),      # gained, gained, same
            car(4, power_kw=90),                       # lost
            car(5, corrections={"drive_type": corrected()}, drive_type="awd"),
        ])
        self.matcher = ScriptedMatcher(_by_power)
        self.jobs = PreviewJobs(clock=self.clock)
        self.lookups = 0
        self.service = DecisionService(
            self.store, self.lookup_vehicle, lambda: self.matcher, self.jobs, " abc1234 ",
            max_cars=max_cars, max_seconds=60, run_in_background=False,
        )

    def lookup_vehicle(self, vehicle_id: str) -> VehicleMatchLookup:
        self.lookups += 1
        return lookup()

    def check(self, **overrides: Any) -> Any:
        body: dict[str, Any] = {
            "field": "drive_type", "action": "set", "value": "fwd",
            "scope": {"kind": "like_this", "rung": 1},
            "evidence_fingerprint": lookup().evidence_fingerprint,
        }
        body.update(overrides)
        return self.service.start_preview(VEHICLE_ID, PreviewRequest(**body))

    def decide(self, preview_id: str, event: str = "apply", **overrides: Any) -> Any:
        body: dict[str, Any] = {
            "operation_id": uuid4(), "preview_id": preview_id, "event": event,
            "reviewer": " Ada ", "reason": " checked the papers ",
        }
        body.update(overrides)
        return self.service.decide(DecisionRequest(**body))


@pytest.fixture
def world() -> _World:
    return _World()


# -------------------------------------------------------------------------- scopes


def test_the_scopes_call_offers_this_car_the_same_data_and_the_ladder(world: _World) -> None:
    world.store.counts = {13: 4, 5: 212, 4: None}

    options = world.service.scopes(
        f" {VEHICLE_ID.lower()} ", ScopesRequest(field="drive_type", action="set", value="fwd")
    ).scopes

    assert [(item.kind, item.rung, item.count, item.too_broad) for item in options] == [
        ("this_car", None, 1, False), ("same_data", None, 4, False),
        ("like_this", 0, 212, False), ("like_this", 1, None, True),
    ]
    assert [item.label for item in options] == [
        "Only this car", "The 4 cars with exactly the same data",
        "All VOLVO V70 cars with no drive type and registry type code B",
        "All VOLVO V70 cars with no drive type",
    ]
    assert options[0].conditions == [] and options[0].narrowable == []
    for option in options[1:]:
        assert option.conditions[0].model_dump() == {
            "field": "manufacturer", "operator": "equals", "values": ["VOLVO"]}
    assert "engine_code" in {item.field for item in options[3].narrowable}
    # The same data only for a date; nothing beyond the car without a manufacturer.
    dated = world.service.scopes(
        VEHICLE_ID, ScopesRequest(field="production_year", action="set", value="2017"))
    assert [item.kind for item in dated.scopes] == ["this_car", "same_data"]
    world.store.anchor_values = {**ANCHOR, "manufacturer": None}
    alone = world.service.scopes(
        VEHICLE_ID, ScopesRequest(field="drive_type", action="set", value="fwd"))
    assert [item.kind for item in alone.scopes] == ["this_car"]


@pytest.mark.parametrize(
    ("vehicle", "request_", "error"),
    [
        ("ABC123", {"field": "drive_type", "action": "set", "value": "fwd"}, InvalidVehicleIdError),
        (VEHICLE_ID, {"field": "colour", "action": "set", "value": "red"}, FieldNotCorrectableError),
        (VEHICLE_ID, {"field": "normalization_stop", "action": "ignore"}, FieldNotCorrectableError),
        (VEHICLE_ID, {"field": "drive_type", "action": "set", "value": "4x4"}, InvalidValueError),
        (VEHICLE_ID, {"field": "drive_type", "action": "set", "value": None}, InvalidValueError),
        (VEHICLE_ID, {"field": "engine_code", "action": "set", "value": "DPCA"}, ValueUnchangedError),
        (VEHICLE_ID, {"field": "drive_type", "action": "ignore"}, NothingToIgnoreError),
    ],
)
def test_a_correction_is_validated_exactly_as_one_cars_is(
    world: _World, vehicle: str, request_: dict[str, Any], error: type[Exception]
) -> None:
    with pytest.raises(error):
        world.service.scopes(vehicle, ScopesRequest(**request_))
    with pytest.raises(error):
        world.service.start_preview(
            vehicle, PreviewRequest(**request_, scope={"kind": "same_data"}))
    assert world.store.limits == [] and not world.jobs.busy()


def test_a_vehicle_the_database_does_not_have_is_not_found(world: _World) -> None:
    world.store.anchor_values = None

    with pytest.raises(CorrectionVehicleNotFoundError):
        world.service.scopes(
            VEHICLE_ID, ScopesRequest(field="drive_type", action="set", value="fwd"))


# ------------------------------------------------------------------------ the check


def test_a_check_runs_on_the_scope_rebuilt_from_the_car_within_the_cap(world: _World) -> None:
    result = world.check(scope={
        "kind": "like_this", "rung": 1, "label": "whatever the screen says", "count": 99,
        "narrow": [{"field": "production_year", "operator": "gte", "values": ["2010"]}],
    })

    assert (result.status, result.affected, result.checked, result.complete) == ("done", 5, 5, True)
    assert (result.cap, world.store.limits) == (500, [500])
    assert world.store.terms[0][0] == ("manufacturer", "equals", ("VOLVO",))
    assert world.store.terms[0][-1] == ("production_year", "gte", ("2010",))
    assert result.scope.label == (
        "All VOLVO V70 cars with no drive type and production year 2010 or more")
    counts = result.counts
    assert (counts.gained, counts.same, counts.lost, counts.already_corrected) == (2, 1, 1, 1)
    assert (result.would_write, result.can_apply) == (3, True)
    assert all(remember is False for _, remember in world.matcher.evaluated)

    assert world.service.preview(result.preview_id) == result
    lost = world.service.preview_cars(result.preview_id, "lost", offset=0, limit=10)
    assert [item.vehicle_id for item in lost.cars] == [vehicle_id(4)]
    stopped = world.service.stop_preview(result.preview_id)  # finished: nothing changes
    assert (stopped.status, stopped.complete) == ("done", True)
    with pytest.raises(PreviewNotFoundError):
        world.service.preview("nope")


def test_a_check_is_refused_before_it_starts(world: _World) -> None:
    with pytest.raises(EvidenceChangedError):
        world.check(evidence_fingerprint="f" * 64)
    with pytest.raises(ScopeNotOfferedError):
        world.check(field="production_year", value="2017", scope={"kind": "like_this"})
    with pytest.raises(InvalidScopeError):
        world.check(scope={"kind": "like_this", "rung": 5})
    with pytest.raises(InvalidScopeError):
        world.check(scope={"kind": "like_this", "narrow": [
            ScopeCondition(field="plate", values=["ABC123"])]})
    world.store.timeout = True
    with pytest.raises(ScopeTooBroadError):
        world.check()
    world.store.timeout = False
    world.store.anchor_values = {**ANCHOR, "manufacturer": None}
    with pytest.raises(ScopeTooBroadError):
        world.check()
    assert world.jobs.busy() is False and world.store.limits == []


def test_a_second_check_is_refused_while_one_runs(world: _World) -> None:
    running = world.jobs.create(
        world.jobs.get(world.check().preview_id).plan, affected=1, cap=500)

    with pytest.raises(PreviewBusyError):
        world.check()

    running.finish("done", world.clock())
    assert world.check().status == "done"


def test_a_check_without_a_fingerprint_is_made_on_the_car_as_it_is(world: _World) -> None:
    assert world.check(evidence_fingerprint=None).status == "done"


# ------------------------------------------------------------------------- applying


def test_apply_writes_the_checked_cars_and_stores_what_the_person_saw(world: _World) -> None:
    preview = world.check()
    operation = uuid4()

    result, created = world.decide(preview.preview_id, operation_id=operation)

    assert created
    (event, members), = world.store.applied
    assert (event.event_id, event.decision_id, event.event) == (operation, operation, "apply")
    assert (event.field, event.action, event.value) == ("drive_type", "set", "fwd")
    assert (event.reviewer, event.reason) == ("Ada", "checked the papers")
    assert (event.manufacturer, event.model_family) == ("VOLVO", "V70")
    assert (event.catalog_batch, event.code_version) == ("batch-1", "abc1234")
    assert event.scope_label == preview.scope.label == "All VOLVO V70 cars with no drive type"
    assert event.scope is not None
    assert (event.scope["kind"], event.scope["rung"], event.scope["anchor_value"]) == (
        "like_this", 1, None)
    measured = event.measurement
    assert (measured["preview_id"], measured["complete"], measured["include_changed"]) == (
        preview.preview_id, True, False)
    assert (measured["affected"], measured["checked"], measured["gained"], measured["lost"]) == (
        5, 5, 2, 1)

    # Gained and same; the lost car and the one a person corrected are left out.
    assert [member.row.vehicle_id for member in members] == [vehicle_id(n) for n in (1, 2, 3)]
    assert [member.outcome for member in members] == ["gained", "gained", "same"]
    first = members[0]
    row = first.row
    assert row.correction_id == member_correction_id(operation, vehicle_id(1), "drive_type")
    assert (row.group_id, row.field, row.action, row.value) == (operation, "drive_type", "set", "fwd")
    assert (row.reviewer, row.reason, row.supersedes_correction_id) == (
        "Ada", "checked the papers", None)
    assert (row.previous_value, row.previous_source) == (None, "registry")
    assert (row.automatic_terminal, row.automatic_ktype) == ("review_required", None)
    # The car's own matcher input is what the row is decided on, and re-checked under its lock.
    assert first.input_hash == row.evidence_fingerprint == matcher_input_hash(car(1).record)
    assert row.evidence == {
        "schema": MEMBER_EVIDENCE_SCHEMA,
        "automatic": {"terminal": "review_required", "top_ktype": None, "reason_codes": []},
        "decision_id": str(operation),
        "checked": {"outcome": "gained", "after": {"terminal": "resolved", "ktype": "K1"}},
        "matcher_input_hash": first.input_hash,
    }
    text = repr(row.evidence)
    assert vehicle_id(1) not in text and "TST001" not in text

    assert (result.decision_id, result.status, result.written) == (operation, "applied", 3)
    assert result.skipped.model_dump() == {"changed_since_check": 0, "corrected_meanwhile": 0}
    assert (result.counts.gained, result.counts.lost, result.scope_label) == (
        2, 1, "All VOLVO V70 cars with no drive type")


def test_harmed_cars_are_written_only_when_the_person_includes_them() -> None:
    # Three gained, one lost, one that gets harder: fewer harmed than fixed.
    world = _World([car(1), car(2), car(3), car(4, power_kw=90), car(5, power_kw=150)])
    preview = world.check()
    assert (preview.counts.lost, preview.counts.worse, preview.would_write) == (1, 1, 3)

    world.decide(preview.preview_id)
    world.decide(preview.preview_id, include_changed=True)

    without, including = (members for _, members in world.store.applied)
    assert [member.outcome for member in without] == ["gained"] * 3
    assert sorted(member.outcome for member in including) == [
        "gained", "gained", "gained", "lost", "worse"]
    assert world.store.applied[1][0].measurement["include_changed"] is True


def test_what_refuses_an_application(world: _World) -> None:
    preview = world.check()

    with pytest.raises(ReasonRequiredError):
        world.decide(preview.preview_id, reason="  ")
    with pytest.raises(PreviewNotFoundError):
        world.decide("gone-with-a-restart")
    world.clock.now += 30 * 60 + 1
    with pytest.raises(PreviewExpiredError):
        world.decide(preview.preview_id)
    with pytest.raises(PreviewExpiredError):
        world.decide(preview.preview_id, "propose")
    assert world.store.applied == [] and world.store.events == {}


def test_a_check_that_did_not_cover_every_car_can_only_be_proposed() -> None:
    world = _World(max_cars=2)

    preview = world.check()
    assert (preview.complete, preview.stopped_by, preview.checked) == (False, "cap", 2)
    with pytest.raises(PreviewIncompleteError):
        world.decide(preview.preview_id)

    result, created = world.decide(preview.preview_id, "propose", reason=None)

    assert created and world.store.applied == []
    assert (result.status, result.written, result.counts.gained) == ("proposed", 0, 2)
    (event,) = world.store.events.values()
    assert (event.event, event.reason, event.measurement["complete"]) == ("propose", None, False)
    assert (event.field, event.value, event.scope_label) == (
        "drive_type", "fwd", "All VOLVO V70 cars with no drive type")


def test_a_running_or_failed_check_is_no_basis_for_a_decision(world: _World) -> None:
    done = world.check()
    job = world.jobs.get(done.preview_id)
    for status in ("running", "failed"):
        job.status = status
        for event in ("apply", "propose"):
            with pytest.raises(PreviewIncompleteError):
                world.decide(done.preview_id, event)
    # A stopped check kept what it found: it can be proposed, never applied.
    job.status, job.stopped_by = "cancelled", "stopped"
    with pytest.raises(PreviewIncompleteError):
        world.decide(done.preview_id)
    assert world.decide(done.preview_id, "propose")[0].status == "proposed"


def test_a_check_that_harms_as_many_as_it_fixes_cannot_be_applied_at_all() -> None:
    world = _World([car(1), car(2, power_kw=90), car(3, power_kw=70)])
    preview = world.check()
    assert (preview.counts.gained, preview.counts.lost, preview.blocked_by) == (
        1, 1, ["harms_more_than_it_fixes"])

    for include in (False, True):
        with pytest.raises(HarmsMoreThanItFixesError):
            world.decide(preview.preview_id, include_changed=include)
    assert world.decide(preview.preview_id, "propose")[0].status == "proposed"


def test_a_check_with_nothing_to_write_cannot_be_applied() -> None:
    world = _World([car(1, corrections={"drive_type": corrected()}, drive_type="awd"),
                    car(2, drive_type="rwd")])
    preview = world.check()

    assert preview.blocked_by == ["nothing_to_apply"]
    with pytest.raises(NothingToApplyError):
        world.decide(preview.preview_id)


def test_a_replay_is_answered_from_the_stored_event_even_without_the_check(
    world: _World,
) -> None:
    preview = world.check()
    operation = uuid4()
    first, _ = world.decide(preview.preview_id, operation_id=operation)
    # A restart: the check is gone, the decision is not.
    world.jobs = PreviewJobs(clock=world.clock)
    world.service._jobs = world.jobs

    replay, created = world.decide(preview.preview_id, operation_id=operation)

    assert (created, replay) == (False, first)
    assert len(world.store.applied) == 1
    for changed in ({"reviewer": "Bo"}, {"reason": "another"}, {"include_changed": True},
                    {"preview_id": "another-check"}, {"event": "propose"}):
        with pytest.raises(OperationReusedError):
            world.decide(**{"preview_id": preview.preview_id, "operation_id": operation, **changed})


# ----------------------------------------------------------------- undo and the list


def test_a_decision_is_withdrawn_as_one_with_a_reason(world: _World) -> None:
    preview = world.check()
    applied, _ = world.decide(preview.preview_id)

    with pytest.raises(ReasonRequiredError):
        world.service.withdraw(applied.decision_id, DecisionWithdrawRequest(
            operation_id=uuid4(), reviewer="Bo", reason=" "))
    result, created = world.service.withdraw(applied.decision_id, DecisionWithdrawRequest(
        operation_id=uuid4(), reviewer=" Bo ", reason=" wrong group "))

    assert created
    assert world.store.withdrawals == [{
        "decision_id": applied.decision_id, "reviewer": "Bo", "reason": "wrong group",
        "code_version": "abc1234",
    }]
    assert result.model_dump() == {
        "decision_id": applied.decision_id, "status": "withdrawn", "withdrawn": 2,
        "left_changed": 1, "member_count": 3,
        "scope_label": "All VOLVO V70 cars with no drive type",
        "skipped": {"changed_by_person": 1},
    }


def test_decisions_are_listed_with_what_who_why_and_where_they_stand(world: _World) -> None:
    preview = world.check()
    applied, _ = world.decide(preview.preview_id)
    world.service.withdraw(applied.decision_id, DecisionWithdrawRequest(
        operation_id=uuid4(), reviewer="Bo", reason="wrong group"))

    (listed,) = world.service.decisions(10).decisions
    one = world.service.decision(applied.decision_id)

    assert listed == one
    assert (one.status, one.field, one.action, one.value) == ("withdrawn", "drive_type", "set", "fwd")
    assert (one.reviewer, one.reason, one.member_count) == ("Ada", "checked the papers", 3)
    assert (one.manufacturer, one.model_family, one.scope["kind"]) == ("VOLVO", "V70", "like_this")
    assert [(event.event, event.reviewer) for event in one.events] == [
        ("apply", "Ada"), ("withdraw", "Bo")]
    assert one.measurement["withdrawal"] == {"members": 3, "withdrawn": 2, "left_changed": 1}
    with pytest.raises(DecisionNotFoundError):
        world.service.decision(uuid4())


@pytest.mark.parametrize(
    "body",
    [
        {"event": "withdraw"},
        {"reviewer": " "},
        {"reviewer": "A\u0000da"},
        {"reason": "x" * 1001},
        {"preview_id": ""},
        {"operation_id": "nope"},
        {"include_changed": "perhaps"},
    ],
)
def test_a_malformed_decision_request_is_refused_by_the_contract(body: dict[str, Any]) -> None:
    from pydantic import ValidationError

    values: dict[str, Any] = {
        "operation_id": uuid4(), "preview_id": "abc", "event": "apply", "reviewer": "Ada",
        "reason": "why", **body,
    }
    with pytest.raises(ValidationError):
        DecisionRequest(**values)


@pytest.mark.parametrize(
    "body",
    [
        {"action": "withdraw"},
        {"action": "ignore", "value": "fwd"},
        {"value": "f\u0000wd"},
        {"scope": {"kind": "this_car"}},
        {"scope": {"kind": "like_this", "rung": -1}},
        {"scope": {"kind": "like_this", "narrow": [{"field": "fuel", "operator": "like"}]}},
        {"evidence_fingerprint": "f" * 65},
    ],
)
def test_a_malformed_check_request_is_refused_by_the_contract(body: dict[str, Any]) -> None:
    from pydantic import ValidationError

    values: dict[str, Any] = {
        "field": "drive_type", "action": "set", "value": "fwd", "scope": {"kind": "same_data"},
        **body,
    }
    with pytest.raises(ValidationError):
        PreviewRequest(**values)
