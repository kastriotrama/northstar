"""Reconcile replayed PostgreSQL decisions with current Neo4j TS aliases.

The audit is read-only and emits aggregate counts only. Plates, VINs, source
entity keys, source record IDs, and individual decision IDs are never written
to the report.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg
from neo4j import GraphDatabase
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from ingestion.config import IngestionSettings
from scripts.validate_local_matcher_cohort import write_private_json


def summarize_promotion_freshness(
    heads: list[dict[str, Any]],
    changed_records: list[dict[str, Any]],
    graph_aliases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Classify current aliases against replay state without retaining identities."""

    replay = {
        str(row["decision_id"]): {
            "terminal": str(row["route"]),
            "candidate_reference": row["selected_candidate_reference"],
        }
        for row in heads
    }
    unknown_changed_decisions = 0
    for row in changed_records:
        decision_id = str(row["decision_id"])
        if decision_id not in replay:
            unknown_changed_decisions += 1
            continue
        replay[decision_id] = {
            "terminal": str(row["current_terminal"]),
            "candidate_reference": row.get("current_candidate_reference"),
        }

    terminal_counts = Counter(
        str(state["terminal"]) for state in replay.values()
    )
    alias_states: Counter[str] = Counter()
    represented_resolved: set[str] = set()
    for alias in graph_aliases:
        decision_id = str(alias["decision_id"])
        state = replay.get(decision_id)
        targets = {
            str(value) for value in alias.get("target_references", ()) if value
        }
        if state is None:
            alias_states["unknown_decision"] += 1
            continue
        terminal = str(state["terminal"])
        expected = state["candidate_reference"]
        if terminal != "resolved":
            alias_states[f"stale_terminal:{terminal}"] += 1
            continue
        if len(targets) != 1:
            alias_states["stale_target_cardinality"] += 1
            continue
        if expected not in targets:
            alias_states["stale_target_mismatch"] += 1
            continue
        alias_states["fresh_resolved"] += 1
        represented_resolved.add(decision_id)

    resolved = terminal_counts.get("resolved", 0)
    stale = sum(
        count for state, count in alias_states.items() if state != "fresh_resolved"
    )
    return {
        "decision_heads": len(heads),
        "replayed_terminal_counts": dict(sorted(terminal_counts.items())),
        "graph_aliases": len(graph_aliases),
        "graph_alias_states": dict(sorted(alias_states.items())),
        "fresh_aliases": alias_states.get("fresh_resolved", 0),
        "stale_aliases_requiring_retirement": stale,
        "resolved_decisions_pending_promotion": resolved - len(represented_resolved),
        "unknown_changed_decisions": unknown_changed_decisions,
    }


def _load_heads(connection: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT d.decision_id, d.route, d.selected_candidate_reference "
            "FROM core.match_decision_heads h "
            "JOIN core.match_routing_decisions d ON d.decision_id=h.decision_id "
            "WHERE h.source_system='Transportstyrelsen' "
            "AND h.source_version='ts-v323-20260817'"
        )
        return [dict(row) for row in cursor.fetchall()]


def _load_graph_aliases(driver: Any) -> list[dict[str, Any]]:
    query = """
    MATCH (alias:Alias {source_system: 'transportstyrelsen'})-[:REFERS_TO]->
          (variant:VehicleVariant)
    WHERE alias.match_decision_id IS NOT NULL
    OPTIONAL MATCH (ktype:Alias {source_system: 'tecdoc', alias_type: 'k_type'})
                   -[:REFERS_TO]->(variant)
    RETURN alias.match_decision_id AS decision_id,
           collect(DISTINCT ktype.alias_text) AS target_references
    """
    records, _, _ = driver.execute_query(query, routing_="r")
    return [dict(record) for record in records]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--replay-report", required=True, type=Path)
    parser.add_argument("--expected-decisions", required=True, type=int)
    parser.add_argument("--expected-aliases", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    settings = IngestionSettings(_env_file=args.env_file)  # type: ignore[call-arg]
    if conninfo_to_dict(settings.database_url).get("host") not in {
        "localhost", "127.0.0.1", "::1",
    }:
        raise ValueError("promotion audit requires local PostgreSQL")
    if urlparse(settings.neo4j_uri).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("promotion audit requires local Neo4j")

    replay_report = json.loads(args.replay_report.read_text())
    changed_records = replay_report.get("changed_records")
    if not isinstance(changed_records, list):
        raise TypeError("replay report has no changed_records list")
    if replay_report.get("count") != args.expected_decisions:
        raise ValueError("replay report decision count differs from requested count")

    with psycopg.connect(
        settings.database_url, options="-c default_transaction_read_only=on"
    ) as connection:
        heads = _load_heads(connection)
    with GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    ) as driver:
        graph_aliases = _load_graph_aliases(driver)
    if len(heads) != args.expected_decisions:
        raise ValueError("current decision count differs from requested count")
    if len(graph_aliases) != args.expected_aliases:
        raise ValueError("current graph alias count differs from requested count")

    summary = summarize_promotion_freshness(heads, changed_records, graph_aliases)
    payload = {
        **summary,
        "source_replay_report": args.replay_report.name,
        "contains_private_plates": False,
        "contains_private_vins": False,
        "contains_source_record_ids": False,
        "read_only": True,
        "postgres_writes": 0,
        "neo4j_writes": 0,
    }
    write_private_json(args.output, payload)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
