"""Load a JSON snapshot from `export_ts_resolution_rules.py` into this database.

Unlike `load_resolution_rules.py` (TecDoc's table, a plain upsert), a TS rule
can't be copied as a raw row: `core.match_resolution_rules` keys every row to
one build's UUID and freezes `matched_rows`/`would_resolve`/`already_resolved`
at save time, both meaningless -- or, for the build reference, simply wrong --
in another database. So this goes through the same path a reviewer saving a
brand new rule from `/ts-data` goes through: `MatchReviewService.save_resolution_rule`,
against *this* database's own latest completed build, which re-validates the
rule and recomputes its counts fresh against this build's own population.

Safe to run more than once: before creating a rule, it checks whether an
existing rule for the same (source_field, source_value) already has the same
target and conditions, and skips it rather than authoring a duplicate --
there is no natural key to upsert on the way TecDoc's table has, since every
save mints a new rule_id.

Defaults to a dry run that only reports what would be created.

Usage:
    python -m scripts.load_ts_resolution_rules ts_resolution_rules_export.json
    python -m scripts.load_ts_resolution_rules ts_resolution_rules_export.json --commit
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from api.app.features.match_review.adjudicator import HeuristicAdjudicator
from api.app.features.match_review.chunk_repository import MatchReviewRepository
from api.app.features.match_review.chunk_schemas import ResolutionRuleRequest, RuleCondition
from api.app.features.match_review.chunk_service import (
    MatchReviewConflictError,
    MatchReviewService,
)
from api.app.features.match_review.integrations import UnconfiguredOemVinProvider
from ingestion.config import get_ingestion_settings
from ingestion.datastores import DatastoreClients


def _conditions_key(conditions: list[dict[str, Any]]) -> str:
    """A comparable fingerprint for a condition list, ignoring key order."""

    return json.dumps(
        [
            {
                "field": c["field"], "value": c.get("value"),
                "values": c.get("values"), "layer": c.get("layer", "source"),
                "operator": c.get("operator", "equals"),
            }
            for c in conditions
        ],
        sort_keys=True,
    )


def _already_present(
    repository: MatchReviewRepository, *, build_id: UUID, rule: dict[str, Any]
) -> bool:
    existing = repository.fetch_resolution_rules(
        build_id, source_field=rule["source_field"], source_value=rule["source_value"],
    )
    target_key = (rule["target_field"], rule["target_value"], _conditions_key(rule["conditions"]))
    return any(
        (item["target_field"], item["target_value"], _conditions_key(item["conditions"]))
        == target_key
        for item in existing
    )


def load_rules(
    service: MatchReviewService,
    repository: MatchReviewRepository,
    rules: list[dict[str, Any]],
    *,
    build_id: UUID,
    commit: bool,
) -> dict[str, int]:
    counts = {"created": 0, "already_present": 0, "skipped_invalid": 0}
    for rule in rules:
        if _already_present(repository, build_id=build_id, rule=rule):
            counts["already_present"] += 1
            continue
        if not commit:
            counts["created"] += 1
            continue
        request = ResolutionRuleRequest(
            build_id=build_id,
            source_field=rule["source_field"],
            source_value=rule["source_value"],
            conditions=[RuleCondition(**c) for c in rule["conditions"]],
            target_field=rule["target_field"],
            target_value=rule["target_value"],
            author=rule["author"],
            note=rule.get("note"),
        )
        try:
            service.save_resolution_rule(request)
        except MatchReviewConflictError as error:
            print(f"  skipped {rule['source_field']}={rule['source_value']!r}: {error}")
            counts["skipped_invalid"] += 1
            continue
        counts["created"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="JSON file written by export_ts_resolution_rules.py")
    parser.add_argument(
        "--commit", action="store_true",
        help="Actually write. Omitted means dry run (report only).",
    )
    args = parser.parse_args()

    payload = json.loads(Path(args.snapshot).read_text())
    rules = payload["rules"]

    settings = get_ingestion_settings()
    datastores = DatastoreClients.from_settings(settings)
    repository = MatchReviewRepository(datastores.postgres.connect)
    service = MatchReviewService(
        repository, oem_provider=UnconfiguredOemVinProvider(), adjudicator=HeuristicAdjudicator(),
    )

    latest_build = repository.fetch_latest_build()
    if latest_build is None:
        raise SystemExit("No completed build in this database -- nothing to attach rules to.")
    build_id = latest_build["build_id"]

    counts = load_rules(service, repository, rules, build_id=build_id, commit=args.commit)

    verb = "Created" if args.commit else "Would create"
    print(
        f"{verb}: {counts['created']}, already present: {counts['already_present']}, "
        f"skipped (invalid here): {counts['skipped_invalid']}"
    )
    print(f"Target build: {build_id}")
    if not args.commit:
        print("Dry run -- pass --commit to actually write.")


if __name__ == "__main__":
    main()
