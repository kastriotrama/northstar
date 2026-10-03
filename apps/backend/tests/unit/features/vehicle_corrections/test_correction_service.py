"""The rules around recording a correction, with a fake store and a scripted lookup."""

from __future__ import annotations

import inspect
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import pytest
from correction_test_support import CAR, OTHER_VEHICLE_ID, SOURCES, VEHICLE_ID, lookup, stored
from pydantic import ValidationError

from api.app.features.vehicle_corrections import fields, service
from api.app.features.vehicle_corrections.schemas import CorrectionRequest
from api.app.features.vehicle_corrections.service import (
    ConfirmationRequiredError,
    CorrectionChangedError,
    CorrectionService,
    EvidenceChangedError,
    FieldNotCorrectableError,
    InvalidValueError,
    InvalidVehicleIdError,
    NothingToIgnoreError,
    NothingToWithdrawError,
    OperationReusedError,
    ReasonRequiredError,
    ValueUnchangedError,
    snapshot,
)
from api.app.features.vehicle_matching.repository import Hypothetical
from api.app.features.vehicle_matching.schemas import VehicleMatchLookup
from api.app.features.vehicle_matching.service import MatchOutcome, VehicleNotFoundError
from ingestion.vehicle_fact_corrections import CorrectionHead, NewCorrection, StoredCorrection

STOP = fields.NORMALIZATION_STOP


class _Store:
    """In-memory chains for one vehicle, one per field, with the writer's own head rules."""

    def __init__(self) -> None:
        self.rows: list[StoredCorrection] = []
        self.record_calls = 0
        #: The matcher input hash each write was decided on, as handed to the store.
        self.checked_on: list[str | None] = []

    def head(self, field: str) -> StoredCorrection | None:
        return next((row for row in reversed(self.rows) if row.field == field), None)

    def fetch(self, correction_id: UUID) -> StoredCorrection | None:
        return next((row for row in self.rows if row.correction_id == correction_id), None)

    def chains(self, vehicle_id: str) -> dict[str, list[StoredCorrection]]:
        ordered: dict[str, list[StoredCorrection]] = {}
        for row in reversed(self.rows):
            ordered.setdefault(row.field, []).append(row)
        return ordered

    def current(self, vehicle_id: str) -> dict[str, tuple[StoredCorrection, int]]:
        return {field: (rows[0], len(rows)) for field, rows in self.chains(vehicle_id).items()}

    def heads(self) -> dict[str, CorrectionHead]:
        return {
            field: CorrectionHead(head.action, head.value, head.correction_id)
            for field, (head, _) in self.current(VEHICLE_ID).items()
        }

    def record(
        self, new: NewCorrection, *, checked_on: str | None = None
    ) -> tuple[StoredCorrection, bool]:
        self.record_calls += 1
        self.checked_on.append(checked_on)
        head = self.head(new.field)
        if (head.correction_id if head else None) != new.supersedes_correction_id:
            raise CorrectionChangedError("changed")
        if new.action == "withdraw" and (head is None or head.action == "withdraw"):
            raise NothingToWithdrawError("nothing")
        row = stored(new, head.chain_position + 1 if head else 0)
        self.rows.append(row)
        return row, True


class _World:
    def __init__(self) -> None:
        self.store = _Store()
        self.car: dict[str, Any] = dict(CAR)
        #: Set to stop the car before matching, the way a record needing review is.
        self.stop_reasons: list[str] = []
        self.lookups: list[VehicleMatchLookup] = []
        #: Anything a test wants the lookup to say instead (a resolved car, its hash).
        self.overrides: dict[str, Any] = {}
        self.service = CorrectionService(self.store, self.lookup_vehicle, "abc1234")

    def _evaluate(self) -> VehicleMatchLookup:
        heads = self.store.heads()
        released = STOP in heads and heads[STOP].action == "ignore"
        stopped: dict[str, Any] = {}
        if self.stop_reasons and not released:
            stopped = {
                "terminal": "normalization_review", "bucket": "not_matchable", "inputs": None,
                "candidates": [], "top_ktype": None,
                "reason_codes": [f"normalization:{reason}" for reason in self.stop_reasons],
            }
        return lookup(
            self.car, SOURCES, heads, self.store.current(VEHICLE_ID),
            stop_reasons=list(self.stop_reasons), **{**self.overrides, **stopped},
        )

    def lookup_vehicle(self, vehicle_id: str) -> VehicleMatchLookup:
        """What the matching service does: lay the corrections over the car, then evaluate."""

        if vehicle_id != VEHICLE_ID:
            raise VehicleNotFoundError(vehicle_id)
        result = self._evaluate()
        self.lookups.append(result)
        return result

    def request(self, action: str = "set", field: str = "engine_code", **body: Any) -> CorrectionRequest:
        """A request built from the lookup, the way the screen builds it."""

        head = self.store.head(field)
        values: dict[str, Any] = {
            "operation_id": uuid4(), "field": field, "action": action,
            "value": "DFGA" if action == "set" else None, "reviewer": "Ada",
            "supersedes_correction_id": head.correction_id if head else None,
            "evidence_fingerprint": self._evaluate().evidence_fingerprint,
        }
        values.update(body)
        return CorrectionRequest(**values)


