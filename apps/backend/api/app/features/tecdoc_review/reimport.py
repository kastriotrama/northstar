"""Run a full TecDoc canonical reimport as a tracked background job.

A full reimport re-extracts every `.dat` file, re-derives every canonical
field (now reading `bodywork_form`/`drive_type` from the sealed
`core.tecdoc_rules` rows rather than a hardcoded dict -- see
`ingestion.tecdoc.reference_data`), and writes both the PostgreSQL candidate
catalog and the live Neo4j graph the matcher queries. That is many minutes of
work, so it cannot run inside the request that asks for it.

Progress is bookkept in `core.ingest_job_runs`
(`ingestion.job_bookkeeping_migrations`), the same table
`RuleApplicationRunner` (`api.app.features.match_review.rule_application`)
uses for its own long-running apply job. Unlike that runner, a reimport is
not scoped to one caller-supplied key (a rule id) -- only one reimport should
ever be in flight at all, since two concurrent runs would both try to write
the same graph -- so `start()` checks for *any* running row under this job
name rather than one keyed to the new batch it is about to create.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from ingestion.config import IngestionSettings, get_ingestion_settings
from ingestion.datastores import DatastoreClients
from ingestion.job_bookkeeping import complete_job_run, fail_job_run
from ingestion.job_bookkeeping_migrations import JOB_RUNS_TABLE, run_job_bookkeeping_migrations
from ingestion.tecdoc.promotion_job import run_full_canonical_promotion

REIMPORT_JOB_NAME = "tecdoc_full_reimport"

_COLUMNS = (
    "id, status, batch_id, records_processed, records_succeeded, "
    "started_at, finished_at, error_summary"
)


class TecDocReimportNotConfiguredError(RuntimeError):
    """TECDOC_SOURCE_PATH must point at an extracted `.dat` drop first."""


class TecDocReimportAlreadyRunningError(RuntimeError):
    """A reimport is already in flight; a second one would race the same graph write."""


ReimportState = Literal["running", "completed", "failed"]


@dataclass(frozen=True)
class TecDocReimportRun:
    """A single reimport run, as the screen sees it."""

    job_id: int
    batch_id: str
    status: ReimportState
    source_ktypes: int
    graph_rows_written: int
    started_at: datetime
    finished_at: datetime | None
    error_summary: str | None

    @property
    def running(self) -> bool:
        return self.status == "running"


def _row(row: tuple[Any, ...]) -> TecDocReimportRun:
    return TecDocReimportRun(
        job_id=int(row[0]),
        # ingest_job_runs_status_values constrains the column to exactly these three.
        status=cast(ReimportState, str(row[1])),
        batch_id=str(row[2]),
        source_ktypes=int(row[3]),
        graph_rows_written=int(row[4]),
        started_at=row[5],
        finished_at=row[6],
        error_summary=row[7],
    )


class SettingsFactory(Protocol):
    def __call__(self) -> IngestionSettings: ...


class TecDocReimportRunner:
    """Starts full reimports and reports on the latest one."""

    def __init__(self, settings_factory: SettingsFactory = get_ingestion_settings) -> None:
        self._settings_factory = settings_factory

    def latest(self) -> TecDocReimportRun | None:
        settings = self._settings_factory()
        datastores = DatastoreClients.from_settings(settings)
        with datastores.postgres.connect() as connection:
            run_job_bookkeeping_migrations(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {_COLUMNS} FROM {JOB_RUNS_TABLE} "
                    "WHERE job_name = %s ORDER BY started_at DESC, id DESC LIMIT 1",
                    (REIMPORT_JOB_NAME,),
                )
                row = cursor.fetchone()
        return _row(row) if row else None

    def start(self) -> TecDocReimportRun:
        """Claim a new run, refusing to start a second one while one is live."""

        settings = self._settings_factory()
        if not settings.tecdoc_source_path:
            raise TecDocReimportNotConfiguredError(
                "TECDOC_SOURCE_PATH is not set. Point it at the extracted .dat drop "
                "before reimporting."
            )
        batch_id = f"tecdoc-reimport-{datetime.now(UTC):%Y%m%d%H%M%S}-{uuid4().hex[:8]}"
        datastores = DatastoreClients.from_settings(settings)
        with datastores.postgres.connect() as connection:
            run_job_bookkeeping_migrations(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT 1 FROM {JOB_RUNS_TABLE} "
                    "WHERE job_name = %s AND status = 'running' LIMIT 1",
                    (REIMPORT_JOB_NAME,),
                )
                if cursor.fetchone() is not None:
                    raise TecDocReimportAlreadyRunningError(
                        "A reimport is already running. Wait for it to finish."
                    )
                cursor.execute(
                    f"INSERT INTO {JOB_RUNS_TABLE} (job_name, batch_id) "
                    f"VALUES (%s, %s) RETURNING {_COLUMNS}",
                    (REIMPORT_JOB_NAME, batch_id),
                )
                row = cursor.fetchone()
            connection.commit()
        if row is None:
            raise RuntimeError("could not claim a reimport run")
        return _row(row)

    def run(self, *, job_id: int, batch_id: str) -> None:
        """Do the work, recording the outcome on the claimed job row.

        Runs detached from the request that started it: any failure is caught
        and written to the job row rather than raised, since raising here
        would only reach a log while the screen polling `latest()` sat at
        "running" forever.
        """

        settings = self._settings_factory()
        source_directory = Path(settings.tecdoc_source_path)  # type: ignore[arg-type]
        try:
            datastores = DatastoreClients.from_settings(settings)
            with (
                datastores.postgres.connect() as connection,
                datastores.neo4j.driver() as driver,
            ):
                summary = run_full_canonical_promotion(
                    connection,
                    driver,
                    source_directory=source_directory,
                    reference_directory=source_directory,
                    batch_id=batch_id,
                    source_version=settings.tecdoc_source_version or "unknown",
                    format_version=settings.tecdoc_format_version or "unknown",
                    source_checksum=settings.tecdoc_source_checksum or "unset",
                    license_reference=settings.tecdoc_license_reference,
                    write_graph=True,
                )
                complete_job_run(
                    connection,
                    job_id,
                    records_processed=summary.source_ktypes,
                    records_succeeded=summary.graph_rows_written,
                    records_failed=summary.source_ktypes - summary.eligible_ktypes,
                )
        except Exception as error:  # noqa: BLE001
            datastores = DatastoreClients.from_settings(settings)
            with datastores.postgres.connect() as connection:
                fail_job_run(
                    connection,
                    job_id,
                    records_processed=0,
                    records_succeeded=0,
                    records_failed=0,
                    error_code=type(error).__name__,
                    error_summary=str(error)[:500] or type(error).__name__,
                )
