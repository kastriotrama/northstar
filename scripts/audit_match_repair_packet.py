"""Audit replay evidence, never substitute an automated audit for human verdicts."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.validate_local_matcher_cohort import digest, write_private_json


def audit_packet(packet: dict[str, Any]) -> dict[str, Any]:
    items = packet["items"]
    if len(items) != packet["count"] or len({item["row_key"] for item in items}) != len(items):
        raise ValueError("incomplete or duplicated review packet")
    changes: Counter[str] = Counter()
    observations: Counter[str] = Counter()
    matched: Counter[str] = Counter()
    strength: Counter[str] = Counter()
    cases = []
    technical_fields = {"year", "fuels", "engine_code", "displacement_cc", "power_kw", "bodywork", "drive_type"}
    for item in items:
        before, after = item["change"]["before"], item["change"]["after"]
        if before["terminal"] != "resolved" and after["terminal"] == "resolved":
            kind = "gained_resolution"
        elif before["terminal"] == "resolved" and after["terminal"] != "resolved":
            kind = "lost_resolution"
        elif before["terminal"] == after["terminal"] == "resolved" and before["top_candidate_reference"] != after["top_candidate_reference"]:
            kind = "changed_resolved_ktype"
        else:
            raise ValueError("packet contains a case outside changed-resolution scope")
        if item["evaluation"]["terminal"] != after["terminal"] or item["evaluation"]["top_candidate_reference"] != after["top_candidate_reference"]:
            raise ValueError("evaluation differs from reported change")
        changes[kind] += 1
        case = {"row_key": item["row_key"], "plate": item.get("plate"), "kind": kind,
                "before": before, "after": after, "verdict": None, "independent_review_required": True}
        if after["terminal"] == "resolved":
            attempts = [attempt for attempt in item["attempts"]
                        if attempt["candidates"] and attempt["candidates"][0]["candidate_reference"] == after["top_candidate_reference"]
                        and attempt["routing"]["route"] == "resolved"]
            if not attempts:
                raise ValueError("resolved identity has no supporting resolved attempt")
            attempt = attempts[0]
            top = attempt["candidates"][0]
            evidence = top["evidence"]
            if evidence["conflicting_fields"] or evidence["phonetic_match"] or "model_partial" in evidence["matched_fields"]:
                raise ValueError("accepted identity has a prohibited conflict or model gate")
            if evidence["match_scope"] != "exact_manufacturer":
                raise ValueError("accepted identity lacks exact manufacturer scope")
            matched.update(evidence["matched_fields"])
            observations.update(key for key in technical_fields if attempt["query"].get(key))
            matched_technical = sorted(technical_fields.intersection(evidence["matched_fields"]))
            strength[str(len(matched_technical))] += 1
            case.update(matched_technical_fields=matched_technical,
                        observed_engine_code=bool(attempt["query"].get("engine_code")),
                        priority="high" if kind == "changed_resolved_ktype" or len(matched_technical) < 4 else "standard")
        else:
            case["priority"] = "high"
        cases.append(case)
    return {
        "count": len(items), "changes": dict(changes), "observed_query_fields": dict(observations),
        "matched_fields": dict(matched), "technical_match_counts": dict(strength), "cases": cases,
        "packet_digest": digest(packet), "contains_private_plates": True,
        "independently_adjudicated": False, "read_only": True,
        "limitations": ["Checks existing replay evidence, not vehicle ground truth",
                        "Candidate engine codes are not observed TS engines",
                        "Review priority is diagnostic, not a new acceptance threshold",
                        "All verdicts remain unset; independent approval still required"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit_packet(json.loads(args.packet.read_text()))
    write_private_json(args.output, result)
    print(json.dumps({key: result[key] for key in ("count", "changes", "observed_query_fields", "technical_match_counts")}))


if __name__ == "__main__":
    main()
