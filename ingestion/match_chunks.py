"""Deterministic technical-signature chunking for TS-to-TecDoc match review.

One chunk groups every source row in a build that shares one normalized
technical signature, so a single review decision can cover the whole group.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from psycopg import Connection

from ingestion.match_chunk_migrations import (
    MATCH_CHUNK_BUILDS_TABLE,
    MATCH_CHUNK_MEMBERS_TABLE,
    MATCH_CHUNKS_TABLE,
)
from ingestion.normalization_migrations import NORMALIZATION_RESULTS_TABLE

# v2 consults the matcher's own evaluation key when one is supplied, so a chunk
# cannot group rows the matcher would evaluate apart. v1 mirrored that key by
# hand and drifted once the matcher gained model recovery and manufacturer
# bridging: 143 chunks over 726 rows held several matcher keys, one of them a
# Golf, a Sharan and a Variant II all recovered from brand.
SIGNATURE_VERSION = "2"

DEFAULT_STATUS_FILTER = ("review_required",)

_SIGNATURE_TEXT_FIELDS = ("engine_code", "drive_type", "bodywork_form")
_SIGNATURE_INTEGER_FIELDS = ("production_year", "displacement_cc", "power_kw")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


EvaluationKeyResolver = Callable[[Mapping[str, Any]], object | None]


def compute_signature(
    normalized_payload: Mapping[str, Any],
    *,
    evaluation_key: EvaluationKeyResolver | None = None,
) -> dict[str, Any]:
    """Group rows that the matcher evaluates identically.

    With `evaluation_key` supplied the matcher's own key is used, so chunk
    grouping is identical to match grouping by construction. Without it the
    normalized fields below are used, which is a close approximation but blind
    to anything the matcher derives after normalization -- model recovery and
    manufacturer bridging most of all.
    """

    if evaluation_key is not None:
        resolved = evaluation_key(normalized_payload)
        if resolved is not None:
            return {
                "signature_version": SIGNATURE_VERSION,
                "evaluation_key": repr(resolved),
            }

    normalized = _mapping(normalized_payload.get("normalized"))
    candidates = _mapping(normalized_payload.get("candidates"))
    energy = normalized.get("energy_sources")
    fuels = (
        sorted({str(value) for value in energy})
        if isinstance(energy, list)
        else []
    )
    signature: dict[str, Any] = {
        "signature_version": SIGNATURE_VERSION,
        "manufacturer": _text(normalized.get("manufacturer"))
        or _text(candidates.get("manufacturer")),
        "model_family": _text(normalized.get("model_family"))
        or _text(candidates.get("model_family")),
        "energy_sources": fuels,
    }
    for field_name in _SIGNATURE_TEXT_FIELDS:
        signature[field_name] = _text(normalized.get(field_name))
    for field_name in _SIGNATURE_INTEGER_FIELDS:
        signature[field_name] = _integer(normalized.get(field_name))
    return signature


def signature_key(signature: Mapping[str, Any]) -> str:
    canonical = json.dumps(signature, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def chunk_id_for(build_id: UUID, key: str) -> UUID:
    """Deterministic chunk identity: retries always mint the same chunk."""

    return uuid5(build_id, key)


@dataclass(frozen=True)
class ChunkBuildSummary:
    build_id: str
    source_batch_prefix: str
    status: str
    row_count: int
    chunk_count: int


class ChunkBuildError(RuntimeError):
    """Raised when a chunk build cannot start or resume safely."""


def build_match_chunks(
    connection: Connection[Any],
    *,
    build_id: UUID,
    source_batch_prefix: str,
    statuses: tuple[str, ...] = DEFAULT_STATUS_FILTER,
    page_size: int = 25_000,
) -> ChunkBuildSummary:
    """Group the latest normalization results into signature chunks.

    Retry-safe: the same ``build_id`` resumes from the highest member already
    stored, and member/chunk inserts deduplicate on their unique keys. Counts
    and reason profiles are recomputed from the members table at finalize time.
    """

    if not source_batch_prefix.strip():
        raise ChunkBuildError("source_batch_prefix must not be empty")
    if not statuses or any(not status.strip() for status in statuses):
        raise ChunkBuildError("statuses must be non-empty")
    if page_size < 1:
        raise ChunkBuildError("page_size must be positive")

    existing = _claim_build(
        connection,
        build_id=build_id,
        source_batch_prefix=source_batch_prefix,
        statuses=statuses,
    )
    if existing is not None:
        return existing

    after_id = _resume_cursor(connection, build_id=build_id)
    while True:
        rows = _fetch_page(
            connection,
            source_batch_prefix=source_batch_prefix,
            statuses=statuses,
            after_source_record_id=after_id,
            limit=page_size,
        )
        if not rows:
            break
        _store_page(connection, build_id=build_id, rows=rows)
        connection.commit()
        after_id = int(rows[-1]["source_record_id"])
        if len(rows) < page_size:
            break

    summary = _finalize_build(connection, build_id=build_id)
    connection.commit()
    return summary


def _claim_build(
    connection: Connection[Any],
    *,
    build_id: UUID,
    source_batch_prefix: str,
    statuses: tuple[str, ...],
) -> ChunkBuildSummary | None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {MATCH_CHUNK_BUILDS_TABLE}
                (build_id, source_batch_id, signature_version, status_filter)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (build_id) DO NOTHING
            """,
            (build_id, source_batch_prefix, SIGNATURE_VERSION, list(statuses)),
        )
        cursor.execute(
            f"""
            SELECT source_batch_id, signature_version, status_filter, status,
                   row_count, chunk_count
            FROM {MATCH_CHUNK_BUILDS_TABLE} WHERE build_id = %s
            """,
            (build_id,),
        )
        row = cursor.fetchone()
    connection.commit()
    if row is None:
        raise ChunkBuildError("build manifest row could not be created")
    stored_prefix, stored_version, stored_filter, status, row_count, chunk_count = row
    if (
        str(stored_prefix) != source_batch_prefix
        or str(stored_version) != SIGNATURE_VERSION
        or tuple(stored_filter) != statuses
    ):
        raise ChunkBuildError(
            "build_id already exists with different pinned inputs; "
            "use a new build_id"
        )
    if str(status) == "completed":
        return ChunkBuildSummary(
            build_id=str(build_id),
            source_batch_prefix=source_batch_prefix,
            status="completed",
            row_count=int(row_count),
            chunk_count=int(chunk_count),
        )
    return None


