"""Shared builders for the many-cars tests: made-up cars and a scripted matcher."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from api.app.features.vehicle_corrections import scope
from api.app.features.vehicle_corrections.preview import PreviewPlan
from api.app.features.vehicle_matching.repository import CarRecord
from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.match_run_service import MatchSourceRecord
from ingestion.tecdoc.match_run_adapters import MatchEvaluation
from ingestion.vehicle_fact_corrections import CorrectionHead

#: A made-up anchor car's vehicle values: a V70 with no drive type.
ANCHOR: dict[str, Any] = {
    "manufacturer": "VOLVO", "vehicle_scope": "passenger", "model_family": "V70",
    "production_year": 2012, "production_month": 3, "power_kw": 120, "displacement_cc": 1984,
    "engine_code": "B4204T", "drive_type": None, "bodywork_form": "estate", "fuel": "petrol",
    "fuel_secondary": None, "electrification_type": None, "variant_code": "BW",
    "version_code": None, "type_approval": None, "registry_type_code": "B",
    "registry_make_code": "VOLVO", "registry_brand_text": "VOLVO V70",
    "registry_model_text": "V70 II",
}

CATALOG: dict[str, VehicleCandidate] = {
    "K1": VehicleCandidate("K1", "VOLVO", "V70", engine_codes=frozenset({"B4204T"})),
    "K2": VehicleCandidate("K2", "VOLVO", "V70", engine_codes=frozenset({"D5244T"})),
    "K3": VehicleCandidate("K3", "VOLVO", "V70"),
}


def vehicle_id(number: int) -> str:
    """A made-up NorthStar id, in id order by `number`."""

    return f"NOR-01J8Z3Y5W2QK4T7B9C1D3E{number:04d}"


def car(
    number: int,
    *,
    corrections: Mapping[str, CorrectionHead] | None = None,
    overlaid: Mapping[str, str] | None = None,
    stopped: bool = False,
    **normalized: Any,
) -> CarRecord:
    """A car as the read seam hands it over: its values are what the matcher reads."""

    values = {"manufacturer": "VOLVO", "model_family": "V70", "engine_code": "B4204T", **normalized}
    values = {name: value for name, value in values.items() if value is not None}
    payload: dict[str, Any] = {
        "normalization_status": "review_required" if stopped else "resolved",
        "normalized": values,
        # Each car's own registry text: no two cars hand the matcher the same bytes.
        "source_evidence": {"model": f"V70 {number}"},
        "inferred_fields": [],
    }
    return CarRecord(
        source_record_id=number,
        plate=f"TST{number:03d}",
        vin=None,
        manufacturer="VOLVO",
        model_family="V70",
        record=MatchSourceRecord(number, payload),
        rule_filled=(),
        vehicle_id=vehicle_id(number),
        overlaid=dict(overlaid or {}),
        corrections=dict(corrections or {}),
        has_corrections=bool(corrections),
    )


def corrected(action: str = "set", value: str | None = "awd") -> CorrectionHead:
    return CorrectionHead(action, value if action == "set" else None, uuid4())  # type: ignore[arg-type]


#: What the scripted matcher decides for a car, by what it reads: `(terminal, ktype)`.
Rule = Callable[[Mapping[str, Any]], tuple[str, str | None]]


def by_drive(values: Mapping[str, Any]) -> tuple[str, str | None]:
    """Front-wheel drive resolves to K1, all-wheel drive to K2, rear is a conflict; none ties."""

    return {
        "fwd": ("resolved", "K1"),
        "awd": ("resolved", "K2"),
        "rwd": ("hard_conflict", None),
    }.get(str(values.get("drive_type")), ("review_required", None))


@dataclass
class ScriptedMatcher:
    """Stands in for the process's matcher: one scripted outcome per set of values."""

    rule: Rule = by_drive
    batch_id: str = "batch-1"
    catalog: dict[str, VehicleCandidate] = field(default_factory=lambda: dict(CATALOG))
    #: Every evaluation asked for: the record id and whether it may be remembered.
    evaluated: list[tuple[int, bool]] = field(default_factory=list)

    def _values(self, record: MatchSourceRecord) -> Mapping[str, Any]:
        values = record.payload.get("normalized")
        return values if isinstance(values, dict) else {}

    def evaluate(
        self, record: MatchSourceRecord, *, remember: bool = True
    ) -> tuple[MatchEvaluation, Any]:
        self.evaluated.append((record.source_record_id, remember))
        if record.payload.get("normalization_status") != "resolved":
            return MatchEvaluation("normalization_review", ("normalization:stopped",)), None
        terminal, ktype = self.rule(self._values(record))
        return MatchEvaluation(terminal, ("match:scripted",), top_candidate_reference=ktype), None  # type: ignore[arg-type]

    def query(self, record: MatchSourceRecord) -> Any:
        return None

    def key(self, record: MatchSourceRecord) -> tuple[object, ...] | None:
        """What two cars must share to get one evaluation: the values the matcher reads."""

        if record.payload.get("normalization_status") != "resolved":
            return None
        return tuple(sorted((name, str(value)) for name, value in self._values(record).items()))


@dataclass
class Page:
    cars: Sequence[CarRecord]
    choices: Mapping[str, tuple[str, str | None]] = field(default_factory=dict)


def plan(
    field_name: str = "drive_type",
    action: str = "set",
    value: str | None = "fwd",
    *,
    anchor_value: str | None = None,
    rung: int = 1,
) -> PreviewPlan:
    chosen = scope.ladder(ANCHOR, field_name, action, anchor_value)[rung]
    return PreviewPlan(
        anchor_vehicle_id=vehicle_id(1),
        field=field_name,
        action=action,
        value=value if action == "set" else None,
        scope=chosen,
        scope_label=scope.label(chosen),
        anchor_value=anchor_value,
    )
