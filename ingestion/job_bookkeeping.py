"""Retry-safe ingest job-run bookkeeping operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from psycopg import Connection

from ingestion.job_bookkeeping_migrations import JOB_RUNS_TABLE

JobRunStatus = Literal["running", "completed", "failed"]


class JobAlreadyRunningError(RuntimeError):
    """Raised when another execution already owns the job/batch pair."""


@dataclass(frozen=True)
class JobRun:
    """Durable state for one job name and source batch."""

    id: int
    job_name: str
    batch_id: str
    status: JobRunStatus
    records_processed: int
    records_succeeded: int
    records_failed: int
    error_code: str | None
    error_summary: str | None
    started_at: datetime
    finished_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True)
class JobRunClaim:
    """Result of claiming work for a job/batch pair."""

    job_run: JobRun
    should_execute: bool


def claim_job_run(
    connection: Connection,
    *,
    job_name: str,
    batch_id: str,
) -> JobRunClaim:
    """Claim a batch or return a no-op claim when it already completed.

    Failed batches may be retried. A running row is never stolen because that
    could create concurrent duplicate writes. The caller owns the transaction
    and should commit the claim before doing long-running work.
    """

    normalized_job = _required_text(job_name, "job_name")
    normalized_batch = _required_text(batch_id, "batch_id")
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {JOB_RUNS_TABLE} (job_name, batch_id) "
            "VALUES (%s, %s) ON CONFLICT (job_name, batch_id) DO NOTHING "
            f"RETURNING {_RETURNING_COLUMNS}",
            (normalized_job, normalized_batch),
        )
        inserted = cursor.fetchone()
        if inserted is not None:
            return JobRunClaim(_row_to_job_run(inserted), should_execute=True)

        cursor.execute(
            f"SELECT {_RETURNING_COLUMNS} FROM {JOB_RUNS_TABLE} "
            "WHERE job_name = %s AND batch_id = %s FOR UPDATE",
            (normalized_job, normalized_batch),
        )
        existing_row = cursor.fetchone()
        if existing_row is None:
            raise RuntimeError("job-run conflict returned no existing row")
        existing = _row_to_job_run(existing_row)
        if existing.status == "completed":
            return JobRunClaim(existing, should_execute=False)
        if existing.status == "running":
            raise JobAlreadyRunningError(
                f"job {normalized_job!r} batch {normalized_batch!r} is already running"
            )

        cursor.execute(
            f"UPDATE {JOB_RUNS_TABLE} SET status = 'running', "
            "records_processed = 0, records_succeeded = 0, records_failed = 0, "
            "error_code = NULL, error_summary = NULL, "
            "started_at = now(), finished_at = NULL, updated_at = now() "
            f"WHERE id = %s RETURNING {_RETURNING_COLUMNS}",
            (existing.id,),
        )
        retried = cursor.fetchone()
    if retried is None:
        raise RuntimeError(f"job run {existing.id} disappeared during retry")
    return JobRunClaim(_row_to_job_run(retried), should_execute=True)


def complete_job_run(
    connection: Connection,
    job_run_id: int,
    *,
    records_processed: int,
    records_succeeded: int,
    records_failed: int,
) -> JobRun:
    """Mark a running job complete, idempotently for identical final counts."""

    _validate_counts(records_processed, records_succeeded, records_failed)
    return _finish_job_run(
        connection,
        job_run_id,
        status="completed",
        records_processed=records_processed,
        records_succeeded=records_succeeded,
        records_failed=records_failed,
        error_code=None,
        error_summary=None,
    )


def fail_job_run(
    connection: Connection,
    job_run_id: int,
    *,
    records_processed: int,
    records_succeeded: int,
    records_failed: int,
    error_code: str,
    error_summary: str,
) -> JobRun:
    """Mark a running job failed with a caller-sanitized operational summary."""

    _validate_counts(records_processed, records_succeeded, records_failed)
    normalized_error_code = _bounded_text(error_code, "error_code", max_length=128)
    normalized_error_summary = _bounded_text(
        error_summary,
        "error_summary",
        max_length=500,
    )
    return _finish_job_run(
        connection,
        job_run_id,
        status="failed",
        records_processed=records_processed,
        records_succeeded=records_succeeded,
        records_failed=records_failed,
        error_code=normalized_error_code,
        error_summary=normalized_error_summary,
    )


def fetch_job_run(
    connection: Connection,
    *,
    job_name: str,
    batch_id: str,
) -> JobRun | None:
    """Fetch one job/batch record."""

    normalized_job = _required_text(job_name, "job_name")
    normalized_batch = _required_text(batch_id, "batch_id")
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {_RETURNING_COLUMNS} FROM {JOB_RUNS_TABLE} "
            "WHERE job_name = %s AND batch_id = %s",
            (normalized_job, normalized_batch),
        )
        row = cursor.fetchone()
    return None if row is None else _row_to_job_run(row)


def _finish_job_run(
    connection: Connection,
    job_run_id: int,
    *,
    status: Literal["completed", "failed"],
    records_processed: int,
    records_succeeded: int,
    records_failed: int,
    error_code: str | None,
    error_summary: str | None,
) -> JobRun:
    if job_run_id < 1:
        raise ValueError("job_run_id must be at least 1")
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {_RETURNING_COLUMNS} FROM {JOB_RUNS_TABLE} WHERE id = %s FOR UPDATE",
            (job_run_id,),
        )
        current_row = cursor.fetchone()
        if current_row is None:
            raise KeyError(f"job run {job_run_id} does not exist")
        current = _row_to_job_run(current_row)
        requested_state = (
            records_processed,
            records_succeeded,
            records_failed,
            error_code,
            error_summary,
        )
        existing_state = (
            current.records_processed,
            current.records_succeeded,
            current.records_failed,
            current.error_code,
            current.error_summary,
        )
        if current.status == status:
            if existing_state == requested_state:
                return current
            raise ValueError(f"{status} job run already has different final counts")
        if current.status != "running":
            raise ValueError(f"cannot mark job run {status} from status {current.status}")
        cursor.execute(
            f"UPDATE {JOB_RUNS_TABLE} SET status = %s, "
            "records_processed = %s, records_succeeded = %s, records_failed = %s, "
            "error_code = %s, error_summary = %s, "
            "finished_at = now(), updated_at = now() "
            f"WHERE id = %s RETURNING {_RETURNING_COLUMNS}",
            (
                status,
                records_processed,
                records_succeeded,
                records_failed,
                error_code,
                error_summary,
                job_run_id,
            ),
        )
        finished = cursor.fetchone()
    if finished is None:
        raise RuntimeError(f"job run {job_run_id} disappeared during completion")
    return _row_to_job_run(finished)


_RETURNING_COLUMNS = (
    "id, job_name, batch_id, status, records_processed, records_succeeded, "
    "records_failed, error_code, error_summary, started_at, finished_at, updated_at"
)


def _row_to_job_run(row: tuple[Any, ...]) -> JobRun:
    return JobRun(
        id=int(row[0]),
        job_name=str(row[1]),
        batch_id=str(row[2]),
        status=row[3],
        records_processed=int(row[4]),
        records_succeeded=int(row[5]),
        records_failed=int(row[6]),
        error_code=None if row[7] is None else str(row[7]),
        error_summary=None if row[8] is None else str(row[8]),
        started_at=row[9],
        finished_at=row[10],
        updated_at=row[11],
    )


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _bounded_text(value: str, field_name: str, *, max_length: int) -> str:
    normalized = _required_text(value, field_name)
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} must be at most {max_length} characters")
    return normalized


def _validate_counts(
    records_processed: int,
    records_succeeded: int,
    records_failed: int,
) -> None:
    if min(records_processed, records_succeeded, records_failed) < 0:
        raise ValueError("job-run counts must not be negative")
    if records_processed != records_succeeded + records_failed:
        raise ValueError("records_processed must equal records_succeeded plus records_failed")