def _resume_cursor(connection: Connection[Any], *, build_id: UUID) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT coalesce(max(members.source_record_id), 0)
            FROM {MATCH_CHUNK_MEMBERS_TABLE} AS members
            JOIN {MATCH_CHUNKS_TABLE} AS chunks USING (chunk_id)
            WHERE chunks.build_id = %s
            """,
            (build_id,),
        )
        row = cursor.fetchone()
    return int(row[0]) if row is not None else 0


def _fetch_page(
    connection: Connection[Any],
    *,
    source_batch_prefix: str,
    statuses: tuple[str, ...],
    after_source_record_id: int,
    limit: int,
) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT source_record_id, source_batch_id, status,
                   normalized_payload, review_reasons
            FROM (
                SELECT DISTINCT ON (source_record_id)
                    source_record_id, source_batch_id, status,
                    normalized_payload, review_reasons
                FROM {NORMALIZATION_RESULTS_TABLE}
                WHERE source_batch_id LIKE %s AND source_record_id > %s
                ORDER BY source_record_id, updated_at DESC, id DESC
            ) AS latest
            WHERE status = ANY(%s)
            ORDER BY source_record_id
            LIMIT %s
            """,
            (
                f"{source_batch_prefix}%",
                after_source_record_id,
                list(statuses),
                limit,
            ),
        )
        rows = cursor.fetchall()
    return [
        {
            "source_record_id": int(row[0]),
            "source_batch_id": str(row[1]),
            "status": str(row[2]),
            "normalized_payload": dict(row[3] or {}),
            "review_reasons": [str(reason) for reason in (row[4] or [])],
        }
        for row in rows
    ]


