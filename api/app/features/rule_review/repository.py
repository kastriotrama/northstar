from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from api.app.features.normalization_review.repository import ConnectionFactory
from ingestion.normalization_migrations import (
    MANUFACTURER_ENTITY_DRAFTS_TABLE,
    NORMALIZATION_RESULTS_TABLE,
    TRANSLATION_RULE_DRAFTS_TABLE,
    TRANSLATION_RULE_VERSIONS_TABLE,
    run_normalization_migrations,
)


class RuleReviewRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def ensure_schema(self) -> None:
        with self._connection_factory() as connection:
            run_normalization_migrations(connection)

    def fetch_drafts(self) -> dict[str, dict[str, Any]]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT rule_id, canonical_value, decision, display_value, change_note "
                f"FROM {TRANSLATION_RULE_DRAFTS_TABLE} ORDER BY rule_id"
            )
            rows = cursor.fetchall()
        return {
            str(row[0]): {
                "canonical_value": row[1],
                "decision": str(row[2]),
                "display_value": row[3],
                "change_note": str(row[4]),
            }
            for row in rows
        }

    def fetch_active_version(self) -> dict[str, Any] | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT version, base_rule_version, overrides, activated_at "
                f"FROM {TRANSLATION_RULE_VERSIONS_TABLE} "
                "ORDER BY activated_at DESC, version DESC LIMIT 1"
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return {
            "version": str(row[0]),
            "base_rule_version": str(row[1]),
            "overrides": dict(row[2]),
            "activated_at": row[3],
        }

    def fetch_manufacturer_entity_drafts(self) -> dict[str, dict[str, Any]]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT entity_id, source_field, source_term, canonical_name, "
                f"entity_role, base_behavior, change_note, created_at, updated_at "
                f"FROM {MANUFACTURER_ENTITY_DRAFTS_TABLE} ORDER BY entity_id"
            )
            rows = cursor.fetchall()
        return {
            str(row[0]): {
                "kind": "manufacturer_entity",
                "source_field": str(row[1]),
                "source_term": str(row[2]),
                "canonical_name": row[3],
                "entity_role": str(row[4]),
                "base_behavior": str(row[5]),
                "change_note": str(row[6]),
                "created_at": row[7],
                "updated_at": row[8],
            }
            for row in rows
        }

    def fetch_manufacturer_entity_lifecycle(self) -> dict[str, dict[str, datetime]]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                WITH expanded AS (
                    SELECT
                        version,
                        activated_at,
                        item.key AS entity_id,
                        item.value AS definition
                    FROM {TRANSLATION_RULE_VERSIONS_TABLE}
                    CROSS JOIN LATERAL jsonb_each(overrides) AS item
                    WHERE item.value->>'kind' = 'manufacturer_entity'
                ), changes AS (
                    SELECT
                        entity_id,
                        activated_at,
                        definition,
                        lag(definition) OVER (
                            PARTITION BY entity_id ORDER BY activated_at, version
                        ) AS previous_definition
                    FROM expanded
                )
                SELECT
                    entity_id,
                    min(activated_at),
                    max(activated_at) FILTER (
                        WHERE previous_definition IS NULL
                           OR definition IS DISTINCT FROM previous_definition
                    )
                FROM changes
                GROUP BY entity_id
                """
            )
            rows = cursor.fetchall()
        return {str(row[0]): {"created_at": row[1], "updated_at": row[2]} for row in rows}

    def fetch_discovered_manufacturer_entities(self) -> list[dict[str, Any]]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                WITH latest_batch AS (
                    SELECT source_batch_id
                    FROM {NORMALIZATION_RESULTS_TABLE}
                    GROUP BY source_batch_id
                    ORDER BY max(created_at) DESC, source_batch_id DESC
                    LIMIT 1
                ), latest AS (
                    SELECT DISTINCT ON (result.source_record_id)
                        result.source_record_id,
                        result.review_reasons
                    FROM {NORMALIZATION_RESULTS_TABLE} AS result
                    JOIN latest_batch USING (source_batch_id)
                    ORDER BY result.source_record_id, result.updated_at DESC, result.id DESC
                ), candidates AS (
                    SELECT
                        CASE
                            WHEN raw.raw_record->>'manufacturer' IS NOT NULL
                                THEN 'manufacturer'
                            ELSE 'brand'
                        END AS source_field,
                        coalesce(
                            raw.raw_record->>'manufacturer',
                            raw.raw_record->>'brand'
                        ) AS source_term,
                        raw.raw_record->>'base_manufacturer' AS base_manufacturer
                    FROM latest
                    JOIN staging.transportstyrelsen_raw AS raw
                        ON raw.id = latest.source_record_id
                    WHERE latest.review_reasons && ARRAY[
                        'manufacturer_unknown',
                        'manufacturer_missing',
                        'manufacturer_missing_compare_brand',
                        'manufacturer_corporate_group_unresolved',
                        'converter_base_manufacturer_unresolved'
                    ]::TEXT[]
                )
                SELECT source_field, source_term, count(*),
                    array_remove(array_agg(DISTINCT base_manufacturer), NULL)
                FROM candidates
                WHERE nullif(trim(source_term), '') IS NOT NULL
                GROUP BY source_field, source_term
                ORDER BY count(*) DESC, source_term
                """
            )
            rows = cursor.fetchall()
        return [
            {
                "source_field": str(row[0]),
                "source_term": str(row[1]),
                "occurrences": int(row[2]),
                "base_manufacturers": [str(value) for value in row[3]],
            }
            for row in rows
        ]

    def fetch_review_reason_summary(self) -> dict[str, int]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                WITH latest_batch AS (
                    SELECT source_batch_id
                    FROM {NORMALIZATION_RESULTS_TABLE}
                    GROUP BY source_batch_id
                    ORDER BY max(created_at) DESC, source_batch_id DESC
                    LIMIT 1
                ), latest AS (
                    SELECT DISTINCT ON (result.source_record_id)
                        result.source_record_id, result.status, result.review_reasons
                    FROM {NORMALIZATION_RESULTS_TABLE} AS result
                    JOIN latest_batch USING (source_batch_id)
                    ORDER BY result.source_record_id, result.updated_at DESC, result.id DESC
                )
                SELECT reason, count(*)
                FROM latest CROSS JOIN LATERAL unnest(review_reasons) AS reason
                WHERE status = 'review_required'
                GROUP BY reason
                """
            )
            rows = cursor.fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def save_draft(
        self,
        *,
        rule_id: str,
        canonical_value: str | None,
        decision: str,
        display_value: str | None,
        change_note: str,
    ) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {TRANSLATION_RULE_DRAFTS_TABLE} (
                    rule_id, canonical_value, decision, display_value, change_note
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (rule_id) DO UPDATE SET
                    canonical_value = EXCLUDED.canonical_value,
                    decision = EXCLUDED.decision,
                    display_value = EXCLUDED.display_value,
                    change_note = EXCLUDED.change_note,
                    updated_at = now()
                """,
                (rule_id, canonical_value, decision, display_value, change_note.strip()),
            )
            connection.commit()

    def delete_draft(self, rule_id: str) -> bool:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {TRANSLATION_RULE_DRAFTS_TABLE} WHERE rule_id = %s",
                (rule_id,),
            )
            deleted = cursor.rowcount > 0
            connection.commit()
        return deleted

    def save_manufacturer_entity_draft(
        self,
        *,
        entity_id: str,
        source_field: str,
        source_term: str,
        canonical_name: str | None,
        entity_role: str,
        base_behavior: str,
        change_note: str,
    ) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {MANUFACTURER_ENTITY_DRAFTS_TABLE} (
                    entity_id, source_field, source_term, canonical_name,
                    entity_role, base_behavior, change_note
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (entity_id) DO UPDATE SET
                    canonical_name = EXCLUDED.canonical_name,
                    entity_role = EXCLUDED.entity_role,
                    base_behavior = EXCLUDED.base_behavior,
                    change_note = EXCLUDED.change_note,
                    updated_at = now()
                """,
                (
                    entity_id,
                    source_field,
                    source_term,
                    canonical_name,
                    entity_role,
                    base_behavior,
                    change_note.strip(),
                ),
            )
            connection.commit()

    def delete_manufacturer_entity_draft(self, entity_id: str) -> bool:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {MANUFACTURER_ENTITY_DRAFTS_TABLE} WHERE entity_id = %s",
                (entity_id,),
            )
            deleted = cursor.rowcount > 0
            connection.commit()
        return deleted

    def activate_drafts(
        self,
        *,
        version: str,
        base_rule_version: str,
        inherited_overrides: dict[str, dict[str, Any]],
        note: str,
    ) -> tuple[int, datetime]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(f"LOCK TABLE {TRANSLATION_RULE_DRAFTS_TABLE} IN EXCLUSIVE MODE")
            cursor.execute(f"LOCK TABLE {MANUFACTURER_ENTITY_DRAFTS_TABLE} IN EXCLUSIVE MODE")
            cursor.execute(
                f"SELECT rule_id, canonical_value, decision, display_value, change_note "
                f"FROM {TRANSLATION_RULE_DRAFTS_TABLE} ORDER BY rule_id"
            )
            rows = cursor.fetchall()
            cursor.execute(
                f"SELECT entity_id, source_field, source_term, canonical_name, entity_role, "
                f"base_behavior, change_note FROM {MANUFACTURER_ENTITY_DRAFTS_TABLE} "
                "ORDER BY entity_id"
            )
            entity_rows = cursor.fetchall()
            if not rows and not entity_rows:
                raise ValueError("no_rule_drafts_to_activate")
            draft_overrides = {
                str(row[0]): {
                    "canonical_value": row[1],
                    "decision": str(row[2]),
                    "display_value": row[3],
                    "change_note": str(row[4]),
                }
                for row in rows
            }
            overrides = {**inherited_overrides, **draft_overrides}
            overrides.update(
                {
                    str(row[0]): {
                        "kind": "manufacturer_entity",
                        "source_field": str(row[1]),
                        "source_term": str(row[2]),
                        "canonical_name": row[3],
                        "entity_role": str(row[4]),
                        "base_behavior": str(row[5]),
                        "change_note": str(row[6]),
                    }
                    for row in entity_rows
                }
            )
            cursor.execute(
                f"INSERT INTO {TRANSLATION_RULE_VERSIONS_TABLE} "
                "(version, base_rule_version, overrides, activation_note) "
                "VALUES (%s, %s, %s, %s) RETURNING activated_at",
                (version, base_rule_version, Jsonb(overrides), note.strip()),
            )
            activation_row = cursor.fetchone()
            if activation_row is None:
                raise RuntimeError("rule_activation_did_not_return_timestamp")
            activated_at = activation_row[0]
            cursor.execute(f"DELETE FROM {TRANSLATION_RULE_DRAFTS_TABLE}")
            cursor.execute(f"DELETE FROM {MANUFACTURER_ENTITY_DRAFTS_TABLE}")
            connection.commit()
        return len(rows) + len(entity_rows), activated_at
