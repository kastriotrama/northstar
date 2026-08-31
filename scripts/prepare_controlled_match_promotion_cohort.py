"""Prepare a read-only, graph-preflighted SCRUM-170/171 promotion cohort.

The script intentionally stops before both immutable-ledger persistence and
Neo4j writes.  It selects only current resolved heads that stayed identical in
the pinned v6 replay and target graph-safe TecDoc KTypes.  The resulting JSON
is private because it contains source entity keys (normally plate numbers).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg
from neo4j import GraphDatabase
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from ingestion.confidence_routing_repository import routing_decision_uuid
from ingestion.config import IngestionSettings
from ingestion.tecdoc.match_promotion import (
    MatchPromotion,
    PromotionMode,
    promote_and_attach_matches,
)
from ingestion.tecdoc.match_run_adapters import load_postgres_ktype_catalog
from scripts.validate_local_matcher_cohort import digest, write_private_json


def select_stable_heads(
    heads: list[dict[str, Any]],
    *,
    changed_decision_ids: set[str],
    catalog_types: Mapping[str, str],
    limit: int,
    minimum_confidence: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Select unchanged, resolved and graph-safe heads without mutating state."""

    if limit < 1:
        raise ValueError("limit must be positive")
    if not 0.0 <= minimum_confidence <= 1.0:
        raise ValueError("minimum_confidence must be between 0 and 1")
    counts: Counter[str] = Counter(current_heads=len(heads))
    selected: list[dict[str, Any]] = []
    eligible = 0
    seen_entities: set[str] = set()
    for head in sorted(heads, key=lambda row: (str(row.get("source_entity_key", "")), str(row["decision_id"]))):
        decision_id = str(head["decision_id"])
        if str(head.get("route")) != "resolved":
            counts["not_resolved"] += 1
            continue
        if decision_id in changed_decision_ids:
            counts["changed_in_v6_replay"] += 1
            continue
        reference = str(head.get("selected_candidate_reference") or "").strip()
        if not reference:
            counts["missing_selected_ktype"] += 1
            continue
        confidence = float(head.get("confidence") or 0.0)
        if confidence < minimum_confidence:
            counts["below_confidence_gate"] += 1
            continue
        candidate_type = catalog_types.get(reference)
        if candidate_type is None:
            counts["catalog_ktype_missing"] += 1
            continue
        if candidate_type != "TecDocKType":
            counts["candidate_only_not_graph_safe"] += 1
            continue
        entity_key = str(head.get("source_entity_key") or "").strip()
        if not entity_key:
            counts["missing_source_entity_key"] += 1
            continue
        if entity_key in seen_entities:
            counts["duplicate_source_entity_key"] += 1
            continue
        payload = head.get("decision_payload")
        if isinstance(payload, dict) and payload.get("hard_conflicts"):
            counts["hard_conflict"] += 1
            continue
        seen_entities.add(entity_key)
        eligible += 1
        if len(selected) < limit:
            selected.append(head)
    counts["eligible_before_limit"] = eligible
    counts["selected"] = len(selected)
    return selected, dict(sorted(counts.items()))


def _load_heads(connection: psycopg.Connection[Any], source_version: str) -> list[dict[str, Any]]:
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT h.decision_id, h.source_entity_key, d.source_batch_id, "
            "d.source_table, d.source_record_id, d.route, d.confidence, "
            "d.selected_candidate_reference, d.candidate_catalog_version, "
            "d.policy_version, d.decision_payload "
            "FROM core.match_decision_heads h "
            "JOIN core.match_routing_decisions d ON d.decision_id=h.decision_id "
            "WHERE h.source_system='Transportstyrelsen' AND h.source_version=%s "
            "ORDER BY h.source_entity_key, h.decision_id",
            (source_version,),
        )
        return [dict(row) for row in cursor.fetchall()]


def _promotion_rows(
    heads: list[dict[str, Any]],
    *,
    source_version: str,
    catalog_version: str,
) -> tuple[MatchPromotion, ...]:
    rows: list[MatchPromotion] = []
    for head in heads:
        entity_key = str(head["source_entity_key"])
        if not entity_key.startswith("plate:"):
            raise ValueError("promotion cohort contains a non-plate source entity key")
        decision_id = routing_decision_uuid(
            source_system="Transportstyrelsen",
            source_batch_id=str(head["source_batch_id"]),
            source_table=str(head["source_table"]),
            source_record_id=int(head["source_record_id"]),
            candidate_catalog_version=catalog_version,
            policy_version=str(head["policy_version"]),
        )
        rows.append(
            MatchPromotion(
                decision_id=decision_id,
                source_system="Transportstyrelsen",
                source_version=source_version,
                source_entity_key=entity_key,
                alias_type="plate",
                alias_text=entity_key.removeprefix("plate:"),
                ktype_reference=str(head["selected_candidate_reference"]),
                confidence=float(head["confidence"]),
            )
        )
    return tuple(rows)


