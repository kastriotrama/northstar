"""PostgreSQL repository for immutable confidence-routing decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid5

from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.confidence_routing import ConfidenceRoutingDecision
from ingestion.confidence_routing_migrations import MATCH_ROUTING_TABLE
from ingestion.confidence_routing_migrations import (
    MATCH_DECISION_HEAD_TABLE,
    MATCH_DECISION_SUPERSESSION_TABLE,
)

ROUTING_NAMESPACE = UUID("8d286d12-6811-43e3-a183-21a9b5cccbcb")
_SOURCE_TABLE_PATTERN = re.compile(r"staging\.[a-z][a-z0-9_]*")


class DecisionWriteMode(StrEnum):
    DRY_RUN = "dry_run"
    PERSIST = "persist"


@dataclass(frozen=True)
class DecisionPersistenceResult:
    decision_id: UUID
    mode: DecisionWriteMode
    persisted: bool
    decision_row_id: int | None
    superseded_decision_id: UUID | None


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


def persist_routing_decision(
    connection: Connection,
    *,
    source_system: str,
    source_version: str,
    source_entity_key: str,
    source_batch_id: str,
    source_table: str,
    source_record_id: int,
    candidate_catalog_version: str,
    decision: ConfidenceRoutingDecision,
    mode: DecisionWriteMode = DecisionWriteMode.DRY_RUN,
    supersession_reason: str = "new catalog or policy evaluation",
) -> DecisionPersistenceResult:
    """Validate a decision, optionally persist it and atomically advance its identity head."""

    normalized_source = _required_text(source_system, "source_system")
    normalized_version = _required_text(source_version, "source_version")
    normalized_entity = _required_text(source_entity_key, "source_entity_key")
    normalized_reason = _required_text(supersession_reason, "supersession_reason")
    normalized_catalog = _required_text(candidate_catalog_version, "candidate_catalog_version")
    normalized_batch = _required_text(source_batch_id, "source_batch_id")
    normalized_table = source_table.strip()
    if not _SOURCE_TABLE_PATTERN.fullmatch(normalized_table):
        raise ValueError("source_table must be a staging.<entity> table")
    if source_record_id < 1:
        raise ValueError("source_record_id must be positive")
    decision_id = routing_decision_uuid(
        source_system=normalized_source,
        source_batch_id=normalized_batch,
        source_table=normalized_table,
        source_record_id=source_record_id,
        candidate_catalog_version=normalized_catalog,
        policy_version=decision.policy_version,
    )

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT 1 FROM {normalized_table} WHERE id = %s AND source_batch_id = %s",
            (source_record_id, normalized_batch),
        )
        if cursor.fetchone() is None:
            raise ValueError("source staging record does not exist in the selected batch")
        cursor.execute(
            f"SELECT decision_id FROM {MATCH_DECISION_HEAD_TABLE} "
            "WHERE source_system = %s AND source_version = %s AND source_entity_key = %s",
            (normalized_source, normalized_version, normalized_entity),
        )
        current = cursor.fetchone()
    predecessor = None if current is None else UUID(str(current[0]))

    if mode == DecisionWriteMode.DRY_RUN:
        return DecisionPersistenceResult(
            decision_id=decision_id,
            mode=mode,
            persisted=False,
            decision_row_id=None,
            superseded_decision_id=predecessor if predecessor != decision_id else None,
        )
    if mode != DecisionWriteMode.PERSIST:
        raise ValueError(f"unsupported decision write mode: {mode}")

    row_id = store_routing_decision(
        connection,
        source_system=normalized_source,
        source_batch_id=normalized_batch,
        source_table=normalized_table,
        source_record_id=source_record_id,
        candidate_catalog_version=normalized_catalog,
        decision=decision,
    )
    superseded = predecessor if predecessor != decision_id else None
    with connection.cursor() as cursor:
        if superseded is not None:
            cursor.execute(
                f"INSERT INTO {MATCH_DECISION_SUPERSESSION_TABLE} "
                "(predecessor_decision_id, successor_decision_id, reason) VALUES (%s, %s, %s) "
                "ON CONFLICT (predecessor_decision_id) DO NOTHING",
                (superseded, decision_id, normalized_reason),
            )
            if cursor.rowcount != 1:
                cursor.execute(
                    f"SELECT successor_decision_id, reason FROM "
                    f"{MATCH_DECISION_SUPERSESSION_TABLE} "
                    "WHERE predecessor_decision_id = %s",
                    (superseded,),
                )
                existing_supersession = cursor.fetchone()
                if existing_supersession != (decision_id, normalized_reason):
                    raise ValueError(f"decision {superseded} was already superseded differently")
        cursor.execute(
            f"INSERT INTO {MATCH_DECISION_HEAD_TABLE} "
            "(source_system, source_version, source_entity_key, decision_id, "
            "selected_candidate_reference) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (source_system, source_version, source_entity_key) DO UPDATE SET "
            "decision_id = EXCLUDED.decision_id, "
            "selected_candidate_reference = EXCLUDED.selected_candidate_reference, "
            "updated_at = now()",
            (
                normalized_source,
                normalized_version,
                normalized_entity,
                decision_id,
                decision.selected_candidate_reference,
            ),
        )
    return DecisionPersistenceResult(
        decision_id=decision_id,
        mode=mode,
        persisted=True,
        decision_row_id=row_id,
        superseded_decision_id=superseded,
    )


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


def fetch_promotable_decision_heads(
    connection: Connection,
    *,
    source_system: str,
    source_version: str,
    limit: int | None = None,
) -> tuple[dict[str, Any], ...]:
    """Read current resolved heads only; provisional/review decisions are never promotable."""

    normalized_source = _required_text(source_system, "source_system")
    normalized_version = _required_text(source_version, "source_version")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    query = (
        f"SELECT h.decision_id, h.source_entity_key, d.selected_candidate_reference, "
        f"d.confidence FROM {MATCH_DECISION_HEAD_TABLE} h "
        f"JOIN {MATCH_ROUTING_TABLE} d ON d.decision_id = h.decision_id "
        "WHERE h.source_system = %s AND h.source_version = %s AND d.route = 'resolved' "
        "AND d.selected_candidate_reference IS NOT NULL "
        "ORDER BY h.source_entity_key, h.decision_id"
    )
    parameters: list[object] = [normalized_source, normalized_version]
    if limit is not None:
        query += " LIMIT %s"
        parameters.append(limit)
    with connection.cursor() as cursor:
        cursor.execute(query, parameters)
        rows = cursor.fetchall()
    return tuple(
        {
            "decision_id": UUID(str(row[0])),
            "source_entity_key": str(row[1]),
            "selected_candidate_reference": str(row[2]),
            "confidence": float(row[3]),
        }
        for row in rows
    )
