import argparse
import json
import logging
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from ingestion.config import get_ingestion_settings
from ingestion.datastores import DatastoreClients
from ingestion.jobs import get_job, list_jobs
from ingestion.logging import configure_logging
from ingestion.normalization_bundle import import_normalization_bundle

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="northstar-ingest",
        description="NorthStar batch ingestion service.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-commands", help="List available ingestion commands.")

    bundle_parser = subparsers.add_parser(
        "import-normalization-bundle",
        help="Populate PostgreSQL from a validated normalization Excel bundle.",
    )
    bundle_parser.add_argument(
        "--file",
        type=Path,
        required=True,
        help="Path to the portable normalization .xlsx bundle.",
    )

    for job in list_jobs():
        job_parser = subparsers.add_parser(job.name, help=job.description)
        job_parser.add_argument(
            "--batch-id",
            default=None,
            required=job.name == "normalize",
            help=(
                "Required source staging batch identifier."
                if job.name == "normalize"
                else "Optional batch identifier for logs and job bookkeeping."
            ),
        )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = get_ingestion_settings()
    configure_logging(settings.log_level)

    if args.command == "list-commands":
        print(
            "import-normalization-bundle\tportable_normalization_bundle\t"
            "Populate PostgreSQL from a validated normalization Excel bundle."
        )
        for job in list_jobs():
            print(f"{job.name}\t{job.source_name}\t{job.description}")
        return 0

    if args.command == "import-normalization-bundle":
        datastores = DatastoreClients.from_settings(settings)
        try:
            with datastores.postgres.connect() as connection:
                summary = import_normalization_bundle(connection, args.file)
        except Exception as error:  # noqa: BLE001
            logger.error(
                "Normalization bundle import stopped safely",
                extra={
                    "job_name": args.command,
                    "source": "portable_normalization_bundle",
                    "error_code": type(error).__name__,
                },
            )
            return 1
        print(json.dumps(asdict(summary), sort_keys=True))
        return 0

    datastores = DatastoreClients.from_settings(settings)
    job = get_job(args.command)
    batch_id = args.batch_id or f"{job.name}-{uuid4()}"

    logger.info(
        "Starting ingestion job",
        extra={"job_name": job.name, "batch_id": batch_id, "source": job.source_name},
    )
    exit_code = job.run(settings=settings, datastores=datastores, batch_id=batch_id)
    logger.info(
        "Finished ingestion job",
        extra={
            "job_name": job.name,
            "batch_id": batch_id,
            "source": job.source_name,
            "exit_code": exit_code,
        },
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