@pytest.fixture
def world() -> _World:
    return _World()


def test_a_set_stores_what_it_replaced_and_answers_with_a_fresh_evaluation(world: _World) -> None:
    request = world.request(value="  DFGA ", reviewer="  Ada  ", reason="  ")

    answer, created = world.service.record(f" {VEHICLE_ID.lower()} ", request)

    assert created
    before, after = world.lookups
    assert answer is after and answer is not before
    (row,) = world.store.rows
    assert (row.correction_id, row.vehicle_id) == (request.operation_id, VEHICLE_ID)
    assert (row.field, row.action, row.value) == ("engine_code", "set", "DFGA")
    assert (row.reviewer, row.reason, row.group_id) == ("Ada", None, None)
    assert (row.previous_value, row.previous_source) == ("DPCA", "review")
    assert (row.catalog_batch, row.automatic_terminal, row.automatic_ktype) == (
        "batch-1", "review_required", "A")
    assert row.code_version == "abc1234"
    # The evidence is the evaluation the person saw, not the one the correction produced.
    assert row.evidence_fingerprint == before.evidence_fingerprint != after.evidence_fingerprint
    assert row.evidence == snapshot(before)
    assert row.evidence["inputs"]["engine_code"] == "DPCA"
    # The answer is the car as matched again: the matcher was handed the new value.
    assert answer.inputs is not None and answer.inputs.engine_code == "DFGA"
    assert answer.overlaid_fields["engine_code"] == "correction"
    (state,) = answer.corrections
    assert (state.field, state.status, state.value, state.history_count) == (
        "engine_code", "set", "DFGA", 1)
    assert (state.previous_value, state.previous_source) == ("DPCA", "review")
    shown = next(item for item in answer.correctable_fields if item.field == "engine_code")
    assert (shown.current_value, shown.current_source) == ("DFGA", "correction")


def test_ignore_withdraw_and_a_later_set_each_append_one_row_to_the_fields_chain(
    world: _World,
) -> None:
    ignored, _ = world.service.record(VEHICLE_ID, world.request("ignore", reason="not this car's"))
    assert ignored.inputs is not None and ignored.inputs.engine_code is None
    assert ignored.corrections[0].status == "ignored"
    assert ignored.overlaid_fields["engine_code"] == "correction"

    changed, _ = world.service.record(VEHICLE_ID, world.request(value="DTSA"))
    again, _ = world.service.record(VEHICLE_ID, world.request(value="DFGA"))
    withdrawn, _ = world.service.record(VEHICLE_ID, world.request("withdraw"))
    after, _ = world.service.record(VEHICLE_ID, world.request(value="DTSB"))

    assert changed.inputs is not None and changed.inputs.engine_code == "DTSA"
    assert again.corrections[0].history_count == 3
    assert withdrawn.corrections[0].status == "withdrawn"
    assert withdrawn.inputs is not None and withdrawn.inputs.engine_code == "DPCA"
    assert withdrawn.overlaid_fields["engine_code"] == "review"
    assert after.corrections[0].history_count == 5
    rows = world.store.rows
    assert [(row.action, row.value) for row in rows] == [
        ("ignore", None), ("set", "DTSA"), ("set", "DFGA"), ("withdraw", None), ("set", "DTSB")]
    assert [row.supersedes_correction_id for row in rows] == [
        None, *[row.correction_id for row in rows[:-1]]]
    # Each row remembers what the matcher used just before it.
    assert [(row.previous_value, row.previous_source) for row in rows] == [
        ("DPCA", "review"), (None, "correction"), ("DTSA", "correction"),
        ("DFGA", "correction"), ("DPCA", "review"),
    ]


def test_two_fields_of_one_car_have_separate_chains(world: _World) -> None:
    world.service.record(VEHICLE_ID, world.request())
    power, _ = world.service.record(VEHICLE_ID, world.request(field="power_kw", value="110"))
    world.service.record(VEHICLE_ID, world.request("withdraw"))

    assert [(item.field, item.status) for item in power.corrections] == [
        ("engine_code", "set"), ("power_kw", "set")]
    engine, kw, withdrawn = world.store.rows
    assert kw.supersedes_correction_id is None
    assert withdrawn.supersedes_correction_id == engine.correction_id
    assert (kw.value, kw.previous_value, kw.previous_source) == ("110", "140", "registry")


