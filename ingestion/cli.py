import argparse
import logging
from collections.abc import Sequence

from ingestion.config import get_ingestion_settings
from ingestion.datastores import DatastoreClients
from ingestion.jobs import get_job, list_jobs
from ingestion.logging import configure_logging

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="northstar-ingest",
        description="NorthStar batch ingestion service.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-commands", help="List available ingestion commands.")

    for job in list_jobs():
        subparsers.add_parser(job.name, help=job.description)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = get_ingestion_settings()
    configure_logging(settings.log_level)

    if args.command == "list-commands":
        for job in list_jobs():
            print(f"{job.name}\t{job.description}")
        return 0

    datastores = DatastoreClients.from_settings(settings)
    job = get_job(args.command)

    logger.info("Starting ingestion job: %s", job.name)
    exit_code = job.run(settings=settings, datastores=datastores)
    logger.info("Finished ingestion job: %s", job.name)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

