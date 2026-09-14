"""Load a JSON snapshot from `export_tecdoc_rule_catalog.py` into this database.

Mirrors `load_policy_versions.py`: both tables are effectively append-only, so
a `rule_version` this database already has is left exactly as it is (its
rules could not have changed under a sealed version), and one it doesn't
have yet is inserted whole -- the version row, then its rules -- so a fresh
database ends up with every sealed generation either side ever produced,
rather than just whichever one it happened to generate locally.

`generated_at` rides along verbatim rather than being restamped to now,
since `fetch_tecdoc_rules` (`api/app/features/rule_review/repository.py`)
picks the newest sealed version to actually serve -- restamping would let an
older generation jump the queue ahead of one genuinely produced more
recently elsewhere.

Defaults to a dry run that only reports what would change.

Usage:
    python -m scripts.load_tecdoc_rule_catalog tecdoc_rule_catalog_export.json
    python -m scripts.load_tecdoc_rule_catalog tecdoc_rule_catalog_export.json --commit
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
from ingestion.tecdoc.canonical_rule_migrations import (
    TECDOC_RULE_VERSIONS_TABLE,
    TECDOC_RULES_TABLE,
    run_tecdoc_rule_migrations,
)

_INSERT_VERSION = f"""
    INSERT INTO {TECDOC_RULE_VERSIONS_TABLE}
        (rule_version, tecdoc_release, source_note, generated_by, rule_count,
         content_fingerprint, sealed, generated_at)
    VALUES (%(rule_version)s, %(tecdoc_release)s, %(source_note)s, %(generated_by)s,
            %(rule_count)s, %(content_fingerprint)s, %(sealed)s, %(generated_at)s)
    ON CONFLICT (rule_version) DO NOTHING
"""

_INSERT_RULE = f"""
    INSERT INTO {TECDOC_RULES_TABLE}
        (rule_version, rule_id, area, entity_type, source_field, source_term,
         key_table, canonical_field, canonical_value, decision, derivation,
         support, evidence, created_at)
    VALUES (%(rule_version)s, %(rule_id)s, %(area)s, %(entity_type)s, %(source_field)s,
            %(source_term)s, %(key_table)s, %(canonical_field)s, %(canonical_value)s,
            %(decision)s, %(derivation)s, %(support)s, %(evidence)s, %(created_at)s)
    ON CONFLICT (rule_version, rule_id) DO NOTHING
"""


@dataclass(frozen=True)
class TecDocCatalogLoadResult:
    versions_created: int
    versions_already_present: int
    rules_created: int


def _parsed_timestamp(row: dict[str, Any], key: str) -> dict[str, Any]:
    raw = row[key]
    return row if isinstance(raw, datetime) else {**row, key: datetime.fromisoformat(str(raw))}


def load_catalog(
    connection: Connection,
    *,
    versions: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    commit: bool,
) -> TecDocCatalogLoadResult:
    run_tecdoc_rule_migrations(connection)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT rule_version FROM {TECDOC_RULE_VERSIONS_TABLE}")
        existing = {row[0] for row in cursor.fetchall()}

        new_versions = {v["rule_version"] for v in versions if v["rule_version"] not in existing}
        versions_created = len(new_versions)
        versions_already_present = len(versions) - versions_created
        rules_created = sum(1 for r in rules if r["rule_version"] in new_versions)

        if not commit:
            return TecDocCatalogLoadResult(
                versions_created=versions_created,
                versions_already_present=versions_already_present,
                rules_created=rules_created,
            )

        for version in versions:
            if version["rule_version"] not in new_versions:
                continue
            cursor.execute(_INSERT_VERSION, _parsed_timestamp(version, "generated_at"))
        for rule in rules:
            if rule["rule_version"] not in new_versions:
                continue
            row = _parsed_timestamp(rule, "created_at")
            cursor.execute(_INSERT_RULE, {**row, "evidence": Jsonb(row["evidence"])})
    connection.commit()
    return TecDocCatalogLoadResult(
        versions_created=versions_created,
        versions_already_present=versions_already_present,
        rules_created=rules_created,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="JSON file written by export_tecdoc_rule_catalog.py")
    parser.add_argument(
        "--commit", action="store_true",
        help="Actually write. Omitted means dry run (report only).",
    )
    args = parser.parse_args()

    payload = json.loads(Path(args.snapshot).read_text())

    settings = get_ingestion_settings()
    datastores = DatastoreClients.from_settings(settings)
    with datastores.postgres.connect() as connection:
        result = load_catalog(
            connection,
            versions=payload["tecdoc_rule_versions"],
            rules=payload["tecdoc_rules"],
            commit=args.commit,
        )

    verb = "Created" if args.commit else "Would create"
    print(
        f"{verb}: {result.versions_created} version(s) ({result.rules_created} rule(s)), "
        f"already present: {result.versions_already_present}"
    )
    if not args.commit:
        print("Dry run -- pass --commit to actually write.")


if __name__ == "__main__":
    main()
