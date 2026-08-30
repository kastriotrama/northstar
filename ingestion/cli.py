import argparse
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from uuid import UUID, uuid4

from ingestion.active_rules import load_active_rules
from ingestion.config import get_ingestion_settings
from ingestion.datastores import DatastoreClients
from ingestion.jobs import get_job, list_jobs
from ingestion.logging import configure_logging
from ingestion.match_run_migrations import run_match_run_migrations
from ingestion.match_run_repository import MatchRunPins
from ingestion.match_run_service import run_dry_match_audit
from ingestion.normalization_bundle import import_normalization_bundle
from ingestion.rule_delta import export_rule_delta
from ingestion.tecdoc.match_run_adapters import (
    TecDocDryRunEvaluator,
    fetch_normalized_ts_page,
    load_ktype_catalog,
    load_postgres_ktype_catalog,
)
from ingestion.tecdoc.model_aliases import ReviewedModelAliasIndex
from ingestion.tecdoc.promotion_job import run_full_canonical_promotion
from ingestion.tecdoc.remote_match_run import run_local_raw_dry_match_audit
from ingestion.vocabulary_alignment import (
    fetch_approved_alignments,
    link_variants_to_fuel_concepts,
    load_fuel_alignment,
    promote_vocabulary_alignments,
)
from ingestion.vocabulary_migrations import run_vocabulary_migrations
from ingestion.vocabulary_seed import INITIAL_FUEL_ALIGNMENT_VERSION, apply_vocabulary_seed
from scripts.import_remote_passenger_reviews import (
    DEFAULT_IMPORT_PREFIX,
    EXPECTED_PASSENGER_COUNT,
)
from scripts.import_remote_passenger_reviews import (
    run as run_remote_passenger_import,
)

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
    remote_parser.add_argument(
        "--expected-source-count", type=int, default=EXPECTED_PASSENGER_COUNT
    )
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
    promotion_parser.add_argument(
        "--candidate-catalog-only",
        action="store_true",
        help=(
            "Build and reconcile the immutable PostgreSQL candidate catalog "
            "without writing canonical vehicles to Neo4j."
        ),
    )

    vocabulary_parser = subparsers.add_parser(
        "promote-vocabulary-alignments",
        help=(
            "Activate a reviewed vocabulary alignment set and materialise it "
            "in the graph. Writes nothing without --commit."
        ),
    )
    vocabulary_parser.add_argument(
        "--alignment-version", default=INITIAL_FUEL_ALIGNMENT_VERSION
    )
    vocabulary_parser.add_argument("--vocabulary", default="fuel")
    vocabulary_parser.add_argument(
        "--activated-by", required=True, help="Actor accountable for the activation."
    )
    vocabulary_parser.add_argument(
        "--commit",
        action="store_true",
        help="Apply the seed and write the graph. Omitted means dry run.",
    )

    match_parser = subparsers.add_parser(
        "match-ts-tecdoc",
        help="Run a version-pinned, write-free TS-to-TecDoc matching audit.",
    )
    match_parser.add_argument("--operation-id", type=UUID, required=True)
    match_parser.add_argument("--source-version", required=True)
    match_parser.add_argument("--source-batch-prefix", required=True)
    match_parser.add_argument("--expected-source-rows", type=int, required=True)
    match_parser.add_argument("--normalization-rule-version", required=True)
    match_parser.add_argument("--candidate-catalog-version", required=True)
    match_parser.add_argument(
        "--candidate-source",
        choices=("neo4j", "postgres"),
        default="neo4j",
        help="Load the pinned KType catalog from Neo4j or a PostgreSQL batch ID.",
    )
    match_parser.add_argument("--expected-ktype-count", type=int, required=True)
    match_parser.add_argument("--policy-version", required=True)
    match_parser.add_argument(
        "--alignment-version",
        default="unpinned-legacy",
        help=(
            "Vocabulary alignment set governing how TS and TecDoc terms are "
            "compared. Defaults to the pre-alignment sentinel so existing runs "
            "stay reproducible; pass a real version once alignments are used."
        ),
    )
    match_parser.add_argument("--code-revision", required=True)
    match_parser.add_argument("--page-size", type=int, default=25_000)
    match_parser.add_argument(
        "--source-mode",
        choices=("normalized", "raw"),
        default="normalized",
        help="Read pinned saved normalization results or normalize retained raw rows.",
    )
    match_parser.add_argument(
        "--max-batches",
        type=int,
        default=None,
        help="Stop cleanly after this many new batches for controlled validation.",
    )
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
        print(
            "match-ts-tecdoc\tTransportstyrelsen+TecDoc\t"
            "Run a version-pinned, write-free TS-to-TecDoc matching audit."
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
            with datastores.postgres.connect() as connection:
                if args.candidate_catalog_only:
                    summary = run_full_canonical_promotion(
                        connection,
                        None,
                        source_directory=args.source_path,
                        reference_directory=args.reference_path,
                        batch_id=args.batch_id,
                        source_version=args.source_version,
                        format_version=args.format_version,
                        source_checksum=args.source_checksum,
                        license_reference=settings.tecdoc_license_reference,
                        chunk_size=args.chunk_size,
                        write_graph=False,
                    )
                else:
                    with datastores.neo4j.driver() as driver:
                        summary = run_full_canonical_promotion(
                            connection,
                            driver,
                            source_directory=args.source_path,
                            reference_directory=args.reference_path,
                            batch_id=args.batch_id,
                            source_version=args.source_version,
                            format_version=args.format_version,
                            source_checksum=args.source_checksum,
                            license_reference=settings.tecdoc_license_reference,
                            chunk_size=args.chunk_size,
                        )
        except Exception as error:  # noqa: BLE001
            logger.error(
                "TecDoc canonical promotion stopped safely",
                extra={"error_code": type(error).__name__},
            )
            return 1
        print(json.dumps(asdict(summary), sort_keys=True))
        return 0

    if args.command == "promote-vocabulary-alignments":
        datastores = DatastoreClients.from_settings(settings)
        try:
            with datastores.postgres.connect() as connection, datastores.neo4j.driver() as driver:
                run_vocabulary_migrations(connection)
                seeded = (
                    apply_vocabulary_seed(
                        connection,
                        alignment_version=args.alignment_version,
                        activated_by=args.activated_by,
                    )
                    if args.commit
                    else {"dry_run": 1}
                )
                alignments = fetch_approved_alignments(
                    connection,
                    alignment_version=args.alignment_version,
                    vocabulary=args.vocabulary,
                )
                written = promote_vocabulary_alignments(
                    driver, alignments, dry_run=not args.commit
                )
                linked = link_variants_to_fuel_concepts(driver, dry_run=not args.commit)
        except Exception as error:  # noqa: BLE001
            logger.error(
                "Vocabulary alignment promotion stopped safely",
                extra={"error_code": type(error).__name__},
            )
            return 1
        print(
            json.dumps(
                {
                    "alignment_version": args.alignment_version,
                    "committed": bool(args.commit),
                    "seed": seeded,
                    "graph": written,
                    "variants_linked": linked,
                },
                sort_keys=True,
            )
        )
        return 0

    if args.command == "match-ts-tecdoc":
        datastores = DatastoreClients.from_settings(settings)
        try:
            with datastores.postgres.connect() as connection, datastores.neo4j.driver() as driver:
                run_match_run_migrations(connection)
                rule_set, manufacturer_rules = load_active_rules(connection)
                catalog = (
                    load_postgres_ktype_catalog(
                        connection,
                        batch_id=args.candidate_catalog_version,
                    )
                    if args.candidate_source == "postgres"
                    else load_ktype_catalog(driver)
                )
                if len(catalog) != args.expected_ktype_count:
                    raise ValueError(
                        "TecDoc KType count mismatch: "
                        f"expected {args.expected_ktype_count}, found {len(catalog)}"
                    )
                pins = MatchRunPins(
                    operation_id=args.operation_id,
                    source_system="Transportstyrelsen",
                    source_version=args.source_version,
                    source_batch_prefix=args.source_batch_prefix,
                    expected_source_rows=args.expected_source_rows,
                    normalization_rule_version=args.normalization_rule_version,
                    candidate_catalog_version=(
                        f"{args.candidate_source}:{args.candidate_catalog_version}"
                    ),
                    policy_version=args.policy_version,
                    code_revision=args.code_revision,
                    alignment_version=args.alignment_version,
                )
                evaluator = TecDocDryRunEvaluator(
                    catalog,
                    manufacturer_rules,
                    ReviewedModelAliasIndex(rule_set),
                    fuel_alignment=load_fuel_alignment(
                        connection, alignment_version=args.alignment_version
                    ),
                )
                if args.source_mode == "raw":
                    counts = run_local_raw_dry_match_audit(
                        connection,
                        pins=pins,
                        rule_set=rule_set,
                        manufacturer_rules=manufacturer_rules,
                        evaluator=evaluator,
                        batch_size=args.page_size,
                        max_batches=args.max_batches,
                    )
                else:
                    if args.max_batches is not None:
                        raise ValueError("max_batches is supported only for raw source mode")
                    counts = run_dry_match_audit(
                        connection,
                        pins=pins,
                        page_size=args.page_size,
                        fetch_page=lambda after_id, limit: fetch_normalized_ts_page(
                            connection,
                            source_batch_prefix=pins.source_batch_prefix,
                            normalization_rule_version=pins.normalization_rule_version,
                            after_source_record_id=after_id,
                            limit=limit,
                        ),
                        evaluate_record=evaluator,
                    )
        except Exception as error:  # noqa: BLE001
            logger.error(
                "TS-to-TecDoc dry-run audit stopped safely",
                extra={"error_code": type(error).__name__},
            )
            return 1
        print(json.dumps(asdict(counts), sort_keys=True))
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