def _store_page(
    connection: Connection[Any],
    *,
    build_id: UUID,
    rows: list[dict[str, Any]],
) -> None:
    chunk_rows: dict[str, tuple[UUID, str]] = {}
    member_rows: list[tuple[UUID, int, str, str, list[str]]] = []
    for row in rows:
        signature = compute_signature(row["normalized_payload"])
        key = signature_key(signature)
        if key not in chunk_rows:
            chunk_rows[key] = (
                chunk_id_for(build_id, key),
                json.dumps(signature, sort_keys=True),
            )
        member_rows.append(
            (
                chunk_rows[key][0],
                row["source_record_id"],
                row["source_batch_id"],
                row["status"],
                row["review_reasons"],
            )
        )
    with connection.cursor() as cursor:
        cursor.executemany(
            f"""
            INSERT INTO {MATCH_CHUNKS_TABLE}
                (chunk_id, build_id, signature_key, signature)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (build_id, signature_key) DO NOTHING
            """,
            [
                (chunk_id, build_id, key, signature_json)
                for key, (chunk_id, signature_json) in chunk_rows.items()
            ],
        )
        cursor.executemany(
            f"""
            INSERT INTO {MATCH_CHUNK_MEMBERS_TABLE}
                (chunk_id, source_record_id, source_batch_id,
                 normalization_status, review_reasons)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (chunk_id, source_record_id) DO NOTHING
            """,
            member_rows,
        )


def _finalize_build(
    connection: Connection[Any], *, build_id: UUID
) -> ChunkBuildSummary:
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE {MATCH_CHUNKS_TABLE} AS chunks
            SET member_count = counted.member_count, updated_at = now()
            FROM (
                SELECT chunk_id, count(*) AS member_count
                FROM {MATCH_CHUNK_MEMBERS_TABLE}
                GROUP BY chunk_id
            ) AS counted
            WHERE chunks.chunk_id = counted.chunk_id AND chunks.build_id = %s
            """,
            (build_id,),
        )
        cursor.execute(
            f"""
            UPDATE {MATCH_CHUNKS_TABLE} AS chunks
            SET reason_profile = profiled.reason_profile, updated_at = now()
            FROM (
                SELECT chunk_id, jsonb_object_agg(reason, occurrences)
                    AS reason_profile
                FROM (
                    SELECT members.chunk_id, reason, count(*) AS occurrences
                    FROM {MATCH_CHUNK_MEMBERS_TABLE} AS members,
                         unnest(members.review_reasons) AS reason
                    GROUP BY members.chunk_id, reason
                ) AS reason_counts
                GROUP BY chunk_id
            ) AS profiled
            WHERE chunks.chunk_id = profiled.chunk_id AND chunks.build_id = %s
            """,
            (build_id,),
        )
        cursor.execute(
            f"""
            UPDATE {MATCH_CHUNK_BUILDS_TABLE} AS builds
            SET status = 'completed',
                finished_at = now(),
                row_count = totals.row_count,
                chunk_count = totals.chunk_count
            FROM (
                SELECT coalesce(sum(member_count), 0) AS row_count,
                       count(*) AS chunk_count
                FROM {MATCH_CHUNKS_TABLE}
                WHERE build_id = %s
            ) AS totals
            WHERE builds.build_id = %s
            RETURNING builds.source_batch_id, builds.row_count,
                      builds.chunk_count
            """,
            (build_id, build_id),
        )
        row = cursor.fetchone()
    if row is None:
        raise ChunkBuildError("build manifest disappeared during finalize")
    return ChunkBuildSummary(
        build_id=str(build_id),
        source_batch_prefix=str(row[0]),
        status="completed",
        row_count=int(row[1]),
        chunk_count=int(row[2]),
    )
