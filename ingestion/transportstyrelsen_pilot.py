"""Deterministic, vehicle-only pilot sampling from a TS fixed-width export."""

from __future__ import annotations

import hashlib
import heapq
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from psycopg import Connection

from ingestion.staging_loaders import copy_raw_records

TS_STAGING_TABLE = "staging.transportstyrelsen_raw"

_FIELDS: dict[str, tuple[int, int]] = {
    "plate": (0, 6),
    "vehicle_type": (31, 39),
    "eu_category": (39, 47),
    "brand": (47, 71),
    "model": (71, 95),
    "model_year": (95, 99),
    "vehicle_year": (99, 103),
    "vin": (111, 128),
    "fab_code": (128, 131),
    "model_no": (131, 137),
    "group_no": (137, 143),
    "build_month": (143, 149),
    "build_date": (149, 157),
    "registration_date": (158, 166),
    "vehicle_class": (166, 174),
    "manufacturer": (175, 235),
    "base_manufacturer": (235, 295),
    "variant": (384, 424),
    "version": (424, 464),
    "type_text": (464, 504),
    "body_code": (504, 506),
    "body_code2": (506, 508),
    "body_code_extra": (508, 510),
    "ccm": (544, 549),
    "gearbox": (549, 550),
    "fuel1": (572, 574),
    "fuel2": (574, 576),
    "fuel3": (576, 578),
    "kw": (578, 582),
    "fuel_combo": (614, 615),
    "is_4wd": (681, 682),
    "ev_config": (1823, 1845),
}
_INTEGER_FIELDS = frozenset({"model_year", "vehicle_year", "ccm", "kw"})


def parse_vehicle_line(line: str) -> dict[str, Any]:
    """Parse approved vehicle fields only; owner data is never selected."""

    if len(line.rstrip("\r\n")) < 683:
        return {}
    record: dict[str, Any] = {}
    for field_name, (start, end) in _FIELDS.items():
        value = line[start:end].strip()
        if not value:
            continue
        if field_name in _INTEGER_FIELDS and value.isdigit():
            record[field_name] = int(value)
        else:
            record[field_name] = value
    return record


def select_deterministic_sample(path: Path, *, limit: int = 1000) -> list[dict[str, Any]]:
    """Return the same hash-ranked cohort for the same source file."""

    if limit < 1:
        raise ValueError("limit must be positive")
    heap: list[tuple[int, int, dict[str, Any]]] = []
    with path.open("r", encoding="iso-8859-1", errors="replace") as source:
        for line_number, line in enumerate(source, start=1):
            record = parse_vehicle_line(line)
            if not record:
                continue
            identity = f"{record.get('plate', '')}|{record.get('vin', '')}|{line_number}"
            rank = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big")
            entry = (-rank, -line_number, record)
            if len(heap) < limit:
                heapq.heappush(heap, entry)
            elif entry > heap[0]:
                heapq.heapreplace(heap, entry)
    return [entry[2] for entry in sorted(heap, reverse=True)]


def iter_sample(records: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    yield from records


def load_pilot_sample(
    connection: Connection,
    *,
    path: Path,
    batch_id: str,
    limit: int = 1000,
) -> int:
    records = select_deterministic_sample(path, limit=limit)
    if len(records) != limit:
        raise ValueError(f"source provided only {len(records)} records for a {limit}-row pilot")
    return copy_raw_records(
        connection,
        table=TS_STAGING_TABLE,
        source_batch_id=batch_id,
        expected_source_count=limit,
        records=iter_sample(records),
    )
