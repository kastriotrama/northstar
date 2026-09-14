"""Load a JSON snapshot from `export_policy_versions.py` into this database.

`core.translation_rule_versions` is append-only -- `version` is its primary
key and an immutability trigger forbids UPDATE/DELETE -- so there is no
edit-collision case the way `load_resolution_rules.py` has for TecDoc's
table: a version this database already has is left exactly as it is (its
content could not have changed since), and a version it doesn't have yet is
inserted verbatim, `activated_at` included, so it keeps whatever place in the
activation order it had wherever it was first created.

This only adds versions to the table; it does not change which one is
active. `fetch_active_version` (`api/app/features/rule_review/repository.py`)
always resolves to the most recently activated row, so a version imported
here becomes active immediately if it is newer than every version already
present -- exactly as if a reviewer had activated it by hand.

Defaults to a dry run that only reports what would change.

Usage:
    python -m scripts.load_policy_versions policy_versions_export.json
    python -m scripts.load_policy_versions policy_versions_export.json --commit
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.config import get_ingestion_settings
from ingestion.datastores import DatastoreClients
from ingestion.normalization_migrations import TRANSLATION_RULE_VERSIONS_TABLE

_INSERT = f"""
    INSERT INTO {TRANSLATION_RULE_VERSIONS_TABLE}
        (version, base_rule_version, overrides, activation_note, activated_at)
    VALUES (%(version)s, %(base_rule_version)s, %(overrides)s, %(activation_note)s,
            %(activated_at)s)
    ON CONFLICT (version) DO NOTHING
"""


@dataclass(frozen=True)
class PolicyVersionLoadResult:
    created: int
    already_present: int


def _parsed_activated_at(version: dict[str, Any]) -> dict[str, Any]:
    """A copy of `version` with `activated_at` as a real `datetime`.

    Mirrors `load_resolution_rules._parsed_updated_at`: the JSON round trip
    (file or HTTP) only ever carries an ISO string, but psycopg needs an
    actual `datetime` to bind against a `timestamptz` parameter.
    """

    raw = version["activated_at"]
    return version if isinstance(raw, datetime) else {**version, "activated_at": datetime.fromisoformat(str(raw))}


def load_versions(
    connection: Connection, versions: list[dict[str, Any]], *, commit: bool
) -> PolicyVersionLoadResult:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT version FROM {TRANSLATION_RULE_VERSIONS_TABLE}")
        existing = {row[0] for row in cursor.fetchall()}

        created = 0
        already_present = 0
        for version in versions:
            if version["version"] in existing:
                already_present += 1
                continue
            if not commit:
                created += 1
                continue
            row = _parsed_activated_at(version)
            cursor.execute(
                _INSERT,
                {
                    "version": row["version"],
                    "base_rule_version": row["base_rule_version"],
                    "overrides": Jsonb(row["overrides"]),
                    "activation_note": row["activation_note"],
                    "activated_at": row["activated_at"],
                },
            )
            created += 1
    if commit:
        connection.commit()
    return PolicyVersionLoadResult(created=created, already_present=already_present)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="JSON file written by export_policy_versions.py")
    parser.add_argument(
        "--commit", action="store_true",
        help="Actually write. Omitted means dry run (report only).",
    )
    args = parser.parse_args()

    payload = json.loads(Path(args.snapshot).read_text())
    versions = payload["versions"]

    settings = get_ingestion_settings()
    datastores = DatastoreClients.from_settings(settings)
    with datastores.postgres.connect() as connection:
        result = load_versions(connection, versions, commit=args.commit)

    verb = "Created" if args.commit else "Would create"
    print(f"{verb}: {result.created}, already present: {result.already_present}")
    if not args.commit:
        print("Dry run -- pass --commit to actually write.")


if __name__ == "__main__":
    main()
