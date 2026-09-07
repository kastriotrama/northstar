import pytest

from ingestion.vehicle_facts_dedupe import (
    SURVIVOR_RANKING,
    build_dedupe_statement,
    dedupe_vehicle_facts,
)


class _FakeCursor:
    def __init__(self, deletions: list[int]) -> None:
        self._deletions = deletions
        self.rowcount = 0
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        self.executed.append((statement, parameters))
        self.rowcount = self._deletions.pop(0) if self._deletions else 0


class _FakeConnection:
    def __init__(self, deletions: list[int]) -> None:
        self._cursor = _FakeCursor(deletions)
        self.commits = 0

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    @property
    def executed(self) -> list[tuple[str, tuple[object, ...]]]:
        return self._cursor.executed


def test_a_row_holding_a_live_resolution_always_survives() -> None:
    """Ranking by "newest, best normalized" alone dropped the row behind every
    one of the 2,516 resolutions already written."""

    assert "(live.source_record_id IS NOT NULL) DESC" in SURVIVOR_RANKING
    # It must outrank both tie-breakers, so it comes first in the ORDER BY.
    ordering = SURVIVOR_RANKING.index("ORDER BY")
    live = SURVIVOR_RANKING.index("live.source_record_id IS NOT NULL")
    normalized = SURVIVOR_RANKING.index("IS NOT NULL)::int")
    newest = SURVIVOR_RANKING.index("vf.source_record_id DESC")
    assert ordering < live < normalized < newest


def test_only_live_resolutions_protect_a_row() -> None:
    """A retired rule must not pin a copy the projection would rather drop."""

    assert "superseded_at IS NULL" in build_dedupe_statement()


def test_rows_without_a_plate_are_never_deduped() -> None:
    """Two such rows exist; guessing they are the same car would be worse."""

    assert "vf.plate IS NOT NULL" in build_dedupe_statement()


def test_partitions_by_plate() -> None:
    assert "PARTITION BY vf.plate" in SURVIVOR_RANKING


def test_dedupe_batches_until_nothing_is_left() -> None:
    connection = _FakeConnection([100_000, 100_000, 22_845, 0])

    summary = dedupe_vehicle_facts(connection)  # type: ignore[arg-type]

    assert summary.rows_removed == 222_845
    assert summary.batches == 3


def test_each_batch_commits_so_a_long_dedupe_is_resumable() -> None:
    connection = _FakeConnection([10, 10, 0])

    dedupe_vehicle_facts(connection)  # type: ignore[arg-type]

    assert connection.commits == 3


def test_max_batches_bounds_a_trial_run() -> None:
    connection = _FakeConnection([10, 10, 10, 0])

    summary = dedupe_vehicle_facts(connection, max_batches=2)  # type: ignore[arg-type]

    assert summary.batches == 2
    assert summary.rows_removed == 20


def test_batch_size_must_be_positive() -> None:
    with pytest.raises(ValueError):
        dedupe_vehicle_facts(_FakeConnection([]), batch_size=0)  # type: ignore[arg-type]


def test_batch_size_bounds_the_delete() -> None:
    connection = _FakeConnection([5, 0])

    dedupe_vehicle_facts(connection, batch_size=5)  # type: ignore[arg-type]

    assert connection.executed[0][1] == (5,)
