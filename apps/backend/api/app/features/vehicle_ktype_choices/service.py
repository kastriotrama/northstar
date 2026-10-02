"""Rules for recording a person's KType choice on one car.

The person chooses among the candidates the lookup showed. The server stores
its own evaluation as the evidence, so it first makes sure that evaluation is
the one the screen showed (the fingerprint) and that the choice sits on top of
the head the screen showed. Nothing is evaluated twice and nothing but
PostgreSQL is written: no graph, no alias, no canonical id, no match decision.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from api.app.features.vehicle_ktype_choices import evidence
from api.app.features.vehicle_ktype_choices.repository import (
    ChoiceRejectedError,
    ChoiceVehicleNotFoundError,
    VehicleBusyError,
)
from api.app.features.vehicle_ktype_choices.schemas import (
    KTypeChoiceHistory,
    KTypeChoiceHistoryEntry,
    KTypeChoiceRequest,
)
from api.app.features.vehicle_matching.schemas import VehicleMatchLookup
from ingestion.vehicle_core_query import is_vehicle_id
from ingestion.vehicle_ktype_choices import (
    ChoiceChangedError,
    NewChoice,
    NothingToWithdrawError,
    OperationReusedError,
    StoredChoice,
)

__all__ = [
    "ChoiceChangedError",
    "ChoiceRejectedError",
    "ChoiceVehicleNotFoundError",
    "EvidenceChangedError",
    "InvalidVehicleIdError",
    "KTypeChoiceService",
    "KTypeNotACandidateError",
    "NothingToWithdrawError",
    "OperationReusedError",
    "VehicleBusyError",
]


class InvalidVehicleIdError(ValueError):
    """The path does not carry a NOR id."""


class EvidenceChangedError(Exception):
    """The car's matching is no longer what the screen showed."""


class KTypeNotACandidateError(Exception):
    """The KType is not among the car's candidates."""


class ChoiceStore(Protocol):
    def fetch(self, choice_id: UUID) -> StoredChoice | None: ...

    def chain(self, vehicle_id: str) -> list[StoredChoice]: ...

    def record(self, new: NewChoice) -> tuple[StoredChoice, bool, int]: ...


def _vehicle_id(raw: str) -> str:
    vehicle_id = raw.strip().upper()
    if not is_vehicle_id(vehicle_id):
        raise InvalidVehicleIdError("Not a NorthStar vehicle id.")
    return vehicle_id


class KTypeChoiceService:
    def __init__(
        self,
        repository: ChoiceStore,
        lookup_vehicle: Callable[[str], VehicleMatchLookup],
        code_version: str,
    ) -> None:
        self._repository = repository
        self._lookup_vehicle = lookup_vehicle
        self._code_version = code_version.strip() or "unknown"

    def record(
        self, vehicle_id: str, request: KTypeChoiceRequest
    ) -> tuple[VehicleMatchLookup, bool]:
        """Record one choice; returns the refreshed lookup and whether a row was written.

        `False` is a replay: the same operation id with the same content, answered
        from what is stored without writing.
        """

        vehicle_id = _vehicle_id(vehicle_id)
        # 1. Replay first: a retry must get its answer even when the car's
        #    matching has moved on since the write succeeded.
        existing = self._repository.fetch(request.operation_id)
        if existing is not None:
            if not existing.same_request(
                vehicle_id=vehicle_id,
                action=request.action,
                ktype=request.ktype,
                reviewer=request.reviewer,
                reason=request.reason,
                supersedes_choice_id=request.supersedes_choice_id,
            ):
                raise OperationReusedError("This operation id already recorded a different choice.")
            return self._lookup_vehicle(vehicle_id), False

        # 2. Evaluate: the matcher memoizes, so this is the lookup the screen made.
        lookup = self._lookup_vehicle(vehicle_id)
        # 3. The stored evidence must be exactly what was shown.
        if request.action != "withdraw" and request.evidence_fingerprint != lookup.evidence_fingerprint:
            raise EvidenceChangedError("The car's matching changed since it was shown.")
        # 4. Only a listed candidate can be chosen; a ruled-out one may be.
        if request.action == "choose" and request.ktype not in {
            candidate.ktype for candidate in lookup.candidates
        }:
            raise KTypeNotACandidateError("That KType is not among this car's candidates.")

        # 5. One transaction: lock, head check, append, derived copy.
        row, created, history_count = self._repository.record(
            NewChoice(
                choice_id=request.operation_id,
                vehicle_id=vehicle_id,
                action=request.action,
                ktype=request.ktype,
                supersedes_choice_id=request.supersedes_choice_id,
                reviewer=request.reviewer,
                reason=request.reason,
                catalog_batch=lookup.catalog_batch,
                automatic_terminal=lookup.terminal,
                automatic_ktype=lookup.top_ktype,
                code_version=self._code_version,
                evidence_fingerprint=lookup.evidence_fingerprint,
                evidence=evidence.snapshot(lookup, self._code_version),
            )
        )
        if not created:
            # The same operation landed between step 1 and the transaction.
            return self._lookup_vehicle(vehicle_id), False
        # 6. No second evaluation: the row was built from this very lookup.
        state = evidence.assess(row, lookup, True, history_count)
        ktype, source = evidence.effective(state, lookup)
        return (
            lookup.model_copy(
                update={"choice": state, "effective_ktype": ktype, "effective_source": source}
            ),
            True,
        )

    def history(self, vehicle_id: str, *, with_evidence: bool = False) -> KTypeChoiceHistory:
        """The car's choices from the current one backwards. No matcher run."""

        vehicle_id = _vehicle_id(vehicle_id)
        rows = self._repository.chain(vehicle_id)
        return KTypeChoiceHistory(
            vehicle_id=vehicle_id,
            current_choice_id=rows[0].choice_id if rows else None,
            entries=[
                KTypeChoiceHistoryEntry(
                    choice_id=row.choice_id,
                    action=row.action,
                    ktype=row.ktype,
                    reviewer=row.reviewer,
                    reason=row.reason,
                    created_at=row.created_at,
                    supersedes_choice_id=row.supersedes_choice_id,
                    catalog_batch=row.catalog_batch,
                    automatic_terminal=row.automatic_terminal,
                    automatic_ktype=row.automatic_ktype,
                    code_version=row.code_version,
                    evidence=row.evidence if with_evidence else None,
                )
                for row in rows
            ],
        )
