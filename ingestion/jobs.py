from dataclasses import dataclass
from ingestion.config import IngestionSettings
from ingestion.datastores import DatastoreClients


@dataclass(frozen=True)
class StubIngestionJob:
    name: str
    description: str

    def run(self, settings: IngestionSettings, datastores: DatastoreClients) -> int:
        _ = settings
        _ = datastores
        return 0


AVAILABLE_JOBS: tuple[StubIngestionJob, ...] = (
    StubIngestionJob(
        name="tecdoc",
        description="Stub TecDoc batch ingestion job.",
    ),
    StubIngestionJob(
        name="transportstyrelsen",
        description="Stub Transportstyrelsen batch ingestion job.",
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
