import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

StoreHealthStatus = Literal["ok", "error"]


@dataclass(frozen=True)
class StoreHealth:
    name: str
    status: StoreHealthStatus
    detail: str | None = None


class DatastoreHealthClient(Protocol):
    name: str

    def check(self) -> StoreHealth:
        """Return the datastore's current health. May raise on failure."""


def run_health_checks(clients: Sequence[DatastoreHealthClient]) -> list[StoreHealth]:
    """Check every client concurrently; a failing client yields an error result."""
    if not clients:
        return []

    with ThreadPoolExecutor(max_workers=len(clients)) as executor:
        return list(executor.map(_safe_check, clients))


def _safe_check(client: DatastoreHealthClient) -> StoreHealth:
    try:
        return client.check()
    except Exception as exc:
        logger.exception("Health check failed for datastore %s", client.name)
        return StoreHealth(name=client.name, status="error", detail=exc.__class__.__name__)
