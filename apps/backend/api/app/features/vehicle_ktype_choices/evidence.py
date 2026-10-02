"""What a choice was based on, and whether it still fits: pure functions.

A choice stores the evaluation the person saw. `fingerprint` names that
evaluation so the server can refuse a choice made on a stale screen;
`snapshot` is what gets stored; `assess` compares a stored choice with today's
evaluation and names every reason it needs another look. Nothing here reads or
writes a database, and nothing is ever changed by an assessment.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from api.app.features.vehicle_ktype_choices.schemas import (
    ChangedInput,
    ChoiceStatus,
    KTypeChoiceState,
    StaleReason,
)
from api.app.features.vehicle_matching.schemas import VehicleMatchLookup
from ingestion.confidence_routing import CONFIDENCE_POLICY_VERSION
from ingestion.vehicle_ktype_choices import StoredChoice

EVIDENCE_SCHEMA = "manual-ktype-choice-evidence-v1"

_STATUS: dict[str, ChoiceStatus] = {"choose": "chosen", "none": "none", "withdraw": "withdrawn"}


def _inputs(lookup: VehicleMatchLookup) -> dict[str, Any] | None:
    return None if lookup.inputs is None else lookup.inputs.model_dump(mode="json")


def fingerprint(lookup: VehicleMatchLookup) -> str:
    """sha256 of what the matcher saw and concluded for this car.

    Candidate order and confidences are left out, so neither a re-sort nor
    float noise makes the same evaluation look different.
    """

    content = {
        "catalog_batch": lookup.catalog_batch,
        "inputs": _inputs(lookup),
        "terminal": lookup.terminal,
        "top_ktype": lookup.top_ktype,
        "candidates": [
            {
                "ktype": candidate.ktype,
                "compatible": candidate.compatible,
                "conflicting_fields": sorted(candidate.conflicting_fields),
            }
            for candidate in sorted(lookup.candidates, key=lambda item: item.ktype)
        ],
    }
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def snapshot(lookup: VehicleMatchLookup, code_version: str) -> dict[str, Any]:
    """The evidence stored with a choice: the server's own evaluation.

    No plate, VIN, vehicle id, other vehicles or decision trace. It does hold
    licensed TecDoc values, so an export of these rows is team-only.
    """

    return {
        "schema": EVIDENCE_SCHEMA,
        "catalog_batch": lookup.catalog_batch,
        "automatic": {
            "terminal": lookup.terminal,
            "bucket": lookup.bucket,
            "confidence": lookup.confidence,
            "top_ktype": lookup.top_ktype,
            "verdict": lookup.verdict,
            "reason_codes": list(lookup.reason_codes),
        },
        "inputs": _inputs(lookup),
        "overlaid_fields": dict(lookup.overlaid_fields),
        "rule_filled": list(lookup.rule_filled),
        "candidates": [candidate.model_dump(mode="json") for candidate in lookup.candidates],
        "candidate_limit": lookup.candidate_limit,
        "separating_fields": list(lookup.separating_fields),
        "missing_separating_fields": list(lookup.missing_separating_fields),
        "source_record_id": lookup.source_record_id,
        "versions": {"code": code_version, "confidence_policy": CONFIDENCE_POLICY_VERSION},
    }


def _stored_candidates(choice: StoredChoice) -> list[dict[str, Any]]:
    candidates = choice.evidence.get("candidates")
    if not isinstance(candidates, list):
        return []
    return [item for item in candidates if isinstance(item, dict)]


def _changed_inputs(choice: StoredChoice, lookup: VehicleMatchLookup) -> list[ChangedInput]:
    then = choice.evidence.get("inputs")
    now = _inputs(lookup)
    if not isinstance(then, dict):
        then = None
    if (then is None) != (now is None):
        return [ChangedInput(field="matchable", then=then is not None, now=now is not None)]
    if then is None or now is None:
        return []
    # Only the keys stored then: an input the matcher gained since does not
    # make an old choice look changed.
    return [
        ChangedInput(field=key, then=value, now=now.get(key))
        for key, value in then.items()
        if now.get(key) != value
    ]


def assess(
    choice: StoredChoice,
    lookup: VehicleMatchLookup,
    ktype_in_catalog: bool,
    history_count: int,
) -> KTypeChoiceState:
    """The head row as the lookup shows it, with every reason it no longer fits."""

    reasons: list[StaleReason] = []
    changed: list[ChangedInput] = []
    stored = _stored_candidates(choice)
    if choice.action != "withdraw":
        if choice.catalog_batch != lookup.catalog_batch:
            reasons.append("catalog_batch_changed")
        changed = _changed_inputs(choice, lookup)
        if changed:
            reasons.append("evidence_changed")
        today = {candidate.ktype for candidate in lookup.candidates}
        if choice.action == "choose" and choice.ktype not in today:
            reasons.append("ktype_not_a_candidate" if ktype_in_catalog else "ktype_not_in_catalog")
        if choice.action == "none" and today - {str(item.get("ktype")) for item in stored}:
            reasons.append("new_candidates")
    chosen = None
    if choice.action == "choose":
        chosen = next((item for item in stored if item.get("ktype") == choice.ktype), None)
    return KTypeChoiceState(
        status=_STATUS[choice.action],
        choice_id=choice.choice_id,
        ktype=choice.ktype,
        reviewer=choice.reviewer,
        reason=choice.reason,
        created_at=choice.created_at,
        catalog_batch=choice.catalog_batch,
        automatic_terminal=choice.automatic_terminal,
        automatic_ktype=choice.automatic_ktype,
        chosen_candidate=chosen,
        needs_review=bool(reasons),
        stale_reasons=reasons,
        changed_inputs=changed,
        history_count=history_count,
    )


def effective(
    state: KTypeChoiceState | None, lookup: VehicleMatchLookup
) -> tuple[str | None, Literal["person", "matcher"] | None]:
    """The KType the car counts as having, and who says so.

    A person's choice wins -- "none of these" included, and a stale one too: it
    stands until a person changes it. Otherwise the matcher's, only when it resolved.
    """

    if state is not None and state.status == "chosen":
        return state.ktype, "person"
    if state is not None and state.status == "none":
        return None, "person"
    if lookup.terminal == "resolved" and lookup.top_ktype:
        return lookup.top_ktype, "matcher"
    return None, None
