"""PostgreSQL access for staged TS records and sanitized normalization results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.normalization_migrations import NORMALIZATION_RESULTS_TABLE
from ingestion.normalization_rules import NormalizationOutcome

SOURCE_TABLE = "staging.transportstyrelsen_raw"
NORMALIZATION_NAMESPACE = UUID("a706945c-ab97-46fd-89cb-2bac944c7912")
REVIEW_NAMESPACE = UUID("fc8c54c6-df15-4d42-8f8a-b90e03a4d22b")


@dataclass(frozen=True)
class StagedRecord:
    id: int
    source_batch_id: str
    raw_record: dict[str, Any]


@dataclass(frozen=True)
class NormalizationSummary:
    batch_id: str
    processed: int
    resolved: int
    provisional: int
    review_required: int
    failed: int
    already_completed: bool = False

    @property
    def succeeded(self) -> int:
        return self.processed - self.failed


def count_staged_records(connection: Connection, batch_id: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT count(*) FROM {SOURCE_TABLE} WHERE source_batch_id = %s",
            (batch_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("staging count returned no row")
    return int(row[0])


def fetch_staged_records(
    connection: Connection,
    *,
    batch_id: str,
    after_id: int = 0,
    limit: int = 500,
) -> tuple[StagedRecord, ...]:
    if not 1 <= limit <= 5000:
        raise ValueError("limit must be between 1 and 5000")
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT id, source_batch_id, raw_record FROM {SOURCE_TABLE} "
            "WHERE source_batch_id = %s AND id > %s ORDER BY id LIMIT %s",
            (batch_id, after_id, limit),
        )
        rows = cursor.fetchall()
    return tuple(
        StagedRecord(id=int(row[0]), source_batch_id=str(row[1]), raw_record=dict(row[2]))
        for row in rows
    )


def normalization_uuid(
    source_record_id: int,
    mapping_version: str,
    rule_version: str,
    pipeline_version: str,
) -> UUID:
    return uuid5(
        NORMALIZATION_NAMESPACE,
        f"{SOURCE_TABLE}:{source_record_id}:{mapping_version}:{rule_version}:"
        f"{pipeline_version}",
    )


def review_uuid(
    source_record_id: int,
    mapping_version: str,
    rule_version: str,
    pipeline_version: str,
) -> UUID:
    return uuid5(
        REVIEW_NAMESPACE,
        f"{SOURCE_TABLE}:{source_record_id}:{mapping_version}:{rule_version}:"
        f"{pipeline_version}",
    )


def store_normalization_result(
    connection: Connection,
    *,
    record: StagedRecord,
    mapping_version: str,
    rule_version: str,
    outcome: NormalizationOutcome,
) -> int:
    """Insert once, accepting only an identical deterministic retry."""

    result_id = normalization_uuid(
        record.id,
        mapping_version,
        rule_version,
        outcome.pipeline_version,
    )
    payload = outcome.to_payload()
    expected = (
        "Transportstyrelsen",
        record.source_batch_id,
        SOURCE_TABLE,
        record.id,
        mapping_version,
        rule_version,
        outcome.pipeline_version,
        outcome.status,
        payload,
        list(outcome.applied_rule_ids),
        list(outcome.review_reasons),
        outcome.confidence,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {NORMALIZATION_RESULTS_TABLE} "
            "(normalization_id, source_system, source_batch_id, source_table, "
            "source_record_id, mapping_version, rule_version, pipeline_version, status, "
            "normalized_payload, applied_rule_ids, review_reasons, confidence) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (normalization_id) DO NOTHING RETURNING id",
            (result_id, *expected[:8], Jsonb(payload), *expected[9:]),
        )
        inserted = cursor.fetchone()
        if inserted is not None:
            return int(inserted[0])
        cursor.execute(
            f"SELECT id, source_system, source_batch_id, source_table, source_record_id, "
            "mapping_version, rule_version, pipeline_version, status, normalized_payload, "
            "applied_rule_ids, "
            f"review_reasons, confidence FROM {NORMALIZATION_RESULTS_TABLE} "
            "WHERE normalization_id = %s",
            (result_id,),
        )
        existing = cursor.fetchone()
    if existing is None:
        raise RuntimeError("normalization conflict returned no existing row")
    actual = (
        str(existing[1]),
        str(existing[2]),
        str(existing[3]),
        int(existing[4]),
        str(existing[5]),
        str(existing[6]),
        str(existing[7]),
        str(existing[8]),
        dict(existing[9]),
        list(existing[10]),
        list(existing[11]),
        float(existing[12]),
    )
    if actual != expected:
        raise ValueError(f"normalization_id {result_id} already has a different payload")
    return int(existing[0])


def summarize_batch(connection: Connection, batch_id: str) -> NormalizationSummary:
    counts = {"resolved": 0, "provisional": 0, "review_required": 0, "failed": 0}
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT status, count(*) FROM {NORMALIZATION_RESULTS_TABLE} "
            "WHERE source_batch_id = %s GROUP BY status",
            (batch_id,),
        )
        for status, count in cursor.fetchall():
            counts[str(status)] = int(count)
    processed = sum(counts.values())
    return NormalizationSummary(
        batch_id=batch_id,
        processed=processed,
        resolved=counts["resolved"],
        provisional=counts["provisional"],
        review_required=counts["review_required"],
        failed=counts["failed"],
    )


def fetch_batch_results(
    connection: Connection,
    batch_id: str,
) -> tuple[dict[str, Any], ...]:
    """Return redaction-safe result rows for stakeholder reporting."""

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT source_record_id, status, normalized_payload, applied_rule_ids, "
            f"review_reasons, confidence FROM {NORMALIZATION_RESULTS_TABLE} "
            "WHERE source_batch_id = %s ORDER BY source_record_id",
            (batch_id,),
        )
        rows = cursor.fetchall()
    return tuple(
        {
            "source_record_id": int(row[0]),
            "status": str(row[1]),
            "normalized_payload": dict(row[2]),
            "applied_rule_ids": list(row[3]),
            "review_reasons": list(row[4]),
            "confidence": float(row[5]),
        }
        for row in rows
    )
