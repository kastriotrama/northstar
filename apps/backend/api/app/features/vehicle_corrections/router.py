"""HTTP surface for a person's corrections of one car's data."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response

from api.app.core.db import get_postgres_connection
from api.app.core.settings import get_settings
from api.app.features.vehicle_corrections.repository import CorrectionRepository
from api.app.features.vehicle_corrections.schemas import CorrectionHistory, CorrectionRequest
from api.app.features.vehicle_corrections.service import (
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
from api.app.features.vehicle_matching.router import (
    ServiceDependency as MatchingServiceDependency,
)
from api.app.features.vehicle_matching.schemas import VehicleMatchLookup
from api.app.features.vehicle_matching.service import NoCatalogError, VehicleNotFoundError

router = APIRouter(prefix="/v1/vehicles", tags=["vehicle-corrections"])


@lru_cache(maxsize=1)
def _repository() -> CorrectionRepository:
    settings = get_settings()
    return CorrectionRepository(lambda: get_postgres_connection(settings))


def get_correction_service(matching: MatchingServiceDependency) -> CorrectionService:
    return CorrectionService(
        _repository(), matching.lookup_vehicle, get_settings().build_version
    )


CorrectionServiceDependency = Annotated[CorrectionService, Depends(get_correction_service)]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


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
