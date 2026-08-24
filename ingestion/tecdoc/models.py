"""Stable source and canonical shapes used by the TecDoc pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TecDocVehicleRow:
    """One denormalized KType row from the restored TecDoc vehicle tree."""

    ktype_id: str
    manufacturer_id: str
    manufacturer_name: str
    model_id: str
    model_name: str
    variant_id: str
    variant_name: str
    year_from: int
    year_to: int | None = None
    platform_id: str | None = None
    platform_code: str | None = None
    platform_generation: str | None = None
    platform_year_from: int | None = None
    platform_year_to: int | None = None
    platform_facelift: bool = False
    engine_id: str | None = None
    engine_code: str | None = None
    displacement_cc: int | None = None
    fuel_type: str | None = None
    power_kw: int | None = None
    transmission_id: str | None = None
    transmission_code: str | None = None
    transmission_type: str | None = None
    gears: int | None = None
    bodywork_id: str | None = None
    bodywork_name: str | None = None
    door_count: int | None = None
    drive_type: str | None = None
    source_row_refs: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> TecDocVehicleRow:
        required = (
            "ktype_id",
            "manufacturer_id",
            "manufacturer_name",
            "model_id",
            "model_name",
            "variant_id",
            "variant_name",
            "year_from",
        )
        missing = [key for key in required if row.get(key) in (None, "")]
        if missing:
            raise ValueError(f"TecDoc vehicle row is missing required fields: {', '.join(missing)}")
        return cls(
            **{
                field: value
                for field, value in row.items()
                if field in cls.__dataclass_fields__
            }
        )


@dataclass(frozen=True)
class CanonicalCandidate:
    entity_type: str
    source_key: str
    attributes: dict[str, Any]


@dataclass(frozen=True)
class TecDocIngestionSummary:
    batch_id: str
    source_version: str
    source_rows: int
    unique_ktypes: int
    candidates_written: int
    ledger_entries_written: int
