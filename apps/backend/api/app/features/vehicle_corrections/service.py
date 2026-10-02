"""Rules for recording a person's correction of one field of one car.

A correction is evidence for the matcher, never a bypass: it changes what the
matcher is handed for this car and the car is matched again with every guard
on. The same holds for the one correction that is not a value, the release of a
car stopped before matching. The server stores its own evaluation as the
evidence, so it first makes sure that evaluation is the one the screen showed
(the fingerprint) and that the correction sits on top of the head the screen
showed for the field. After the write the car is evaluated afresh, and that
evaluation is the answer. Nothing but PostgreSQL is written: no graph, no alias,
no canonical id, no match decision.

Two things guard a car the matcher resolves today. A value correction that would
take its KType away, or move it to another one, is evaluated before anything is
written and refused until the person confirms it. And the write itself goes
ahead only if, under the vehicle's lock, the matcher would still be handed what
the lookup it was decided on was made of.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Protocol
from uuid import UUID

from api.app.features.vehicle_corrections import fields
from api.app.features.vehicle_corrections.fields import (
    FieldNotCorrectableError,
    InvalidValueError,
)
from api.app.features.vehicle_corrections.repository import (
    CorrectionRejectedError,
    CorrectionVehicleNotFoundError,
    EvidenceChangedError,
    VehicleBusyError,
)
from api.app.features.vehicle_corrections.schemas import (
    CorrectionFieldHistory,
    CorrectionHistory,
    CorrectionHistoryEntry,
    CorrectionRequest,
)
from api.app.features.vehicle_matching.repository import Hypothetical
from api.app.features.vehicle_matching.schemas import VehicleMatchLookup
from api.app.features.vehicle_matching.service import MatchOutcome
from ingestion.vehicle_core_fields import SOURCE_CORRECTION
from ingestion.vehicle_core_query import is_vehicle_id
from ingestion.vehicle_fact_corrections import (
    CorrectionChangedError,
    NewCorrection,
    NothingToWithdrawError,
    OperationReusedError,
    StoredCorrection,
)

__all__ = [
    "ConfirmationRequiredError",
    "CorrectionChangedError",
    "CorrectionRejectedError",
    "CorrectionService",
    "CorrectionVehicleNotFoundError",
    "EvidenceChangedError",
    "FieldNotCorrectableError",
    "InvalidValueError",
    "InvalidVehicleIdError",
    "NothingToIgnoreError",
    "NothingToWithdrawError",
    "OperationReusedError",
    "ReasonRequiredError",
    "ValueUnchangedError",
    "VehicleBusyError",
]

EVIDENCE_SCHEMA = "vehicle-fact-correction-evidence-v1"


class InvalidVehicleIdError(ValueError):
    """The path does not carry a NOR id."""


class ConfirmationRequiredError(Exception):
    """The correction would take a resolved car's KType away or move it to another one.

    Carries where the matcher ends for the car today and where it would end, so
    the person can be shown both before confirming.
    """

    def __init__(self, message: str, before: MatchOutcome, after: MatchOutcome) -> None:
        super().__init__(message)
        self.before = before
        self.after = after


class ValueUnchangedError(Exception):
    """The value is the one the matcher already uses for this car."""


class NothingToIgnoreError(Exception):
    """The matcher has no value for the field on this car, or the car is not stopped."""


class ReasonRequiredError(Exception):
    """Releasing a stopped car needs a reason."""


class CorrectionStore(Protocol):
    def fetch(self, correction_id: UUID) -> StoredCorrection | None: ...

    def chains(self, vehicle_id: str) -> dict[str, list[StoredCorrection]]: ...

    def record(
        self, new: NewCorrection, *, checked_on: str | None = None
    ) -> tuple[StoredCorrection, bool]: ...


def vehicle_id_of(raw: str) -> str:
    """The path's vehicle id in its stored spelling; refuses anything that is not a NOR id."""

    vehicle_id = raw.strip().upper()
    if not is_vehicle_id(vehicle_id):
        raise InvalidVehicleIdError("Not a NorthStar vehicle id.")
    return vehicle_id


def snapshot(lookup: VehicleMatchLookup) -> dict[str, Any]:
    """The evidence stored with a correction: what the matcher saw and concluded before it.

    No plate, VIN, vehicle id, candidate list or decision trace.
    """

    return {
        "schema": EVIDENCE_SCHEMA,
        "automatic": {
            "terminal": lookup.terminal,
            "top_ktype": lookup.top_ktype,
            "reason_codes": list(lookup.reason_codes),
        },
        "inputs": None if lookup.inputs is None else lookup.inputs.model_dump(mode="json"),
        "overlaid_fields": dict(lookup.overlaid_fields),
    }


