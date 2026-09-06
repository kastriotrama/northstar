from typing import Any

import pytest

from ingestion.vehicle_facts import (
    RefreshSummary,
    build_refresh_statement,
    projected_columns,
    refresh_vehicle_facts,
)


class _FakeCursor:
    def __init__(self, pages: list[tuple[int, int]]) -> None:
        self._pages = pages
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[Any, ...]) -> None:
        self.executed.append((statement, parameters))

    def fetchone(self) -> tuple[int, int] | None:
        return self._pages.pop(0) if self._pages else (0, 0)


class _FakeConnection:
    """Records the cursor arguments each page was asked for."""

    def __init__(self, pages: list[tuple[int, int]]) -> None:
        self._cursor = _FakeCursor(pages)
        self.commits = 0

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    @property
    def executed(self) -> list[tuple[str, tuple[Any, ...]]]:
        return self._cursor.executed


def test_numeric_columns_survive_non_numeric_registry_text() -> None:
    """One malformed `kw` must not abort a 50,000-row page."""

    statement = build_refresh_statement()

    assert "~ '^-?[0-9]+$'" in statement
    assert "::int" in statement


def test_absent_values_normalize_to_null_rather_than_empty_string() -> None:
    statement = build_refresh_statement()

    assert "nullif(btrim(" in statement


def test_refresh_pages_forward_by_source_record_id() -> None:
    connection = _FakeConnection([(50_000, 812), (17, 999), (0, 0)])

    summary = refresh_vehicle_facts(connection, page_size=50_000)  # type: ignore[arg-type]

    assert summary == RefreshSummary(
        rows_written=50_017, pages=2, highest_source_record_id=999
    )
    cursors = [parameters[0] for _, parameters in connection.executed]
    assert cursors == [0, 812, 999], "each page must resume after the last one"


def test_refresh_resumes_from_a_supplied_cursor() -> None:
    connection = _FakeConnection([(5, 1_200), (0, 0)])

    refresh_vehicle_facts(connection, since_source_record_id=1_000)  # type: ignore[arg-type]

    assert connection.executed[0][1][0] == 1_000


def test_refresh_commits_every_page_so_a_backfill_is_resumable() -> None:
    connection = _FakeConnection([(10, 1), (10, 2), (0, 0)])

    refresh_vehicle_facts(connection)  # type: ignore[arg-type]

    assert connection.commits == 3


def test_max_pages_bounds_a_trial_run() -> None:
    connection = _FakeConnection([(10, 1), (10, 2), (10, 3), (0, 0)])

    summary = refresh_vehicle_facts(connection, max_pages=2)  # type: ignore[arg-type]

    assert summary.pages == 2
    assert summary.rows_written == 20


def test_page_size_must_be_positive() -> None:
    with pytest.raises(ValueError):
        refresh_vehicle_facts(_FakeConnection([]), page_size=0)  # type: ignore[arg-type]


def test_upsert_refreshes_every_column_but_the_key() -> None:
    """A re-read row must pick up new normalization and dropped resolutions."""

    statement = build_refresh_statement()
    names = [name for name, _ in projected_columns()]

    assert "ON CONFLICT (source_record_id) DO UPDATE SET" in statement
    for name in names:
        if name == "source_record_id":
            continue
        assert f"{name} = EXCLUDED.{name}" in statement
    assert "source_record_id = EXCLUDED.source_record_id" not in statement


def test_rule_overlay_reads_only_live_resolutions() -> None:
    """A retired rule stops counting the moment its rows are re-read."""

    statement = build_refresh_statement()

    assert "superseded_at IS NULL" in statement


def test_cursor_comes_from_the_upsert_rather_than_a_second_scan() -> None:
    statement = build_refresh_statement()

    assert "RETURNING source_record_id" in statement
    assert statement.count("ORDER BY nr.source_record_id") == 1


def test_free_space_probe_reports_plenty_when_it_cannot_read_the_path() -> None:
    """A guard that throws is worse than no guard: the refresh must still run."""

    from ingestion.vehicle_facts import DEFAULT_MIN_FREE_BYTES, _free_space

    assert _free_space("/nonexistent-path-for-this-test") > DEFAULT_MIN_FREE_BYTES


def test_refresh_stops_and_reports_a_resumable_cursor_when_disk_runs_low(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backfill that dies on DiskFull takes the whole database down with it."""

    import ingestion.vehicle_facts as module

    connection = _FakeConnection([(10, 4_242), (10, 9_999)])
    readings = iter([10**12, 0])
    monkeypatch.setattr(module, "_free_space", lambda _: next(readings))

    summary = refresh_vehicle_facts(connection, free_bytes=1_000)  # type: ignore[arg-type]

    assert summary.stopped_for_disk is True
    assert summary.pages == 1
    # The cursor is exactly what the operator passes to --since once there is room.
    assert summary.highest_source_record_id == 4_242


def test_disk_guard_can_be_switched_off() -> None:
    connection = _FakeConnection([(10, 1), (0, 0)])

    summary = refresh_vehicle_facts(connection, free_bytes=None)  # type: ignore[arg-type]

    assert summary.stopped_for_disk is False
    assert summary.rows_written == 10
