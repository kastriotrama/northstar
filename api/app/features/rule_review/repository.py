from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from api.app.features.normalization_review.repository import ConnectionFactory
from ingestion.normalization_migrations import (
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
            cursor.execute(
                f"SELECT rule_id, canonical_value, decision, display_value, change_note "
                f"FROM {TRANSLATION_RULE_DRAFTS_TABLE} ORDER BY rule_id"
            )
            rows = cursor.fetchall()
            if not rows:
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
            connection.commit()
        return len(rows), activated_at
