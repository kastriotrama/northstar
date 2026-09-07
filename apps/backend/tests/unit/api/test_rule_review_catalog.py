"""The rule list must hold all three kinds of rule that change a value.

Catalog rules come from the reviewed dictionaries, code rules are compiled into the
pipeline, and resolution rules are authored on the projection from the TS data screen.
A reader looking for what changed a field should not have to know which is which.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Self
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from api.app.features.rule_review.repository import RuleReviewRepository
from api.app.features.rule_review.schemas import RuleDraftRequest
from api.app.features.rule_review.service import RuleReviewError, RuleReviewService

RESOLUTION_RULE = {
    "rule_id": "25ceeb43-0cb2-4d5c-87a5-10e9af147cd2",
    "build_id": "1f0f5b2e-0000-4000-8000-000000000000",
    "source_field": "brand",
    "source_value": "AUDI",
    "target_field": "engine_code",
    "target_value": "CUUB",
    "conditions": [
        {"field": "brand", "layer": "source", "values": ["AUDI"], "operator": "equals"},
        {"field": "model", "layer": "source", "values": ["Q3"], "operator": "equals"},
        {"field": "drive_type", "layer": "normalized", "value": "fwd", "operator": "equals"},
    ],
    "author": "Valon Shabani",
    "note": "Confirmed against the type approval.",
    "matched_rows": 2704,
    "resolved_rows": 2704,
    "status": "applied",
    "created_at": datetime(2026, 9, 5, tzinfo=UTC),
    "applied_at": datetime(2026, 9, 5, tzinfo=UTC),
    "retired_at": None,
}


class FakeRepository:
    def __init__(self, resolution: list[dict[str, Any]] | None = None) -> None:
        self.resolution = resolution if resolution is not None else [RESOLUTION_RULE]

    def ensure_schema(self) -> None:
        return None

    def fetch_drafts(self) -> dict[str, dict[str, Any]]:
        return {}

    def fetch_active_version(self) -> dict[str, Any] | None:
        return None

    def fetch_resolution_rules(self, limit: int = 2000) -> list[dict[str, Any]]:
        return self.resolution


def build(resolution: list[dict[str, Any]] | None = None) -> RuleReviewService:
    return RuleReviewService(FakeRepository(resolution), MagicMock())


def test_the_list_holds_catalog_code_and_projection_rules() -> None:
    catalog = build().list_rule_catalog(limit=5000)
    origins = {entry.origin for entry in catalog.items}
    assert origins == {"catalog", "code", "resolution"}
    assert catalog.total == (
        catalog.catalog_total + catalog.code_total + catalog.resolution_total
    )
    assert catalog.resolution_total == 1


def test_a_projection_rule_reads_as_its_predicate() -> None:
    catalog = build().list_rule_catalog(origin="resolution", limit=10)
    entry = catalog.items[0]

    assert entry.rule_id == f"RES:{RESOLUTION_RULE['rule_id']}"
    assert entry.canonical_field == "engine_code"
    assert entry.effective_canonical_value == "CUUB"
    assert entry.effective_decision == "applied"
    assert entry.editable is False
    assert entry.source_terms == [
        "brand equals AUDI",
        "model equals Q3",
        "drive_type (normalized) equals fwd",
    ]
    # Every field the predicate reads, not just the one the population was keyed on.
    assert entry.source_fields == ["brand", "drive_type", "model"]
    assert "Valon Shabani" in (entry.notes or "")
    assert "2,704" in (entry.notes or "")


def test_a_retired_rule_still_appears_with_its_state() -> None:
    retired = {**RESOLUTION_RULE, "status": "retired", "retired_at": datetime.now(UTC)}
    catalog = build([retired]).list_rule_catalog(origin="resolution", limit=10)
    assert catalog.items[0].effective_decision == "retired"


def test_projection_rules_are_searchable_by_field_and_value() -> None:
    service = build()
    for term in ("CUUB", "engine_code", "Q3", "Valon"):
        found = service.list_rule_catalog(query=term, origin="resolution", limit=10)
        assert found.filtered_total == 1, f"{term!r} should find the projection rule"


def test_a_projection_rule_cannot_be_redrafted_as_a_translation_override() -> None:
    service = build()
    request = RuleDraftRequest(decision="accepted", change_note="not the place for this")
    with pytest.raises(RuleReviewError, match="rule_is_a_projection_resolution_rule"):
        service.save_draft(f"RES:{RESOLUTION_RULE['rule_id']}", request)


def test_the_area_and_field_filters_offer_the_projection_values() -> None:
    catalog = build().list_rule_catalog(limit=1)
    assert "resolution_rule" in catalog.areas
    assert "engine_code" in catalog.canonical_fields


class _FakeCursor:
    """Stands in for psycopg so the missing-table branch can be reached without one."""

    def __init__(self, table_exists: bool) -> None:
        self.table_exists = table_exists
        self.statements: list[str] = []
        self._result: list[tuple[Any, ...]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> None:
        self.statements.append(statement)
        if "to_regclass" in statement:
            self._result = [(self.table_exists,)]
        else:
            self._result = [
                (
                    UUID(RESOLUTION_RULE["rule_id"]),
                    UUID(RESOLUTION_RULE["build_id"]),
                    "brand",
                    "AUDI",
                    "engine_code",
                    "CUUB",
                    RESOLUTION_RULE["conditions"],
                    "Valon Shabani",
                    None,
                    2704,
                    2704,
                    "applied",
                    RESOLUTION_RULE["created_at"],
                    RESOLUTION_RULE["applied_at"],
                    None,
                )
            ]

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._result[0] if self._result else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._result


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor


def repository_for(cursor: _FakeCursor) -> RuleReviewRepository:
    @contextmanager
    def factory() -> Any:
        yield _FakeConnection(cursor)

    return RuleReviewRepository(factory)


def test_a_database_without_the_table_reads_as_no_projection_rules() -> None:
    """The match chunk migrations create it and the rule review schema does not run them.

    Selecting from a missing table raises UndefinedTable and poisons the transaction,
    so the read must ask first rather than let the whole catalog fail.
    """

    cursor = _FakeCursor(table_exists=False)
    assert repository_for(cursor).fetch_resolution_rules() == []
    assert len(cursor.statements) == 1, "must not select after to_regclass says no"
    assert "to_regclass" in cursor.statements[0]


def test_the_rows_are_read_when_the_table_is_there() -> None:
    cursor = _FakeCursor(table_exists=True)
    rules = repository_for(cursor).fetch_resolution_rules()
    assert len(rules) == 1
    assert rules[0]["rule_id"] == RESOLUTION_RULE["rule_id"]
    assert rules[0]["target_value"] == "CUUB"
    assert rules[0]["status"] == "applied"