def test_the_month_the_fuel_and_the_electrification_are_corrected_like_any_field(
    world: _World,
) -> None:
    month, _ = world.service.record(
        VEHICLE_ID, world.request(field="production_month", value="09"))
    fuel, _ = world.service.record(
        VEHICLE_ID, world.request(field="fuel", value="electricity, diesel"))
    electrified, _ = world.service.record(
        VEHICLE_ID, world.request(field="electrification_type", value="hybrid"))

    rows = {row.field: row for row in world.store.rows}
    # What each replaced came from the car's own record.
    assert (rows["production_month"].value, rows["production_month"].previous_value,
            rows["production_month"].previous_source) == ("9", "3", "registry")
    assert (rows["fuel"].value, rows["fuel"].previous_value, rows["fuel"].previous_source) == (
        "diesel,electricity", "petrol,electricity", "registry")
    assert (rows["electrification_type"].value, rows["electrification_type"].previous_value,
            rows["electrification_type"].previous_source) == ("hybrid", "plug_in_hybrid", "registry")
    assert month.inputs is not None and month.inputs.build_month == 201809
    assert fuel.inputs is not None and fuel.inputs.fuels == [
        "diesel", "electricity", "hybrid_diesel"]
    assert electrified.inputs is not None and electrified.inputs.electrification == "hybrid"
    assert {name: source for name, source in electrified.overlaid_fields.items()
            if source == "correction"} == {
        "production_month": "correction", "fuel": "correction",
        "electrification_type": "correction"}

    gone, _ = world.service.record(VEHICLE_ID, world.request("ignore", field="fuel"))
    assert gone.inputs is not None and gone.inputs.fuels == []
    assert (world.store.rows[-1].previous_value, world.store.rows[-1].previous_source) == (
        "diesel,electricity", "correction")


def test_a_fuel_is_unchanged_when_its_carriers_are_the_same_in_any_order(world: _World) -> None:
    with pytest.raises(ValueUnchangedError):
        world.service.record(VEHICLE_ID, world.request(field="fuel", value="electricity,petrol"))
    with pytest.raises(ValueUnchangedError):
        world.service.record(VEHICLE_ID, world.request(field="production_month", value="03"))
    with pytest.raises(ValueUnchangedError):
        world.service.record(
            VEHICLE_ID, world.request(field="electrification_type", value="plug_in_hybrid"))
    assert world.store.rows == []
    answer, created = world.service.record(VEHICLE_ID, world.request(field="fuel", value="petrol"))
    assert created and answer.inputs is not None and answer.inputs.fuels == ["petrol"]


def test_a_replay_returns_the_car_as_it_is_now_without_appending(world: _World) -> None:
    request = world.request(reason="because")
    world.service.record(VEHICLE_ID, request)
    # The car's matching has moved on since the write succeeded.
    world.car["power_kw"] = 110

    answer, created = world.service.record(VEHICLE_ID, request)

    assert not created
    assert world.store.record_calls == 1 and len(world.store.rows) == 1
    assert answer is world.lookups[-1]
    assert answer.corrections[0].correction_id == request.operation_id
    assert answer.inputs is not None and answer.inputs.power_kw == 110


def test_a_replay_is_recognized_by_the_stored_spelling_of_its_value(world: _World) -> None:
    power = world.request(field="power_kw", value=" 0110 ")
    world.service.record(VEHICLE_ID, power)
    fuel = world.request(field="fuel", value="electricity , diesel")
    world.service.record(VEHICLE_ID, fuel)

    for request in (power, fuel):
        _, created = world.service.record(VEHICLE_ID, request)
        assert not created
    assert [row.value for row in world.store.rows] == ["110", "diesel,electricity"]


@pytest.mark.parametrize(
    "changed",
    [{"value": "DTSA"}, {"reviewer": "Bob"}, {"reason": "other"},
     {"action": "ignore", "value": None}, {"field": "model_family"}, {"field": "nonsense"},
     {"value": ""}, {"supersedes_correction_id": uuid4()}],
)
def test_an_operation_id_cannot_carry_different_content(
    world: _World, changed: dict[str, Any]
) -> None:
    request = world.request()
    world.service.record(VEHICLE_ID, request)

    with pytest.raises(OperationReusedError):
        world.service.record(VEHICLE_ID, request.model_copy(update=changed))
    with pytest.raises(OperationReusedError):
        world.service.record(OTHER_VEHICLE_ID, request)
    assert len(world.store.rows) == 1


def test_a_replay_that_lands_inside_the_transaction_is_answered_too(world: _World) -> None:
    request = world.request()

    def raced(
        new: NewCorrection, *, checked_on: str | None = None
    ) -> tuple[StoredCorrection, bool]:
        row = stored(new)
        world.store.rows.append(row)
        return row, False

    world.store.record = raced  # type: ignore[method-assign]

    answer, created = world.service.record(VEHICLE_ID, request)

    assert not created
    assert answer is world.lookups[-1] and len(world.lookups) == 2
    assert answer.corrections[0].correction_id == request.operation_id


