"""Pure TecDoc-to-canonical candidate mapping and deterministic deduplication."""

from __future__ import annotations

from collections.abc import Iterable

from ingestion.tecdoc.models import CanonicalCandidate, TecDocVehicleRow


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def candidates_for_row(row: TecDocVehicleRow) -> tuple[CanonicalCandidate, ...]:
    """Map a KType row without inventing unavailable platform/component facts."""

    variant_attributes: dict[str, object] = {
        "source_name": _clean(row.variant_name),
        "model_family_source_key": f"model:{row.model_id}",
        "market": [],
        "year_from": row.year_from,
        "year_to": row.year_to,
        "drive_type": row.drive_type,
        "platform_source_key": (
            f"platform:{row.platform_id}"
            if row.platform_id and row.platform_code and row.platform_year_from
            else None
        ),
        "engine_source_key": f"engine:{row.engine_id}" if row.engine_id else None,
        "transmission_source_key": (
            f"transmission:{row.transmission_id}" if row.transmission_id else None
        ),
        "bodywork_source_key": (
            f"bodywork:{row.bodywork_id}:{row.door_count or 0}" if row.bodywork_id else None
        ),
        "power_kw": row.power_kw,
    }
    candidates = [
        CanonicalCandidate(
            "manufacturer",
            f"manufacturer:{row.manufacturer_id}",
            {"canonical_name": _clean(row.manufacturer_name)},
        ),
        CanonicalCandidate(
            "model_family",
            f"model:{row.model_id}",
            {
                "canonical_name": _clean(row.model_name),
                "manufacturer_source_key": f"manufacturer:{row.manufacturer_id}",
            },
        ),
        CanonicalCandidate(
            "vehicle_variant",
            f"variant:{row.variant_id}",
            variant_attributes,
        ),
        CanonicalCandidate(
            "alias",
            f"ktype:{row.ktype_id}",
            {
                "alias_text": row.ktype_id,
                "alias_type": "k_type",
                "source_system": "tecdoc",
                "target_source_key": f"variant:{row.variant_id}",
            },
        ),
    ]
    if row.platform_id and row.platform_code and row.platform_year_from:
        candidates.append(
            CanonicalCandidate(
                "platform",
                f"platform:{row.platform_id}",
                {
                    "platform_code": _clean(row.platform_code),
                    "generation": _clean(row.platform_generation),
                    "year_from": row.platform_year_from,
                    "year_to": row.platform_year_to,
                    "facelift": row.platform_facelift,
                    "model_family_source_key": f"model:{row.model_id}",
                },
            )
        )
    if row.engine_id and row.engine_code and row.displacement_cc and row.fuel_type:
        candidates.append(
            CanonicalCandidate(
                "engine",
                f"engine:{row.engine_id}",
                {
                    "engine_code": _clean(row.engine_code),
                    "displacement_cc": row.displacement_cc,
                    "fuel_type": row.fuel_type,
                },
            )
        )
    if row.transmission_id and row.transmission_code and row.transmission_type:
        candidates.append(
            CanonicalCandidate(
                "transmission",
                f"transmission:{row.transmission_id}",
                {
                    "transmission_code": _clean(row.transmission_code),
                    "type": row.transmission_type,
                    "gears": row.gears,
                },
            )
        )
    if row.bodywork_id and row.bodywork_name:
        candidates.append(
            CanonicalCandidate(
                "bodywork",
                f"bodywork:{row.bodywork_id}:{row.door_count or 0}",
                {"canonical_name": _clean(row.bodywork_name), "door_count": row.door_count},
            )
        )
    return tuple(candidates)


def deduplicate_candidates(rows: Iterable[TecDocVehicleRow]) -> tuple[CanonicalCandidate, ...]:
    """Collapse shared components by stable TecDoc source key."""

    unique: dict[tuple[str, str], CanonicalCandidate] = {}
    for row in rows:
        for candidate in candidates_for_row(row):
            key = (candidate.entity_type, candidate.source_key)
            existing = unique.get(key)
            if existing is not None and existing.attributes != candidate.attributes:
                raise ValueError(f"Conflicting TecDoc rows for {candidate.source_key}")
            unique[key] = candidate
    return tuple(unique[key] for key in sorted(unique))
