"""Applying one correction to many cars: the scopes, the check, and the decision.

A correction entered on one car (the anchor) can apply to the cars with exactly
the same data, or to "all cars like this". Nothing is saved for more than one
car before a check of what it would change, car by car (`preview`). What was
checked is what is written: an application takes its cars from the check, and
under each car's row lock leaves out any that is no longer what was checked or
that a person corrected in the meantime. A car the correction would harm -- a
resolved car losing or changing its KType, an unresolved one getting harder --
is written only when the person includes it, and a check that harms at least
as many cars as it fixes cannot be applied at all.

Nothing but PostgreSQL is written: the decision's event, one ordinary
correction row per car and the vehicles' copies.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from typing import Any, Protocol
from uuid import UUID

from api.app.features.vehicle_corrections import scope
from api.app.features.vehicle_corrections.decision_repository import (
    DecisionRejectedError,
    NothingToApplyError,
    PlannedMember,
    ScopeTimeoutError,
    VehiclesBusyError,
)
from api.app.features.vehicle_corrections.preview import (
    HARMED,
    WRITABLE,
    CheckedCar,
    CheckMatcher,
    CheckPageLike,
    PreviewBusyError,
    PreviewJobs,
    PreviewNotFoundError,
    PreviewPlan,
    harms_more_than_it_fixes,
    measurement,
    run_check,
)
from api.app.features.vehicle_corrections.repository import EvidenceChangedError
from api.app.features.vehicle_corrections.schemas import (
    CorrectionPreview,
    DecisionEvent,
    DecisionList,
    DecisionRequest,
    DecisionResult,
    DecisionSummary,
    DecisionWithdrawal,
    DecisionWithdrawRequest,
    PreviewCarPage,
    PreviewCounts,
    PreviewRequest,
    ScopeOption,
    ScopeOptions,
    ScopesRequest,
    SkippedCars,
)
from api.app.features.vehicle_corrections.scope import (
    InvalidScopeError,
    ScopeNotOfferedError,
    ScopeTooBroadError,
)
from api.app.features.vehicle_corrections.service import (
    CorrectionVehicleNotFoundError,
    ReasonRequiredError,
    replaced_value,
    value_of,
    vehicle_id_of,
)
from api.app.features.vehicle_matching.schemas import VehicleMatchLookup
from ingestion.vehicle_core_query import VehicleTerm
from ingestion.vehicle_correction_decisions import (
    STATUS_OF,
    DecisionChangedError,
    DecisionNotFoundError,
    DecisionRecord,
    NewDecisionEvent,
    StoredDecisionEvent,
    member_correction_id,
)
from ingestion.vehicle_fact_corrections import (
    NewCorrection,
    NothingToWithdrawError,
    OperationReusedError,
)
from ingestion.vehicle_facts_query import UnknownFieldError

__all__ = [
    "DecisionChangedError",
    "DecisionNotFoundError",
    "DecisionRejectedError",
    "DecisionService",
    "EvidenceChangedError",
    "HarmsMoreThanItFixesError",
    "InvalidScopeError",
    "NothingToApplyError",
    "NothingToWithdrawError",
    "OperationReusedError",
    "PreviewBusyError",
    "PreviewExpiredError",
    "PreviewIncompleteError",
    "PreviewNotFoundError",
    "ScopeNotOfferedError",
    "ScopeTooBroadError",
    "VehiclesBusyError",
]

#: What a car's row stores as evidence when a decision about many cars wrote it:
#: where the matcher ended for the car in the check, before and with the
#: correction. No plate, VIN or vehicle id.
MEMBER_EVIDENCE_SCHEMA = "vehicle-correction-decision-member-evidence-v1"
THIS_CAR_LABEL = "Only this car"


class PreviewExpiredError(Exception):
    """The check is too old to apply: the cars may have moved on."""


class PreviewIncompleteError(Exception):
    """The check has not covered every car of its scope (or is still running, or failed)."""


class HarmsMoreThanItFixesError(Exception):
    """The check found at least as many harmed cars as fixed ones."""


class DecisionStore(Protocol):
    """What the service needs of the decision repository."""

    def anchor(self, vehicle_id: str) -> dict[str, Any] | None: ...

    def count_scope(self, terms: Sequence[VehicleTerm]) -> int | None: ...

    def scope_population(
        self, terms: Sequence[VehicleTerm], *, limit: int
    ) -> tuple[int, list[str]]: ...

    def check_page(self, vehicle_ids: Sequence[str]) -> CheckPageLike: ...

    def fetch_event(self, event_id: UUID) -> StoredDecisionEvent | None: ...

    def decision(self, decision_id: UUID) -> DecisionRecord | None: ...

    def recent(self, limit: int) -> list[DecisionRecord]: ...

    def propose(self, event: NewDecisionEvent) -> tuple[StoredDecisionEvent, bool]: ...

    def apply(
        self, event: NewDecisionEvent, members: Sequence[PlannedMember]
    ) -> tuple[StoredDecisionEvent, bool]: ...

    def withdraw(
        self,
        decision_id: UUID,
        *,
        operation_id: UUID,
        reviewer: str,
        reason: str | None,
        code_version: str,
    ) -> tuple[StoredDecisionEvent, bool]: ...


def _counts(measured: dict[str, Any]) -> PreviewCounts:
    """The check's counts as an event stored them; a number it does not carry is 0."""

    known = {name: measured[name] for name in PreviewCounts.model_fields if name in measured}
    return PreviewCounts(**known)