def test_a_correction_made_on_a_stale_screen_is_refused(world: _World) -> None:
    stale_set, stale_ignore = world.request(), world.request("ignore")
    world.car["power_kw"] = 110

    with pytest.raises(EvidenceChangedError):
        world.service.record(VEHICLE_ID, stale_set)
    with pytest.raises(EvidenceChangedError):
        world.service.record(VEHICLE_ID, stale_ignore)
    with pytest.raises(EvidenceChangedError):
        world.service.record(VEHICLE_ID, world.request(evidence_fingerprint="0" * 64))
    assert world.store.rows == [] and world.store.record_calls == 0


def test_the_head_must_be_the_one_the_screen_showed(world: _World) -> None:
    world.service.record(VEHICLE_ID, world.request())

    # The matching is today's, but the field's correction is not the one named.
    with pytest.raises(CorrectionChangedError):
        world.service.record(VEHICLE_ID, world.request(value="DTSA", supersedes_correction_id=None))
    with pytest.raises(CorrectionChangedError):
        world.service.record(
            VEHICLE_ID, world.request(value="DTSA", supersedes_correction_id=uuid4()))
    assert len(world.store.rows) == 1


@pytest.mark.parametrize("field", ["energy_sources", "colour", "ktype", "Engine_Code", "x y"])
def test_only_a_correctable_field_can_be_corrected(world: _World, field: str) -> None:
    for action in ("set", "ignore", "withdraw"):
        with pytest.raises(FieldNotCorrectableError):
            world.service.record(VEHICLE_ID, world.request(action, field=field))
    assert world.store.record_calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [("engine_code", None), ("engine_code", "   "), ("engine_code", "x" * 81),
     ("power_kw", "abc"), ("power_kw", "0"), ("power_kw", "2001"), ("displacement_cc", "1.9"),
     ("production_year", "1492"), ("production_month", "13"), ("drive_type", "4wd"),
     ("bodywork_form", "wagon"), ("fuel", "steam"), ("fuel", "petrol,petrol"),
     ("electrification_type", "mild")],
)
def test_a_value_the_field_does_not_take_is_refused(
    world: _World, field: str, value: str | None
) -> None:
    with pytest.raises(InvalidValueError):
        world.service.record(VEHICLE_ID, world.request(field=field, value=value))
    assert world.store.record_calls == 0


def test_setting_the_value_the_matcher_already_uses_is_refused(world: _World) -> None:
    with pytest.raises(ValueUnchangedError):
        world.service.record(VEHICLE_ID, world.request(value=" DPCA "))
    with pytest.raises(ValueUnchangedError):
        world.service.record(VEHICLE_ID, world.request(field="power_kw", value="0140"))
    world.service.record(VEHICLE_ID, world.request(value="DFGA"))
    with pytest.raises(ValueUnchangedError):
        world.service.record(VEHICLE_ID, world.request(value="DFGA"))
    assert len(world.store.rows) == 1


def test_only_a_value_the_matcher_has_can_be_ignored(world: _World) -> None:
    with pytest.raises(NothingToIgnoreError):
        world.service.record(VEHICLE_ID, world.request("ignore", field="drive_type"))
    world.service.record(VEHICLE_ID, world.request("ignore"))
    with pytest.raises(NothingToIgnoreError):
        world.service.record(VEHICLE_ID, world.request("ignore"))
    # A field nobody has a value for can still be filled.
    filled, created = world.service.record(
        VEHICLE_ID, world.request(field="drive_type", value="awd"))

    assert created
    assert [(row.field, row.action) for row in world.store.rows] == [
        ("engine_code", "ignore"), ("drive_type", "set")]
    assert world.store.rows[1].previous_value is None
    assert filled.inputs is not None and filled.inputs.drive_type == "awd"


def test_an_ignored_value_can_be_set_again_to_what_the_car_says(world: _World) -> None:
    world.service.record(VEHICLE_ID, world.request("ignore"))

    answer, created = world.service.record(VEHICLE_ID, world.request(value="DPCA"))

    assert created and answer.corrections[0].status == "set"
    assert (world.store.rows[1].previous_value, world.store.rows[1].previous_source) == (
        None, "correction")


def test_withdrawing_needs_a_correction_in_force_and_no_fingerprint(world: _World) -> None:
    with pytest.raises(NothingToWithdrawError):
        world.service.record(VEHICLE_ID, world.request("withdraw"))
    world.service.record(VEHICLE_ID, world.request())
    world.car["power_kw"] = 110  # a correction made on older evidence can still be withdrawn
    evaluated = len(world.lookups)

    answer, created = world.service.record(
        VEHICLE_ID, world.request("withdraw", evidence_fingerprint=None))

    assert created and answer.corrections[0].status == "withdrawn"
    # The evidence a withdrawal stores is the server's own evaluation before it.
    before = world.lookups[evaluated]
    assert world.store.rows[1].evidence_fingerprint == before.evidence_fingerprint
    assert (world.store.rows[1].previous_value, world.store.rows[1].previous_source) == (
        "DFGA", "correction")
    with pytest.raises(NothingToWithdrawError):
        world.service.record(VEHICLE_ID, world.request("withdraw"))
    assert [row.action for row in world.store.rows] == ["set", "withdraw"]


