from __future__ import annotations

from contextlib import AbstractContextManager
from threading import Lock
from typing import Any, Protocol
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.match_run_migrations import (
    MATCH_REVIEW_RULE_DECISIONS_TABLE,
    MATCH_RUN_PATTERN_INVENTORY_TABLE,
    MATCH_RUN_PATTERN_MEMBERS_TABLE,
    MATCH_RUN_BLOCKER_COUNTS_TABLE,
    MATCH_RUNS_TABLE,
    run_match_run_migrations,
)
from ingestion.review_queue import transition_review_item
from ingestion.review_queue_migrations import REVIEW_QUEUE_TABLE, run_review_queue_migrations
from ingestion.tecdoc.blocker_review import CATEGORY_BY_CODE
from ingestion.tecdoc.reference_data import canonical_bodywork_by_kt086
from api.app.features.match_review.patterns import explain_review_item

_SCHEMA_READY = False
_SCHEMA_LOCK = Lock()


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


class MatchReviewRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def ensure_schema(self) -> None:
        global _SCHEMA_READY
        if _SCHEMA_READY:
            return
        with _SCHEMA_LOCK:
            if _SCHEMA_READY:
                return
            with self._connection_factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT to_regclass('core.match_runs'), "
                        "to_regclass('core.match_review_rule_decisions'), "
                        "to_regclass('core.match_run_pattern_inventory'), "
                        "to_regclass('core.match_run_pattern_batches'), "
                        "to_regclass('core.match_run_pattern_members'), "
                        "to_regclass('core.review_queue')"
                    )
                    existing = cursor.fetchone()
                if existing is not None and all(existing):
                    _SCHEMA_READY = True
                    return
                run_match_run_migrations(connection)
                run_review_queue_migrations(connection)
            _SCHEMA_READY = True

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

    def fetch_pattern_candidate_contexts(
        self, *, operation_id: str, candidate_references: tuple[str, ...]
    ) -> dict[str, dict[str, Any]]:
        if not candidate_references:
            return {}
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT candidate_catalog_version FROM {MATCH_RUNS_TABLE} "
                "WHERE operation_id=%s",
                (operation_id,),
            )
            run = cursor.fetchone()
            if run is None:
                return {}
            catalog_version = str(run[0]).removeprefix("postgres:")
            cursor.execute(
                """
                SELECT ka.attributes->>'alias_text' AS candidate_reference,
                       manufacturer.attributes->>'canonical_name' AS manufacturer,
                       family.attributes->>'canonical_name' AS model,
                       variant.attributes->>'drive_type' AS drive_type,
                       coalesce(variant.attributes->>'vehicle_fuel_type',
                                variant.attributes->>'fuel_type') AS fuel_type,
                       coalesce(bodywork.attributes->>'canonical_name',
                                variant.attributes->>'tecdoc_bodywork_official_label') AS bodywork,
                       variant.attributes->>'tecdoc_body_type_code' AS body_type_code
                FROM core.tecdoc_canonical_candidates variant
                JOIN core.tecdoc_canonical_candidates ka
                  ON ka.batch_id=variant.batch_id AND ka.entity_type='alias'
                 AND ka.attributes->>'alias_type'='k_type'
                 AND ka.attributes->>'target_source_key'=variant.source_key
                JOIN core.tecdoc_canonical_candidates family
                  ON family.batch_id=variant.batch_id AND family.entity_type='model_family'
                 AND family.source_key=variant.attributes->>'model_family_source_key'
                JOIN core.tecdoc_canonical_candidates manufacturer
                  ON manufacturer.batch_id=variant.batch_id AND manufacturer.entity_type='manufacturer'
                 AND manufacturer.source_key=variant.attributes->>'manufacturer_source_key'
                LEFT JOIN core.tecdoc_canonical_candidates bodywork
                  ON bodywork.batch_id=variant.batch_id AND bodywork.entity_type='bodywork'
                 AND bodywork.source_key=variant.attributes->>'bodywork_source_key'
                WHERE variant.batch_id=%s AND variant.entity_type='vehicle_variant'
                  AND ka.attributes->>'alias_text' = ANY(%s)
                """,
                (catalog_version, list(candidate_references)),
            )
            bodywork_by_code = canonical_bodywork_by_kt086()
            contexts: dict[str, dict[str, Any]] = {}
            for row in cursor.fetchall():
                bodywork = row[5] or bodywork_by_code.get(str(row[6] or "").zfill(3))
                contexts[str(row[0])] = {
                    "candidate_reference": str(row[0]),
                    "manufacturer": row[1],
                    "model": row[2],
                    "drive_type": row[3],
                    "fuel_type": row[4],
                    "bodyworks": [str(bodywork)] if bodywork else [],
                }
            return contexts

    def fetch_pattern_decisions(self, operation_id: str) -> dict[str, dict[str, Any]]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT DISTINCT ON (pattern_key) pattern_key, decision_id, action,
                       selected_values, reviewer, reason, created_at
                FROM {MATCH_REVIEW_RULE_DECISIONS_TABLE}
                WHERE operation_id=%s
                ORDER BY pattern_key, created_at DESC, decision_id DESC
                """,
                (operation_id,),
            )
            return {
                str(row[0]): {
                    "decision_id": str(row[1]),
                    "action": str(row[2]),
                    "selected_values": list(row[3] or []),
                    "reviewer": str(row[4]),
                    "reason": str(row[5]),
                    "created_at": row[6],
                }
                for row in cursor.fetchall()
            }

    def fetch_pattern_inventory(self, operation_id: str) -> list[dict[str, Any]]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT pattern_key, blocker_category, pattern_evidence, occurrence_count, "
                f"examples FROM {MATCH_RUN_PATTERN_INVENTORY_TABLE} "
                "WHERE operation_id=%s ORDER BY occurrence_count DESC, pattern_key",
                (operation_id,),
            )
            return [
                {
                    "pattern_key": str(row[0]),
                    "blocker_category": str(row[1]),
                    "pattern_evidence": dict(row[2] or {}),
                    "occurrence_count": int(row[3]),
                    "examples": list(row[4] or []),
                }
                for row in cursor.fetchall()
            ]

    def fetch_pattern_members(
        self, *, operation_id: str, pattern_key: str, limit: int, offset: int
    ) -> tuple[int, list[dict[str, Any]]]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT count(*) FROM {MATCH_RUN_PATTERN_MEMBERS_TABLE} "
                "WHERE operation_id=%s AND pattern_key=%s",
                (operation_id, pattern_key),
            )
            total = int((cursor.fetchone() or (0,))[0])
            cursor.execute(
                f"""
                SELECT member.source_record_id, raw.raw_record
                FROM {MATCH_RUN_PATTERN_MEMBERS_TABLE} member
                JOIN staging.transportstyrelsen_raw raw
                  ON raw.id=member.source_record_id
                WHERE member.operation_id=%s AND member.pattern_key=%s
                ORDER BY member.source_record_id
                LIMIT %s OFFSET %s
                """,
                (operation_id, pattern_key, limit, offset),
            )
            return total, [
                {
                    "pattern_key": pattern_key,
                    "source_record_id": int(row[0]),
                    "source_evidence": dict(row[1] or {}),
                }
                for row in cursor.fetchall()
            ]

    def fetch_pattern_technical_evidence(
        self, *, operation_id: str, pattern_key: str
    ) -> dict[str, Any]:
        """Return plate-free TS/TecDoc values for a hard-conflict pattern."""

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT pattern_evidence FROM {MATCH_RUN_PATTERN_INVENTORY_TABLE} "
                "WHERE operation_id=%s AND pattern_key=%s",
                (operation_id, pattern_key),
            )
            inventory_row = cursor.fetchone()
            if inventory_row is None:
                raise KeyError(f"pattern {pattern_key} does not exist")
            evidence = dict(inventory_row[0] or {})
            source_values = dict(evidence.get("source_values") or {})
            candidate_values = dict(evidence.get("candidate_values") or {})
            conflicts = [str(value) for value in source_values.get("conflicting_fields") or []]
            references = [
                str(value) for value in candidate_values.get("candidate_references") or []
            ]
            cursor.execute(
                f"""
                SELECT raw.raw_record
                FROM {MATCH_RUN_PATTERN_MEMBERS_TABLE} member
                JOIN staging.transportstyrelsen_raw raw ON raw.id=member.source_record_id
                WHERE member.operation_id=%s AND member.pattern_key=%s
                ORDER BY member.source_record_id
                LIMIT 4
                """,
                (operation_id, pattern_key),
            )
            ts_examples = [
                {
                    "manufacturer": str(raw.get("brand") or raw.get("manufacturer") or "Unknown"),
                    "model": str(raw.get("model") or "Model unavailable"),
                    "values": {
                        key: raw[key]
                        for key in (
                            "body_code", "is_4wd", "fuel1", "fuel2", "fuel3", "ev_config",
                            "engine_code", "ccm", "displacement_cc", "kw", "power_kw",
                            "model_year", "production_year", "eeg_type_approval", "variant", "version",
                        )
                        if raw.get(key) is not None and str(raw.get(key)).strip()
                    },
                }
                for (raw_record,) in cursor.fetchall()
                for raw in (dict(raw_record or {}),)
            ]
            cursor.execute(
                f"SELECT candidate_catalog_version FROM {MATCH_RUNS_TABLE} "
                "WHERE operation_id=%s",
                (operation_id,),
            )
            run = cursor.fetchone()
            if run is None or not references:
                return {
                    "pattern_key": pattern_key,
                    "conflicting_fields": conflicts,
                    "ts_examples": ts_examples,
                    "tecdoc_candidates": [],
                    "comparisons": {},
                }
            catalog_version = str(run[0]).removeprefix("postgres:")
            cursor.execute(
                """
                SELECT ka.attributes->>'alias_text' AS candidate_reference,
                       family.attributes->>'canonical_name' AS model,
                       variant.attributes->>'year_from' AS year_from,
                       variant.attributes->>'year_to' AS year_to,
                       coalesce(variant.attributes->>'vehicle_fuel_type',
                                variant.attributes->>'fuel_type') AS fuel_type,
                       engine.attributes->>'engine_code' AS engine_code,
                       variant.attributes->>'displacement_cc' AS displacement_cc,
                       variant.attributes->>'power_kw' AS power_kw,
                       variant.attributes->>'drive_type' AS drive_type,
                       coalesce(bodywork.attributes->>'canonical_name',
                                variant.attributes->>'tecdoc_bodywork_official_label') AS bodywork,
                       variant.attributes->>'tecdoc_body_type_code' AS body_type_code
                FROM core.tecdoc_canonical_candidates variant
                JOIN core.tecdoc_canonical_candidates ka
                  ON ka.batch_id=variant.batch_id AND ka.entity_type='alias'
                 AND ka.attributes->>'alias_type'='k_type'
                 AND ka.attributes->>'target_source_key'=variant.source_key
                JOIN core.tecdoc_canonical_candidates family
                  ON family.batch_id=variant.batch_id AND family.entity_type='model_family'
                 AND family.source_key=variant.attributes->>'model_family_source_key'
                LEFT JOIN core.tecdoc_canonical_candidates engine
                  ON engine.batch_id=variant.batch_id AND engine.entity_type='engine'
                 AND engine.source_key=variant.attributes->>'engine_source_key'
                LEFT JOIN core.tecdoc_canonical_candidates bodywork
                  ON bodywork.batch_id=variant.batch_id AND bodywork.entity_type='bodywork'
                 AND bodywork.source_key=variant.attributes->>'bodywork_source_key'
                WHERE variant.batch_id=%s AND variant.entity_type='vehicle_variant'
                  AND ka.attributes->>'alias_text'=ANY(%s)
                ORDER BY ka.attributes->>'alias_text'
                """,
                (catalog_version, references),
            )
            bodywork_by_code = canonical_bodywork_by_kt086()
            tecdoc_candidates = []
            for row in cursor.fetchall():
                values = {
                    "candidate_reference": str(row[0]),
                    "model": row[1], "year_from": row[2], "year_to": row[3],
                    "fuel_type": row[4], "engine_code": row[5], "displacement_cc": row[6],
                    "power_kw": row[7], "drive_type": row[8],
                    "bodywork": row[9] or bodywork_by_code.get(str(row[10] or "").zfill(3)),
                }
                tecdoc_candidates.append({key: value for key, value in values.items() if value is not None})
            source_keys: dict[str, tuple[str, ...]] = {
                "bodywork": ("body_code",), "drive_type": ("is_4wd",), "fuels": ("fuel1", "fuel2", "fuel3", "ev_config"),
                "fuel": ("fuel1", "fuel2", "fuel3", "ev_config"), "engine": ("engine_code",), "engine_code": ("engine_code",),
                "year": ("model_year", "production_year"), "displacement_cc": ("ccm", "displacement_cc"),
                "power_kw": ("kw", "power_kw"),
            }
            comparisons: dict[str, dict[str, Any]] = {}
            for field in conflicts:
                keys: tuple[str, ...] = source_keys.get(field, (field,))
                ts_values_set: set[str] = set()
                for example in ts_examples:
                    example_values = example.get("values")
                    if not isinstance(example_values, dict):
                        continue
                    for key in keys:
                        if key in example_values:
                            ts_values_set.add(f"{key}={example_values[key]}")
                ts_values = sorted(ts_values_set)
                catalog_values = sorted({
                    f"{candidate['candidate_reference']}: {field}={candidate.get(field, 'missing')}"
                    for candidate in tecdoc_candidates
                })
                comparisons[field] = {"ts_values": ts_values, "tecdoc_values": catalog_values}
            return {
                "pattern_key": pattern_key,
                "conflicting_fields": conflicts,
                "ts_examples": ts_examples,
                "tecdoc_candidates": tecdoc_candidates,
                "comparisons": comparisons,
            }

    def record_pattern_decision(
        self, *, operation_id: str, pattern: dict[str, Any], action: str,
        selected_values: list[str], reviewer: str, reason: str,
    ) -> dict[str, Any]:
        decision_id = uuid4()
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT decision_id FROM {MATCH_REVIEW_RULE_DECISIONS_TABLE} "
                "WHERE operation_id=%s AND pattern_key=%s "
                "ORDER BY created_at DESC, decision_id DESC LIMIT 1",
                (operation_id, pattern["pattern_key"]),
            )
            previous = cursor.fetchone()
            cursor.execute(
                f"""
                INSERT INTO {MATCH_REVIEW_RULE_DECISIONS_TABLE}
                    (decision_id, operation_id, pattern_key, blocker_category,
                     pattern_evidence, action, selected_values, reviewer, reason,
                     supersedes_decision_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    decision_id, UUID(operation_id), pattern["pattern_key"],
                    pattern["category"], Jsonb({
                        "title": pattern["title"],
                        "source_values": pattern["source_values"],
                        "candidate_values": pattern["candidate_values"],
                    }), action, Jsonb(selected_values), reviewer.strip(), reason.strip(),
                    previous[0] if previous else None,
                ),
            )
            connection.commit()
        return {
            "decision_id": str(decision_id),
            "action": action,
            "selected_values": selected_values,
            "reviewer": reviewer.strip(),
            "reason": reason.strip(),
            "created_at": self._read_decision_created_at(decision_id),
        }

    def _read_decision_created_at(self, decision_id: UUID) -> Any:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT created_at FROM {MATCH_REVIEW_RULE_DECISIONS_TABLE} WHERE decision_id=%s",
                (decision_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("saved pattern decision could not be read back")
        return row[0]

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
        item = {
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
        item.update(explain_review_item(item))
        return item
