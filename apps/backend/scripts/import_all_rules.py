"""Load a JSON snapshot from `export_all_rules.py` into this database.

The counterpart to `export_all_rules.py`: applies all three sections
(policy overrides, TecDoc resolution rules, TS resolution rules) through the
same load path each standalone `load_*` script uses, so the safety rules
documented there still apply here -- TecDoc rows upsert and skip a locally
newer edit, policy lands as one new immutable version, TS rules re-validate
and recompute against this database's own latest build (which must exist
here already).

Defaults to a dry run that only reports what would change.

Usage:
    python -m scripts.import_all_rules rules_export.json
    python -m scripts.import_all_rules rules_export.json --commit
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from api.app.features.match_review.adjudicator import HeuristicAdjudicator
from api.app.features.match_review.chunk_repository import MatchReviewRepository
from api.app.features.match_review.chunk_service import MatchReviewService
from api.app.features.match_review.integrations import UnconfiguredOemVinProvider
from ingestion.config import get_ingestion_settings
from ingestion.datastores import DatastoreClients
from scripts.load_resolution_rules import load_rules as load_tecdoc_resolution_rules
from scripts.load_rule_policy import load_overrides
from scripts.load_ts_resolution_rules import load_rules as load_ts_resolution_rules


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="JSON file written by export_all_rules.py")
    parser.add_argument(
        "--commit", action="store_true",
        help="Actually write. Omitted means dry run (report only).",
    )
    args = parser.parse_args()

    payload = json.loads(Path(args.snapshot).read_text())
    snapshot_name = Path(args.snapshot).name

    settings = get_ingestion_settings()
    datastores = DatastoreClients.from_settings(settings)

    print("== policy overrides ==")
    with datastores.postgres.connect() as connection:
        overrides = payload["rule_policy"]["overrides"]
        note = f"Imported {len(overrides)} policy override(s) from {snapshot_name}"
        new, changed, unchanged = load_overrides(
            connection, overrides, note=note, commit=args.commit
        )
    verb = "Wrote" if args.commit else "Would write"
    print(f"{verb} new version: {new} new, {changed} changed, {unchanged} unchanged")

    print("\n== tecdoc resolution rules ==")
    with datastores.postgres.connect() as connection:
        tecdoc_result = load_tecdoc_resolution_rules(
            connection, payload["tecdoc_resolution_rules"], commit=args.commit
        )
    verb = "Wrote" if args.commit else "Would write"
    print(
        f"{verb}: {tecdoc_result.single_target} single-target, "
        f"{tecdoc_result.compatible} compatible"
    )
    if tecdoc_result.conflicts:
        print(f"Skipped {len(tecdoc_result.conflicts)} row(s) with a newer local edit:")
        for conflict in tecdoc_result.conflicts:
            print(f"  {conflict}")

    print("\n== ts resolution rules ==")
    repository = MatchReviewRepository(datastores.postgres.connect)
    service = MatchReviewService(
        repository, oem_provider=UnconfiguredOemVinProvider(), adjudicator=HeuristicAdjudicator(),
    )
    latest_build = repository.fetch_latest_build()
    if latest_build is None:
        print("No completed build in this database -- skipping TS resolution rules.")
    else:
        build_id = latest_build["build_id"]
        ts_result = load_ts_resolution_rules(
            service, repository, payload["ts_resolution_rules"],
            build_id=build_id, commit=args.commit,
        )
        verb = "Created" if args.commit else "Would create"
        print(
            f"{verb}: {ts_result.created}, already present: {ts_result.already_present}, "
            f"skipped (invalid here): {ts_result.skipped_invalid}"
        )
        print(f"Target build: {build_id}")

    if not args.commit:
        print("\nDry run -- pass --commit to actually write.")


if __name__ == "__main__":
    main()
