"""A saved correction or choice refreshes the car's stored result, and never fails the save."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any
from uuid import UUID, uuid4

import pytest

from api.app.features.vehicle_match_results.sync import MatchResultSync
from ingestion.vehicle_core_query import MATCH_RESULT_FIELD, compile_term


class _Refresher:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[list[str]] = []
        self.error = error

    def refresh_vehicles(self, vehicle_ids: Any) -> object:
        self.calls.append(list(vehicle_ids))
        if self.error is not None:
            raise self.error
        return None


class _Connection:
    def __init__(self, rows: list[tuple[str]], error: Exception | None = None) -> None:
        self.rows = rows
        self.error = error
        self.asked: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, parameters: tuple[Any, ...]) -> _Connection:
        if self.error is not None:
            raise self.error
        self.asked.append((sql, parameters))
        return self

    def fetchall(self) -> list[tuple[str]]:
        return self.rows


def _sync(refresher: _Refresher, connection: _Connection, **options: Any) -> MatchResultSync:
    return MatchResultSync(
        lambda: refresher, lambda: nullcontext(connection),  # type: ignore[arg-type, return-value]
        run_in_background=False, **options,
    )


def test_a_changed_car_is_refreshed() -> None:
    refresher = _Refresher()
    _sync(refresher, _Connection([])).vehicle_changed("NOR-1")
    assert refresher.calls == [["NOR-1"]]


def test_a_failing_refresh_never_fails_the_save(caplog: pytest.LogCaptureFixture) -> None:
    refresher = _Refresher(RuntimeError("matcher down"))
    _sync(refresher, _Connection([])).vehicle_changed("NOR-1")
    assert refresher.calls == [["NOR-1"]]
    assert "next refresh run picks them up" in caplog.text


def test_a_decision_refreshes_the_cars_its_corrections_name() -> None:
    refresher = _Refresher()
    connection = _Connection([("NOR-2",), ("NOR-1",)])
    decision: UUID = uuid4()
    _sync(refresher, connection).decision_changed(decision)
    assert refresher.calls == [["NOR-1", "NOR-2"]]
    sql, parameters = connection.asked[0]
    assert "core.vehicle_fact_corrections" in sql and "group_id = %s" in sql
    assert parameters == (decision,)


def test_a_decision_without_cars_runs_nothing() -> None:
    refresher = _Refresher()
    _sync(refresher, _Connection([])).decision_changed(uuid4())
    assert refresher.calls == []


def test_a_decision_whose_cars_cannot_be_read_is_left_to_the_next_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    refresher = _Refresher()
    _sync(refresher, _Connection([], RuntimeError("database down"))).decision_changed(uuid4())
    assert refresher.calls == []
    assert "Could not read the cars of a decision" in caplog.text


# ----------------------------------------------------------- the Vehicles list's filter


def test_matcher_states_are_read_from_the_stored_result_of_undecided_cars() -> None:
    compiled = compile_term(MATCH_RESULT_FIELD, "equals", ["several", "none"])
    assert "core.vehicle_match_results" in compiled.sql
    assert "stored_result.state = ANY(%s)" in compiled.sql
    assert "v.match_state IS NULL" in compiled.sql
    assert compiled.parameters == [["several", "none"]]


def test_a_persons_choice_is_read_from_the_vehicle() -> None:
    compiled = compile_term(MATCH_RESULT_FIELD, "equals", ["chosen", "chosen_none"])
    assert compiled.sql == "(v.match_state = ANY(%s))"
    assert compiled.parameters == [["manual", "manual_none"]]


def test_not_evaluated_is_an_undecided_car_without_a_stored_result() -> None:
    compiled = compile_term(MATCH_RESULT_FIELD, "equals", ["not_evaluated"])
    assert "NOT EXISTS" in compiled.sql and compiled.parameters == []


def test_states_can_be_excluded() -> None:
    compiled = compile_term(MATCH_RESULT_FIELD, "not_equals", ["resolved"])
    assert compiled.sql.startswith("NOT (")


@pytest.mark.parametrize(
    ("operator", "values"),
    [("equals", ["solved"]), ("contains", ["several"]), ("equals", [" "]), ("gte", ["several"])],
)
def test_an_unknown_state_or_operator_is_refused(operator: str, values: list[str]) -> None:
    with pytest.raises(ValueError, match="match_result"):
        compile_term(MATCH_RESULT_FIELD, operator, values)
