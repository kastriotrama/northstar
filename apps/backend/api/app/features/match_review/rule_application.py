"""Run a resolution rule as a tracked background job.

A build-scoped rule resolved a couple of hundred rows, so applying it inside the
request that asked for it was reasonable. Against the whole population the same
rule covers a couple of hundred thousand, which is roughly a minute of work -- too
long to hold a request open, and long enough that a reviewer needs to see it
moving rather than a spinner that might mean anything.

Progress is bookkept in `core.ingest_job_runs`, which already models a long unit
of work with a status, a running count and a terminal error, and whose
constraints refuse the states this must never reach. Nothing here is in memory,
so progress survives a reload of the page or a restart of the process -- the
work itself does not survive a restart, but its record does, and a rule can
simply be run again: applying only ever fills gaps.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from psycopg import Connection

from ingestion.job_bookkeeping_migrations import JOB_RUNS_TABLE
from ingestion.vehicle_facts_query import CompiledPredicate
from ingestion.vehicle_facts_rules import APPLY_JOB_NAME, apply_rule


class RuleAlreadyRunningError(RuntimeError):
    """This rule is already being applied; two runs would duplicate work."""


@dataclass(frozen=True)
class RuleApplication:
    """A single run of one rule, as the screen sees it."""

    job_id: int
    rule_id: UUID
    status: str
    rows_written: int
    started_at: datetime
    finished_at: datetime | None
    error_summary: str | None

    @property
    def running(self) -> bool:
        return self.status == "running"


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


def _row(row: tuple[Any, ...]) -> RuleApplication:
    # batch_id is "<rule_id>:<run_id>"; the rule is the half worth surfacing.
    rule_id = UUID(str(row[2]).split(":", 1)[0])
    return RuleApplication(
        job_id=int(row[0]),
        rule_id=rule_id,
        status=str(row[1]),
        rows_written=int(row[3]),
        started_at=row[4],
        finished_at=row[5],
        error_summary=row[6],
    )


_COLUMNS = (
    "id, status, batch_id, records_succeeded, started_at, finished_at, error_summary"
)


class RuleApplicationRunner:
    """Starts rule applications and reports on them."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def latest(self, rule_id: UUID) -> RuleApplication | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_COLUMNS} FROM {JOB_RUNS_TABLE} "
                "WHERE job_name = %s AND batch_id LIKE %s "
                "ORDER BY started_at DESC, id DESC LIMIT 1",
                (APPLY_JOB_NAME, f"{rule_id}:%"),
            )
            row = cursor.fetchone()
        return _row(row) if row else None

    def start(self, rule_id: UUID) -> RuleApplication:
        """Claim a run, refusing to start a second one for the same rule.

        The guard is a row in the table rather than a lock in the process, so it
        holds across workers and survives a restart of any one of them.
        """

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_COLUMNS} FROM {JOB_RUNS_TABLE} "
                "WHERE job_name = %s AND batch_id LIKE %s AND status = 'running' "
                "LIMIT 1",
                (APPLY_JOB_NAME, f"{rule_id}:%"),
            )
            running = cursor.fetchone()
            if running is not None:
                raise RuleAlreadyRunningError(
                    "This rule is already being applied. Wait for that run to finish."
                )
            cursor.execute(
                f"INSERT INTO {JOB_RUNS_TABLE} (job_name, batch_id) "
                f"VALUES (%s, %s) RETURNING {_COLUMNS}",
                (APPLY_JOB_NAME, f"{rule_id}:{uuid4()}"),
            )
            row = cursor.fetchone()
            connection.commit()
        if row is None:
            raise RuntimeError("could not claim a run for this rule")
        return _row(row)

    def run(
        self,
        *,
        job_id: int,
        rule_id: UUID,
        build_id: UUID,
        predicate: CompiledPredicate,
        target_field: str,
        target_value: str,
        applied_by: str,
        on_finish: Callable[[int], None] | None = None,
    ) -> None:
        """Do the work, keeping the job row current as batches land.

        Any failure is recorded on the row and swallowed: this runs detached
        from the request that asked for it, so raising would only reach a log,
        while the reviewer polling the job would see it hang at 'running'.
        """

        try:
            with self._connection_factory() as connection:

                def report(rows_written: int, _cursor: int) -> None:
                    self._progress(job_id, rows_written)

                summary = apply_rule(
                    connection,
                    rule_id=rule_id,
                    build_id=build_id,
                    predicate=predicate,
                    target_field=target_field,
                    target_value=target_value,
                    progress=report,
                )
            self._finish(job_id, summary.rows_written)
            if on_finish is not None:
                on_finish(summary.rows_written)
        except Exception as error:  # noqa: BLE001
            self._fail(job_id, type(error).__name__, str(error))

    def _progress(self, job_id: int, rows_written: int) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {JOB_RUNS_TABLE} SET records_processed = %s, "
                "records_succeeded = %s, updated_at = now() WHERE id = %s",
                (rows_written, rows_written, job_id),
            )
            connection.commit()

    def _finish(self, job_id: int, rows_written: int) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {JOB_RUNS_TABLE} SET status = 'completed', "
                "records_processed = %s, records_succeeded = %s, "
                "finished_at = now(), updated_at = now() WHERE id = %s",
                (rows_written, rows_written, job_id),
            )
            connection.commit()

    def _fail(self, job_id: int, code: str, summary: str) -> None:
        # The table caps these and refuses empties, so a bare exception with no
        # message must not be allowed to violate its own error-state constraint.
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {JOB_RUNS_TABLE} SET status = 'failed', "
                "error_code = %s, error_summary = %s, "
                "finished_at = now(), updated_at = now() WHERE id = %s",
                ((code or "Error")[:128], (summary or code or "failed")[:500], job_id),
            )
            connection.commit()