def _result(event: StoredDecisionEvent, scope_label: str) -> DecisionResult:
    measured = event.measurement
    skipped = measured.get("skipped")
    return DecisionResult(
        decision_id=event.decision_id,
        status=STATUS_OF[event.event],
        written=int(measured.get("written") or 0),
        skipped=SkippedCars(**(skipped if isinstance(skipped, dict) else {})),
        counts=_counts(measured),
        scope_label=scope_label,
        written_by_outcome=dict(measured.get("written_by_outcome") or {}),
    )


def _withdrawal(event: StoredDecisionEvent, scope_label: str) -> DecisionWithdrawal:
    numbers = event.measurement.get("withdrawal")
    numbers = numbers if isinstance(numbers, dict) else {}
    left = int(numbers.get("left_changed") or 0)
    return DecisionWithdrawal(
        decision_id=event.decision_id,
        status=STATUS_OF[event.event],
        withdrawn=int(numbers.get("withdrawn") or 0),
        left_changed=left,
        member_count=int(numbers.get("members") or 0),
        scope_label=scope_label,
        skipped={"changed_by_person": left},
    )


def _summary(record: DecisionRecord) -> DecisionSummary:
    root = record.root
    return DecisionSummary(
        decision_id=root.decision_id,
        status=record.status,
        field=str(root.field),
        action=root.action or "set",
        value=root.value,
        scope_label=str(root.scope_label),
        scope=dict(root.scope or {}),
        manufacturer=str(root.manufacturer),
        model_family=root.model_family,
        reviewer=root.reviewer,
        reason=root.reason,
        created_at=root.created_at,
        member_count=record.member_count,
        measurement=dict(record.head.measurement),
        events=[
            DecisionEvent(
                event_id=event.event_id,
                event=event.event,
                reviewer=event.reviewer,
                reason=event.reason,
                created_at=event.created_at,
            )
            for event in record.events
        ],
    )


