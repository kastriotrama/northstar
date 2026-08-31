"""Verify the reviewed non-hard-conflict subset of the mixed-fuel packet."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.validate_local_matcher_cohort import write_private_json

ELIGIBILITY_GAIN = "approve-stable-identity-eligibility-gains-for-holdout"
RESOLVED_DOWNGRADE = "approve-resolved-to-review-conservative-downgrade"
PROVISIONAL_DOWNGRADE = "approve-provisional-to-review-conservative-downgrade"
IDENTITY_REJECTION = "reject-unresolved-candidate-identity-changes"


def classify_remaining_case(item: dict[str, Any]) -> str | None:
    """Return the reviewed cohort ID for one non-hard-conflict change."""

    before = item["change"]["before"]
    after = item["change"]["after"]
    if "hard_conflict" in {before["terminal"], after["terminal"]}:
        return None
    same_reference = (
        before["top_candidate_reference"] == after["top_candidate_reference"]
    )
    transition = (before["terminal"], after["terminal"])
    if transition == ("provisional", "resolved") and same_reference:
        return ELIGIBILITY_GAIN
    if transition == ("resolved", "review_required") and same_reference:
        return RESOLVED_DOWNGRADE
    if transition == ("provisional", "review_required") and same_reference:
        return PROVISIONAL_DOWNGRADE
    if transition == ("review_required", "review_required") and not same_reference:
        return IDENTITY_REJECTION
    raise ValueError("non-hard-conflict case is outside the reviewed cohort selectors")


def audit_review(
    packet: dict[str, Any], manifest: dict[str, Any], *, packet_sha256: str
) -> dict[str, Any]:
    """Prove exact remaining-case coverage without activating the policy."""

    if manifest.get("status") != "reviewed":
        raise ValueError("remaining-change manifest is not reviewed")
    if manifest.get("source_packet_sha256") != packet_sha256:
        raise ValueError("remaining-change manifest packet checksum differs")
    if not manifest.get("approved_for_frozen_holdout"):
        raise ValueError("remaining-change policy is not approved for holdout")
    if manifest.get("runtime_activation") or manifest.get("direct_match_identity_approval"):
        raise ValueError("remaining-change review cannot activate runtime identity")
    if manifest.get("independently_adjudicated"):
        raise ValueError("local matcher evidence is not independent ground truth")
    items = packet.get("items")
    if not isinstance(items, list) or len(items) != packet.get("count"):
        raise ValueError("review packet accounting is incomplete")
    group_rows = manifest.get("groups")
    if not isinstance(group_rows, list):
        raise TypeError("remaining-change manifest requires groups")
    expected = {str(row["group_id"]): int(row["expected_count"]) for row in group_rows}
    if len(expected) != len(group_rows):
        raise ValueError("remaining-change manifest has duplicate groups")

    actual: Counter[str] = Counter()
    transition_counts: Counter[str] = Counter()
    for item in items:
        group_id = classify_remaining_case(item)
        if group_id is None:
            continue
        actual[group_id] += 1
        before = item["change"]["before"]["terminal"]
        after = item["change"]["after"]["terminal"]
        transition_counts[f"{before}->{after}"] += 1
    if dict(actual) != expected:
        raise ValueError("remaining reviewed cohort counts differ from manifest")
    return {
        "manifest_version": manifest["version"],
        "reviewed_case_count": sum(actual.values()),
        "group_counts": dict(sorted(actual.items())),
        "transition_counts": dict(sorted(transition_counts.items())),
        "hard_conflict_cases_excluded": len(items) - sum(actual.values()),
        "approved_for_frozen_holdout": True,
        "independently_adjudicated": False,
        "runtime_activation": False,
        "direct_match_identity_approval": False,
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
