"""Validated review-queue write, worklist, and lifecycle operations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.review_queue_migrations import REVIEW_QUEUE_TABLE, REVIEW_STATUSES

ReviewStatus = Literal["pending", "in_review", "resolved", "rejected"]

_SOURCE_TABLE_PATTERN = re.compile(
    r"^staging\.(?:transportstyrelsen_raw|tecdoc_[a-z][a-z0-9_]*)$"
)
_TERMINAL_STATUSES = frozenset({"resolved", "rejected"})
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"in_review", "resolved", "rejected"}),
    "in_review": frozenset({"pending", "resolved", "rejected"}),
    "resolved": frozenset(),
    "rejected": frozenset(),
}


@dataclass(frozen=True)
class CandidateMatch:
    """One possible canonical match shown to a reviewer."""

    candidate_reference: str
    candidate_type: str
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        reference = self.candidate_reference.strip()
        candidate_type = self.candidate_type.strip()
        if not reference:
            raise ValueError("candidate_reference must not be empty")
        if not candidate_type:
            raise ValueError("candidate_type must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("candidate confidence must be between 0.0 and 1.0")
        return {
            "candidate_reference": reference,
            "candidate_type": candidate_type,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class ReviewQueueItem:
    """One normalization decision awaiting or recording human review."""

    id: int
    review_id: UUID
    source_system: str
    source_batch_id: str | None
    source_table: str
    source_record_id: int
    reason_code: str
    reason_detail: str | None
    target_entity_type: str | None
    candidate_matches: tuple[dict[str, Any], ...]
    confidence: float | None
    status: ReviewStatus
    resolution: dict[str, Any]
    resolved_by: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


def enqueue_review_item(
    connection: Connection,
    *,
    review_id: UUID,
    source_system: str,
    source_table: str,
    source_record_id: int,
    reason_code: str,
    candidate_matches: tuple[CandidateMatch, ...] = (),
    confidence: float | None = None,
    source_batch_id: str | None = None,
    reason_detail: str | None = None,
    target_entity_type: str | None = None,
) -> int:
    """Create one pending review item, idempotently by caller-issued UUID.

    The caller owns the transaction. Retrying the same review id and payload
    returns the original row; reusing it for different content is rejected.
    """

    normalized_source = source_system.strip()
    normalized_reason = reason_code.strip()
    normalized_table = source_table.strip()
    if not normalized_source:
        raise ValueError("source_system must not be empty")
    if not _SOURCE_TABLE_PATTERN.fullmatch(normalized_table):
        raise ValueError("source_table must reference an approved staging table")
    if source_record_id < 1:
        raise ValueError("source_record_id must be at least 1")
    if not normalized_reason:
        raise ValueError("reason_code must not be empty")
    if confidence is not None and not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be between 0.0 and 1.0")

    candidates = [candidate.to_payload() for candidate in candidate_matches]
    normalized_batch = _optional_text(source_batch_id)
    normalized_detail = _optional_text(reason_detail)
    normalized_target = _optional_text(target_entity_type)

    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {REVIEW_QUEUE_TABLE} "
            "(review_id, source_system, source_batch_id, source_table, "
            "source_record_id, reason_code, reason_detail, target_entity_type, "
            "candidate_matches, confidence) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (review_id) DO NOTHING RETURNING id",
            (
                review_id,
                normalized_source,
                normalized_batch,
                normalized_table,
                source_record_id,
                normalized_reason,
                normalized_detail,
                normalized_target,
                Jsonb(candidates),
                confidence,
            ),
        )
        inserted = cursor.fetchone()
        if inserted is not None:
            return int(inserted[0])
        cursor.execute(
            "SELECT id, source_system, source_batch_id, source_table, "
            "source_record_id, reason_code, reason_detail, target_entity_type, "
            f"candidate_matches, confidence FROM {REVIEW_QUEUE_TABLE} "
            "WHERE review_id = %s",
            (review_id,),
        )
        existing = cursor.fetchone()

    if existing is None:
        raise RuntimeError("review item conflict returned no existing row")
    expected = (
        normalized_source,
        normalized_batch,
        normalized_table,
        source_record_id,
        normalized_reason,
        normalized_detail,
        normalized_target,
        candidates,
        confidence,
    )
    actual = (
        str(existing[1]),
        None if existing[2] is None else str(existing[2]),
        str(existing[3]),
        int(existing[4]),
        str(existing[5]),
        None if existing[6] is None else str(existing[6]),
        None if existing[7] is None else str(existing[7]),
        list(existing[8]),
        None if existing[9] is None else float(existing[9]),
    )
    if actual != expected:
        raise ValueError(f"review_id {review_id} is already used by a different item")
    return int(existing[0])


def fetch_review_items_by_status(
    connection: Connection,
    status: ReviewStatus,
    *,
    limit: int = 100,
    source_system: str | None = None,
) -> tuple[ReviewQueueItem, ...]:
    """Return one status worklist in stable oldest-first order."""

    _validate_status(status)
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    normalized_source = _optional_text(source_system)
    parameters: tuple[Any, ...]
    predicate = "WHERE status = %s"
    parameters = (status, limit)
    if normalized_source is not None:
        predicate += " AND source_system = %s"
        parameters = (status, normalized_source, limit)
    with connection.cursor() as cursor:
        cursor.execute(
            _SELECT_ITEM_SQL
            + f" FROM {REVIEW_QUEUE_TABLE} "
            + predicate
            + " ORDER BY created_at, id LIMIT %s",
            parameters,
        )
        rows = cursor.fetchall()
    return tuple(_row_to_item(row) for row in rows)


def transition_review_item(
    connection: Connection,
    item_id: int,
    status: ReviewStatus,
    *,
    resolved_by: str | None = None,
    resolution: dict[str, Any] | None = None,
) -> ReviewQueueItem:
    """Apply a controlled queue lifecycle transition.

    Terminal decisions require an actor and structured resolution. Resolved
    and rejected rows are immutable through this sanctioned path; a changed
    normalization rule should create a new review event during reprocessing.
    """

    _validate_status(status)
    normalized_actor = _optional_text(resolved_by)
    resolution_payload = resolution or {}
    if status in _TERMINAL_STATUSES:
        if normalized_actor is None:
            raise ValueError("resolved_by is required for a terminal status")
        if not resolution_payload:
            raise ValueError("resolution is required for a terminal status")
    elif normalized_actor is not None or resolution_payload:
        raise ValueError("non-terminal statuses cannot carry resolution data")

    with connection.cursor() as cursor:
        cursor.execute(
            _SELECT_ITEM_SQL
            + f" FROM {REVIEW_QUEUE_TABLE} WHERE id = %s FOR UPDATE",
            (item_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise KeyError(f"review item {item_id} does not exist")
        current = _row_to_item(row)
        if current.status == status:
            if status not in _TERMINAL_STATUSES:
                return current
            if current.resolved_by == normalized_actor and current.resolution == resolution_payload:
                return current
            raise ValueError("terminal review item already has a different resolution")
        if status not in _ALLOWED_TRANSITIONS[current.status]:
            raise ValueError(f"cannot transition review item from {current.status} to {status}")

        cursor.execute(
            f"UPDATE {REVIEW_QUEUE_TABLE} SET "
            "status = %s, resolution = %s, resolved_by = %s, "
            "resolved_at = CASE WHEN %s IN ('resolved', 'rejected') THEN now() ELSE NULL END, "
            "updated_at = now() WHERE id = %s "
            "RETURNING id, review_id, source_system, source_batch_id, source_table, "
            "source_record_id, reason_code, reason_detail, target_entity_type, "
            "candidate_matches, confidence, status, resolution, resolved_by, "
            "created_at, updated_at, resolved_at",
            (
                status,
                Jsonb(resolution_payload),
                normalized_actor,
                status,
                item_id,
            ),
        )
        updated = cursor.fetchone()
    if updated is None:
        raise RuntimeError(f"review item {item_id} disappeared during transition")
    return _row_to_item(updated)


_SELECT_ITEM_SQL = (
    "SELECT id, review_id, source_system, source_batch_id, source_table, "
    "source_record_id, reason_code, reason_detail, target_entity_type, "
    "candidate_matches, confidence, status, resolution, resolved_by, "
    "created_at, updated_at, resolved_at"
)


def _row_to_item(row: tuple[Any, ...]) -> ReviewQueueItem:
    return ReviewQueueItem(
        id=int(row[0]),
        review_id=row[1],
        source_system=str(row[2]),
        source_batch_id=None if row[3] is None else str(row[3]),
        source_table=str(row[4]),
        source_record_id=int(row[5]),
        reason_code=str(row[6]),
        reason_detail=None if row[7] is None else str(row[7]),
        target_entity_type=None if row[8] is None else str(row[8]),
        candidate_matches=tuple(dict(candidate) for candidate in row[9]),
        confidence=None if row[10] is None else float(row[10]),
        status=row[11],
        resolution=dict(row[12]),
        resolved_by=None if row[13] is None else str(row[13]),
        created_at=row[14],
        updated_at=row[15],
        resolved_at=row[16],
    )


def _validate_status(status: str) -> None:
    if status not in REVIEW_STATUSES:
        raise ValueError(f"status must be one of {REVIEW_STATUSES!r}")


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
