from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from psycopg import Connection
from psycopg.types.json import Jsonb

from api.app.features.review_queue.schemas import ReviewStatus
from ingestion.normalization_migrations import NORMALIZATION_RESULTS_TABLE
from ingestion.review_queue import ReviewQueueItem, transition_review_item
from ingestion.review_queue_migrations import REVIEW_QUEUE_TABLE, run_review_queue_migrations


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


class ReviewQueueRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def ensure_schema(self) -> None:
        with self._connection_factory() as connection:
            run_review_queue_migrations(connection)

    def fetch_items(
        self, *, status: ReviewStatus | None, batch_id: str | None, limit: int
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        values: list[Any] = []
        if status:
            conditions.append("q.status = %s")
            values.append(status)
        if batch_id:
            conditions.append("q.source_batch_id = %s")
            values.append(batch_id)
        predicate = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        parameters = (*values, limit)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT q.id, q.review_id, q.source_batch_id, q.source_record_id,
                       q.reason_code, q.reason_detail, q.target_entity_type,
                       q.candidate_matches,
                       CASE WHEN q.status = 'resolved' THEN result.confidence
                            ELSE q.confidence END,
                       q.status, q.resolution,
                       q.resolved_by, q.created_at, q.updated_at, q.resolved_at,
                       raw.raw_record, result.normalized_payload, result.review_reasons,
                       q.review_draft
                FROM {REVIEW_QUEUE_TABLE} q
                LEFT JOIN staging.transportstyrelsen_raw raw ON raw.id = q.source_record_id
                LEFT JOIN LATERAL (
                    SELECT normalized_payload, review_reasons, confidence
                    FROM {NORMALIZATION_RESULTS_TABLE} nr
                    WHERE nr.source_record_id = q.source_record_id
                      AND (q.source_batch_id IS NULL OR nr.source_batch_id = q.source_batch_id)
                    ORDER BY nr.updated_at DESC, nr.id DESC LIMIT 1
                ) result ON TRUE
                {predicate}
                ORDER BY CASE q.status WHEN 'in_review' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                         q.updated_at DESC, q.id DESC
                LIMIT %s
                """,
                parameters,
            )
            rows = cursor.fetchall()
        return [self._row(row) for row in rows]

    def fetch_counts(self, *, batch_id: str | None) -> dict[str, int]:
        counts = {status: 0 for status in ("pending", "in_review", "resolved", "rejected")}
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT status, count(*) FROM {REVIEW_QUEUE_TABLE} "
                + ("WHERE source_batch_id = %s " if batch_id else "")
                + "GROUP BY status",
                (batch_id,) if batch_id else (),
            )
            for status, count in cursor.fetchall():
                counts[str(status)] = int(count)
        return counts

    def fetch_rule_activity(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                WITH active AS (
                    SELECT overrides FROM core.translation_rule_versions
                    ORDER BY activated_at DESC, version DESC LIMIT 1
                ), activity AS (
                    SELECT d.rule_id,
                           'translation_rule'::text AS rule_kind, 'draft'::text AS action,
                           coalesce(active.overrides->d.rule_id->>'canonical_value',
                                    active.overrides->d.rule_id->>'display_value') AS previous_value,
                           coalesce(d.canonical_value, d.display_value, d.decision) AS new_value,
                           d.change_note, d.updated_at AS changed_at
                    FROM core.translation_rule_drafts d LEFT JOIN active ON TRUE
                    UNION ALL
                    SELECT d.entity_id, 'manufacturer_entity', 'draft',
                           coalesce(active.overrides->d.entity_id->>'canonical_name',
                                    active.overrides->d.entity_id->>'entity_role'),
                           coalesce(d.canonical_name, d.entity_role), d.change_note,
                           d.updated_at
                    FROM core.manufacturer_entity_drafts d LEFT JOIN active ON TRUE
                )
                SELECT activity.rule_id, activity.rule_kind, activity.action,
                       activity.previous_value, activity.new_value, activity.change_note,
                       activity.changed_at, NULL::text AS version,
                       related.resolved_by, related.id
                FROM activity
                LEFT JOIN LATERAL (
                    SELECT q.id, q.resolved_by
                    FROM core.review_queue q
                    WHERE q.resolution->>'rule_reference' = activity.rule_id
                    ORDER BY q.resolved_at DESC NULLS LAST, q.id DESC LIMIT 1
                ) related ON TRUE
                ORDER BY activity.changed_at DESC, activity.rule_id
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()
        return [
            {
                "rule_id": str(row[0]),
                "rule_kind": str(row[1]),
                "action": str(row[2]),
                "previous_value": row[3],
                "new_value": row[4],
                "change_note": str(row[5]),
                "changed_at": row[6],
                "version": row[7],
                "changed_by": row[8],
                "related_review_item_id": row[9],
            }
            for row in rows
        ]

    def transition(
        self,
        item_id: int,
        status: ReviewStatus,
        *,
        reviewer: str | None,
        resolution: dict[str, Any] | None,
        review_draft: dict[str, Any] | None = None,
    ) -> ReviewQueueItem:
        with self._connection_factory() as connection:
            item = transition_review_item(
                connection, item_id, status, resolved_by=reviewer, resolution=resolution
            )
            if review_draft is not None or status in {"resolved", "rejected"}:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"UPDATE {REVIEW_QUEUE_TABLE} SET review_draft = %s, "
                        "updated_at = now() WHERE id = %s",
                        (
                            Jsonb({} if status in {"resolved", "rejected"} else review_draft),
                            item_id,
                        ),
                    )
            if (
                status == "resolved"
                and resolution is not None
                and resolution.get("decision_scope") == "vehicle_only"
            ):
                self._persist_vehicle_review_result(connection, item, resolution)
            connection.commit()
            return item

    @staticmethod
    def _persist_vehicle_review_result(
        connection: Connection[Any],
        item: ReviewQueueItem,
        resolution: dict[str, Any],
    ) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT source_system, source_table, source_batch_id, mapping_version,
                       rule_version, pipeline_version, normalized_payload,
                       applied_rule_ids, review_reasons
                FROM {NORMALIZATION_RESULTS_TABLE}
                WHERE source_record_id = %s
                  AND (%s::text IS NULL OR source_batch_id = %s)
                ORDER BY updated_at DESC, id DESC LIMIT 1
                """,
                (item.source_record_id, item.source_batch_id, item.source_batch_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("normalization result for reviewed vehicle was not found")

            payload = dict(row[6])
            normalized = dict(payload.get("normalized") or {})
            candidates = dict(payload.get("candidates") or {})
            field = str(resolution["field"])
            value: Any = resolution["canonical_value"]
            if field == "energy_sources" and isinstance(value, str):
                value = [part.strip() for part in value.split(",") if part.strip()]
            normalized[field] = value
            candidates.pop(field, None)
            payload["normalized"] = normalized
            payload["candidates"] = candidates
            trace = list(payload.get("decision_trace") or [])
            trace.append(
                {
                    "field": field,
                    "before": None,
                    "after": value,
                    "signal": "human_review",
                    "rule_ids": [f"REVIEW-{item.id}"],
                }
            )
            payload["decision_trace"] = trace
            addressed_reasons = set((item.reason_detail or "").split(","))
            reasons = [reason for reason in list(row[8] or []) if reason not in addressed_reasons]
            if reasons:
                reviewed_status, confidence = "review_required", 0.55
            elif candidates or normalized.get("model_family_candidate"):
                reviewed_status, confidence = "provisional", 0.8
            else:
                reviewed_status, confidence = "resolved", 0.95
            payload["review_reasons"] = reasons
            payload["status"] = reviewed_status
            payload["confidence"] = confidence
            reviewed_rule_version = f"{row[4]}+review-{item.id}"
            payload["rule_version"] = reviewed_rule_version
            applied_rule_ids = [*list(row[7] or []), f"REVIEW-{item.id}"]
            normalization_id = uuid5(NAMESPACE_URL, f"northstar-review-result:{item.review_id}")
            cursor.execute(
                f"""
                INSERT INTO {NORMALIZATION_RESULTS_TABLE} (
                    normalization_id, source_system, source_batch_id, source_table,
                    source_record_id, mapping_version, rule_version, pipeline_version,
                    status, normalized_payload, applied_rule_ids, review_reasons, confidence
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (normalization_id) DO NOTHING
                """,
                (
                    normalization_id,
                    row[0],
                    row[2],
                    row[1],
                    item.source_record_id,
                    row[3],
                    reviewed_rule_version,
                    row[5],
                    reviewed_status,
                    Jsonb(payload),
                    applied_rule_ids,
                    reasons,
                    confidence,
                ),
            )

    @staticmethod
    def _row(row: tuple[Any, ...]) -> dict[str, Any]:
        payload = dict(row[16] or {})
        return {
            "id": int(row[0]),
            "review_id": str(row[1]),
            "source_batch_id": row[2],
            "source_record_id": int(row[3]),
            "reason_code": str(row[4]),
            "reason_detail": row[5],
            "target_entity_type": row[6],
            "candidate_matches": list(row[7] or []),
            "confidence": row[8],
            "status": str(row[9]),
            "resolution": dict(row[10] or {}),
            "resolved_by": row[11],
            "created_at": row[12],
            "updated_at": row[13],
            "resolved_at": row[14],
            "source_evidence": dict(row[15] or {}),
            "normalized": dict(payload.get("normalized") or {}),
            "candidates": dict(payload.get("candidates") or {}),
            "review_reasons": list(row[17] or []),
            "review_draft": dict(row[18] or {}),
        }
