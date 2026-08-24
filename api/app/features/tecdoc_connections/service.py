from api.app.features.tecdoc_connections.repository import ResolvedConnectionRepository
from api.app.features.tecdoc_connections.schemas import (
    ResolvedConnection,
    ResolvedConnectionPage,
)


class ResolvedConnectionService:
    def __init__(self, repository: ResolvedConnectionRepository) -> None:
        self._repository = repository

    def list(self, *, query: str, limit: int, offset: int) -> ResolvedConnectionPage:
        rows = self._repository.load()
        needle = query.strip().casefold()
        filtered = tuple(
            row for row in rows
            if not needle or needle in " ".join(str(value) for value in (
                row.get("vehicle_id"), row.get("plate"), row.get("manufacturer"), row.get("ts_model"),
                row.get("tecdoc_model"), row.get("ktype"),
                " ".join(row.get("engine_codes") or []),
            )).casefold()
        )
        return ResolvedConnectionPage(
            total=len(rows), filtered_total=len(filtered), limit=limit, offset=offset,
            privacy_note="Restricted local view: registration plate is shown; VIN, source ID and raw payload are omitted.",
            items=[ResolvedConnection(**row) for row in filtered[offset:offset + limit]],
        )