# ------------------------------------------------------- releasing a car stopped before matching


def test_a_stopped_car_is_released_with_a_reason_and_matched(world: _World) -> None:
    world.stop_reasons = ["tyre_size_unrecognized", "bodywork_code_unresolved_for_category"]
    stopped = world.lookup_vehicle(VEHICLE_ID)
    assert (stopped.terminal, stopped.inputs) == ("normalization_review", None)

    answer, created = world.service.record(
        VEHICLE_ID, world.request("ignore", field=STOP, reason=" tyres checked by hand "))

    assert created
    (row,) = world.store.rows
    assert (row.field, row.action, row.value, row.reason) == (
        STOP, "ignore", None, "tyres checked by hand")
    assert (row.previous_value, row.previous_source) == (
        "tyre_size_unrecognized,bodywork_code_unresolved_for_category", "registry")
    assert (row.automatic_terminal, row.automatic_ktype) == ("normalization_review", None)
    assert row.evidence["inputs"] is None
    # The matcher ran: the answer is whatever it now says, and the reasons stay on show.
    assert (answer.terminal, answer.top_ktype) == ("review_required", "A")
    assert answer.inputs is not None
    assert answer.stop_reasons == world.stop_reasons
    (release,) = answer.corrections
    assert (release.field, release.status, release.value) == (STOP, "ignored", None)
    # It is not a value: the fields a person can correct are the same as before.
    assert STOP not in [item.field for item in answer.correctable_fields]
    assert [item.field for item in answer.correctable_fields] == list(fields.SPECS)


def test_a_release_can_be_taken_back(world: _World) -> None:
    world.stop_reasons = ["tyre_size_unrecognized"]
    world.service.record(VEHICLE_ID, world.request("ignore", field=STOP, reason="checked"))

    again, created = world.service.record(VEHICLE_ID, world.request("withdraw", field=STOP))

    assert created and again.terminal == "normalization_review"
    assert (again.corrections[0].field, again.corrections[0].status) == (STOP, "withdrawn")
    assert (world.store.rows[1].previous_value, world.store.rows[1].previous_source) == (
        None, "correction")
    with pytest.raises(NothingToWithdrawError):
        world.service.record(VEHICLE_ID, world.request("withdraw", field=STOP))
    # And released once more, on top of the withdrawal.
    released, _ = world.service.record(
        VEHICLE_ID, world.request("ignore", field=STOP, reason="checked again"))
    assert released.terminal == "review_required"
    assert released.corrections[0].history_count == 3


def test_a_stop_without_reasons_is_recorded_in_the_matchers_own_word(world: _World) -> None:
    world.stop_reasons = ["normalization_review_required"]

    world.service.record(VEHICLE_ID, world.request("ignore", field=STOP, reason="an AIS car"))

    assert world.store.rows[0].previous_value == "normalization_review_required"


def test_a_release_needs_a_reason_a_stopped_car_and_no_value(world: _World) -> None:
    world.stop_reasons = ["tyre_size_unrecognized"]

    for reason in (None, "", "   "):
        with pytest.raises(ReasonRequiredError):
            world.service.record(VEHICLE_ID, world.request("ignore", field=STOP, reason=reason))
    # The stop is not a value: there is nothing to set.
    with pytest.raises(InvalidValueError):
        world.service.record(VEHICLE_ID, world.request(field=STOP, value="resolved"))
    with pytest.raises(InvalidValueError):
        world.service.record(VEHICLE_ID, world.request(field=STOP, value=None, reason="x"))
    # A withdrawal needs no reason, only something to withdraw.
    with pytest.raises(NothingToWithdrawError):
        world.service.record(VEHICLE_ID, world.request("withdraw", field=STOP))
    assert world.store.rows == []

    world.service.record(VEHICLE_ID, world.request("ignore", field=STOP, reason="checked"))
    with pytest.raises(NothingToIgnoreError):  # already released
        world.service.record(VEHICLE_ID, world.request("ignore", field=STOP, reason="again"))
    assert len(world.store.rows) == 1


@pytest.mark.parametrize(
    ("terminal", "reason_codes"),
    [("review_required", ["match:x"]), ("resolved", ["match:x"]),
     ("policy_excluded", ["policy:exclude_from_passenger_car_dataset"]),
     ("failed", ["normalization_failed"]), ("unmatched", ["manufacturer_missing"])],
)
def test_only_a_car_stopped_for_normalization_review_can_be_released(
    world: _World, terminal: str, reason_codes: list[str]
) -> None:
    def other_outcome(vehicle_id: str) -> VehicleMatchLookup:
        return lookup(terminal=terminal, reason_codes=reason_codes)

    service_ = CorrectionService(world.store, other_outcome, "abc1234")
    request = world.request(
        "ignore", field=STOP, reason="please",
        evidence_fingerprint=other_outcome(VEHICLE_ID).evidence_fingerprint)

    with pytest.raises(NothingToIgnoreError):
        service_.record(VEHICLE_ID, request)
    assert world.store.rows == []


