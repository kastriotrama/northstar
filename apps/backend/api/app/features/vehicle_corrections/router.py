"""HTTP surface for a person's corrections of a car's data: one car, or the cars like it."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api.app.core.db import get_postgres_connection
from api.app.core.settings import get_settings
from api.app.features.vehicle_corrections.decision_repository import DecisionRepository
from api.app.features.vehicle_corrections.decision_service import (
    DecisionChangedError,
    DecisionNotFoundError,
    DecisionRejectedError,
    DecisionService,
    HarmsMoreThanItFixesError,
    InvalidScopeError,
    NothingToApplyError,
    PreviewBusyError,
    PreviewExpiredError,
    PreviewIncompleteError,
    PreviewNotFoundError,
    ScopeNotOfferedError,
    ScopeTooBroadError,
    VehiclesBusyError,
)
from api.app.features.vehicle_corrections.preview import PreviewJobs
from api.app.features.vehicle_corrections.repository import CorrectionRepository
from api.app.features.vehicle_corrections.schemas import (
    CorrectionHistory,
    CorrectionPreview,
    CorrectionRequest,
    DecisionList,
    DecisionRequest,
    DecisionResult,
    DecisionSummary,
    DecisionWithdrawal,
    DecisionWithdrawRequest,
    Outcome,
    PreviewCarPage,
    PreviewRequest,
    ScopeOptions,
    ScopesRequest,
)
from api.app.features.vehicle_corrections.service import (
    ConfirmationRequiredError,
    CorrectionChangedError,
    CorrectionRejectedError,
    CorrectionService,
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
from api.app.features.vehicle_match_results.router import match_result_sync
from api.app.features.vehicle_matching.router import (
    ServiceDependency as MatchingServiceDependency,
)
from api.app.features.vehicle_matching.schemas import VehicleMatchLookup
from api.app.features.vehicle_matching.service import NoCatalogError, VehicleNotFoundError

router = APIRouter(prefix="/v1/vehicles", tags=["vehicle-corrections"])
#: Checks and decisions are not one car's: they have their own path.
decisions_router = APIRouter(prefix="/v1/vehicle-corrections", tags=["vehicle-corrections"])


@lru_cache(maxsize=1)
def _repository() -> CorrectionRepository:
    settings = get_settings()
    return CorrectionRepository(lambda: get_postgres_connection(settings))


@lru_cache(maxsize=1)
def _decision_repository() -> DecisionRepository:
    settings = get_settings()
    return DecisionRepository(lambda: get_postgres_connection(settings))


@lru_cache(maxsize=1)
def _previews() -> PreviewJobs:
    return PreviewJobs()


def get_correction_service(matching: MatchingServiceDependency) -> CorrectionService:
    return CorrectionService(
        _repository(),
        matching.lookup_vehicle,
        get_settings().build_version,
        what_if=matching.what_if,
        on_saved=match_result_sync().vehicle_changed,
    )


def get_decision_service(matching: MatchingServiceDependency) -> DecisionService:
    settings = get_settings()
    return DecisionService(
        _decision_repository(),
        matching.lookup_vehicle,
        matching.matcher,
        _previews(),
        settings.build_version,
        max_cars=settings.correction_preview_max_cars,
        max_seconds=settings.correction_preview_max_seconds,
        on_changed=match_result_sync().decision_changed,
    )


CorrectionServiceDependency = Annotated[CorrectionService, Depends(get_correction_service)]
DecisionServiceDependency = Annotated[DecisionService, Depends(get_decision_service)]


def _error(status_code: int, code: str, message: str, **more: Any) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message, **more}
    )


_UNAVAILABLE = "Corrections are temporarily unavailable. Nothing was saved; try again."
_HISTORY_UNAVAILABLE = "The correction history is temporarily unavailable; try again."
_NOT_STORABLE = "The request holds a value that cannot be stored. Nothing was saved."


@router.post(
    "/{vehicle_id}/corrections",
    response_model=VehicleMatchLookup,
    status_code=201,
    responses={200: {"description": "Replay of an operation already recorded."}},
)
def record_correction(
    vehicle_id: str,
    request: CorrectionRequest,
    response: Response,
    service: CorrectionServiceDependency,
) -> VehicleMatchLookup:
    """Set a value for one field of this car, ignore its present value, or withdraw.

    The field `normalization_stop` is the one that is not a value: `ignore`
    releases a car its record's normalization keeps from the matcher (a reason
    is required), `withdraw` puts the stop back.

    Append-only and idempotent by `operation_id`: resend the same body after a
    network error or a 503 and it is recorded once. The car is matched again at
    once; the answer is its lookup after the write: 201 when recorded, 200 for a
    replay. Only a 503 is worth retrying: every 4xx says the same request would
    be refused again.

    A `set` or an `ignore` on a car the matcher resolves today is evaluated with
    the correction laid on top first. If the car would no longer resolve, or
    would resolve to another KType, the answer is 409 `confirmation_required`
    with `before` and `after` (`{terminal, ktype}`), and nothing is saved until
    the same request is sent with `confirm_change: true`.
    """

    try:
        lookup, created = service.record(vehicle_id, request)
    except InvalidVehicleIdError as error:
        raise _error(422, "invalid_vehicle_id", str(error)) from error
    except (VehicleNotFoundError, CorrectionVehicleNotFoundError) as error:
        raise _error(404, "vehicle_not_found", "No such vehicle.") from error
    except OperationReusedError as error:
        raise _error(409, "operation_id_reused", str(error)) from error
    except CorrectionChangedError as error:
        raise _error(409, "correction_changed", str(error)) from error
    except EvidenceChangedError as error:
        raise _error(409, "evidence_changed", str(error)) from error
    except ConfirmationRequiredError as error:
        raise _error(
            409,
            "confirmation_required",
            str(error),
            before={"terminal": error.before.terminal, "ktype": error.before.ktype},
            after={"terminal": error.after.terminal, "ktype": error.after.ktype},
        ) from error
    except FieldNotCorrectableError as error:
        raise _error(422, "field_not_correctable", str(error)) from error
    except InvalidValueError as error:
        raise _error(422, "invalid_value", str(error)) from error
    except ValueUnchangedError as error:
        raise _error(422, "value_unchanged", str(error)) from error
    except ReasonRequiredError as error:
        raise _error(422, "reason_required", str(error)) from error
    except NothingToIgnoreError as error:
        raise _error(422, "nothing_to_ignore", str(error)) from error
    except NothingToWithdrawError as error:
        raise _error(422, "nothing_to_withdraw", str(error)) from error
    except VehicleBusyError as error:
        raise _error(503, "vehicle_busy", str(error)) from error
    except CorrectionRejectedError as error:
        raise _error(422, "not_storable", f"{error} Nothing was saved.") from error
    except (psycopg.errors.DataError, psycopg.errors.IntegrityError) as error:
        # The database refused the content itself; a retry would fail the same way.
        raise _error(422, "not_storable", _NOT_STORABLE) from error
    except (NoCatalogError, psycopg.Error) as error:
        raise _error(503, "unavailable", _UNAVAILABLE) from error
    if not created:
        response.status_code = 200
    return lookup


@router.get("/{vehicle_id}/corrections", response_model=CorrectionHistory)
def correction_history(
    vehicle_id: str, service: CorrectionServiceDependency
) -> CorrectionHistory:
    """This car's corrections by field, each from the current one backwards.

    Empty when nobody corrected the car. Does not run the matcher.
    """

    try:
        return service.history(vehicle_id)
    except InvalidVehicleIdError as error:
        raise _error(422, "invalid_vehicle_id", str(error)) from error
    except psycopg.Error as error:
        raise _error(503, "unavailable", _HISTORY_UNAVAILABLE) from error


# ------------------------------------------------------------- one correction, many cars

_CHECK_UNAVAILABLE = "The check is temporarily unavailable. Nothing was saved; try again."
_DECISION_UNAVAILABLE = "Decisions are temporarily unavailable. Nothing was saved; try again."


def _anchor_error(error: Exception) -> HTTPException | None:
    """What the scopes call and the check refuse a correction for, as one car's is refused."""

    if isinstance(error, InvalidVehicleIdError):
        return _error(422, "invalid_vehicle_id", str(error))
    if isinstance(error, VehicleNotFoundError | CorrectionVehicleNotFoundError):
        return _error(404, "vehicle_not_found", "No such vehicle.")
    if isinstance(error, FieldNotCorrectableError):
        return _error(422, "field_not_correctable", str(error))
    if isinstance(error, InvalidValueError):
        return _error(422, "invalid_value", str(error))
    if isinstance(error, ValueUnchangedError):
        return _error(422, "value_unchanged", str(error))
    if isinstance(error, NothingToIgnoreError):
        return _error(422, "nothing_to_ignore", str(error))
    return None