def _replay_value(request: CorrectionRequest) -> str | None:
    """The request's value as a stored row would carry it, for telling a replay apart.

    A value the field does not accept was never stored, so it is compared as sent.
    """

    if request.action != "set":
        return None
    try:
        return fields.canonical_value(request.field, request.value)
    except (FieldNotCorrectableError, InvalidValueError):
        return request.value


def _requested_value(request: CorrectionRequest) -> str | None:
    """What the request asks to store, or the reason it makes no sense on its own."""

    if request.field == fields.NORMALIZATION_STOP:
        if request.action == "set":
            raise InvalidValueError(
                "The stop before matching has no value: release the car or put the stop back."
            )
        if request.action == "ignore" and request.reason is None:
            raise ReasonRequiredError("Say why this car should be matched anyway.")
        return None
    return value_of(request.field, request.action, request.value)


def value_of(field: str, action: str, raw: str | None) -> str | None:
    """The value a correction of a value field stores: checked for a `set`, none otherwise.

    Raises `FieldNotCorrectableError` for anything but a correctable field (the
    stop before matching is not one) and `InvalidValueError` for a value the
    field does not take. One car's correction and a check over many cars
    validate through here, so they accept exactly the same.
    """

    fields.spec_for(field)
    if action != "set":
        return None
    return fields.canonical_value(field, raw)


def replaced_value(
    lookup: VehicleMatchLookup, field: str, action: str, value: str | None
) -> tuple[str | None, str]:
    """What the matcher uses for a value field on this car today, and where it comes from.

    Refuses a correction that would change nothing the matcher is handed:
    `ValueUnchangedError` for a `set` to the present value, `NothingToIgnoreError`
    for an `ignore` where there is none.
    """

    shown = next((item for item in lookup.correctable_fields if item.field == field), None)
    if shown is None:
        raise RuntimeError("The lookup does not describe the car's correctable fields.")
    if value is not None and fields.same_value(field, value, shown.current_value):
        raise ValueUnchangedError("That is already the value the matcher uses for this car.")
    if action == "ignore" and shown.current_value is None:
        raise NothingToIgnoreError("The matcher has no value for this field on this car.")
    return shown.current_value, shown.current_source


def _replaced(
    lookup: VehicleMatchLookup, request: CorrectionRequest, value: str | None
) -> tuple[str | None, str]:
    """What the matcher uses for the field today and where it comes from.

    This is what the correction replaces and the row remembers. A correction
    that would change nothing the matcher is handed is refused here.
    """

    if request.field == fields.NORMALIZATION_STOP:
        if request.action != "ignore":
            return None, SOURCE_CORRECTION
        if lookup.terminal != fields.STOPPED_TERMINAL:
            raise NothingToIgnoreError("This car is not stopped before matching.")
        return ",".join(lookup.stop_reasons) or None, fields.SOURCE_REGISTRY
    return replaced_value(lookup, request.field, request.action, value)


def _harms(before: MatchOutcome, after: MatchOutcome) -> bool:
    """True when a car that resolves today would lose its KType or get another one."""

    return before.terminal == "resolved" and (
        after.terminal != "resolved" or after.ktype != before.ktype
    )


