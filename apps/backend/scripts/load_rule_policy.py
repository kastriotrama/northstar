"""Load a JSON snapshot from `export_rule_policy.py` into this database.

`core.translation_rule_versions` is insert-only (a trigger rejects UPDATE and
DELETE), so an incoming policy can't be upserted the way the two resolution-
rule tables are -- it has to land as a brand new version, exactly the way
`RuleReviewService.activate` mints one when a reviewer activates their
drafts. This does the same thing: read this database's own latest overrides,
layer the incoming file's overrides on top (file wins per rule_id, same as
activating a draft that overrides an inherited value), and insert one new
version row with the merge -- but only if the merge actually differs from
what's already here, so importing the same file twice is a no-op the second
time.

`base_rule_version` is always this database's own hardcoded base
(`REVIEWED_RULE_SET_VERSION`), not whatever the export recorded -- the base
ships with the code, so the two databases only agree on it if they're running
the same code, which is the only case this script is meant for.

Defaults to a dry run that only reports what would change.

Usage:
    python -m scripts.load_rule_policy rule_policy_export.json
    python -m scripts.load_rule_policy rule_policy_export.json --commit
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.config import get_ingestion_settings
from ingestion.datastores import DatastoreClients
from ingestion.normalization_migrations import (
    TRANSLATION_RULE_VERSIONS_TABLE,
    run_normalization_migrations,
)
from ingestion.translation_dictionaries import REVIEWED_RULE_SET_VERSION


def _fetch_latest_overrides(connection: Connection) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT overrides FROM {TRANSLATION_RULE_VERSIONS_TABLE} "
            "ORDER BY activated_at DESC, version DESC LIMIT 1"
        )
        row = cursor.fetchone()
    return dict(row[0]) if row is not None else {}


def load_overrides(
    connection: Connection, incoming: dict[str, Any], *, note: str, commit: bool
) -> tuple[int, int, int]:
    """Returns (new, changed, unchanged) rule_id counts."""

    run_normalization_migrations(connection)
    current = _fetch_latest_overrides(connection)
    new = sum(1 for key in incoming if key not in current)
    changed = sum(
        1 for key, value in incoming.items() if key in current and current[key] != value
    )
    unchanged = len(incoming) - new - changed
    if not commit or (new == 0 and changed == 0):
        return new, changed, unchanged

    merged = {**current, **incoming}
    version = datetime.now(UTC).strftime("policy-import-%Y%m%dT%H%M%S%fZ")
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {TRANSLATION_RULE_VERSIONS_TABLE} "
            "(version, base_rule_version, overrides, activation_note) "
            "VALUES (%s, %s, %s, %s)",
            (version, REVIEWED_RULE_SET_VERSION, Jsonb(merged), note),
        )
    connection.commit()
    return new, changed, unchanged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="JSON file written by export_rule_policy.py")
    parser.add_argument(
        "--commit", action="store_true",
        help="Actually write. Omitted means dry run (report only).",
    )
    args = parser.parse_args()

    payload = json.loads(Path(args.snapshot).read_text())
    overrides = payload["overrides"]

    settings = get_ingestion_settings()
    datastores = DatastoreClients.from_settings(settings)
    with datastores.postgres.connect() as connection:
        note = f"Imported {len(overrides)} policy override(s) from {Path(args.snapshot).name}"
        new, changed, unchanged = load_overrides(
            connection, overrides, note=note, commit=args.commit
        )

    verb = "Wrote" if args.commit else "Would write"
    print(f"{verb} new version: {new} new, {changed} changed, {unchanged} unchanged")
    if not args.commit:
        print("Dry run -- pass --commit to actually write.")


if __name__ == "__main__":
    main()