def select_graph_safe_heads(
    heads: list[dict[str, Any]],
    graph_rows: list[dict[str, Any]],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Keep only rows whose planned v6 alias has no graph collision."""

    if limit < 1:
        raise ValueError("limit must be positive")
    by_decision = {str(row["decision_id"]): row for row in graph_rows}
    counts: Counter[str] = Counter(graph_checked=len(heads))
    safe: list[dict[str, Any]] = []
    for head in heads:
        planned = str(head["planned_decision_id"])
        state = by_decision.get(planned)
        if state is None:
            counts["graph_preflight_missing_row"] += 1
            continue
        expected = state.get("expected_variant_id")
        targets = {str(value) for value in state.get("targets", ()) if value}
        decisions = {str(value) for value in state.get("decisions", ()) if value}
        if not expected:
            counts["graph_ktype_missing"] += 1
            continue
        if targets and targets != {str(expected)}:
            counts["graph_alias_collision"] += 1
            continue
        if len(targets) > 1:
            counts["graph_alias_collision"] += 1
            continue
        if decisions and decisions != {planned}:
            counts["graph_alias_requires_retirement"] += 1
            continue
        counts["graph_alias_absent_or_idempotent"] += 1
        if len(safe) < limit:
            safe.append(head)
    counts["graph_safe_selected"] = len(safe)
    return safe, dict(sorted(counts.items()))


def _load_graph_alias_states(driver: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query = """
    UNWIND $rows AS row
    OPTIONAL MATCH (ktype:Alias {source_system: 'tecdoc', alias_type: 'k_type',
                                 alias_text: row.ktype_reference})-[:REFERS_TO]->(expected:VehicleVariant)
    OPTIONAL MATCH (alias:Alias {source_system: 'transportstyrelsen', alias_type: 'plate',
                                 alias_text: row.alias_text})-[:REFERS_TO]->(target:VehicleVariant)
    RETURN row.decision_id AS decision_id,
           collect(DISTINCT expected.id)[0] AS expected_variant_id,
           collect(DISTINCT target.id) AS targets,
           collect(DISTINCT alias.match_decision_id) AS decisions
    """
    records, _, _ = driver.execute_query(query, rows=rows, routing_="r")
    return [dict(record) for record in records]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--replay-report", required=True, type=Path)
    parser.add_argument("--catalog-version", required=True)
    parser.add_argument("--expected-candidates", required=True, type=int)
    parser.add_argument("--catalog-digest", required=True)
    parser.add_argument("--source-version", required=True)
    parser.add_argument("--expected-decisions", required=True, type=int)
    parser.add_argument("--cohort-size", type=int, default=1_000)
    parser.add_argument("--minimum-confidence", type=float, default=0.975)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    settings = IngestionSettings(_env_file=args.env_file)  # type: ignore[call-arg]
    if conninfo_to_dict(settings.database_url).get("host") not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("promotion cohort requires local PostgreSQL")
    if urlparse(settings.neo4j_uri).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("promotion cohort requires local Neo4j")
    replay = json.loads(args.replay_report.read_text())
    changed = replay.get("changed_records")
    if not isinstance(changed, list):
        raise TypeError("replay report has no changed_records list")
    if replay.get("count") != args.expected_decisions:
        raise ValueError("replay report decision count differs from expected decisions")
    if replay.get("new_candidate_catalog_version") != args.catalog_version:
        raise ValueError("replay report catalog version differs from requested catalog")
    if replay.get("new_catalog_digest") != args.catalog_digest:
        raise ValueError("replay report catalog digest differs from requested catalog")

    with psycopg.connect(settings.database_url, options="-c default_transaction_read_only=on") as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        catalog = load_postgres_ktype_catalog(connection, batch_id=args.catalog_version)
        catalog_digest = digest([asdict(candidate) for candidate in catalog])
        if len(catalog) != args.expected_candidates or catalog_digest != args.catalog_digest:
            raise ValueError("PostgreSQL candidate catalog does not match the pinned digest")
        heads = _load_heads(connection, args.source_version)
        if len(heads) != args.expected_decisions:
            raise ValueError("current decision-head count differs from expected decisions")

    changed_ids = {str(row["decision_id"]) for row in changed}
    catalog_types = {candidate.candidate_reference: candidate.candidate_type for candidate in catalog}
    selected, selection_counts = select_stable_heads(
        heads,
        changed_decision_ids=changed_ids,
        catalog_types=catalog_types,
        # Keep the full eligible pool until graph collision checks have run;
        # existing aliases are deliberately filtered before taking the limit.
        limit=len(heads),
        minimum_confidence=args.minimum_confidence,
    )
    planned_promotions = _promotion_rows(
        selected,
        source_version=args.source_version,
        catalog_version=args.catalog_version,
    )
    planned_by_entity = {
        promotion.source_entity_key: promotion for promotion in planned_promotions
    }
    graph_probe_rows = [
        {
            "decision_id": str(promotion.decision_id),
            "alias_text": promotion.alias_text,
            "ktype_reference": promotion.ktype_reference,
        }
        for promotion in planned_promotions
    ]
    with GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
        connection_timeout=5,
        connection_acquisition_timeout=5,
    ) as driver:
        driver.verify_connectivity()
        graph_states = _load_graph_alias_states(driver, graph_probe_rows)
        graph_safe, graph_counts = select_graph_safe_heads(
            [
                {
                    **head,
                    "planned_decision_id": str(
                        planned_by_entity[str(head["source_entity_key"])].decision_id
                    ),
                }
                for head in selected
            ],
            graph_states,
            limit=args.cohort_size,
        )
        if len(graph_safe) != args.cohort_size:
            raise ValueError(
                "fewer than the requested number of graph-safe promotion rows are available"
            )
        promotions = tuple(
            planned_by_entity[str(head["source_entity_key"])] for head in graph_safe
        )
        preflight_count = promote_and_attach_matches(
            driver, promotions, mode=PromotionMode.DRY_RUN, controlled_limit=args.cohort_size
        )
    if preflight_count != len(promotions):
        raise RuntimeError("Neo4j promotion preflight did not validate every row")

    evidence_rows = []
    selection_counts.update(graph_counts)
    selection_counts["selected"] = len(graph_safe)
    for head in graph_safe:
        reference = str(head["selected_candidate_reference"])
        candidate = next(item for item in catalog if item.candidate_reference == reference)
        promotion = planned_by_entity[str(head["source_entity_key"])]
        evidence_rows.append(
            {
                "decision_id": str(promotion.decision_id),
                "current_decision_id": str(head["decision_id"]),
                "source_entity_key": str(head["source_entity_key"]),
                "alias_text": str(head["source_entity_key"]).removeprefix("plate:"),
                "ktype_reference": reference,
                "confidence": float(head["confidence"]),
                "terminal": "resolved",
                "v6_replay": "unchanged_identity_and_terminal",
                "candidate_type": candidate.candidate_type,
                "manufacturer": candidate.manufacturer,
                "model": candidate.model,
                "year_from": candidate.year_from,
                "year_to": candidate.year_to,
                "fuels": sorted(candidate.fuels),
                "engine_codes": sorted(candidate.engine_codes),
                "displacement_cc": candidate.displacement_cc,
                "power_kw": candidate.power_kw,
                "drive_type": candidate.drive_type,
                "bodyworks": sorted(candidate.bodyworks),
            }
        )
    payload = {
        "status": "validated_dry_run_cohort",
        "cohort_size": len(evidence_rows),
        "selection_counts": selection_counts,
        "graph_preflight_count": preflight_count,
        "source_version": args.source_version,
        "replay_report": args.replay_report.name,
        "replay_report_digest": digest(replay),
        "candidate_catalog_version": args.catalog_version,
        "candidate_catalog_digest": catalog_digest,
        "minimum_confidence": args.minimum_confidence,
        "decision_ids_are_immutable": True,
        "v6_decision_ids_are_planned_only": True,
        "ledger_persistence_required_before_alias_write": True,
        "decisions_persisted": 0,
        "aliases_written": 0,
        "postgres_writes": 0,
        "neo4j_writes": 0,
        "read_only": True,
        "contains_private_plates": True,
        "contains_private_vins": False,
        "rows": evidence_rows,
    }
    write_private_json(args.output, payload)
    print(json.dumps({
        "status": payload["status"],
        "cohort_size": len(evidence_rows),
        "selection_counts": selection_counts,
        "graph_preflight_count": preflight_count,
        "postgres_writes": 0,
        "neo4j_writes": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
