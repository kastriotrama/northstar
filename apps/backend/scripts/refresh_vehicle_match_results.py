"""Fill or refresh the stored outcome of matching (`core.vehicle_match_results`).

The Matching statistics and car lists read that table; this is where the
matcher actually runs. Writes only that table and `core.vehicle_match_runs`.

    # new and changed cars only (the normal run; safe to repeat, continues a stopped run)
    python -m scripts.refresh_vehicle_match_results --catalog-batch <batch> --workers 4

    # a quick overview: a seeded random 10,000 cars
    python -m scripts.refresh_vehicle_match_results --catalog-batch <batch> --sample 10000

    # after a matcher or catalog change: every car again
    python -m scripts.refresh_vehicle_match_results --catalog-batch <batch> --rebuild --workers 4

    # named cars
    python -m scripts.refresh_vehicle_match_results --catalog-batch <batch> --vehicle NOR-...

The catalog batch is required: the newest batch in a database can be a test batch.
Each page of cars is committed on its own, so stopping the run loses at most one page.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from api.app.core.settings import get_settings
from api.app.features.vehicle_match_results.refresh import MatchResultRefresher, RefreshCounts
from api.app.features.vehicle_matching.repository import SAMPLE_SEED, VehicleMatchingRepository
from api.app.features.vehicle_matching.service import build_matcher
from ingestion.config import get_ingestion_settings
from ingestion.datastores import DatastoreClients
from ingestion.vehicle_match_result_migrations import verify_vehicle_match_result_schema_contract


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog-batch", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--rebuild", action="store_true", help="Match every car again.")
    mode.add_argument("--sample", type=int, metavar="N", help="A seeded random N cars.")
    mode.add_argument("--vehicle", action="append", metavar="NOR-ID", help="Named cars; repeatable.")
    mode.add_argument("--vehicles-from", type=Path, metavar="FILE",
                      help="Named cars, one NOR ID per line: the cars a matcher change can reach.")
    parser.add_argument("--seed", default=SAMPLE_SEED, help="Seed of --sample.")
    parser.add_argument("--scope", default="passenger")
    parser.add_argument("--include-deregistered", action="store_true")
    parser.add_argument("--limit", type=int, help="Stop after this many cars.")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--force", action="store_true",
                        help="With --sample, --vehicle or --vehicles-from: match even cars that "
                             "did not change (after a matcher change they did not, the matcher did).")
    parser.add_argument("--matcher-version", default=None,
                        help="Stored on every row; defaults to the build version.")
    args = parser.parse_args(argv)

    datastores = DatastoreClients.from_settings(get_ingestion_settings())
    connect = datastores.postgres.connect
    with connect() as connection:
        verify_vehicle_match_result_schema_contract(connection)
    repository = VehicleMatchingRepository(connect)
    started = time.monotonic()
    matcher = build_matcher(repository, args.catalog_batch)
    version = args.matcher_version or get_settings().build_version
    refresher = MatchResultRefresher(connect, lambda: matcher, version, page_size=args.page_size)

    def progress(counts: RefreshCounts) -> None:
        done = counts.evaluated + counts.unchanged
        elapsed = time.monotonic() - started
        rate = counts.evaluated / elapsed if elapsed else 0.0
        print(f"{done}/{counts.target} cars: {counts.evaluated} matched, "
              f"{counts.unchanged} unchanged, {elapsed:.0f}s, {rate:.1f} matched/s",
              file=sys.stderr, flush=True)

    registered_only = not args.include_deregistered
    named = args.vehicle or (
        args.vehicles_from.read_text(encoding="utf-8").split() if args.vehicles_from else None
    )
    if named:
        counts = refresher.refresh_vehicles(
            [value.strip().upper() for value in named], force=args.force,
            workers=args.workers, progress=progress,
        )
    elif args.sample:
        ids = repository.sample_vehicle_ids(
            seed=args.seed, size=args.sample, scope=args.scope, registered_only=registered_only
        )
        counts = refresher.refresh_vehicles(
            ids, force=args.force, mode="sample", workers=args.workers, progress=progress
        )
    else:
        counts = refresher.refresh_scope(
            rebuild=args.rebuild, scope=args.scope, registered_only=registered_only,
            workers=args.workers, limit=args.limit, progress=progress,
        )
    print(f"run {counts.run_id}: {counts.evaluated} matched, {counts.unchanged} unchanged of "
          f"{counts.target} on {matcher.batch_id} in {time.monotonic() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
