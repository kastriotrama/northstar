import argparse
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from ingestion.config import get_ingestion_settings
from ingestion.datastores import DatastoreClients
from ingestion.jobs import get_job, list_jobs
from ingestion.logging import configure_logging
from ingestion.normalization_bundle import import_normalization_bundle
from ingestion.rule_delta import export_rule_delta
from scripts.import_remote_passenger_reviews import (
    DEFAULT_IMPORT_PREFIX,
    EXPECTED_PASSENGER_COUNT,
    run as run_remote_passenger_import,
)
from ingestion.tecdoc.promotion_job import run_full_canonical_promotion

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

    delta_parser = subparsers.add_parser(
        "export-rule-delta",
        help="Export immutable reviewed-rule versions as guarded SQL.",
    )
    remote_parser = subparsers.add_parser(
        "import-remote-passenger",
        help="Resume the contract-checked VD-AI passenger import.",
    )
    remote_parser.add_argument("--prefix", default=DEFAULT_IMPORT_PREFIX)
    remote_parser.add_argument("--batch-size", type=int, default=25_000)
    remote_parser.add_argument("--expected-source-count", type=int, default=EXPECTED_PASSENGER_COUNT)
    remote_parser.add_argument("--retain-raw", action="store_true")
    remote_parser.add_argument("--recover-stale-part", action="store_true")
    delta_parser.add_argument(
        "--baseline-version",
        required=True,
        help="Immutable rule version that the target environment must already contain.",
    )

    promotion_parser = subparsers.add_parser(
        "promote-tecdoc-canonical",
        help="Promote every safe passenger-car KType to PostgreSQL and Neo4j.",
    )
    promotion_parser.add_argument("--batch-id", required=True)
    promotion_parser.add_argument("--source-path", type=Path, required=True)
    promotion_parser.add_argument("--reference-path", type=Path, required=True)
    promotion_parser.add_argument("--source-version", default="0326")
    promotion_parser.add_argument("--format-version", default="2.70")
    promotion_parser.add_argument("--source-checksum", required=True)
    promotion_parser.add_argument("--chunk-size", type=int, default=500)
    delta_parser.add_argument(
        "--target-version",
        default=None,
        help="Immutable target version; defaults to the latest active database version.",
    )
    delta_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Destination .sql path.",
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
        print(
            "export-rule-delta\ttranslation_rule_versions\t"
            "Export a deterministic guarded SQL rule delta."
        )
        print(
            "import-remote-passenger\tVD-AI PostgreSQL\t"
            "Resume the contract-checked full passenger import."
        )
        for job in list_jobs():
            print(f"{job.name}\t{job.source_name}\t{job.description}")
        return 0

    if args.command == "import-normalization-bundle":
        datastores = DatastoreClients.from_settings(settings)
        try:
            with datastores.postgres.connect() as connection:
                bundle_summary = import_normalization_bundle(connection, args.file)
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
        print(json.dumps(asdict(bundle_summary), sort_keys=True))
        return 0

    if args.command == "export-rule-delta":
        datastores = DatastoreClients.from_settings(settings)
        try:
            with datastores.postgres.connect() as connection:
                delta_summary = export_rule_delta(
                    connection,
                    baseline_version=args.baseline_version,
                    target_version=args.target_version,
                    output_path=args.output,
                )
        except Exception as error:  # noqa: BLE001
            logger.error(
                "Rule-delta export stopped safely",
                extra={
                    "job_name": args.command,
                    "source": "translation_rule_versions",
                    "error_code": type(error).__name__,
                },
            )
            return 1
        print(json.dumps(asdict(delta_summary), sort_keys=True))
        return 0

    if args.command == "import-remote-passenger":
        if not settings.remote_database_url:
            logger.error(
                "Remote passenger import stopped safely",
                extra={"error_code": "RemoteDatabaseUrlMissing"},
            )
            return 2
        os.environ["REMOTE_DATABASE_URL"] = settings.remote_database_url
        os.environ["DATABASE_URL"] = settings.database_url
        try:
            run_remote_passenger_import(
                prefix=args.prefix,
                batch_size=args.batch_size,
                retain_raw=args.retain_raw,
                expected_source_count=args.expected_source_count,
                recover_stale=args.recover_stale_part,
            )
        except Exception as error:  # noqa: BLE001
            logger.error(
                "Remote passenger import stopped safely",
                extra={"error_code": type(error).__name__},
            )
            return 1
        return 0

    if args.command == "promote-tecdoc-canonical":
        datastores = DatastoreClients.from_settings(settings)
        try:
            with datastores.postgres.connect() as connection, datastores.neo4j.driver() as driver:
                summary = run_full_canonical_promotion(
                    connection, driver, source_directory=args.source_path,
                    reference_directory=args.reference_path, batch_id=args.batch_id,
                    source_version=args.source_version, format_version=args.format_version,
                    source_checksum=args.source_checksum,
                    license_reference=settings.tecdoc_license_reference,
                    chunk_size=args.chunk_size,
                )
        except Exception as error:  # noqa: BLE001
            logger.error("TecDoc canonical promotion stopped safely", extra={"error_code": type(error).__name__})
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
