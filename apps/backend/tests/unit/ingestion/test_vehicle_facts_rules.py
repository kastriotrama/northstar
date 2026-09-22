from typing import Any, Self
from uuid import uuid4

import pytest

from ingestion.match_chunk_migrations import MATCH_FIELD_RESOLUTIONS_TABLE
from ingestion.vehicle_facts_query import CompiledPredicate
from ingestion.vehicle_facts_rules import (
    _target_predicate,
    apply_rule,
    apply_rule_batch,
)

RULE_ID = uuid4()
BUILD_ID = uuid4()
PREDICATE = CompiledPredicate("brand = ANY(%s) AND model = ANY(%s)", [["VOLVO"], ["XC40"]])


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows
        self.executed: list[tuple[str, list[Any]]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, parameters: list[Any]) -> None:
        self.executed.append((statement, list(parameters)))

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows.pop(0) if self._rows else None


class _FakeConnection:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._cursor = _FakeCursor(rows)
        self.commits = 0

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    @property
    def executed(self) -> list[tuple[str, list[Any]]]:
        return self._cursor.executed


def test_filling_a_gap_only_touches_cars_that_have_none() -> None:
    connection = _FakeConnection([(12, 900, 12)])

    apply_rule_batch(
        connection,  # type: ignore[arg-type]
        rule_id=RULE_ID,
        build_id=BUILD_ID,
        predicate=PREDICATE,
        target_field="bodywork_form",
        target_value="suv",
        after_id=0,
    )

    assert len(connection.executed) == 1
    statement, parameters = connection.executed[0]
    assert "n_bodywork_form IS NULL AND r_bodywork_form IS NULL" in statement
    # The asserted value is bound once for the ledger and once for the overlay,
    # never for the batch: a gap-filling rule does not compare against it.
    assert parameters[:2] == [["VOLVO"], ["XC40"]]
    assert parameters[2:4] == [0, 50_000]


def test_an_override_takes_the_cars_whose_value_disagrees_with_it() -> None:
    """The case this exists for: the registry derived estate, it is an SUV."""

    connection = _FakeConnection([(7, 900, 7)])

    apply_rule_batch(
        connection,  # type: ignore[arg-type]
        rule_id=RULE_ID,
        build_id=BUILD_ID,
        predicate=PREDICATE,
        target_field="bodywork_form",
        target_value="suv",
        after_id=0,
        override=True,
    )

    supersede, write = connection.executed
    assert (
        "coalesce(r_bodywork_form::text, n_bodywork_form::text) IS DISTINCT FROM %s"
        in write[0]
    )
    assert "n_bodywork_form IS NULL" not in write[0]
    # Rows already saying "suv" fall outside `IS DISTINCT FROM`, so a second
    # run writes nothing rather than churning the ledger.
    assert write[1][:3] == [["VOLVO"], ["XC40"], "suv"]
    assert "IS DISTINCT FROM %s" in supersede[0]


def test_an_override_supersedes_the_resolution_it_replaces_first() -> None:
    """The active-resolution index is partial on `superseded_at IS NULL`, so
    vacating it has to be finished before the replacement row is inserted."""

    connection = _FakeConnection([(7, 900, 7)])

    apply_rule_batch(
        connection,  # type: ignore[arg-type]
        rule_id=RULE_ID,
        build_id=BUILD_ID,
        predicate=PREDICATE,
        target_field="bodywork_form",
        target_value="suv",
        after_id=0,
        override=True,
    )

    supersede, write = connection.executed
    assert f"UPDATE {MATCH_FIELD_RESOLUTIONS_TABLE}" in supersede[0]
    assert "SET superseded_at = now()" in supersede[0]
    assert "fr.target_field = %s" in supersede[0]
    assert supersede[1][-1] == "bodywork_form"
    assert f"INSERT INTO {MATCH_FIELD_RESOLUTIONS_TABLE}" in write[0]


def test_an_ordinary_rule_never_supersedes_anything() -> None:
    connection = _FakeConnection([(12, 900, 12)])

    apply_rule_batch(
        connection,  # type: ignore[arg-type]
        rule_id=RULE_ID,
        build_id=BUILD_ID,
        predicate=PREDICATE,
        target_field="bodywork_form",
        target_value="suv",
        after_id=0,
    )

    assert all("superseded_at = now()" not in sql for sql, _ in connection.executed)


def test_an_override_run_carries_the_mode_into_every_batch() -> None:
    """Two batches, both overriding: the mode is the rule's, not the batch's."""

    connection = _FakeConnection([(50_000, 50_000, 50_000), (12, 50_012, 12)])

    summary = apply_rule(
        connection,  # type: ignore[arg-type]
        rule_id=RULE_ID,
        build_id=BUILD_ID,
        predicate=PREDICATE,
        target_field="bodywork_form",
        target_value="suv",
        override=True,
    )

    assert summary.rows_written == 50_012
    assert summary.batches == 2
    assert sum("superseded_at = now()" in sql for sql, _ in connection.executed) == 2


def test_the_overlay_keeps_an_integer_target_typed() -> None:
    connection = _FakeConnection([(3, 900, 3)])

    apply_rule_batch(
        connection,  # type: ignore[arg-type]
        rule_id=RULE_ID,
        build_id=BUILD_ID,
        predicate=PREDICATE,
        target_field="power_kw",
        target_value="150",
        after_id=0,
        override=True,
    )

    _, write = connection.executed
    assert "::bigint" in write[0]


def test_an_unknown_target_field_is_refused_before_any_sql_runs() -> None:
    connection = _FakeConnection([])

    with pytest.raises(ValueError):
        apply_rule_batch(
            connection,  # type: ignore[arg-type]
            rule_id=RULE_ID,
            build_id=BUILD_ID,
            predicate=PREDICATE,
            target_field="colour; DROP TABLE core.vehicle_facts --",
            target_value="suv",
            after_id=0,
            override=True,
        )
    assert connection.executed == []


def test_the_target_predicate_states_both_modes() -> None:
    assert _target_predicate("drive_type", override=False) == (
        "n_drive_type IS NULL AND r_drive_type IS NULL"
    )
    assert _target_predicate("drive_type", override=True) == (
        "coalesce(r_drive_type::text, n_drive_type::text) IS DISTINCT FROM %s"
    )
