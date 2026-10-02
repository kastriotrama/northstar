"""HTTP surface for a person's KType choice on one car."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api.app.core.db import get_postgres_connection
from api.app.core.settings import get_settings
from api.app.features.vehicle_ktype_choices.repository import KTypeChoiceRepository
from api.app.features.vehicle_ktype_choices.schemas import (
    KTypeChoiceHistory,
    KTypeChoiceRequest,
)
from api.app.features.vehicle_ktype_choices.service import (
    ChoiceChangedError,
    ChoiceVehicleNotFoundError,
    EvidenceChangedError,
    InvalidVehicleIdError,
    KTypeChoiceService,
    KTypeNotACandidateError,
    NothingToWithdrawError,
    OperationReusedError,
    VehicleBusyError,
)
from api.app.features.vehicle_matching.router import (
    ServiceDependency as MatchingServiceDependency,
)
from api.app.features.vehicle_matching.schemas import VehicleMatchLookup
from api.app.features.vehicle_matching.service import NoCatalogError, VehicleNotFoundError

router = APIRouter(prefix="/v1/vehicles", tags=["vehicle-ktype-choices"])


@lru_cache(maxsize=1)
def _repository() -> KTypeChoiceRepository:
    settings = get_settings()
    return KTypeChoiceRepository(lambda: get_postgres_connection(settings))


def get_choice_service(matching: MatchingServiceDependency) -> KTypeChoiceService:
    return KTypeChoiceService(
        _repository(), matching.lookup_vehicle, get_settings().build_version
    )


ChoiceServiceDependency = Annotated[KTypeChoiceService, Depends(get_choice_service)]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


_UNAVAILABLE = "KType choices are temporarily unavailable. Nothing was saved; try again."


@router.post(
    "/{vehicle_id}/ktype-choices",
    response_model=VehicleMatchLookup,
    status_code=201,
    responses={200: {"description": "Replay of an operation already recorded."}},
)
def record_ktype_choice(
    vehicle_id: str,
    request: KTypeChoiceRequest,
    response: Response,
    service: ChoiceServiceDependency,
) -> VehicleMatchLookup:
    """Choose a candidate KType for this car, record "none of these", or withdraw.

    Append-only and idempotent by `operation_id`: resend the same body after a
    network error or a 503 and it is recorded once. Returns the car's refreshed
    lookup; 201 when recorded, 200 for a replay.
    """

    try:
        lookup, created = service.record(vehicle_id, request)
    except InvalidVehicleIdError as error:
        raise _error(422, "invalid_vehicle_id", str(error)) from error
    except (VehicleNotFoundError, ChoiceVehicleNotFoundError) as error:
        raise _error(404, "vehicle_not_found", "No such vehicle.") from error
    except OperationReusedError as error:
        raise _error(409, "operation_id_reused", str(error)) from error
    except ChoiceChangedError as error:
        raise _error(409, "choice_changed", str(error)) from error
    except EvidenceChangedError as error:
        raise _error(409, "evidence_changed", str(error)) from error
    except KTypeNotACandidateError as error:
        raise _error(422, "ktype_not_a_candidate", str(error)) from error
    except NothingToWithdrawError as error:
        raise _error(422, "nothing_to_withdraw", str(error)) from error
    except VehicleBusyError as error:
        raise _error(503, "vehicle_busy", str(error)) from error
    except (NoCatalogError, psycopg.Error) as error:
        raise _error(503, "unavailable", _UNAVAILABLE) from error
    if not created:
        response.status_code = 200
    return lookup


@router.get("/{vehicle_id}/ktype-choices", response_model=KTypeChoiceHistory)
def ktype_choice_history(
    vehicle_id: str,
    service: ChoiceServiceDependency,
    evidence: bool = Query(default=False, description="Include each row's stored evidence."),
) -> KTypeChoiceHistory:
    """This car's choices from the current one backwards; empty when nobody decided."""

    try:
        return service.history(vehicle_id, with_evidence=evidence)
    except InvalidVehicleIdError as error:
        raise _error(422, "invalid_vehicle_id", str(error)) from error
    except psycopg.Error as error:
        raise _error(503, "unavailable", _UNAVAILABLE) from error