class DecisionService:
    def __init__(
        self,
        repository: DecisionStore,
        lookup_vehicle: Callable[[str], VehicleMatchLookup],
        matcher: Callable[[], CheckMatcher],
        jobs: PreviewJobs,
        code_version: str,
        *,
        max_cars: int = 500,
        max_seconds: float = 240,
        run_in_background: bool = True,
    ) -> None:
        self._repository = repository
        self._lookup_vehicle = lookup_vehicle
        self._matcher = matcher
        self._jobs = jobs
        self._code_version = code_version.strip() or "unknown"
        self._max_cars = max_cars
        self._max_seconds = max_seconds
        self._run_in_background = run_in_background

    # ------------------------------------------------------------------------ scopes

    def _anchor(
        self, vehicle_id: str, field: str, action: str, raw: str | None
    ) -> tuple[str, str | None, VehicleMatchLookup, str | None, dict[str, Any]]:
        """Validate the correction exactly as one car's is; read the car it was entered on.

        Returns the vehicle id, the value to store, the car's lookup, what the
        matcher uses for the field on it today, and its vehicle values.
        """

        vehicle_id = vehicle_id_of(vehicle_id)
        value = value_of(field, action, raw)
        lookup = self._lookup_vehicle(vehicle_id)
        current, _ = replaced_value(lookup, field, action, value)
        anchor = self._repository.anchor(vehicle_id)
        if anchor is None:
            raise CorrectionVehicleNotFoundError(vehicle_id)
        return vehicle_id, value, lookup, current, anchor

    def scopes(self, vehicle_id: str, request: ScopesRequest) -> ScopeOptions:
        """Whom this correction could apply to, each option with its count. Saves nothing."""

        _, _, _, current, anchor = self._anchor(
            vehicle_id, request.field, request.action, request.value
        )
        options = [ScopeOption(kind="this_car", label=THIS_CAR_LABEL, count=1)]
        for option in scope.options(anchor, request.field, request.action, current):
            count = self._repository.count_scope(option.terms)
            options.append(
                ScopeOption(
                    kind=option.kind,
                    label=scope.label(option, count),
                    count=count,
                    too_broad=count is None,
                    rung=option.rung,
                    conditions=list(option.conditions),
                    narrowable=scope.narrowable(option, anchor),
                )
            )
        return ScopeOptions(scopes=options)

    # ------------------------------------------------------------------------ the check

    def start_preview(self, vehicle_id: str, request: PreviewRequest) -> CorrectionPreview:
        """Start checking what the correction would change for the cars of a scope.

        The scope's cars are picked here, so a scope that is too broad or
        malformed fails the request itself. Raises `PreviewBusyError` while
        another check runs, `EvidenceChangedError` when the anchor car is not
        what the screen showed, and the scope's own errors.
        """

        vehicle_id, value, lookup, current, anchor = self._anchor(
            vehicle_id, request.field, request.action, request.value
        )
        if (
            request.evidence_fingerprint is not None
            and request.evidence_fingerprint != lookup.evidence_fingerprint
        ):
            raise EvidenceChangedError("The car's matching changed since it was shown.")
        if self._jobs.busy():
            raise PreviewBusyError("Another check is running. Try again in a moment.")
        chosen = scope.resolve(anchor, request.field, request.action, current, request.scope)
        try:
            affected, ids = self._repository.scope_population(
                chosen.terms, limit=self._max_cars
            )
        except ScopeTimeoutError as error:
            raise ScopeTooBroadError(
                "These are too many cars to check. Narrow the group."
            ) from error
        except (UnknownFieldError, ValueError) as error:
            raise InvalidScopeError(str(error)) from error
        plan = PreviewPlan(
            anchor_vehicle_id=vehicle_id,
            field=request.field,
            action=request.action,
            value=value,
            scope=chosen,
            scope_label=scope.label(chosen, affected),
            anchor_value=current,
        )
        job = self._jobs.create(plan, affected=affected, cap=self._max_cars)
        arguments: dict[str, Any] = {
            "read_page": self._repository.check_page,
            "matcher": self._matcher,
            "max_seconds": self._max_seconds,
            "clock": self._jobs.now,
        }
        if self._run_in_background:
            threading.Thread(
                target=run_check, args=(job, ids), kwargs=arguments, daemon=True
            ).start()
        else:
            run_check(job, ids, **arguments)
        return self._jobs.snapshot(job)

    def preview(self, preview_id: str) -> CorrectionPreview:
        return self._jobs.snapshot(self._jobs.get(preview_id))

    def preview_cars(
        self, preview_id: str, outcome: str | None, *, offset: int, limit: int
    ) -> PreviewCarPage:
        return self._jobs.cars(self._jobs.get(preview_id), outcome, offset=offset, limit=limit)

    def stop_preview(self, preview_id: str) -> CorrectionPreview:
        """Ask a running check to stop; it keeps what it found so far."""

        job = self._jobs.get(preview_id)
        job.cancel.set()
        return self._jobs.snapshot(job)

    # ------------------------------------------------------------------------ decisions

    def decide(self, request: DecisionRequest) -> tuple[DecisionResult, bool]:
        """Apply a checked correction to its cars, or store it as a proposal.

        Returns the result and whether anything was recorded; `False` is a
        replay of an operation already stored, answered from the stored event
        (the check itself may be long gone by then).
        """

        existing = self._repository.fetch_event(request.operation_id)
        if existing is not None:
            return self._replayed(existing, request), False

        job = self._jobs.get(request.preview_id)
        if self._jobs.expired(job):
            raise PreviewExpiredError("The check is too old. Check again.")
        with job.lock:
            status, complete = job.status, job.complete
            cars = list(job.cars)
            counts = dict(job.counts)
            catalog_batch = job.catalog_batch
        if status in {"running", "failed"}:
            raise PreviewIncompleteError(
                "The check is still running." if status == "running" else "The check failed."
            )
        plan = job.plan
        members: list[CheckedCar] = []
        if request.event == "apply":
            if not complete:
                raise PreviewIncompleteError(
                    "Not every car was checked, so this cannot be applied. Narrow the group, "
                    "or save it as a proposal."
                )
            if request.reason is None:
                raise ReasonRequiredError("Give a reason to apply this to several cars.")
            if harms_more_than_it_fixes(counts):
                raise HarmsMoreThanItFixesError(
                    "This would harm at least as many cars as it fixes. Narrow the group, "
                    "or save it as a proposal."
                )
            wanted = WRITABLE | HARMED if request.include_changed else WRITABLE
            members = [car for car in cars if car.outcome in wanted]
            if not members:
                raise NothingToApplyError("The check found no car to write.")

        event = NewDecisionEvent(
            event_id=request.operation_id,
            decision_id=request.operation_id,
            event=request.event,
            supersedes_event_id=None,
            field=plan.field,
            action="set" if plan.action == "set" else "ignore",
            value=plan.value,
            scope=plan.scope.stored(plan.anchor_value),
            scope_label=plan.scope_label,
            manufacturer=plan.scope.manufacturer,
            model_family=plan.scope.model_family,
            reviewer=request.reviewer,
            reason=request.reason,
            catalog_batch=catalog_batch or "unknown",
            code_version=self._code_version,
            measurement={**measurement(job), "include_changed": request.include_changed},
        )
        if request.event == "propose":
            stored, created = self._repository.propose(event)
        else:
            stored, created = self._repository.apply(
                event, [self._member(event, car) for car in members]
            )
        return _result(stored, plan.scope_label), created

    @staticmethod
    def _replayed(existing: StoredDecisionEvent, request: DecisionRequest) -> DecisionResult:
        measured = existing.measurement
        if (
            existing.event != request.event
            or existing.event_id != existing.decision_id
            or existing.reviewer != request.reviewer
            or existing.reason != request.reason
            or measured.get("preview_id") != request.preview_id
            or bool(measured.get("include_changed")) != request.include_changed
        ):
            raise OperationReusedError("This operation id already recorded something else.")
        return _result(existing, str(existing.scope_label))

    def _member(self, event: NewDecisionEvent, car: CheckedCar) -> PlannedMember:
        """The row an application writes for one checked car, and what it was checked on."""

        before, after = car.before, car.after
        assert before is not None and after is not None  # only evaluated cars are written
        assert event.field is not None and event.action is not None
        return PlannedMember(
            row=NewCorrection(
                correction_id=member_correction_id(event.event_id, car.vehicle_id, event.field),
                vehicle_id=car.vehicle_id,
                field=event.field,
                action=event.action,
                value=event.value,
                # What the row supersedes is read under the car's lock.
                supersedes_correction_id=None,
                group_id=event.event_id,
                reviewer=event.reviewer,
                reason=event.reason,
                previous_value=car.previous_value,
                previous_source=car.previous_source,
                catalog_batch=event.catalog_batch,
                automatic_terminal=before.terminal,
                automatic_ktype=before.ktype,
                code_version=event.code_version,
                evidence_fingerprint=car.input_hash,
                evidence={
                    "schema": MEMBER_EVIDENCE_SCHEMA,
                    "automatic": {
                        "terminal": before.terminal,
                        "top_ktype": before.ktype,
                        "reason_codes": [],
                    },
                    "decision_id": str(event.decision_id),
                    "checked": {
                        "outcome": car.outcome,
                        "after": {"terminal": after.terminal, "ktype": after.ktype},
                    },
                    "matcher_input_hash": car.input_hash,
                },
            ),
            input_hash=car.input_hash,
            outcome=car.outcome,
        )

    def withdraw(
        self, decision_id: UUID, request: DecisionWithdrawRequest
    ) -> tuple[DecisionWithdrawal, bool]:
        """Take a whole decision back; cars a person changed since are left as they are."""

        if request.reason is None:
            raise ReasonRequiredError("Say why this decision is undone.")
        stored, created = self._repository.withdraw(
            decision_id,
            operation_id=request.operation_id,
            reviewer=request.reviewer,
            reason=request.reason,
            code_version=self._code_version,
        )
        record = self._repository.decision(decision_id)
        label = str(record.root.scope_label) if record is not None else ""
        return _withdrawal(stored, label), created

    def decisions(self, limit: int) -> DecisionList:
        """The newest decisions first."""

        return DecisionList(
            decisions=[_summary(record) for record in self._repository.recent(limit)]
        )

    def decision(self, decision_id: UUID) -> DecisionSummary:
        record = self._repository.decision(decision_id)
        if record is None:
            raise DecisionNotFoundError(str(decision_id))
        return _summary(record)

