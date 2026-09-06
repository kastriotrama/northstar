"""Fetch public RDW approval evidence, never vehicle/owner data or active rules."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from scripts.validate_local_matcher_cohort import digest, write_private_json

DATASETS = {"names": "x5v3-sewk", "bodywork": "ky2r-jqad"}


def approval_query(approvals: list[str]) -> str:
    if not approvals or any(not value or "'" in value for value in approvals):
        raise ValueError("exact nonempty approval identifiers required")
    return "typegoedkeuringsnummer in (" + ",".join(f"'{value}'" for value in approvals) + ")"


def join_golf_evidence(
    targets: list[dict[str, Any]], names: list[dict[str, str]], bodies: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Exact approval/variant/version/revision joins; never infer from a prefix."""
    name_index: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    body_index: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in names:
        name_key = (row.get("typegoedkeuringsnummer", ""), row.get("codevariantgk", ""), row.get("codeuitvoeringtgk", ""))
        name_index[name_key].append(row)
    for row in bodies:
        body_key = (row.get("typegoedkeuringsnummer", ""), row.get("codevarianttgk", ""), row.get("codeuitvoeringtgk", ""), row.get("volgnummerrevisieuitvoering", ""))
        body_index[body_key].append(row)
    result = []
    for target in targets:
        raw = target["raw_source_evidence"]
        key = (str(raw.get("eeg_type_approval") or ""), str(raw.get("variant") or ""), str(raw.get("version") or ""))
        matches = name_index.get(key, []) if all(key) else []
        joined: list[dict[str, Any]] = [
            {"name": row, "bodywork": body_index.get((*key, row.get("volgnummerrevisieuitvoering", "")), [])}
            for row in matches
        ]
        names_found = sorted({row.get("handelsbenamingfabrikant", "") for row in matches} - {""})
        types_found = sorted({row.get("typeaanduidingfabrikant", "") for row in matches} - {""})
        body_codes = sorted({row.get("codecarrosserietype", "") for item in joined for row in item["bodywork"]} - {""})
        source_type = str(raw.get("type_text") or "")
        status = "missing_exact_approval_variant_version"
        if matches:
            status = "exact_source_evidence"
            if not source_type or types_found != [source_type] or len(names_found) != 1 or len(body_codes) != 1:
                status = "incomplete_or_ambiguous_source_evidence"
        result.append({
            "row_key": target["row_key"], "approval": key[0], "variant": key[1], "version": key[2],
            "source_type": source_type, "ts_body_code": raw.get("body_code"),
            "status": status, "rdw_names": names_found, "rdw_types": types_found, "rdw_body_codes": body_codes,
            "source_body_disagreement": bool(body_codes) and raw.get("body_code") not in body_codes,
            "independent_rows": joined, "approved_model_rule": False,
        })
    return result


def fetch_dataset(dataset: str, approvals: list[str]) -> dict[str, Any]:
    metadata_url = f"https://opendata.rdw.nl/api/views/{dataset}.json"
    with urlopen(metadata_url, timeout=30) as response:
        metadata = json.load(response)
    rows = []
    requests = []
    for start in range(0, len(approvals), 15):
        query = {"$where": approval_query(approvals[start:start + 15]), "$limit": 50000}
        url = f"https://opendata.rdw.nl/resource/{dataset}.json?{urlencode(query)}"
        with urlopen(url, timeout=30) as response:
            page = json.load(response)
        if not isinstance(page, list) or len(page) >= 50000:
            raise ValueError("RDW query incomplete or exceeded bounded audit limit")
        rows.extend(page)
        requests.append({"url": url, "rows": len(page), "response_digest": digest(page)})
    with urlopen(metadata_url, timeout=30) as response:
        after = json.load(response)
    if metadata.get("rowsUpdatedAt") != after.get("rowsUpdatedAt"):
        raise ValueError("RDW source changed during audit")
    return {"dataset": dataset, "name": metadata.get("name"),
            "rows_updated_at": metadata.get("rowsUpdatedAt"), "requests": requests, "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposals", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    packet = json.loads(args.proposals.read_text())
    golf = packet["golf"]["targets"]
    approvals = sorted({str(t["raw_source_evidence"].get("eeg_type_approval") or "") for t in golf}
                       | {r["source_conditions"]["eeg_type_approval"] for r in packet["volvo_manifest"]["rules"]})
    approvals = [value for value in approvals if value]
    fetched = {key: fetch_dataset(dataset, approvals) for key, dataset in DATASETS.items()}
    evidence = join_golf_evidence(golf, fetched["names"]["rows"], fetched["bodywork"]["rows"])
    volvo = []
    for rule in packet["volvo_manifest"]["rules"]:
        approval = rule["source_conditions"]["eeg_type_approval"]
        rows = [row for row in fetched["names"]["rows"] if row.get("typegoedkeuringsnummer") == approval]
        volvo.append({
            "rule_id": rule["rule_id"], "approval": approval, "proposed_family": rule["model"],
            "support_count": rule["support_count"], "remaining_conflicts": rule["remaining_conflicts"],
            "rdw_names": sorted({row.get("handelsbenamingfabrikant", "") for row in rows}),
            "rdw_types": sorted({row.get("typeaanduidingfabrikant", "") for row in rows}),
            "rdw_rows": len(rows), "approval_state": "requires_domain_review",
            "warning": "Approval-level manufacturer evidence only; not an exact TS variant/version or KType confirmation",
        })
    payload = {"retrieved_at": datetime.now(UTC).isoformat(), "read_only": True,
               "proposal_digest": digest(packet), "datasets": fetched, "golf": evidence, "volvo": volvo,
               "golf_status_counts": dict(Counter(row["status"] for row in evidence)),
               "golf_source_body_disagreement_count": sum(row["source_body_disagreement"] for row in evidence),
               "activated_rules": 0}
    write_private_json(args.output, payload)
    print({key: payload[key] for key in ("golf_status_counts", "golf_source_body_disagreement_count", "activated_rules")})


if __name__ == "__main__":
    main()
