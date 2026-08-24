import logging
from dataclasses import dataclass
from typing import Protocol

from ingestion.active_rules import load_active_rules
from ingestion.confidence_routing_migrations import run_confidence_routing_migrations
from ingestion.config import IngestionSettings
from ingestion.datastores import DatastoreClients
from ingestion.graph_migrations import run_graph_migrations
from ingestion.job_bookkeeping_migrations import run_job_bookkeeping_migrations
from ingestion.ledger_migrations import run_ledger_migrations
from ingestion.match_run_migrations import run_match_run_migrations
from ingestion.normalization_migrations import run_normalization_migrations
from ingestion.normalization_service import normalize_batch
from ingestion.review_queue_migrations import run_review_queue_migrations
from ingestion.staging_migrations import run_staging_migrations
from ingestion.tecdoc.extraction import extract_vehicle_tree
from ingestion.tecdoc.migrations import run_tecdoc_migrations
from ingestion.tecdoc.service import ingest_tecdoc_vehicle_tree

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
class MigrateLedgerJob:
    """Apply idempotent enrichment-ledger migrations before graph writes."""

    name: str = "migrate-ledger"
    description: str = "Apply enrichment-ledger migrations (idempotent, append-only enforced)."
    source_name: str = "system"

    def run(
        self,
        settings: IngestionSettings,
        datastores: DatastoreClients,
        batch_id: str,
    ) -> int:
        _ = settings
        with datastores.postgres.connect() as connection:
            applied = run_ledger_migrations(connection)
        logger.info(
            "Ledger migrations applied",
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
class MigrateReviewQueueJob:
    """Apply the durable normalization review-queue schema."""

    name: str = "migrate-review-queue"
    description: str = "Apply review-queue migrations (idempotent, contract verified)."
    source_name: str = "system"

    def run(
        self,
        settings: IngestionSettings,
        datastores: DatastoreClients,
        batch_id: str,
    ) -> int:
        _ = settings
        with datastores.postgres.connect() as connection:
            applied = run_review_queue_migrations(connection)
        logger.info(
            "Review queue migrations applied",
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
class MigrateJobBookkeepingJob:
    """Apply the durable ingest-job bookkeeping schema."""

    name: str = "migrate-job-bookkeeping"
    description: str = "Apply ingest-job bookkeeping migrations (idempotent)."
    source_name: str = "system"

    def run(
        self,
        settings: IngestionSettings,
        datastores: DatastoreClients,
        batch_id: str,
    ) -> int:
        _ = settings
        with datastores.postgres.connect() as connection:
            applied = run_job_bookkeeping_migrations(connection)
        logger.info(
            "Job-bookkeeping migrations applied",
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
class MigrateConfidenceRoutingJob:
    """Apply the durable Stage 2 confidence-routing schema."""

    name: str = "migrate-confidence-routing"
    description: str = "Apply confidence-routing decision migrations (idempotent)."
    source_name: str = "system"

    def run(
        self,
        settings: IngestionSettings,
        datastores: DatastoreClients,
        batch_id: str,
    ) -> int:
        _ = settings
        with datastores.postgres.connect() as connection:
            applied = run_confidence_routing_migrations(connection)
        logger.info(
            "Confidence-routing migrations applied",
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
class MigrateMatchRunsJob:
    """Apply durable full-cohort matching run/checkpoint storage."""

    name: str = "migrate-match-runs"
    description: str = "Apply full TS-to-TecDoc match-run checkpoint migrations."
    source_name: str = "system"

    def run(
        self,
        settings: IngestionSettings,
        datastores: DatastoreClients,
        batch_id: str,
    ) -> int:
        _ = settings
        with datastores.postgres.connect() as connection:
            applied = run_match_run_migrations(connection)
        logger.info(
            "Match-run migrations applied",
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


@dataclass(frozen=True)
class NormalizeTransportstyrelsenJob:
    """Normalize one explicitly selected Transportstyrelsen staging batch."""

    name: str = "normalize"
    description: str = "Normalize an existing Transportstyrelsen staging batch."
    source_name: str = "Transportstyrelsen"

    def run(
        self,
        settings: IngestionSettings,
        datastores: DatastoreClients,
        batch_id: str,
    ) -> int:
        _ = settings
        try:
            with datastores.postgres.connect() as connection:
                run_staging_migrations(connection)
                run_review_queue_migrations(connection)
                run_job_bookkeeping_migrations(connection)
                run_normalization_migrations(connection)
                rule_set, manufacturer_entity_rules = load_active_rules(connection)
                summary = normalize_batch(
                    connection,
                    batch_id=batch_id,
                    rule_set=rule_set,
                    manufacturer_entity_rules=manufacturer_entity_rules,
                )
        # This is the outer job boundary: every unexpected provider failure is
        # converted into a sanitized exit code instead of leaking details.
        except Exception as error:  # noqa: BLE001
            logger.error(
                "Normalization job stopped safely",
                extra={
                    "job_name": self.name,
                    "batch_id": batch_id,
                    "source": self.source_name,
                    "error_code": type(error).__name__,
                },
            )
            return 1
        logger.info(
            "Normalization job completed",
            extra={
                "job_name": self.name,
                "batch_id": batch_id,
                "source": self.source_name,
                "processed": summary.processed,
                "resolved": summary.resolved,
                "provisional": summary.provisional,
                "review_required": summary.review_required,
                "failed": summary.failed,
                "already_completed": summary.already_completed,
            },
        )
        return 0


@dataclass(frozen=True)
class IngestTecDocJob:
    """Restore-contract ingestion for one versioned TecDoc vehicle tree."""

    name: str = "tecdoc"
    description: str = "Ingest a restored, versioned TecDoc vehicle-tree batch."
    source_name: str = "TecDoc"

    def run(
        self,
        settings: IngestionSettings,
        datastores: DatastoreClients,
        batch_id: str,
    ) -> int:
        required = {
            "TECDOC_SOURCE_PATH": settings.tecdoc_source_path,
            "TECDOC_SOURCE_VERSION": settings.tecdoc_source_version,
            "TECDOC_FORMAT_VERSION": settings.tecdoc_format_version,
            "TECDOC_SOURCE_CHECKSUM": settings.tecdoc_source_checksum,
        }
        missing = sorted(name for name, value in required.items() if not value)
        if missing:
            logger.error(
                "TecDoc ingestion configuration is incomplete",
                extra={"job_name": self.name, "missing_settings": missing},
            )
            return 2
        try:
            with datastores.postgres.connect() as connection:
                run_staging_migrations(connection)
                run_ledger_migrations(connection)
                run_tecdoc_migrations(connection)
                rows = extract_vehicle_tree(
                    connection,
                    source_schema=settings.tecdoc_source_schema,
                    fetch_size=settings.ingestion_batch_size,
                )
                summary = ingest_tecdoc_vehicle_tree(
                    connection,
                    rows=rows,
                    batch_id=batch_id,
                    source_version=settings.tecdoc_source_version or "",
                    format_version=settings.tecdoc_format_version or "",
                    license_reference=settings.tecdoc_license_reference,
                    source_path=settings.tecdoc_source_path or "",
                    source_checksum=settings.tecdoc_source_checksum or "",
                )
        except Exception as error:  # noqa: BLE001
            logger.error(
                "TecDoc ingestion stopped safely",
                extra={"job_name": self.name, "error_code": type(error).__name__},
            )
            return 1
        logger.info(
            "TecDoc ingestion completed",
            extra={
                "job_name": self.name,
                "batch_id": batch_id,
                "source_rows": summary.source_rows,
                "unique_ktypes": summary.unique_ktypes,
                "candidates_written": summary.candidates_written,
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
    MigrateLedgerJob(),
    MigrateReviewQueueJob(),
    MigrateJobBookkeepingJob(),
    MigrateConfidenceRoutingJob(),
    MigrateMatchRunsJob(),
    StubIngestionJob(
        name="load",
        description="Stub raw source loading command.",
        source_name="pipeline",
    ),
    NormalizeTransportstyrelsenJob(),
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
    IngestTecDocJob(),
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
