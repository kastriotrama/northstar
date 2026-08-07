"""PostgreSQL repository for immutable confidence-routing decisions."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID, uuid5

from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.confidence_routing import ConfidenceRoutingDecision
from ingestion.confidence_routing_migrations import MATCH_ROUTING_TABLE

ROUTING_NAMESPACE = UUID("8d286d12-6811-43e3-a183-21a9b5cccbcb")
_SOURCE_TABLE_PATTERN = re.compile(r"staging\.[a-z][a-z0-9_]*")


def routing_decision_uuid(
    *,
    source_system: str,
    source_batch_id: str,
    source_table: str,
    source_record_id: int,
    candidate_catalog_version: str,
    policy_version: str,
) -> UUID:
    return uuid5(
        ROUTING_NAMESPACE,
        ":".join(
            (
                source_system,
                source_batch_id,
                source_table,
                str(source_record_id),
                candidate_catalog_version,
                policy_version,
            )
        ),
    )


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def store_routing_decision(
    connection: Connection,
    *,
    source_system: str,
    source_batch_id: str,
    source_table: str,
    source_record_id: int,
    candidate_catalog_version: str,
    decision: ConfidenceRoutingDecision,
) -> int:
    """Insert once and accept only byte-equivalent deterministic retries."""

    normalized_source = _required_text(source_system, "source_system")
    normalized_batch = _required_text(source_batch_id, "source_batch_id")
    normalized_table = source_table.strip()
    if not _SOURCE_TABLE_PATTERN.fullmatch(normalized_table):
        raise ValueError("source_table must be a staging.<entity> table")
    if source_record_id < 1:
        raise ValueError("source_record_id must be positive")
    normalized_catalog = _required_text(candidate_catalog_version, "candidate_catalog_version")
    decision_id = routing_decision_uuid(
        source_system=normalized_source,
        source_batch_id=normalized_batch,
        source_table=normalized_table,
        source_record_id=source_record_id,
        candidate_catalog_version=normalized_catalog,
        policy_version=decision.policy_version,
    )
    payload = decision.to_payload()
    expected = (
        normalized_source,
        normalized_batch,
        normalized_table,
        source_record_id,
        normalized_catalog,
        decision.policy_version,
        decision.route,
        decision.confidence,
        decision.selected_candidate_reference,
        payload,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT 1 FROM {normalized_table} WHERE id = %s AND source_batch_id = %s",
            (source_record_id, normalized_batch),
        )
        if cursor.fetchone() is None:
            raise ValueError("source staging record does not exist in the selected batch")
        cursor.execute(
            f"INSERT INTO {MATCH_ROUTING_TABLE} "
            "(decision_id, source_system, source_batch_id, source_table, source_record_id, "
            "candidate_catalog_version, policy_version, route, confidence, "
            "selected_candidate_reference, decision_payload) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (decision_id) DO NOTHING RETURNING id",
            (decision_id, *expected[:9], Jsonb(payload)),
        )
        inserted = cursor.fetchone()
        if inserted is not None:
            return int(inserted[0])
        cursor.execute(
            f"SELECT id, source_system, source_batch_id, source_table, source_record_id, "
            "candidate_catalog_version, policy_version, route, confidence, "
            f"selected_candidate_reference, decision_payload FROM {MATCH_ROUTING_TABLE} "
            "WHERE decision_id = %s",
            (decision_id,),
        )
        existing = cursor.fetchone()
    if existing is None:
        raise RuntimeError("routing decision conflict returned no existing row")
    actual = (
        str(existing[1]),
        str(existing[2]),
        str(existing[3]),
        int(existing[4]),
        str(existing[5]),
        str(existing[6]),
        str(existing[7]),
        float(existing[8]),
        None if existing[9] is None else str(existing[9]),
        dict(existing[10]),
    )
    if actual != expected:
        raise ValueError(f"decision_id {decision_id} already has a different payload")
    return int(existing[0])


def fetch_batch_routing_decisions(
    connection: Connection,
    source_batch_id: str,
) -> tuple[dict[str, Any], ...]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT source_record_id, candidate_catalog_version, policy_version, route, "
            f"confidence, selected_candidate_reference, decision_payload "
            f"FROM {MATCH_ROUTING_TABLE} WHERE source_batch_id = %s "
            "ORDER BY source_record_id, candidate_catalog_version, policy_version",
            (source_batch_id,),
        )
        rows = cursor.fetchall()
    return tuple(
        {
            "source_record_id": int(row[0]),
            "candidate_catalog_version": str(row[1]),
            "policy_version": str(row[2]),
            "route": str(row[3]),
            "confidence": float(row[4]),
            "selected_candidate_reference": None if row[5] is None else str(row[5]),
            "decision_payload": dict(row[6]),
        }
        for row in rows
    )
