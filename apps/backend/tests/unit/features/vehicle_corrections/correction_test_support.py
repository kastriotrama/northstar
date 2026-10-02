"""Shared builders for the correction unit tests: a car, its lookup and a stored row."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from api.app.features.vehicle_corrections import fields
from api.app.features.vehicle_ktype_choices import evidence as choice_evidence
from api.app.features.vehicle_matching.repository import overlay_corrections
from api.app.features.vehicle_matching.schemas import (
    KTypeCandidate,
    MatcherInputs,
    VehicleMatchLookup,
)
from ingestion.vehicle_fact_corrections import CorrectionHead, NewCorrection, StoredCorrection

VEHICLE_ID = "NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7G"
OTHER_VEHICLE_ID = "NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7H"

#: The record a made-up car hands to the matcher before anybody corrected it.
CAR: dict[str, Any] = {
    "manufacturer": "Volvo", "model_family": "XC60", "engine_code": "DPCA", "power_kw": 140,
    "displacement_cc": 1969, "bodywork_form": "suv", "production_year": 2018,
    "production_month": 3, "production_date": "2018-03", "production_date_precision": "month",
    "energy_sources": ["petrol", "electricity"],
    "fuel_match_tokens": ["petrol", "electricity", "hybrid_petrol"],
    "electrification_type": "plug_in_hybrid",
}
#: The engine code came from a reviewer's rule, the month from the vehicle itself.
SOURCES: dict[str, str] = {"engine_code": "review", "production_month": "transportstyrelsen"}


def candidate(ktype: str, *, engines: tuple[str, ...] = (), power: int | None = 140,
              conflicts: tuple[str, ...] = ()) -> KTypeCandidate:
    return KTypeCandidate(
        ktype=ktype, candidate_only=False, confidence=0.9, manufacturer="VOLVO",
        model="XC60 II (246)", year_from=2017, year_to=2022, fuels=["hybrid_petrol"],
        engine_codes=list(engines), displacement_cc=1969, power_kw=power, drive_type="awd",
        bodyworks=["suv"], matched_fields=["model"], missing_fields=[],
        conflicting_fields=list(conflicts), compatible=not conflicts,
    )


CANDIDATES = [candidate("A", engines=("DFGA", "DPCA")), candidate("B", engines=("DTSA",), power=110)]


def inputs_of(normalized: dict[str, Any]) -> MatcherInputs:
    """What the matcher keys on for these values: a scripted stand-in for its own reading."""

    year, month = normalized.get("production_year"), normalized.get("production_month")
    fuels = normalized.get("fuel_match_tokens") or normalized.get("energy_sources") or []
    return MatcherInputs(
        manufacturer=str(normalized.get("manufacturer") or "").upper(),
        model_values=[str(normalized["model_family"])] if normalized.get("model_family") else [],
        production_year=year, fuels=sorted(fuels), engine_code=normalized.get("engine_code"),
        displacement_cc=normalized.get("displacement_cc"), power_kw=normalized.get("power_kw"),
        drive_type=normalized.get("drive_type"), bodywork_form=normalized.get("bodywork_form"),
        model_recovered_from=None,
        build_month=year * 100 + month if year and month else None,
        electrification=normalized.get("electrification_type"),
    )


def lookup(
    car: dict[str, Any] | None = None,
    sources: dict[str, str] | None = None,
    heads: dict[str, CorrectionHead] | None = None,
    details: dict[str, tuple[StoredCorrection, int]] | None = None,
    **overrides: Any,
) -> VehicleMatchLookup:
    """A vehicle lookup as the matching service builds it: corrections laid over the car."""

    normalized, overlaid, _ = overlay_corrections(
        dict(CAR if car is None else car), dict(SOURCES if sources is None else sources),
        heads or {},
    )
    values: dict[str, Any] = {
        "vehicle_id": VEHICLE_ID, "source_record_id": 7, "plate": "ABC123",
        "vin": "YV1BW84S1F1234567", "catalog_batch": "batch-1", "terminal": "review_required",
        "bucket": "several", "confidence": 0.9, "top_ktype": "A", "reason_codes": ["match:x"],
        "verdict": "Too close to call.", "rule_filled": [],
        "overlaid_fields": dict(sorted(overlaid.items())), "inputs": inputs_of(normalized),
        "candidates": list(CANDIDATES), "candidate_limit": 5, "separating_fields": ["engine_code"],
        "missing_separating_fields": [],
        "decision_trace": [{"signal": "routing_gate", "explanation": "secret trace"}],
        "other_vehicle_ids": [OTHER_VEHICLE_ID],
    }
    values.update(overrides)
    result = VehicleMatchLookup(**values)
    result.evidence_fingerprint = choice_evidence.fingerprint(result)
    result.corrections = fields.states(details or {})
    result.correctable_fields = fields.describe(
        normalized, overlaid, result.inputs, result.candidates
    )
    return result


def stored(new: NewCorrection, chain_position: int = 0,
           created_at: datetime | None = None) -> StoredCorrection:
    """The row a store keeps for `new` at this place in its field's chain."""

    return StoredCorrection(
        chain_position=chain_position,
        created_at=created_at or datetime(2026, 10, 2, 9, 30, tzinfo=UTC),
        **{name: getattr(new, name) for name in NewCorrection.__dataclass_fields__},
    )


def row(field: str = "engine_code", action: str = "set", value: str | None = "DFGA",
        **overrides: Any) -> StoredCorrection:
    """A stored row with plausible provenance, for tests that only read rows."""

    values: dict[str, Any] = {
        "correction_id": uuid4(), "vehicle_id": VEHICLE_ID, "field": field, "chain_position": 0,
        "action": action, "value": value if action == "set" else None,
        "supersedes_correction_id": None, "group_id": None, "reviewer": "Ada", "reason": None,
        "previous_value": "DPCA", "previous_source": "review", "catalog_batch": "batch-1",
        "automatic_terminal": "review_required", "automatic_ktype": "A", "code_version": "abc1234",
        "evidence_fingerprint": "a" * 64, "evidence": {"schema": "x", "automatic": {}},
        "created_at": datetime(2026, 10, 2, 9, 30, tzinfo=UTC),
    }
    values.update(overrides)
    return StoredCorrection(**values)


def new_id() -> UUID:
    return uuid4()
