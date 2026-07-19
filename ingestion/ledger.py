"""Append-only enrichment-ledger writer and provenance reader (SCRUM-17).

`record_ledger_entry` is the single sanctioned write path: every graph write
and enrichment event records provenance through it. Corrections never mutate
existing rows -- they are new compensating entries pointing at the corrected
row via `corrects_ledger_id` (the append-only trigger rejects UPDATE/DELETE
at the database level regardless).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.ledger_migrations import LEDGER_TABLE
from northstar.node_ids import is_valid_node_id


@dataclass(frozen=True)
class LedgerEntry:
    """One immutable provenance row."""

    id: int
    event_id: UUID
    source: str
    target_node_id: str
    attributes_added: tuple[str, ...]
    nodes_benefited: int
    cost_eur: Decimal
    confidence: float
    evidence: dict[str, Any]
    source_batch_id: str | None
    corrects_ledger_id: int | None
    created_at: datetime


def record_ledger_entry(
    connection: Connection,
    *,
    event_id: UUID,
    source: str,
    target_node_id: str,
    confidence: float,
    attributes_added: Sequence[str] = (),
    nodes_benefited: int = 1,
    cost_eur: Decimal = Decimal("0"),
    evidence: dict[str, Any] | None = None,
    source_batch_id: str | None = None,
    corrects_ledger_id: int | None = None,
) -> int:
    """Append one provenance entry and return its ledger id.

    Does NOT commit: the caller owns the transaction, so a ledger entry can
    be committed atomically with the other Postgres changes of the same
    logical operation. Standalone callers must commit afterwards.
    """

    normalized_source = source.strip()
    if not normalized_source:
        raise ValueError("source must not be empty")
    if not is_valid_node_id(target_node_id):
        raise ValueError(f"target_node_id is not a canonical node id: {target_node_id!r}")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be between 0.0 and 1.0")
    if nodes_benefited < 1:
        raise ValueError("nodes_benefited must be at least 1")
    if cost_eur < 0:
        raise ValueError("cost_eur must be non-negative")

    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {LEDGER_TABLE} "
            "(event_id, source, target_node_id, attributes_added, nodes_benefited, "
            "cost_eur, confidence, evidence, source_batch_id, corrects_ledger_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (event_id) DO NOTHING RETURNING id",
            (
                event_id,
                normalized_source,
                target_node_id,
                list(attributes_added),
                nodes_benefited,
                cost_eur,
                confidence,
                Jsonb(evidence or {}),
                source_batch_id,
                corrects_ledger_id,
            ),
        )
        row = cursor.fetchone()
        if row is not None:
            return int(row[0])
        cursor.execute(
            "SELECT id, source, target_node_id, attributes_added, nodes_benefited, "
            "cost_eur, confidence, evidence, source_batch_id, corrects_ledger_id "
            f"FROM {LEDGER_TABLE} WHERE event_id = %s",
            (event_id,),
        )
        existing = cursor.fetchone()
    expected = (
        normalized_source,
        target_node_id,
        list(attributes_added),
        nodes_benefited,
        cost_eur,
        confidence,
        evidence or {},
        source_batch_id,
        corrects_ledger_id,
    )
    if existing is None:
        raise RuntimeError("ledger event conflict returned no existing row")
    actual = (
        str(existing[1]),
        str(existing[2]),
        list(existing[3]),
        int(existing[4]),
        Decimal(existing[5]),
        float(existing[6]),
        dict(existing[7]),
        None if existing[8] is None else str(existing[8]),
        None if existing[9] is None else int(existing[9]),
    )
    if actual != expected:
        raise ValueError(f"event_id {event_id} is already used by a different ledger event")
    return int(existing[0])


def fetch_entries_for_node(
    connection: Connection,
    target_node_id: str,
) -> tuple[LedgerEntry, ...]:
    """Return the full provenance history for one canonical node, oldest first."""

    if not is_valid_node_id(target_node_id):
        raise ValueError(f"target_node_id is not a canonical node id: {target_node_id!r}")

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, event_id, source, target_node_id, attributes_added, nodes_benefited, "
            "cost_eur, confidence, evidence, source_batch_id, corrects_ledger_id, "
            f"created_at FROM {LEDGER_TABLE} "
            "WHERE target_node_id = %s ORDER BY id",
            (target_node_id,),
        )
        rows = cursor.fetchall()

    return tuple(
        LedgerEntry(
            id=int(row[0]),
            event_id=row[1],
            source=str(row[2]),
            target_node_id=str(row[3]),
            attributes_added=tuple(row[4]),
            nodes_benefited=int(row[5]),
            cost_eur=Decimal(row[6]),
            confidence=float(row[7]),
            evidence=dict(row[8]),
            source_batch_id=None if row[9] is None else str(row[9]),
            corrects_ledger_id=None if row[10] is None else int(row[10]),
            created_at=row[11],
        )
        for row in rows
    )