class CorrectionService:
    def __init__(
        self,
        repository: CorrectionStore,
        lookup_vehicle: Callable[[str], VehicleMatchLookup],
        code_version: str,
        what_if: Callable[[str, Hypothetical], MatchOutcome] | None = None,
    ) -> None:
        self._repository = repository
        self._lookup_vehicle = lookup_vehicle
        self._code_version = code_version.strip() or "unknown"
        #: Where the matcher would end for a vehicle with one more correction on
        #: top (`VehicleMatchingService.what_if`). Asked only for a car that
        #: resolves today; a service built without it cannot correct such a car.
        self._what_if = what_if

    def record(
        self, vehicle_id: str, request: CorrectionRequest
    ) -> tuple[VehicleMatchLookup, bool]:
        """Record one correction; returns the car's fresh lookup and whether a row was written.

        `False` is a replay: the same operation id with the same content, answered
        from what is stored without writing.
        """

        vehicle_id = vehicle_id_of(vehicle_id)
        # 1. Replay first: a retry must get its answer even when the car's
        #    matching has moved on since the write succeeded.
        existing = self._repository.fetch(request.operation_id)
        if existing is not None:
            if not existing.same_request(
                vehicle_id=vehicle_id,
                field=request.field,
                action=request.action,
                value=_replay_value(request),
                reviewer=request.reviewer,
                reason=request.reason,
                supersedes_correction_id=request.supersedes_correction_id,
            ):
                raise OperationReusedError(
                    "This operation id already recorded a different correction."
                )
            return self._lookup_vehicle(vehicle_id), False

        # 2. Evaluate: the matcher memoizes, so this is the lookup the screen made.
        lookup = self._lookup_vehicle(vehicle_id)
        # 3. What the request asks for must make sense on its own ...
        value = _requested_value(request)
        # 4. ... and must have been decided on this evaluation: the stored
        #    evidence is exactly what was shown.
        shown_today = request.evidence_fingerprint == lookup.evidence_fingerprint
        if request.action != "withdraw" and not shown_today:
            raise EvidenceChangedError("The car's matching changed since it was shown.")
        # 5. A correction changes what the matcher is handed, or it is refused.
        previous_value, previous_source = _replaced(lookup, request, value)
        # 6. A resolved car never changes or loses its KType silently.
        self._confirm(vehicle_id, lookup, request, value)

        # 7. One transaction: lock, the car is still what was looked up, head
        #    check, append, the vehicle's copy.
        _, created = self._repository.record(
            NewCorrection(
                correction_id=request.operation_id,
                vehicle_id=vehicle_id,
                field=request.field,
                action=request.action,
                value=value,
                supersedes_correction_id=request.supersedes_correction_id,
                group_id=None,
                reviewer=request.reviewer,
                reason=request.reason,
                previous_value=previous_value,
                previous_source=previous_source,
                catalog_batch=lookup.catalog_batch,
                automatic_terminal=lookup.terminal,
                automatic_ktype=lookup.top_ktype,
                code_version=self._code_version,
                evidence_fingerprint=lookup.evidence_fingerprint,
                evidence=snapshot(lookup),
            ),
            checked_on=lookup.matcher_input_hash,
        )
        # 8. The car is matched again on what is stored now -- also when the same
        #    operation landed between step 1 and the transaction.
        return self._lookup_vehicle(vehicle_id), created

    def _confirm(
        self,
        vehicle_id: str,
        lookup: VehicleMatchLookup,
        request: CorrectionRequest,
        value: str | None,
    ) -> None:
        """Refuse a value correction that would harm a resolved car, unless it is confirmed.

        The car is evaluated with the correction laid on top, by the real
        matcher, before anything is written. A withdrawal and the release of a
        stopped car are not asked about: neither is a new claim about the car.
        """

        action: Literal["set", "ignore"]
        if request.action == "set":
            action = "set"
        elif request.action == "ignore":
            action = "ignore"
        else:
            return
        if request.field == fields.NORMALIZATION_STOP or lookup.terminal != "resolved":
            return
        # A request built on a stale screen is told so, not asked to confirm
        # something it could not save anyway (the store checks again under the lock).
        head = next((item for item in lookup.corrections if item.field == request.field), None)
        if (head.correction_id if head else None) != request.supersedes_correction_id:
            raise CorrectionChangedError("Someone changed this correction; reload the car.")
        if self._what_if is None:
            raise RuntimeError("The correction service was built without the matcher's what-if.")
        before = MatchOutcome(lookup.terminal, lookup.top_ktype)
        after = self._what_if(vehicle_id, Hypothetical(request.field, action, value))
        if _harms(before, after) and not request.confirm_change:
            raise ConfirmationRequiredError(
                "This car resolves today. With this correction it would "
                + (
                    "resolve to another KType."
                    if after.terminal == "resolved"
                    else "no longer resolve."
                ),
                before,
                after,
            )

    def history(self, vehicle_id: str) -> CorrectionHistory:
        """The car's corrections by field, each from the current one backwards. No matcher run."""

        vehicle_id = vehicle_id_of(vehicle_id)
        return CorrectionHistory(
            vehicle_id=vehicle_id,
            fields=[
                CorrectionFieldHistory(
                    field=field,
                    current_correction_id=rows[0].correction_id if rows else None,
                    entries=[
                        CorrectionHistoryEntry(
                            correction_id=row.correction_id,
                            action=row.action,
                            value=row.value,
                            reviewer=row.reviewer,
                            reason=row.reason,
                            created_at=row.created_at,
                            supersedes_correction_id=row.supersedes_correction_id,
                            previous_value=row.previous_value,
                            previous_source=row.previous_source,
                            group_id=row.group_id,
                            catalog_batch=row.catalog_batch,
                            automatic_terminal=row.automatic_terminal,
                            automatic_ktype=row.automatic_ktype,
                            code_version=row.code_version,
                        )
                        for row in rows
                    ],
                )
                for field, rows in sorted(self._repository.chains(vehicle_id).items())
            ],
        )