@router.post("/{vehicle_id}/corrections/scopes", response_model=ScopeOptions)
def correction_scopes(
    vehicle_id: str, request: ScopesRequest, service: DecisionServiceDependency
) -> ScopeOptions:
    """Whom a correction entered on this car could apply to, with a count each.

    Always "only this car"; "the cars with exactly the same data" when the car
    has a manufacturer; and for most fields one or more "all cars like this"
    options, narrowest first. An option whose count did not finish in time
    comes back with `count: null` and `too_broad: true`. Saves nothing.
    """

    try:
        return service.scopes(vehicle_id, request)
    except (NoCatalogError, psycopg.Error) as error:
        raise _error(503, "unavailable", _CHECK_UNAVAILABLE) from error
    except Exception as error:
        refused = _anchor_error(error)
        if refused is None:
            raise
        raise refused from error


@router.post(
    "/{vehicle_id}/corrections/preview", response_model=CorrectionPreview, status_code=202
)
def start_correction_preview(
    vehicle_id: str, request: PreviewRequest, service: DecisionServiceDependency
) -> CorrectionPreview:
    """Start checking what a correction would change for the cars of a scope.

    Answers 202 with the job; poll `GET /v1/vehicle-corrections/previews/{id}`
    until its status settles. Nothing is written. The server runs one check at
    a time (429 `busy`), on at most `CORRECTION_PREVIEW_MAX_CARS` cars and for
    at most `CORRECTION_PREVIEW_MAX_SECONDS`; a check that did not cover every
    car can be kept as a proposal but not applied.
    """

    try:
        return service.start_preview(vehicle_id, request)
    except PreviewBusyError as error:
        raise _error(429, "busy", str(error)) from error
    except EvidenceChangedError as error:
        raise _error(409, "evidence_changed", str(error)) from error
    except ScopeTooBroadError as error:
        raise _error(422, "scope_too_broad", str(error)) from error
    except ScopeNotOfferedError as error:
        raise _error(422, "scope_not_offered", str(error)) from error
    except InvalidScopeError as error:
        raise _error(422, "invalid_scope", str(error)) from error
    except (NoCatalogError, psycopg.Error) as error:
        raise _error(503, "unavailable", _CHECK_UNAVAILABLE) from error
    except Exception as error:
        refused = _anchor_error(error)
        if refused is None:
            raise
        raise refused from error


