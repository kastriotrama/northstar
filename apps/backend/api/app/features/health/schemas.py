from typing import Literal

from pydantic import BaseModel


class DatastoreHealth(BaseModel):
    name: str
    status: Literal["ok", "error"]
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    environment: str
    datastores: list[DatastoreHealth]
