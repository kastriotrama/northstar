import logging
from dataclasses import dataclass
from typing import Protocol

from ingestion.config import IngestionSettings
from ingestion.datastores import DatastoreClients
from ingestion.graph_migrations import run_graph_migrations
from ingestion.staging_migrations import run_staging_migrations

logger = logging.getLogger(__name__)


class IngestionJob(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def source_name(self) -> str: ...

    def run(
        self,
        settings: IngestionSettings,
        datastores: DatastoreClients,
        batch_id: str,
    ) -> int:
        """Run the job and return an exit code."""
        ...


@dataclass(frozen=True)
class MigrateGraphJob:
    """Apply idempotent Neo4j constraint and index migrations before load."""

    name: str = "migrate-graph"
    description: str = "Apply Neo4j constraint and index migrations (idempotent)."
    source_name: str = "system"

    def run(
        self,
        settings: IngestionSettings,
        datastores: DatastoreClients,
        batch_id: str,
    ) -> int:
        _ = settings
        with datastores.neo4j.driver() as driver:
            applied = run_graph_migrations(driver)
        logger.info(
            "Graph migrations applied",
            extra={
                "job_name": self.name,
                "batch_id": batch_id,
                "source": self.source_name,
                "statements_applied": len(applied),
                "statement_names": list(applied),
            },
        )
        return 0


@dataclass(frozen=True)
class MigrateStagingJob:
    """Apply idempotent PostgreSQL staging schema migrations before load."""

    name: str = "migrate-staging"
    description: str = "Apply PostgreSQL staging schema migrations (idempotent)."
    source_name: str = "system"

    def run(
        self,
        settings: IngestionSettings,
        datastores: DatastoreClients,
        batch_id: str,
    ) -> int:
        _ = settings
        with datastores.postgres.connect() as connection:
            applied = run_staging_migrations(connection)
        logger.info(
            "Staging migrations applied",
            extra={
                "job_name": self.name,
                "batch_id": batch_id,
                "source": self.source_name,
                "statements_applied": len(applied),
                "statement_names": list(applied),
            },
        )
        return 0


@dataclass(frozen=True)
class StubIngestionJob:
    name: str
    description: str
    source_name: str

    def run(
        self,
        settings: IngestionSettings,
        datastores: DatastoreClients,
        batch_id: str,
    ) -> int:
        _ = settings
        _ = datastores
        logger.info(
            "Stub ingestion job completed without importing records",
            extra={
                "job_name": self.name,
                "batch_id": batch_id,
                "source": self.source_name,
                "status": "stubbed",
                "records_processed": 0,
            },
        )
        return 0


AVAILABLE_JOBS: tuple[IngestionJob, ...] = (
    StubIngestionJob(
        name="healthcheck",
        description="Stub ingestion dependency healthcheck command.",
        source_name="system",
    ),
    MigrateGraphJob(),
    MigrateStagingJob(),
    StubIngestionJob(
        name="load",
        description="Stub raw source loading command.",
        source_name="pipeline",
    ),
    StubIngestionJob(
        name="normalize",
        description="Stub source normalization command.",
        source_name="pipeline",
    ),
    StubIngestionJob(
        name="graph-write",
        description="Stub graph write command.",
        source_name="pipeline",
    ),
    StubIngestionJob(
        name="index",
        description="Stub search indexing command.",
        source_name="pipeline",
    ),
    StubIngestionJob(
        name="tecdoc",
        description="Stub TecDoc batch ingestion job.",
        source_name="TecDoc",
    ),
    StubIngestionJob(
        name="transportstyrelsen",
        description="Stub Transportstyrelsen batch ingestion job.",
        source_name="Transportstyrelsen",
    ),
)


def list_jobs() -> tuple[IngestionJob, ...]:
    return AVAILABLE_JOBS


def get_job(command_name: str) -> IngestionJob:
    for job in AVAILABLE_JOBS:
        if job.name == command_name:
            return job

    message = f"Unknown ingestion job: {command_name}"
    raise ValueError(message)
