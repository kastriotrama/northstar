import logging
from dataclasses import dataclass

from ingestion.config import IngestionSettings
from ingestion.datastores import DatastoreClients

logger = logging.getLogger(__name__)


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


AVAILABLE_JOBS: tuple[StubIngestionJob, ...] = (
    StubIngestionJob(
        name="healthcheck",
        description="Stub ingestion dependency healthcheck command.",
        source_name="system",
    ),
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


def list_jobs() -> tuple[StubIngestionJob, ...]:
    return AVAILABLE_JOBS


def get_job(command_name: str) -> StubIngestionJob:
    for job in AVAILABLE_JOBS:
        if job.name == command_name:
            return job

    message = f"Unknown ingestion job: {command_name}"
    raise ValueError(message)