@decisions_router.get("/previews/{preview_id}", response_model=CorrectionPreview)
def correction_preview(preview_id: str, service: DecisionServiceDependency) -> CorrectionPreview:
    """A check as it stands: partial counts while it runs, final ones once it has ended."""

    try:
        return service.preview(preview_id)
    except PreviewNotFoundError as error:
        raise _error(404, "preview_not_found", str(error)) from error


@decisions_router.get("/previews/{preview_id}/cars", response_model=PreviewCarPage)
def correction_preview_cars(
    preview_id: str,
    service: DecisionServiceDependency,
    outcome: Annotated[Outcome | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PreviewCarPage:
    """The checked cars of one outcome (all of them without `outcome`), before and after."""

    try:
        return service.preview_cars(preview_id, outcome, offset=offset, limit=limit)
    except PreviewNotFoundError as error:
        raise _error(404, "preview_not_found", str(error)) from error


@decisions_router.delete("/previews/{preview_id}", response_model=CorrectionPreview)
def stop_correction_preview(
    preview_id: str, service: DecisionServiceDependency
) -> CorrectionPreview:
    """Stop a running check. It keeps what it found; stopping a finished one changes nothing."""

    try:
        return service.stop_preview(preview_id)
    except PreviewNotFoundError as error:
        raise _error(404, "preview_not_found", str(error)) from error


@decisions_router.post(
    "/decisions",
    response_model=DecisionResult,
    status_code=201,
    responses={200: {"description": "Replay of an operation already recorded."}},
)
def decide_correction(
    request: DecisionRequest, response: Response, service: DecisionServiceDependency
) -> DecisionResult:
    """Apply a checked correction to its cars (`apply`), or keep it as a proposal (`propose`).

    What was checked is what is written: the cars come from the check, and a
    car that changed since, or that a person corrected meanwhile, is left out
    and counted. One transaction; idempotent by `operation_id` (201 when
    recorded, 200 for a replay). Only a 503 is worth retrying, with the same id.
    """

    try:
        result, created = service.decide(request)
    except PreviewNotFoundError as error:
        raise _error(404, "preview_not_found", str(error)) from error
    except PreviewExpiredError as error:
        raise _error(409, "preview_expired", str(error)) from error
    except PreviewIncompleteError as error:
        raise _error(409, "preview_incomplete", str(error)) from error
    except OperationReusedError as error:
        raise _error(409, "operation_id_reused", str(error)) from error
    except ReasonRequiredError as error:
        raise _error(422, "reason_required", str(error)) from error
    except HarmsMoreThanItFixesError as error:
        raise _error(422, "harms_more_than_it_fixes", str(error)) from error
    except NothingToApplyError as error:
        raise _error(422, "nothing_to_apply", str(error)) from error
    except VehiclesBusyError as error:
        raise _error(503, "vehicles_busy", str(error)) from error
    except DecisionRejectedError as error:
        raise _error(422, "not_storable", f"{error} Nothing was saved.") from error
    except psycopg.Error as error:
        raise _error(503, "unavailable", _DECISION_UNAVAILABLE) from error
    if not created:
        response.status_code = 200
    return result


@decisions_router.post(
    "/decisions/{decision_id}/withdraw",
    response_model=DecisionWithdrawal,
    status_code=201,
    responses={200: {"description": "Replay of an operation already recorded."}},
)
def withdraw_correction_decision(
    decision_id: UUID,
    request: DecisionWithdrawRequest,
    response: Response,
    service: DecisionServiceDependency,
) -> DecisionWithdrawal:
    """Undo a decision on every car it still stands on.

    A car a person changed since is left as it is and counted. One
    transaction; idempotent by `operation_id`.
    """

    try:
        result, created = service.withdraw(decision_id, request)
    except DecisionNotFoundError as error:
        raise _error(404, "decision_not_found", "No such decision.") from error
    except OperationReusedError as error:
        raise _error(409, "operation_id_reused", str(error)) from error
    except DecisionChangedError as error:
        raise _error(409, "decision_changed", str(error)) from error
    except ReasonRequiredError as error:
        raise _error(422, "reason_required", str(error)) from error
    except NothingToWithdrawError as error:
        raise _error(422, "nothing_to_withdraw", str(error)) from error
    except VehiclesBusyError as error:
        raise _error(503, "vehicles_busy", str(error)) from error
    except DecisionRejectedError as error:
        raise _error(422, "not_storable", f"{error} Nothing was saved.") from error
    except psycopg.Error as error:
        raise _error(503, "unavailable", _DECISION_UNAVAILABLE) from error
    if not created:
        response.status_code = 200
    return result


@decisions_router.get("/decisions", response_model=DecisionList)
def correction_decisions(
    service: DecisionServiceDependency,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DecisionList:
    """The newest decisions first: what, for which cars, who, why, and where each stands."""

    try:
        return service.decisions(limit)
    except psycopg.Error as error:
        raise _error(503, "unavailable", _DECISION_UNAVAILABLE) from error


@decisions_router.get("/decisions/{decision_id}", response_model=DecisionSummary)
def correction_decision(
    decision_id: UUID, service: DecisionServiceDependency
) -> DecisionSummary:
    try:
        return service.decision(decision_id)
    except DecisionNotFoundError as error:
        raise _error(404, "decision_not_found", "No such decision.") from error
    except psycopg.Error as error:
        raise _error(503, "unavailable", _DECISION_UNAVAILABLE) from error