def test_refusals_come_in_one_order(world: _World) -> None:
    """The vehicle, the field, the value, the evidence, the change, then the chain."""

    stale = "0" * 64
    wrong_head = uuid4()

    def refusal(vehicle_id: str = VEHICLE_ID, **body: Any) -> type[Exception]:
        body = {"evidence_fingerprint": stale, "supersedes_correction_id": wrong_head, **body}
        with pytest.raises(Exception) as caught:  # the type is the assertion
            world.service.record(vehicle_id, world.request(**body))
        return caught.type

    assert refusal("ABC123", field="nonsense", value="") is InvalidVehicleIdError
    assert refusal(OTHER_VEHICLE_ID, field="nonsense", value="") is VehicleNotFoundError
    assert refusal(field="nonsense", value="") is FieldNotCorrectableError
    assert refusal(value="") is InvalidValueError
    assert refusal(value="DPCA") is EvidenceChangedError
    current = world.lookups[-1].evidence_fingerprint
    assert refusal(value="DPCA", evidence_fingerprint=current) is ValueUnchangedError
    assert refusal(evidence_fingerprint=current) is CorrectionChangedError
    assert refusal(action="ignore", field="drive_type", value=None,
                   evidence_fingerprint=current) is NothingToIgnoreError
    # The release: its own shape first, then the evidence, then whether the car is stopped.
    assert refusal(field=STOP, value="x") is InvalidValueError
    assert refusal(action="ignore", field=STOP, value=None) is ReasonRequiredError
    assert refusal(action="ignore", field=STOP, value=None, reason="r") is EvidenceChangedError
    assert refusal(action="ignore", field=STOP, value=None, reason="r",
                   evidence_fingerprint=current) is NothingToIgnoreError
    assert world.store.rows == []


def test_a_path_that_is_not_a_nor_id_is_refused_before_anything_runs(world: _World) -> None:
    with pytest.raises(InvalidVehicleIdError):
        world.service.record("matching", world.request())
    with pytest.raises(InvalidVehicleIdError):
        world.service.history("ABC123")
    assert world.lookups == [] and world.store.rows == []


def test_history_lists_each_field_from_its_current_row_backwards(world: _World) -> None:
    assert world.service.history(VEHICLE_ID).fields == []
    world.service.record(VEHICLE_ID, world.request(field="power_kw", value="110", reason="plate"))
    world.service.record(VEHICLE_ID, world.request())
    world.service.record(VEHICLE_ID, world.request("ignore"))
    lookups = len(world.lookups)

    history = world.service.history(f" {VEHICLE_ID.lower()} ")

    assert len(world.lookups) == lookups  # no matcher run
    assert history.vehicle_id == VEHICLE_ID
    engine, power = history.fields
    assert (engine.field, power.field) == ("engine_code", "power_kw")
    assert engine.current_correction_id == world.store.rows[-1].correction_id
    assert [(entry.action, entry.value) for entry in engine.entries] == [
        ("ignore", None), ("set", "DFGA")]
    assert engine.entries[0].supersedes_correction_id == engine.entries[1].correction_id
    assert (engine.entries[1].previous_value, engine.entries[1].previous_source) == (
        "DPCA", "review")
    (entry,) = power.entries
    assert power.current_correction_id == entry.correction_id
    assert (entry.value, entry.reason, entry.code_version, entry.group_id) == (
        "110", "plate", "abc1234", None)
    assert (entry.catalog_batch, entry.automatic_terminal, entry.automatic_ktype) == (
        "batch-1", "review_required", "A")


def test_a_blank_code_version_is_recorded_as_unknown(world: _World) -> None:
    blank = CorrectionService(world.store, world.lookup_vehicle, "  ")

    blank.record(VEHICLE_ID, world.request())

    assert world.store.rows[0].code_version == "unknown"


def test_the_stored_evidence_names_no_car(world: _World) -> None:
    shown = world.lookup_vehicle(VEHICLE_ID)

    evidence = snapshot(shown)

    assert set(evidence) == {"schema", "automatic", "inputs", "overlaid_fields"}
    assert evidence["schema"] == "vehicle-fact-correction-evidence-v1"
    assert evidence["automatic"] == {
        "terminal": "review_required", "top_ktype": "A", "reason_codes": ["match:x"]}
    assert evidence["inputs"]["power_kw"] == 140
    assert evidence["overlaid_fields"] == {
        "engine_code": "review", "production_month": "transportstyrelsen"}
    text = repr(evidence)
    for private in (VEHICLE_ID, OTHER_VEHICLE_ID, "ABC123", "YV1BW84S1F1234567", "secret trace"):
        assert private not in text
    # A car stopped before matching has no inputs to store.
    assert snapshot(shown.model_copy(update={"inputs": None}))["inputs"] is None


