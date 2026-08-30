"""Prove that the final 20k catalog control is completely adjudicated."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.audit_mixed_fuel_hard_conflict_review import audit_review as audit_hard
from scripts.audit_mixed_fuel_hard_conflict_review import (
    PEUGEOT_REJECTION,
    classify_hard_conflict_case,
)
from scripts.audit_mixed_fuel_remaining_review import audit_review as audit_remaining
from scripts.validate_local_matcher_cohort import write_private_json


def audit_candidate_policy(
    comparison: dict[str, Any], packet: dict[str, Any],
    hard_manifest: dict[str, Any], remaining_manifest: dict[str, Any],
    acceptance: dict[str, Any], *, packet_sha256: str,
) -> dict[str, Any]:
    """Require exact control/packet equality and both reviewed decision sets."""

    if comparison.get("count") != 20_000:
        raise ValueError("candidate policy requires the pinned 20k control")
    packet_items = packet.get("items")
    changed_records = comparison.get("changed_records")
    if not isinstance(packet_items, list) or not isinstance(changed_records, list):
        raise TypeError("candidate policy requires packet and comparison records")
    packet_by_key = {str(item["change"]["row_key"]): item["change"] for item in packet_items}
    control_by_key = {str(item["row_key"]): item for item in changed_records}
    if len(packet_by_key) != len(packet_items) or len(control_by_key) != len(changed_records):
        raise ValueError("candidate policy inputs contain duplicate row keys")
    if not control_by_key.keys() <= packet_by_key.keys():
        raise ValueError("active control adds changes outside the reviewed packet")
    missing_keys = packet_by_key.keys() - control_by_key.keys()
    repair_rows = comparison.get("source_model_policy_repair_rows", [])
    if not isinstance(repair_rows, list):
        raise TypeError("source-model repair rows must be a list")
    repair_by_key = {str(row["row_key"]): row for row in repair_rows}
    expected_repair_keys = {
        str(item["change"]["row_key"])
        for item in packet_items
        if classify_hard_conflict_case(item) == PEUGEOT_REJECTION
    }
    if missing_keys and (
        missing_keys != expected_repair_keys or repair_by_key.keys() != missing_keys
    ):
        raise ValueError("active control changes differ from the reviewed packet")
    for row_key in missing_keys:
        repair = repair_by_key[row_key]
        if repair["before"] != repair["after"] or any(
            repair["before"].get(key) != value
            for key, value in {
                "terminal": "review_required",
                "top_candidate_reference": "000121650",
            }.items()
        ):
            raise ValueError("reviewed Peugeot repair did not restore the safe hypothesis")
    if missing_keys and (
        comparison.get("source_model_policy_version")
        != acceptance.get("source_model_policy_version")
        or comparison.get("source_model_policy_digest")
        != acceptance.get("source_model_policy_digest")
    ):
        raise ValueError("source-model repair policy differs from acceptance pins")
    for row_key, reviewed in packet_by_key.items():
        if row_key in missing_keys:
            continue
        control = control_by_key[row_key]
        if reviewed["before"] != control["before"] or reviewed["after"] != control["after"]:
            raise ValueError("active control result differs from reviewed evidence")

    hard = audit_hard(packet, hard_manifest, packet_sha256=packet_sha256)
    remaining = audit_remaining(packet, remaining_manifest, packet_sha256=packet_sha256)
    reviewed_count = int(hard["reviewed_case_count"]) + int(remaining["reviewed_case_count"])
    if reviewed_count != len(packet_items):
        raise ValueError("candidate policy has unreviewed development changes")
    if acceptance.get("status") != "approved_before_unblinding":
        raise ValueError("holdout acceptance is not approved")

    decision_counts: Counter[str] = Counter()
    for manifest in (hard_manifest, remaining_manifest):
        for group in manifest["groups"]:
            decision_counts[str(group["decision"])] += int(group["expected_count"])
    return {
        "status": "approved_for_frozen_holdout",
        "development_control_count": comparison["count"],
        "development_change_count": len(packet_items),
        "final_control_change_count": len(changed_records),
        "suppressed_rejected_hard_conflicts": len(missing_keys),
        "reviewed_change_count": reviewed_count,
        "decision_counts": dict(sorted(decision_counts.items())),
        "transition_counts": comparison["transitions"],
        "selected_identity_change_count": comparison["selected_identity_change_count"],
        "before_counts": comparison["before_counts"],
        "after_counts": comparison["after_counts"],
        "before_catalog_version": comparison["before_catalog_version"],
        "before_catalog_digest": comparison["before_catalog_digest"],
        "after_catalog_version": comparison["after_catalog_version"],
        "after_catalog_digest": comparison["after_catalog_digest"],
        "source_digest": comparison["source_digest"],
        "rules_digest": comparison["rules_digest"],
        "alignment_version": comparison["alignment_version"],
        "source_packet_sha256": packet_sha256,
        "hard_manifest_version": hard_manifest["version"],
        "remaining_manifest_version": remaining_manifest["version"],
        "holdout_acceptance_version": acceptance["version"],
        "runtime_activation": False,
        "postgres_writes": 0,
        "neo4j_writes": 0,
        "contains_private_plates": False,
        "contains_private_vins": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", required=True, type=Path)
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--hard-manifest", required=True, type=Path)
    parser.add_argument("--remaining-manifest", required=True, type=Path)
    parser.add_argument("--acceptance", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    packet_bytes = args.packet.read_bytes()
    result = audit_candidate_policy(
        json.loads(args.comparison.read_text()),
        json.loads(packet_bytes),
        json.loads(args.hard_manifest.read_text()),
        json.loads(args.remaining_manifest.read_text()),
        json.loads(args.acceptance.read_text()),
        packet_sha256=hashlib.sha256(packet_bytes).hexdigest(),
    )
    write_private_json(args.output, result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
