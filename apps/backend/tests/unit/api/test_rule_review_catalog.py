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
    def __init__(
        self,
        resolution: list[dict[str, Any]] | None = None,
        tecdoc: list[dict[str, Any]] | None = None,
        tecdoc_version: str | None = None,
        tecdoc_resolution: list[dict[str, Any]] | None = None,
    ) -> None:
        self.resolution = resolution if resolution is not None else [RESOLUTION_RULE]
        self.tecdoc = tecdoc or []
        self.tecdoc_version = tecdoc_version
        self.tecdoc_resolution = tecdoc_resolution or []

    def ensure_schema(self) -> None:
        return None

    def fetch_drafts(self) -> dict[str, dict[str, Any]]:
        return {}

    def fetch_active_version(self) -> dict[str, Any] | None:
        return None

    def fetch_resolution_rules(self, limit: int = 2000) -> list[dict[str, Any]]:
        return self.resolution

    def fetch_tecdoc_rules(
        self, limit: int = 20000
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """No sealed TecDoc version, the shape a database without one returns."""

        return self.tecdoc_version, self.tecdoc

    def fetch_tecdoc_resolution_rules(self, limit: int = 2000) -> list[dict[str, Any]]:
        return self.tecdoc_resolution


def build(
    resolution: list[dict[str, Any]] | None = None,
    tecdoc: list[dict[str, Any]] | None = None,
    tecdoc_version: str | None = None,
    tecdoc_resolution: list[dict[str, Any]] | None = None,
) -> RuleReviewService:
    return RuleReviewService(
        FakeRepository(resolution, tecdoc, tecdoc_version, tecdoc_resolution), MagicMock()
    )


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


# --- TecDoc rules in the same catalog ------------------------------------------------
# Both sources are normalized into one canonical vocabulary, so both belong in one
# list. These pin the three things that merge has to get right: the rules arrive, the
# `source` dimension separates them from the TS rows, and the model-name inventory
# stays out of the default view.

TECDOC_RULE: dict[str, Any] = {
    "rule_id": "TD:transmission.transmission_type_name:abc123456789",
    "area": "transmission",
    "entity_type": "transmission",
    "source_field": "transmission_type_name",
    "source_term": "Manual Transmission",
    "key_table": "085",
    "canonical_field": "transmission_type",
    "canonical_value": None,
    "decision": "proposed",
    "derivation": "generated",
    "support": 385,
    "evidence": {"reason": "unmapped", "observed_rows": 385},
}

TECDOC_INVENTORY_RULE: dict[str, Any] = {
    **TECDOC_RULE,
    "rule_id": "TD:model_family.canonical_name:def123456789",
    "area": "model_family",
    "entity_type": "model_family",
    "source_field": "canonical_name",
    "source_term": "XC90",
    "key_table": None,
    "canonical_field": "model_family",
    "support": 4,
    "evidence": {"reason": "open_vocabulary", "observed_rows": 4},
}


def test_tecdoc_rules_join_the_catalog_under_their_own_source() -> None:
    catalog = build(tecdoc=[TECDOC_RULE], tecdoc_version="tecdoc-0326-v1").list_rule_catalog(
        limit=5000
    )

    tecdoc = [entry for entry in catalog.items if entry.source == "tecdoc"]
    assert len(tecdoc) == 1
    assert catalog.tecdoc_rule_version == "tecdoc-0326-v1"
    assert {entry.source for entry in catalog.items} == {"transportstyrelsen", "tecdoc"}


def test_the_source_filter_separates_the_two_datasets() -> None:
    service = build(tecdoc=[TECDOC_RULE], tecdoc_version="v1")

    assert all(
        entry.source == "tecdoc"
        for entry in service.list_rule_catalog(source="tecdoc", limit=5000).items
    )
    assert all(
        entry.source == "transportstyrelsen"
        for entry in service.list_rule_catalog(source="transportstyrelsen", limit=5000).items
    )


def test_inventory_rules_are_hidden_by_default_but_counted() -> None:
    """9,834 model names would bury the 36 rules a reviewer can act on."""

    catalog = build(
        tecdoc=[TECDOC_RULE, TECDOC_INVENTORY_RULE], tecdoc_version="v1"
    ).list_rule_catalog(source="tecdoc", limit=5000)

    assert catalog.tecdoc_total == 1
    assert catalog.tecdoc_inventory_total == 1
    assert [entry.source_terms for entry in catalog.items] == [["Manual Transmission"]]


def test_inventory_rules_appear_when_asked_for() -> None:
    catalog = build(
        tecdoc=[TECDOC_RULE, TECDOC_INVENTORY_RULE], tecdoc_version="v1"
    ).list_rule_catalog(source="tecdoc", include_inventory=True, limit=5000)

    assert catalog.tecdoc_total == 2
    assert any(entry.inventory_only for entry in catalog.items)


def test_a_generated_rule_is_never_editable_from_this_screen() -> None:
    """It lives in a sealed version; a correction is a new generation, not an edit."""

    catalog = build(tecdoc=[TECDOC_RULE], tecdoc_version="v1").list_rule_catalog(
        source="tecdoc", limit=5000
    )

    assert catalog.items[0].editable is False


def test_an_unmapped_rule_says_it_reaches_the_graph_unnormalized() -> None:
    catalog = build(tecdoc=[TECDOC_RULE], tecdoc_version="v1").list_rule_catalog(
        source="tecdoc", limit=5000
    )
    entry = catalog.items[0]

    assert entry.effective_canonical_value is None
    assert entry.support == 385
    assert "385 rows" in (entry.notes or "")
    assert "unnormalized" in (entry.notes or "")


def test_a_database_with_no_sealed_tecdoc_version_reads_as_no_tecdoc_rules() -> None:
    catalog = build().list_rule_catalog(limit=5000)

    assert catalog.tecdoc_total == 0
    assert catalog.tecdoc_rule_version is None
    assert all(entry.source == "transportstyrelsen" for entry in catalog.items)


TS_SYNONYM_ROW = {
    "canonical_field": "fuel",
    "comparison_key": "ELECTRICITY",
    "source_term": "electricity",
    "key_table": None,
    "decision": "accepted",
    "canonical_value": "electric",
    "note": "",
    "reviewed_by": "pytest",
    "created_at": datetime(2026, 9, 9, tzinfo=UTC),
    "updated_at": datetime(2026, 9, 9, tzinfo=UTC),
    "source_system": "transportstyrelsen",
    "relation": "equivalent",
    "support": None,
}


def test_a_ts_authored_synonym_row_reports_its_own_source_not_tecdoc() -> None:
    """A row TS authored into the shared table must not read as TecDoc's own --
    that was a real bug: every row from this table used to be hardcoded 'tecdoc'."""

    catalog = build(tecdoc_resolution=[TS_SYNONYM_ROW]).list_rule_catalog(limit=5000)
    entry = next(e for e in catalog.items if e.area == "tecdoc_resolution_rule")

    assert entry.source == "transportstyrelsen"
    assert entry.canonical_field == "fuel"
    assert entry.source_terms == ["electricity"]
    assert entry.effective_canonical_value == "electric"


def test_compatible_rows_for_the_same_term_get_distinct_rule_ids() -> None:
    """TS's undifferentiated 2wd is compatible with both TecDoc fwd and rwd --
    two rows sharing canonical_field/comparison_key, which the old rule_id
    (built from only those two) would have collided on."""

    fwd = {**TS_SYNONYM_ROW, "canonical_field": "drive", "comparison_key": "2WD",
           "source_term": "2wd", "canonical_value": "fwd", "relation": "compatible",
           "support": 744197}
    rwd = {**fwd, "canonical_value": "rwd"}

    catalog = build(tecdoc_resolution=[fwd, rwd]).list_rule_catalog(limit=5000)
    entries = [e for e in catalog.items if e.area == "tecdoc_resolution_rule"]

    assert len({e.rule_id for e in entries}) == 2
