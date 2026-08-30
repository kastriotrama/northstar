"""Verify the reviewed hard-conflict subset of the mixed-fuel change packet."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.validate_local_matcher_cohort import write_private_json

OVERLAP_REMOVAL = "approve-overlap-removes-false-fuel-conflict"
PEUGEOT_REJECTION = "reject-peugeot-3008-iii-hybrid-hard-conflict"
MINI_REJECTION = "reject-mini-f56-electric-to-petrol-hard-conflict"


def classify_hard_conflict_case(item: dict[str, Any]) -> str | None:
    """Return the reviewed cohort ID for one hard-conflict-touching change."""

    before = item["change"]["before"]
    after = item["change"]["after"]
    terminals = {before["terminal"], after["terminal"]}
    if "hard_conflict" not in terminals:
        return None
    before_reference = before["top_candidate_reference"]
    after_reference = after["top_candidate_reference"]
    if (
        before["terminal"] == "hard_conflict"
        and after["terminal"] in {"provisional", "review_required"}
        and before_reference == after_reference
        and "conflict:fuels" in before["reason_codes"]
        and "conflict:fuels" not in after["reason_codes"]
    ):
        return OVERLAP_REMOVAL
    if (
        before["terminal"] == "review_required"
        and after["terminal"] == "hard_conflict"
        and before_reference == "000121650"
        and after_reference == "000156880"
        and "conflict:power_kw" in after["reason_codes"]
    ):
        return PEUGEOT_REJECTION
    if (
        before["terminal"] == after["terminal"] == "hard_conflict"
        and before_reference == "000156380"
        and after_reference == "000100572"
        and "conflict:fuels" in after["reason_codes"]
    ):
        return MINI_REJECTION
    raise ValueError("hard-conflict case is outside the reviewed cohort selectors")


def audit_review(
    packet: dict[str, Any], manifest: dict[str, Any], *, packet_sha256: str
) -> dict[str, Any]:
    """Prove exact reviewed coverage without treating recommendations as truth."""

    if manifest.get("status") != "reviewed":
        raise ValueError("hard-conflict manifest is not reviewed")
    if manifest.get("source_packet_sha256") != packet_sha256:
        raise ValueError("hard-conflict manifest packet checksum differs")
    if manifest.get("runtime_activation") or manifest.get("match_identity_approved"):
        raise ValueError("hard-conflict review cannot activate runtime identity")
    if manifest.get("independently_adjudicated"):
        raise ValueError("local matcher evidence is not independent ground truth")
    items = packet.get("items")
    if not isinstance(items, list) or len(items) != packet.get("count"):
        raise ValueError("review packet accounting is incomplete")
    group_rows = manifest.get("groups")
    if not isinstance(group_rows, list):
        raise ValueError("hard-conflict manifest requires groups")
    expected = {str(row["group_id"]): int(row["expected_count"]) for row in group_rows}
    if len(expected) != len(group_rows):
        raise ValueError("hard-conflict manifest has duplicate groups")

    actual: Counter[str] = Counter()
    transition_counts: Counter[str] = Counter()
    for item in items:
        group_id = classify_hard_conflict_case(item)
        if group_id is None:
            continue
        actual[group_id] += 1
        before = item["change"]["before"]["terminal"]
        after = item["change"]["after"]["terminal"]
        transition_counts[f"{before}->{after}"] += 1
    if dict(actual) != expected:
        raise ValueError("hard-conflict reviewed cohort counts differ from manifest")
    decisions = Counter(str(row["decision"]) for row in group_rows)
    return {
        "manifest_version": manifest["version"],
        "reviewed_case_count": sum(actual.values()),
        "group_counts": dict(sorted(actual.items())),
        "transition_counts": dict(sorted(transition_counts.items())),
        "decision_group_counts": dict(sorted(decisions.items())),
        "remaining_changed_cases": len(items) - sum(actual.values()),
        "independently_adjudicated": False,
        "full_mixed_fuel_policy_approved": False,
        "runtime_activation": False,
        "match_identity_approved": False,
        "contains_private_plates": False,
        "read_only": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    packet_bytes = args.packet.read_bytes()
    result = audit_review(
        json.loads(packet_bytes),
        json.loads(args.manifest.read_text()),
        packet_sha256=hashlib.sha256(packet_bytes).hexdigest(),
    )
    write_private_json(args.output, result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
