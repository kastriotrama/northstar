"""Build the pilot database: a seeded slice of the full build, to ship to live.

Reads the database `DATABASE_URL` points at (the full build) and never writes to
it. With `--commit` it creates a NEW database on the same server, builds its
schema from the repo's migrations, copies every rule and the pinned TecDoc
catalog batch whole, copies the rows of a seeded random slice of vehicles, and
verifies the result against the source. See `ingestion/pilot_database.py` and
"Pilot database" in `docs/PRODUCTION_DEPLOYMENT.md`.

Defaults to a dry run: it prints the plan (tables, row counts, estimated size)
and creates nothing.

The default seed is the match impact report's sample seed. The slice and the
report's sample are one seeded ordering cut at two lengths, so with this seed
the report's 30,000 cars are the first 30,000 of the slice: a baseline measured
on live can be compared car by car with the local one. Another seed gives a
slice that holds only a fraction of them.

A verified build writes a manifest (`--manifest`, and a copy inside the pilot):
seed, size, catalog batch, source snapshot time and, per table, row count and
content checksum. `--verify DATABASE --manifest FILE` recomputes them on any
database in a read-only session -- run it on live after `pg_restore`.

Usage:
    python -m scripts.build_pilot_database --target-database NAME --catalog-batch BATCH
    python -m scripts.build_pilot_database --target-database NAME --catalog-batch BATCH --commit
    python -m scripts.build_pilot_database --verify NAME --manifest NAME.manifest.json

Exit status: 0 on a clean plan, a verified build or a database that equals its
manifest; 1 when a check failed or the plan has problems; 2 when the build
refused to start or could not finish.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import psycopg

from ingestion.config import get_ingestion_settings
from ingestion.pilot_database import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_MAINTENANCE_DATABASE,
    DEFAULT_SCOPE,
    DEFAULT_SEED,
    DEFAULT_SIZE,
    PilotBuildError,
    PilotOptions,
    build_pilot_database,
    describe_database_error,
    format_manifest_verification,
    format_summary,
    read_manifest,
    verify_pilot_database,
)


def default_manifest_path(target_database: str) -> Path:
    """`<target>.manifest.json` in the working directory."""

    return Path(f"{target_database}.manifest.json")


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--target-database",
        help="Name of the database to create on the source's server. It must not exist. "
             "Required unless --verify is given.",
    )
    parser.add_argument(
        "--catalog-batch",
        help="The TecDoc catalog batch live is pinned to; the only batch copied. "
             "Required unless --verify is given.",
    )
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE,
                        help=f"Vehicles in the slice (default {DEFAULT_SIZE}).")
    parser.add_argument(
        "--seed", default=DEFAULT_SEED,
        help=f"Seed of the random pick (default {DEFAULT_SEED!r}, the match impact report's "
             "sample seed: its 30,000 cars are then the first 30,000 of the slice, so live "
             "and local baselines compare car by car. Another seed loses that).",
    )
    parser.add_argument("--scope", default=DEFAULT_SCOPE,
                        help=f"vehicle_scope the slice is cut from (default {DEFAULT_SCOPE!r}).")
    parser.add_argument(
        "--registered-only", action=argparse.BooleanOptionalAction, default=True,
        help="Only vehicles the registry lists as registered (default: yes).",
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="Create and fill the target database. Omitted means dry run (plan only).",
    )
    parser.add_argument(
        "--replace", action="store_true",
        help="With --commit: drop an existing target first. Only a database an earlier "
             "run of this script created is ever dropped.",
    )
    parser.add_argument(
        "--manifest", type=Path,
        help="With --commit: where a verified build writes its manifest (default "
             "<target-database>.manifest.json in the working directory). With --verify: "
             "the manifest to check the database against (required).",
    )
    parser.add_argument(
        "--verify", metavar="DATABASE",
        help="Build nothing: recompute row counts and content checksums of DATABASE (on "
             "the server DATABASE_URL points at, read-only session) and compare them with "
             "--manifest. Exits 1 on any mismatch, naming the table.",
    )
    parser.add_argument(
        "--maintenance-database", default=DEFAULT_MAINTENANCE_DATABASE,
        help="Database to connect to for CREATE DATABASE "
             f"(default {DEFAULT_MAINTENANCE_DATABASE!r}).",
    )
    parser.add_argument(
        "--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE,
        help=f"Keys per source query (default {DEFAULT_CHUNK_SIZE}).",
    )
    args = parser.parse_args(argv)
    if args.verify is None and not (args.target_database and args.catalog_batch):
        parser.error("--target-database and --catalog-batch are required (or use --verify)")
    return args


def _verify(args: argparse.Namespace) -> int:
    if args.commit or args.replace or args.target_database:
        print("error: --verify builds nothing; do not combine it with --target-database, "
              "--commit or --replace")
        return 2
    if args.manifest is None:
        print("error: --verify needs --manifest FILE")
        return 2
    try:
        manifest = read_manifest(args.manifest)
        checks = verify_pilot_database(
            get_ingestion_settings().database_url, args.verify, manifest
        )
    except PilotBuildError as error:
        print(f"error: {error}")
        return 2
    except psycopg.Error as error:
        print(f"error: database error ({describe_database_error(error)})")
        return 2
    print(format_manifest_verification(args.verify, manifest, checks))
    return 0 if all(check.ok for check in checks) else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_arguments(argv)
    if args.verify is not None:
        return _verify(args)
    if args.replace and not args.commit:
        print("error: --replace only makes sense with --commit")
        return 2
    manifest_path = args.manifest or default_manifest_path(args.target_database)
    if args.commit and not manifest_path.resolve().parent.is_dir():
        print(f"error: the folder of --manifest {manifest_path} does not exist")
        return 2
    options = PilotOptions(
        target_database=args.target_database,
        catalog_batch=args.catalog_batch,
        size=args.size,
        seed=args.seed,
        scope=args.scope,
        registered_only=args.registered_only,
    )
    try:
        outcome = build_pilot_database(
            get_ingestion_settings().database_url,
            options,
            commit=args.commit,
            replace=args.replace,
            maintenance_database=args.maintenance_database,
            chunk_size=args.chunk_size,
            manifest_path=manifest_path if args.commit else None,
            report=lambda line: print(line, flush=True),
        )
    except PilotBuildError as error:
        print(f"error: {error}")
        return 2
    except psycopg.Error as error:
        # Database messages can quote row values (plates, VINs); name the error only.
        print(f"error: database error ({describe_database_error(error)})")
        return 2
    if not outcome.built:
        print("\nDry run -- nothing was created. Pass --commit to build the pilot database.")
        return 0 if outcome.ok else 1
    print()
    print(format_summary(outcome))
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
