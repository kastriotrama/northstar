"""Prepare unapproved Golf/Volvo proposals and a read-only KType readiness audit."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg
from neo4j import READ_ACCESS, GraphDatabase
from neo4j.exceptions import DriverError, Neo4jError
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from ingestion.active_rules import load_active_rules
from ingestion.config import IngestionSettings
from ingestion.normalization_rules import normalize_ts_record
from ingestion.tecdoc.match_run_adapters import load_postgres_ktype_catalog
from ingestion.tecdoc.model_fingerprint_proposals import (
    ModelFingerprintObservation,
    propose_model_fingerprints,
)
from ingestion.tecdoc.reference_data import engine_fuel_evidence, load_key_table_labels
from ingestion.tecdoc.remote_match_run import _fetch_local_raw_page
from scripts.inspect_bodywork_repair_package import GROUPS, select_group
from scripts.replay_match_repair_evidence import review_source_evidence
from scripts.validate_local_matcher_cohort import digest, write_private_json


def candidate_only_targets(report: dict[str, Any]) -> Counter[str]:
    selected = [row["after"] for row in report["comparison"]["changed_records"]
                if row["before"]["terminal"] == "review_required" and row["after"]["terminal"] == "provisional"]
    if any("candidate_only_not_graph_safe" not in row["reason_codes"] for row in selected):
        raise ValueError("provisional cohort includes a different route")
    return Counter(row["top_candidate_reference"] for row in selected)


def candidate_readiness(
    candidate: dict[str, Any], relationships: list[dict[str, Any]], *,
    engine_fuel_labels: dict[str, str], catalog_displacements: dict[str, set[int]],
) -> dict[str, Any]:
    """Expose all prerequisites without changing candidate or graph eligibility."""
    attrs = candidate["attributes"]
    blockers = {"independent_confirmation_required", "explicit_promotion_required"}
    if attrs.get("promotion_status") != "candidate_only":
        blockers.add("unexpected_promotion_status")
    if not candidate.get("source_row_refs"):
        blockers.add("variant_provenance_missing")
    if not attrs.get("year_from"):
        blockers.add("year_missing")
    active = [row for row in relationships if row["status"] == "candidate"
              and row["evidence"].get("engine_deleted") is False]
    if any(row["evidence"].get("engine_deleted") not in (False, True) for row in relationships):
        blockers.add("engine_activity_unknown")
    distinct = {row["to_source_key"] for row in active}
    if len(distinct) != 1:
        blockers.add("engine_ambiguous" if distinct else "active_engine_missing")
    if len(distinct) != len(active):
        blockers.add("duplicate_engine_assertions")
    engines = []
    for row in active:
        fields, evidence = row["attributes"], row["evidence"]
        code = fields.get("engine_fuel_code")
        label = engine_fuel_labels.get(str(code))
        fuel_evidence = engine_fuel_evidence(code, engine_fuel_labels)
        canonical = fuel_evidence.scalar_fuel_type
        if canonical is None:
            blockers.add("mixed_engine_fuel_requires_promotion_contract" if fuel_evidence.representation == "mixed"
                         else "engine_fuel_label_unmapped" if label else "engine_fuel_label_missing")
        if not evidence.get("engine_source_row_ref") or not evidence.get("ktype_source_row_refs") or not evidence.get("applicability"):
            blockers.add("engine_provenance_incomplete")
        if any(item.get("exclude") is not False for item in evidence.get("applicability", [])):
            blockers.add("engine_applicability_requires_review")
        lower, upper = fields.get("displacement_cc_from"), fields.get("displacement_cc_to")
        exact = lower if lower and lower == upper else None
        consensus = sorted(catalog_displacements.get(row["to_source_key"], set()))
        if exact is None:
            blockers.add("full_source_displacement_verification_required")
            if len(consensus) != 1:
                blockers.add("catalog_displacement_not_unique")
        engines.append({"engine_source_key": row["to_source_key"], "engine_code": fields.get("engine_code"),
                        "engine_fuel_code": code, "official_engine_fuel_label": label,
                        "fuel_evidence": fuel_evidence.as_attributes(),
                        "canonical_engine_fuel": canonical, "exact_engine_displacement": exact,
                        "catalog_displacement_values": consensus, "evidence": evidence})
    return {"source_key": candidate["source_key"], "stored_reason": attrs.get("candidate_only_reason"),
            "vehicle_fuel_type": attrs.get("vehicle_fuel_type"), "engine_count": len(distinct),
            "blockers": sorted(blockers), "engines": engines, "ready_to_promote": False,
            "note": "Vehicle fuel or TS match confidence cannot substitute for engine identity/fuel evidence"}


def explicit_golf_anchor(raw: dict[str, Any]) -> str | None:
    """Use explicit source text only, never the candidate winner or TS body code."""
    model = str(raw.get("model") or "").upper()
    brand = str(raw.get("brand") or "").upper()
    pattern = r"\bGOLF\s+(VARIANT|PLUS|SPORTSVAN)\b"
    direct = re.search(pattern, model)
    embedded = re.search(pattern, brand)
    if model.strip():
        if direct and (embedded is None or embedded[1] == direct[1]):
            return f"GOLF {direct[1]}"
        return None
    return f"GOLF {embedded[1]}" if embedded else None


def vehicle_group(raw: dict[str, Any], row_key: str) -> str:
    # Deduplicate repeated VINs when present, otherwise plates. This key is
    # private, not a canonical vehicle ID or an externally shared identifier.
    value = raw.get("vin") or raw.get("plate")
    return digest(["vehicle", str(value).strip().upper()]) if value else row_key


def golf_observation(raw: dict[str, Any], normalized: dict[str, Any]) -> ModelFingerprintObservation:
    return ModelFingerprintObservation(
        manufacturer=str(normalized.get("manufacturer") or ""),
        type_text=str(raw.get("type_text") or ""), type_approval=str(raw.get("eeg_type_approval") or ""),
        variant=str(raw.get("variant") or ""), version=str(raw.get("version") or ""),
        production_year=normalized.get("production_year"),
        fuel="|".join(sorted(normalized.get("fuel_match_tokens") or [])),
        displacement_cc=normalized.get("displacement_cc"), power_kw=normalized.get("power_kw"),
        model=explicit_golf_anchor(raw),
    )


def volvo_proposals(items: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if item["group"] not in {"xc40", "xc60_ii"}:
            continue
        approval = str(item["raw_source_evidence"].get("eeg_type_approval") or "")
        if approval:
            grouped[(item["group"], approval)].append(item)
    rules = []
    for (group, approval), rows in sorted(grouped.items()):
        rule_id = f"BODY-PROPOSED-{digest([group, approval])[:16]}"
        rules.append({
            "rule_id": rule_id, "status": "proposed", "field": "bodywork", "manufacturer": "VOLVO",
            "model": GROUPS[group][2], "source_value": "estate", "allowed_values": ["suv"],
            "source_conditions": {"body_code": "AC", "eeg_type_approval": approval},
            "reviewed_by": "", "evidence_ref": "", "support_count": len(rows),
            "terminal_counts": dict(Counter(row["evaluation"]["terminal"] for row in rows)),
            "row_keys": [row["row_key"] for row in rows],
            "remaining_conflicts": dict(Counter(reason for row in rows for reason in row["evaluation"]["reason_codes"]
                                                if reason.startswith("conflict:") or reason == "context_conflict:drive_type")),
            "approval_question": "Is AC/estate compatible but non-confirming for this exact catalog family and source approval?",
        })
    return {"version": "volvo-bodywork-proposals-v1", "status": "proposed", "rules": rules,
            "coverage_count": sum(len(rows) for rows in grouped.values()),
            "uncovered_count": sum(item["group"] in {"xc40", "xc60_ii"} for item in items) - sum(len(rows) for rows in grouped.values())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--reference-directory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    targets = candidate_only_targets(report)
    settings = IngestionSettings(_env_file=args.env_file)  # type: ignore[call-arg]
    if conninfo_to_dict(settings.database_url).get("host") not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("repair audit requires local PostgreSQL")
    labels = load_key_table_labels(args.reference_directory, key_table_id="088")
    label_digest = digest(labels)
    with psycopg.connect(settings.database_url, options="-c default_transaction_read_only=on") as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        rules, manufacturers = load_active_rules(conn)
        catalog = load_postgres_ktype_catalog(conn, batch_id=report["catalog_version"])
        rows = _fetch_local_raw_page(conn, source_batch_prefix=report["source_prefix"], after_id=0, limit=report["count"])
        if digest(rows) != report["source_digest"] or digest([asdict(c) for c in catalog]) != report["catalog_digest"]:
            raise ValueError("source/catalog differs from pinned report")
        if digest([asdict(rules), manufacturers]) != report["rules_digest"]:
            raise ValueError("rules differ from pinned report")
        source_keys = [f"variant:{key}" for key in targets]
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT source_key,attributes,source_row_refs FROM core.tecdoc_canonical_candidates "
                        "WHERE batch_id=%s AND entity_type='vehicle_variant' AND source_key=ANY(%s)",
                        (report["catalog_version"], source_keys))
            candidate_rows = cur.fetchall()
            if len(candidate_rows) != len(targets):
                raise ValueError("candidate-only audit targets missing")
            cur.execute("SELECT from_source_key,to_source_key,status,attributes,evidence FROM core.tecdoc_candidate_relationships "
                        "WHERE batch_id=%s AND relationship_type='USES_ENGINE' AND from_source_key=ANY(%s)",
                        (report["catalog_version"], source_keys))
            relationships = cur.fetchall()
            engines = list({row["to_source_key"] for row in relationships})
            cur.execute("SELECT r.to_source_key, v.attributes->>'displacement_cc' AS displacement "
                        "FROM core.tecdoc_candidate_relationships r JOIN core.tecdoc_canonical_candidates v "
                        "ON v.batch_id=r.batch_id AND v.source_key=r.from_source_key AND v.entity_type='vehicle_variant' "
                        "WHERE r.batch_id=%s AND r.relationship_type='USES_ENGINE' AND r.status='candidate' "
                        "AND r.to_source_key=ANY(%s)", (report["catalog_version"], engines))
            consensus: dict[str, set[int]] = defaultdict(set)
            for row in cur.fetchall():
                if row["displacement"]:
                    consensus[row["to_source_key"]].add(int(row["displacement"]))
    readiness = [dict(candidate_readiness(c, [r for r in relationships if r["from_source_key"] == c["source_key"]],
                                         engine_fuel_labels=labels, catalog_displacements=consensus),
                      affected_cars=targets[c["source_key"].removeprefix("variant:")]) for c in candidate_rows]
    graph_snapshot: dict[str, Any] = {"checked": False}
    if urlparse(settings.neo4j_uri).hostname in {"localhost", "127.0.0.1", "::1"}:
        try:
            with (
                GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password),
                                     connection_timeout=5, connection_acquisition_timeout=5) as driver,
                driver.session(default_access_mode=READ_ACCESS) as session,
            ):
                graph_rows = session.run(
                    "UNWIND $refs AS ref OPTIONAL MATCH (a:Alias {source_system:'tecdoc',alias_type:'k_type',alias_text:ref})"
                    "-[:REFERS_TO]->(v:VehicleVariant) RETURN ref,collect(DISTINCT v.id) AS targets,"
                    "collect(DISTINCT labels(v)) AS labels", refs=list(targets)).data()
            graph_snapshot = {"checked": True, "rows": graph_rows}
        except (DriverError, Neo4jError) as error:
            # Provider exception messages can include connection details.
            graph_snapshot = {"checked": False, "error_type": type(error).__name__}
    by_ref = {c.candidate_reference: c for c in catalog}
    report_rows = {r["row_key"]: r for r in report["records"]}
    items = []
    golf = []
    seen = set()
    for source_id, raw in rows:
        key = digest([source_id, raw])
        outcome = report_rows[key]
        candidate = by_ref.get(outcome["top_candidate_reference"])
        group = select_group(raw, outcome, candidate.model if candidate else "")
        is_golf = "GOLF" in f"{raw.get('brand') or ''} {raw.get('model') or ''}".upper()
        if not group and not is_golf:
            continue
        normalized = normalize_ts_record(raw, rule_set=rules, manufacturer_entity_rules=manufacturers)
        if group:
            items.append({"group": group, "row_key": key, "plate": raw.get("plate"),
                          "raw_source_evidence": review_source_evidence(raw), "evaluation": outcome})
        if is_golf and normalized.normalized.get("manufacturer") == "Volkswagen":
            observation = golf_observation(raw, dict(normalized.normalized))
            identity = (vehicle_group(raw, key), observation.fingerprint_id(), observation.model)
            if identity not in seen:
                golf.append(observation)
                seen.add(identity)
    allowed = {label.upper() for candidate in catalog if candidate.manufacturer == "VW"
               for label in (candidate.model, *candidate.model_aliases)}
    proposals = propose_model_fingerprints(tuple(golf), minimum_anchor_count=2,
                                           allowed_models_by_manufacturer={"Volkswagen": allowed})
    golf_targets = [item for item in items if item["group"] == "golf_vii"]
    volvo = volvo_proposals(items)
    result = {
        "read_only": True, "status": "proposed", "contains_private_plates": True,
        "source_digest": report["source_digest"], "catalog_digest": report["catalog_digest"],
        "rules_digest": report["rules_digest"], "reference_labels_digest": label_digest,
        "candidate_count": len(readiness), "affected_provisional_cars": sum(targets.values()),
        "readiness": readiness, "readiness_evidence_digest": digest([candidate_rows, relationships, consensus]),
        "graph_snapshot": graph_snapshot,
        "golf": {"target_count": len(golf_targets), "deduplicated_source_observations": len(golf),
                 "explicit_anchors": dict(Counter(o.model for o in golf if o.model)),
                 "explicit_variant_anchors_with_approval": sum(o.model == "GOLF VARIANT" and bool(o.type_approval) for o in golf),
                 "proposals": [asdict(p) for p in proposals],
                 "target_type_counts": dict(Counter(str(i["raw_source_evidence"].get("type_text")) for i in golf_targets)),
                 "targets": golf_targets, "approval_state": "not_approved"},
        "volvo_manifest": volvo,
        "limitations": ["No inferred engine fuel replacement", "No independent model or KType verdicts",
                        "Catalog displacement consensus is not complete-source approval",
                        "No proposals activated or matches persisted", "Same development cohort, not held-out accuracy"],
    }
    write_private_json(args.output, result)
    print(json.dumps({"affected_provisional_cars": sum(targets.values()), "candidate_count": len(readiness),
                      "golf": {key: value for key, value in result["golf"].items() if key != "targets"},
                      "volvo_rules": len(volvo["rules"]), "volvo_covered": volvo["coverage_count"],
                      "graph_checked": graph_snapshot["checked"]}), flush=True)


if __name__ == "__main__":
    main()