def test_a_lookup_that_describes_no_fields_is_a_wiring_error(world: _World) -> None:
    bare = CorrectionService(
        world.store,
        lambda vehicle_id: world.lookup_vehicle(vehicle_id).model_copy(
            update={"correctable_fields": []}),
        "abc1234",
    )

    with pytest.raises(RuntimeError, match="correctable fields"):
        bare.record(VEHICLE_ID, world.request())
    assert world.store.rows == []


@pytest.mark.parametrize(
    "body",
    [
        {"action": "ignore", "value": "DFGA"},
        {"action": "withdraw", "value": "DFGA"},
        {"action": "ignore", "evidence_fingerprint": None},
        {"action": "set", "evidence_fingerprint": ""},
        {"reviewer": "   "},
        {"reviewer": "x" * 121},
        {"reason": "x" * 1001},
        {"action": "replace"},
        {"operation_id": "not-a-uuid"},
        {"supersedes_correction_id": "not-a-uuid"},
        {"field": None},
        {"value": 150},
        # A control character can never be stored: refused before anything runs.
        {"value": "DF\x00GA"},
        {"value": "DF\x1bGA"},
        {"reviewer": "A\x00da"},
        {"reviewer": "Ada\nLovelace"},
        {"reason": "bad\x00byte"},
    ],
)
def test_the_request_refuses_what_the_table_would_refuse(body: dict[str, Any]) -> None:
    values: dict[str, Any] = {
        "operation_id": str(uuid4()), "field": "engine_code", "action": "set", "value": "DFGA",
        "reviewer": "Ada", "evidence_fingerprint": "a" * 64,
    }
    values.update(body)
    if values["action"] != "set" and "value" not in body:
        values["value"] = None

    with pytest.raises(ValidationError):
        CorrectionRequest(**values)


def test_the_request_trims_who_why_and_which_field() -> None:
    request = CorrectionRequest(
        operation_id=uuid4(), field=" engine_code ", action="withdraw", value="  ",
        reviewer=" Ada ", reason="  typo \n")

    assert (request.field, request.reviewer, request.reason) == ("engine_code", "Ada", "typo")
    assert (request.value, request.evidence_fingerprint) == (None, None)
    # The value of a `set` is the service's to judge, so that it can say why;
    # the spacing a pasted value carries is tidied there too.
    blank = CorrectionRequest(
        operation_id=uuid4(), field="engine_code", action="set", reviewer="Ada",
        evidence_fingerprint="a" * 64)
    assert blank.value is None
    pasted = blank.model_copy(update={"value": "DFGA\r\n"})
    assert CorrectionRequest(**pasted.model_dump()).value == "DFGA\r\n"
    # A reason may run over several lines.
    assert CorrectionRequest(
        operation_id=uuid4(), field="engine_code", action="withdraw", reviewer="Ada",
        reason="line one\nline two").reason == "line one\nline two"


def test_the_service_raises_no_http_errors() -> None:
    assert "HTTPException" not in inspect.getsource(service)
    assert "fastapi" not in inspect.getsource(service)
    assert "fastapi" not in inspect.getsource(fields)


def test_a_replay_is_judged_on_the_request_not_on_the_evidence(world: _World) -> None:
    world.service.record(VEHICLE_ID, world.request())
    row = world.store.rows[0]
    same = {"vehicle_id": VEHICLE_ID, "field": "engine_code", "action": "set", "value": "DFGA",
            "reviewer": "Ada", "reason": None, "supersedes_correction_id": None}

    assert row.same_request(**same)
    assert replace(row, evidence={}, previous_value="x", previous_source="y").same_request(**same)
    assert not row.same_request(**{**same, "field": "model_family"})


# ------------------------------------------------------ a resolved car is never harmed silently


class _WhatIf:
    """Stands in for `VehicleMatchingService.what_if`: where the matcher would end."""

    def __init__(self, after: MatchOutcome) -> None:
        self.after = after
        self.asked: list[tuple[str, Hypothetical]] = []

    def __call__(self, vehicle_id: str, hypothetical: Hypothetical) -> MatchOutcome:
        self.asked.append((vehicle_id, hypothetical))
        return self.after


def _resolved(world: _World, after: MatchOutcome) -> _WhatIf:
    """The car resolves to KType A today; with a correction it would end at `after`."""

    what_if = _WhatIf(after)
    world.overrides = {"terminal": "resolved", "bucket": "one", "top_ktype": "A"}
    world.service = CorrectionService(world.store, world.lookup_vehicle, "abc1234", what_if=what_if)
    return what_if


