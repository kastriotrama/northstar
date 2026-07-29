from datetime import UTC, datetime

import pytest

from ingestion.job_bookkeeping import (
    JobAlreadyRunningError,
    claim_job_run,
    complete_job_run,
    fail_job_run,
)


class FakeCursor:
    def __init__(self, rows: list[tuple[object, ...] | None]) -> None:
        self._rows = rows
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        self.executed.append((sql, params))

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows.pop(0)


class FakeConnection:
    def __init__(self, rows: list[tuple[object, ...] | None]) -> None:
        self.cursor_instance = FakeCursor(rows)

    def cursor(self) -> FakeCursor:
        return self.cursor_instance


NOW = datetime(2026, 7, 29, tzinfo=UTC)


def job_row(
    status: str,
    *,
    processed: int = 0,
    succeeded: int = 0,
    failed: int = 0,
) -> tuple[object, ...]:
    return (
        7,
        "transportstyrelsen",
        "batch-42",
        status,
        processed,
        succeeded,
        failed,
        None if status != "failed" else "source_timeout",
        None if status != "failed" else "Sanitized source timeout",
        NOW,
        None if status == "running" else NOW,
        NOW,
    )


def test_first_claim_executes_and_normalizes_identifiers() -> None:
    connection = FakeConnection([job_row("running")])

    claim = claim_job_run(
        connection,  # type: ignore[arg-type]
        job_name=" transportstyrelsen ",
        batch_id=" batch-42 ",
    )

    assert claim.should_execute is True
    assert claim.job_run.status == "running"
    assert connection.cursor_instance.executed[0][1] == (
        "transportstyrelsen",
        "batch-42",
    )


def test_completed_claim_is_a_no_op() -> None:
    connection = FakeConnection([None, job_row("completed")])

    claim = claim_job_run(
        connection,  # type: ignore[arg-type]
        job_name="transportstyrelsen",
        batch_id="batch-42",
    )

    assert claim.should_execute is False
    assert claim.job_run.status == "completed"


def test_running_claim_is_not_stolen() -> None:
    connection = FakeConnection([None, job_row("running")])

    with pytest.raises(JobAlreadyRunningError, match="already running"):
        claim_job_run(
            connection,  # type: ignore[arg-type]
            job_name="transportstyrelsen",
            batch_id="batch-42",
        )


def test_failed_claim_is_reset_for_retry() -> None:
    connection = FakeConnection(
        [None, job_row("failed", processed=4, succeeded=3, failed=1), job_row("running")]
    )

    claim = claim_job_run(
        connection,  # type: ignore[arg-type]
        job_name="transportstyrelsen",
        batch_id="batch-42",
    )

    assert claim.should_execute is True
    assert claim.job_run.records_processed == 0
    assert "finished_at = NULL" in connection.cursor_instance.executed[-1][0]


@pytest.mark.parametrize(
    ("processed", "succeeded", "failed", "message"),
    [
        (-1, 0, -1, "negative"),
        (3, 1, 1, "must equal"),
    ],
)
def test_complete_rejects_invalid_counts(
    processed: int,
    succeeded: int,
    failed: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        complete_job_run(
            None,  # type: ignore[arg-type]
            7,
            records_processed=processed,
            records_succeeded=succeeded,
            records_failed=failed,
        )


def test_completion_is_idempotent_for_identical_counts() -> None:
    row = job_row("completed", processed=5, succeeded=4, failed=1)
    connection = FakeConnection([row])

    completed = complete_job_run(
        connection,  # type: ignore[arg-type]
        7,
        records_processed=5,
        records_succeeded=4,
        records_failed=1,
    )

    assert completed.status == "completed"
    assert len(connection.cursor_instance.executed) == 1


def test_failure_requires_bounded_sanitized_error_metadata() -> None:
    with pytest.raises(ValueError, match="error_code"):
        fail_job_run(
            None,  # type: ignore[arg-type]
            7,
            records_processed=1,
            records_succeeded=0,
            records_failed=1,
            error_code=" ",
            error_summary="Sanitized failure",
        )
