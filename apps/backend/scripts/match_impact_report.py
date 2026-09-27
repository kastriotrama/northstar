"""Measure how many cars reach one KType, repeatably, before and after a change.

Read-only. Evaluates a seeded sample of NorthStar vehicles (`core.vehicles`)
with the Vehicles tab's matcher against a pinned catalog batch, prints a
summary and writes the full report as JSON so a later run can be compared
car by car:

    python -m scripts.match_impact_report --catalog-batch <batch> --label baseline \\
        --size 30000 --workers 3 --out outputs/match-impact/baseline.json
    python -m scripts.match_impact_report --catalog-batch <batch> --label engine-tolerant \\
        --size 30000 --workers 3 --baseline outputs/match-impact/baseline.json \\
        --out outputs/match-impact/engine-tolerant.json

The catalog batch is required: the newest batch in a database can be a test batch.

`--cars-from` evaluates exactly the cars of an earlier report instead of a new
sample -- the way to re-measure a fixed test set, such as the 50k cars of the
sample database, after the data under them changed.

`--reference` takes a CSV with `vehicle_id,ktype_reference` columns: cars whose
correct KType is known. Reference cars outside the sample are evaluated too,
but scored only against the reference, never in the sample's counts.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

from api.app.features.vehicle_matching.impact import (
    ImpactReport,
    build_report,
    evaluate_cars,
    render,
)
from api.app.features.vehicle_matching.repository import CarRecord, VehicleMatchingRepository
from api.app.features.vehicle_matching.service import build_matcher
from ingestion.config import get_ingestion_settings
from ingestion.datastores import DatastoreClients

_PAGE = 1000


def load_reference(path: Path) -> dict[str, str]:
    with path.open(newline="") as handle:
        rows = csv.DictReader(handle)
        missing = {"vehicle_id", "ktype_reference"} - set(rows.fieldnames or ())
        if missing:
            raise ValueError(f"{path} lacks columns: {', '.join(sorted(missing))}")
        return {row["vehicle_id"].strip(): row["ktype_reference"].strip() for row in rows}


def report_cars(path: Path) -> tuple[list[str], str]:
    """The cars an earlier report evaluated, in a stable order, and its population label."""

    report = ImpactReport.from_json(json.loads(path.read_text()))
    return sorted(report.cars), f"cars of {path.name} ({report.population})"


def _load(repository: VehicleMatchingRepository, ids: list[str]) -> list[CarRecord]:
    cars: list[CarRecord] = []
    for start in range(0, len(ids), _PAGE):
        cars += repository.vehicle_car_records(ids[start : start + _PAGE])
    return cars


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog-batch", required=True)
    parser.add_argument("--label", required=True, help="Name of this run, e.g. baseline.")
    parser.add_argument("--seed", default="northstar-match-impact-v1")
    parser.add_argument("--size", type=int, default=30000)
    parser.add_argument("--scope", default="passenger")
    parser.add_argument("--include-deregistered", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--cars-from", type=Path,
                        help="Evaluate exactly the cars of this earlier report instead of a sample.")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--baseline", type=Path, help="A previous report to compare with.")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    baseline = ImpactReport.from_json(json.loads(args.baseline.read_text())) if args.baseline else None
    reference = load_reference(args.reference) if args.reference else None

    datastores = DatastoreClients.from_settings(get_ingestion_settings())
    repository = VehicleMatchingRepository(datastores.postgres.connect)
    started = time.monotonic()
    matcher = build_matcher(repository, args.catalog_batch)
    if args.cars_from:
        ids, population = report_cars(args.cars_from)
    else:
        ids = repository.sample_vehicle_ids(
            seed=args.seed, size=args.size, scope=args.scope,
            registered_only=not args.include_deregistered,
        )
        population = f"{args.scope}{'' if args.include_deregistered else ', registered'}"
    sampled = set(ids)
    extra = [vehicle_id for vehicle_id in (reference or {}) if vehicle_id not in sampled]
    cars = _load(repository, ids)
    reference_cars = _load(repository, extra)
    print(f"{len(cars)} sample + {len(reference_cars)} reference cars loaded in "
          f"{time.monotonic() - started:.0f}s; evaluating...", file=sys.stderr)
    outcomes = evaluate_cars(matcher, cars, workers=args.workers)
    reference_outcomes = evaluate_cars(matcher, reference_cars, workers=args.workers)
    report = build_report(
        outcomes, matcher.catalog, label=args.label, catalog_batch=matcher.batch_id,
        seed=args.seed, population=population, reference=reference,
        reference_outcomes=reference_outcomes,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report.to_json(), sort_keys=True) + "\n")
    print(render(report, baseline))
    print(f"\n{time.monotonic() - started:.0f}s; report written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