@pytest.mark.parametrize(
    ("after", "sentence"),
    [
        (MatchOutcome("review_required", None), "no longer resolve"),
        (MatchOutcome("hard_conflict", None), "no longer resolve"),
        (MatchOutcome("resolved", "B"), "another KType"),
    ],
)
def test_a_correction_that_would_harm_a_resolved_car_needs_confirmation(
    world: _World, after: MatchOutcome, sentence: str
) -> None:
    what_if = _resolved(world, after)
    request = world.request(value="DFGA")

    with pytest.raises(ConfirmationRequiredError) as refused:
        world.service.record(VEHICLE_ID, request)

    assert (refused.value.before, refused.value.after) == (MatchOutcome("resolved", "A"), after)
    assert sentence in str(refused.value)
    assert what_if.asked == [(VEHICLE_ID, Hypothetical("engine_code", "set", "DFGA"))]
    assert (world.store.rows, world.store.record_calls) == ([], 0)

    # The person saw it and sends the same request, confirmed: it is recorded.
    _, created = world.service.record(
        VEHICLE_ID, request.model_copy(update={"confirm_change": True})
    )
    assert created and [row.correction_id for row in world.store.rows] == [request.operation_id]


def test_an_ignore_is_asked_about_too_and_carries_no_value(world: _World) -> None:
    what_if = _resolved(world, MatchOutcome("review_required", None))

    with pytest.raises(ConfirmationRequiredError):
        world.service.record(VEHICLE_ID, world.request("ignore"))

    assert what_if.asked == [(VEHICLE_ID, Hypothetical("engine_code", "ignore", None))]


def test_a_correction_that_keeps_a_resolved_cars_ktype_needs_no_confirmation(
    world: _World,
) -> None:
    what_if = _resolved(world, MatchOutcome("resolved", "A"))

    _, created = world.service.record(VEHICLE_ID, world.request(value="DFGA"))

    assert created and len(what_if.asked) == 1


def test_only_a_value_correction_of_a_resolved_car_is_asked_about(world: _World) -> None:
    # A car that does not resolve today has no KType to lose.
    unresolved = _WhatIf(MatchOutcome("hard_conflict", None))
    world.service = CorrectionService(
        world.store, world.lookup_vehicle, "abc1234", what_if=unresolved
    )
    world.service.record(VEHICLE_ID, world.request(value="DFGA"))
    assert unresolved.asked == []

    # A withdrawal is no new claim about the car: it is not gated, resolved or not.
    what_if = _resolved(world, MatchOutcome("review_required", None))
    _, created = world.service.record(VEHICLE_ID, world.request("withdraw"))
    assert created and what_if.asked == []


def test_releasing_a_stopped_car_is_not_gated(world: _World) -> None:
    what_if = _WhatIf(MatchOutcome("review_required", None))
    world.service = CorrectionService(world.store, world.lookup_vehicle, "abc1234", what_if=what_if)
    world.stop_reasons = ["tyre_size_unrecognized"]

    _, created = world.service.record(
        VEHICLE_ID, world.request("ignore", STOP, reason="the tyres are fine")
    )

    assert created and what_if.asked == []


def test_a_service_without_the_what_if_cannot_correct_a_resolved_car(world: _World) -> None:
    world.overrides = {"terminal": "resolved", "bucket": "one", "top_ktype": "A"}

    with pytest.raises(RuntimeError):
        world.service.record(VEHICLE_ID, world.request(value="DFGA"))
    assert world.store.rows == []


def test_the_write_is_decided_on_the_lookups_matcher_input(world: _World) -> None:
    """The store checks it again under the vehicle's lock; here it must be handed over."""

    world.overrides = {"matcher_input_hash": "b" * 64}

    world.service.record(VEHICLE_ID, world.request(value="DFGA"))

    assert world.store.checked_on == ["b" * 64]


def test_a_many_cars_check_validates_a_value_exactly_as_one_car_does() -> None:
    assert service.value_of("power_kw", "set", " 0150 ") == "150"
    assert service.value_of("power_kw", "ignore", None) is None
    with pytest.raises(InvalidValueError):
        service.value_of("power_kw", "set", "many")
    with pytest.raises(FieldNotCorrectableError):
        service.value_of(STOP, "ignore", None)
    shown = lookup()
    assert service.replaced_value(shown, "engine_code", "set", "DFGA") == ("DPCA", "review")
    with pytest.raises(ValueUnchangedError):
        service.replaced_value(shown, "engine_code", "set", "DPCA")
    with pytest.raises(NothingToIgnoreError):
        service.replaced_value(shown, "drive_type", "ignore", None)


def test_a_stored_correction_is_announced_once_and_a_replay_is_not(world: _World) -> None:
    saved: list[str] = []
    service_ = CorrectionService(
        world.store, world.lookup_vehicle, "abc1234", on_saved=saved.append
    )
    request = world.request()

    service_.record(VEHICLE_ID, request)
    _, created = service_.record(VEHICLE_ID, request)

    assert not created
    assert saved == [VEHICLE_ID]
