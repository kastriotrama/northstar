import re
from typing import Any

from api.app.features.tecdoc_connections.repository import ResolvedConnectionRepository
from api.app.features.tecdoc_connections.schemas import (
    ResolvedConnection,
    ResolvedConnectionPage,
)

_CODE_SEPARATORS = re.compile(r"[^A-Z0-9]+")


def _normalized_engine_code(value: object) -> str:
    return _CODE_SEPARATORS.sub("", str(value or "").upper())


def _with_engine_provenance(row: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(row)
    compatible = tuple(
        dict.fromkeys(str(value).strip() for value in row.get("engine_codes") or () if value)
    )
    source_code = str(row.get("ts_engine_code") or "").strip() or None
    enriched["engine_codes"] = list(compatible)
    enriched["tecdoc_compatible_engine_codes"] = list(compatible)
    enriched["selected_engine_code"] = None
    enriched["engine_used_for_ktype_selection"] = source_code is not None

    if source_code is not None:
        matches = tuple(
            code
            for code in compatible
            if _normalized_engine_code(code) == _normalized_engine_code(source_code)
        )
        if len(matches) == 1:
            enriched["selected_engine_code"] = matches[0]
            enriched["engine_selection_status"] = "source_exact"
            enriched["engine_evidence_source"] = "ts_exact_tecdoc_allocation"
        elif len(matches) > 1:
            enriched["engine_selection_status"] = "ambiguous"
            enriched["engine_evidence_source"] = "ts_exact_tecdoc_allocation"
        else:
            enriched["engine_selection_status"] = "contradicted"
            enriched["engine_evidence_source"] = "ts_vs_tecdoc_conflict"
    elif len(compatible) == 1:
        enriched["engine_selection_status"] = "tecdoc_only"
        enriched["engine_evidence_source"] = "ktype_uses_engine"
    elif len(compatible) > 1:
        enriched["engine_selection_status"] = "ambiguous"
        enriched["engine_evidence_source"] = "ktype_uses_engine"
    else:
        enriched["engine_selection_status"] = "unavailable"
        enriched["engine_evidence_source"] = "none"
    retained_reasons = tuple(row.get("evidence") or ()) + tuple(
        row.get("routing_reasons") or ()
    )
    stale_conflicts = sorted(
        str(reason)
        for reason in retained_reasons
        if "context_conflict_requires_review" in str(reason)
        or str(reason).startswith("context_conflict:")
        or str(reason).startswith("conflict:")
    )
    enriched["match_validation_status"] = (
        "rerun_required" if stale_conflicts else "current_resolved"
    )
    enriched["match_validation_reasons"] = stale_conflicts
    return enriched


class ResolvedConnectionService:
    def __init__(self, repository: ResolvedConnectionRepository) -> None:
        self._repository = repository

    def list(self, *, query: str, limit: int, offset: int) -> ResolvedConnectionPage:
        rows = tuple(_with_engine_provenance(row) for row in self._repository.load())
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
            current_resolved_total=sum(
                row["match_validation_status"] == "current_resolved" for row in rows
            ),
            rerun_required_total=sum(
                row["match_validation_status"] == "rerun_required" for row in rows
            ),
            items=[
                ResolvedConnection(**row)
                for row in filtered[offset : offset + limit]
            ],
        )
