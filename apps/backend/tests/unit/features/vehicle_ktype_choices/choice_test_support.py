"""Shared builders for the KType choice unit tests: a lookup and a stored row."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from api.app.features.vehicle_ktype_choices import evidence
from api.app.features.vehicle_matching.schemas import (
    KTypeCandidate,
    MatcherInputs,
    VehicleMatchLookup,
)
from ingestion.vehicle_ktype_choices import StoredChoice

VEHICLE_ID = "NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7G"


def candidate(ktype: str, *, conflicts: tuple[str, ...] = (), confidence: float = 0.9,
              candidate_only: bool = False) -> KTypeCandidate:
    return KTypeCandidate(
        ktype=ktype, candidate_only=candidate_only, confidence=confidence, manufacturer="VOLVO",
        model="XC60", year_from=2017, year_to=2022, fuels=["diesel"], engine_codes=[],
        displacement_cc=1969, power_kw=140, drive_type=None, bodyworks=[],
        matched_fields=["model"], missing_fields=[], conflicting_fields=list(conflicts),
        compatible=not conflicts,
    )


def inputs(**overrides: Any) -> MatcherInputs:
    values: dict[str, Any] = {
        "manufacturer": "VOLVO", "model_values": ["XC60"], "production_year": 2018,
        "fuels": ["diesel"], "engine_code": None, "displacement_cc": 1969, "power_kw": 140,
        "drive_type": None, "bodywork_form": "suv", "model_recovered_from": None,
    }
    values.update(overrides)
    return MatcherInputs(**values)


def lookup(**overrides: Any) -> VehicleMatchLookup:
    values: dict[str, Any] = {
        "vehicle_id": VEHICLE_ID, "source_record_id": 7, "plate": "ABC123",
        "vin": "YV1BW84S1F1234567", "catalog_batch": "batch-1", "terminal": "review_required",
        "bucket": "several", "confidence": 0.9, "top_ktype": "A", "reason_codes": ["match:x"],
        "verdict": "Too close to call.", "rule_filled": [], "overlaid_fields": {"power_kw": "ais"},
        "inputs": inputs(), "candidates": [candidate("A"), candidate("B")], "candidate_limit": 5,
        "separating_fields": ["power_kw"], "missing_separating_fields": [],
        "decision_trace": [{"signal": "routing_gate", "explanation": "secret trace"}],
        "other_vehicle_ids": ["NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7H"],
    }
    values.update(overrides)
    result = VehicleMatchLookup(**values)
    result.evidence_fingerprint = evidence.fingerprint(result)
    return result


def stored(shown: VehicleMatchLookup, action: str = "choose", ktype: str | None = "A",
           **overrides: Any) -> StoredChoice:
    """A row as the service would have stored it for the lookup `shown`."""

    values: dict[str, Any] = {
        "choice_id": uuid4(), "vehicle_id": VEHICLE_ID, "action": action,
        "ktype": ktype if action == "choose" else None, "supersedes_choice_id": None,
        "reviewer": "Ada", "reason": None, "catalog_batch": shown.catalog_batch,
        "automatic_terminal": shown.terminal, "automatic_ktype": shown.top_ktype,
        "code_version": "abc1234", "evidence_fingerprint": shown.evidence_fingerprint,
        "evidence": evidence.snapshot(shown, "abc1234"),
        "created_at": datetime(2026, 10, 2, 9, 30, tzinfo=UTC),
    }
    values.update(overrides)
    return StoredChoice(**values)


def new_id() -> UUID:
    return uuid4()
