from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol

from psycopg import Connection

from ingestion.match_run_migrations import (
    MATCH_RUN_BLOCKER_COUNTS_TABLE,
    MATCH_RUNS_TABLE,
    run_match_run_migrations,
)
from ingestion.review_queue import transition_review_item
from ingestion.review_queue_migrations import REVIEW_QUEUE_TABLE, run_review_queue_migrations
from ingestion.tecdoc.blocker_review import CATEGORY_BY_CODE


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


class MatchReviewRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def ensure_schema(self) -> None:
        with self._connection_factory() as connection:
            run_match_run_migrations(connection)
            run_review_queue_migrations(connection)

    def fetch_run(self, operation_id: str | None) -> dict[str, Any] | None:
        predicate = "WHERE operation_id = %s" if operation_id else ""
        parameters = (operation_id,) if operation_id else ()
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT operation_id, status, processed, expected_source_rows, "
                f"last_batch_number, candidate_catalog_version, policy_version, updated_at, "
                f"resolved, provisional, review_required, unmatched, hard_conflict, "
                f"normalization_review, policy_excluded, failed FROM {MATCH_RUNS_TABLE} "
                f"{predicate} ORDER BY updated_at DESC LIMIT 1",
                parameters,
            )
            row = cursor.fetchone()
        if row is None:
            return None
        names = (
            "operation_id", "status", "processed", "expected_source_rows",
            "last_batch_number", "candidate_catalog_version", "policy_version", "updated_at",
            "resolved", "provisional", "review_required", "unmatched", "hard_conflict",
            "normalization_review", "policy_excluded", "failed",
        )
        return dict(zip(names, row, strict=True))

    def fetch_blocker_counts(self, operation_id: str) -> dict[str, int]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT blocker_category, occurrence_count FROM {MATCH_RUN_BLOCKER_COUNTS_TABLE} "
                "WHERE operation_id=%s",
                (operation_id,),
            )
            return {str(code): int(count) for code, count in cursor.fetchall()}

    def fetch_review_counts(self, operation_id: str) -> dict[str, dict[str, int]]:
        target = f"ts_tecdoc_match:{operation_id}"
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT split_part(reason_code, ':', 2), status, count(*) "
                f"FROM {REVIEW_QUEUE_TABLE} WHERE target_entity_type=%s "
                "GROUP BY 1, status",
                (target,),
            )
            result: dict[str, dict[str, int]] = {}
            for category, status, count in cursor.fetchall():
                result.setdefault(str(category), {})[str(status)] = int(count)
            return result

    def fetch_items(
        self,
        *,
        operation_id: str,
        category: str | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        conditions = ["q.target_entity_type=%s"]
        parameters: list[object] = [f"ts_tecdoc_match:{operation_id}"]
        if category:
            conditions.append("q.reason_code=%s")
            parameters.append(f"ts_tecdoc_match_blocker:{category}")
        if status:
            conditions.append("q.status=%s")
            parameters.append(status)
        predicate = " AND ".join(conditions)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT count(*) FROM {REVIEW_QUEUE_TABLE} q WHERE {predicate}",
                parameters,
            )
            count_row = cursor.fetchone()
            total = int(count_row[0]) if count_row is not None else 0
            cursor.execute(
                f"SELECT q.id, q.reason_code, q.source_record_id, q.source_batch_id, "
                "raw.raw_record, q.reason_detail, q.candidate_matches, q.confidence, "
                "q.status, q.resolution, q.resolved_by, q.updated_at "
                f"FROM {REVIEW_QUEUE_TABLE} q "
                "JOIN staging.transportstyrelsen_raw raw ON raw.id=q.source_record_id "
                f"WHERE {predicate} ORDER BY CASE q.status WHEN 'pending' THEN 0 "
                "WHEN 'in_review' THEN 1 ELSE 2 END, q.id LIMIT %s OFFSET %s",
                (*parameters, limit, offset),
            )
            rows = cursor.fetchall()
        return total, [self._item_row(row, operation_id) for row in rows]

    def decide(
        self,
        *,
        operation_id: str,
        item_id: int,
        action: str,
        reviewer: str,
        reason: str,
        selected_candidate_reference: str | None,
        scope: str,
    ) -> dict[str, Any]:
        target = f"ts_tecdoc_match:{operation_id}"
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT reason_code, candidate_matches FROM {REVIEW_QUEUE_TABLE} "
                "WHERE id=%s AND target_entity_type=%s FOR UPDATE",
                (item_id, target),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(f"match review item {item_id} does not exist")
            category = str(row[0]).removeprefix("ts_tecdoc_match_blocker:")
            candidates = list(row[1] or [])
            candidate_references = [str(item["candidate_reference"]) for item in candidates]
            if action == "accept_top_candidate":
                if not candidate_references:
                    raise ValueError("this blocker has no candidate to accept")
                selected_candidate_reference = candidate_references[0]
            elif action == "select_candidate":
                if selected_candidate_reference not in candidate_references:
                    raise ValueError("selected KType is not in the reviewed candidate set")
            else:
                selected_candidate_reference = None
            resolution = {
                "decision_type": "ts_tecdoc_match_review_v1",
                "action": action,
                "blocker_category": category,
                "selected_candidate_reference": selected_candidate_reference,
                "decision_scope": scope,
                "reason": reason.strip(),
                "operation_id": operation_id,
                "graph_write": False,
                "alias_attachment": False,
            }
            transition_review_item(
                connection,
                item_id,
                "resolved",
                resolved_by=reviewer.strip(),
                resolution=resolution,
            )
            connection.commit()
        _, items = self.fetch_items(
            operation_id=operation_id,
            category=category,
            status="resolved",
            limit=1000,
            offset=0,
        )
        item = next((candidate for candidate in items if candidate["id"] == item_id), None)
        if item is None:
            raise RuntimeError("saved match review decision could not be read back")
        return item

    @staticmethod
    def _item_row(row: tuple[Any, ...], operation_id: str) -> dict[str, Any]:
        category = str(row[1]).removeprefix("ts_tecdoc_match_blocker:")
        metadata = CATEGORY_BY_CODE.get(category, CATEGORY_BY_CODE["other_match_blocker"])
        return {
            "id": int(row[0]),
            "operation_id": operation_id,
            "category": category,
            "category_title": metadata.title,
            "category_guidance": metadata.guidance,
            "source_record_id": int(row[2]),
            "source_batch_id": None if row[3] is None else str(row[3]),
            "source_evidence": dict(row[4] or {}),
            "reason_codes": [value for value in str(row[5] or "").split(",") if value],
            "candidate_matches": list(row[6] or []),
            "confidence": None if row[7] is None else float(row[7]),
            "status": str(row[8]),
            "resolution": dict(row[9] or {}),
            "resolved_by": None if row[10] is None else str(row[10]),
            "updated_at": row[11],
        }
