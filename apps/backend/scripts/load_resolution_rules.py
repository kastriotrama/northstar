"""Load a JSON snapshot from `export_resolution_rules.py` into this database.

The counterpart to `export_resolution_rules.py`: takes whatever rules another
environment exported and upserts them here, so a rule added online can reach
a local DB (or CI, or a fresh clone) without hand-retyping it. The DB stays
the only source of truth for what's actually live -- the file is just a
carrier between databases.

Every write is an upsert keyed on the same natural key the live table already
enforces (`canonical_field, source_system, comparison_key`, plus
`canonical_value` for a `compatible` row), so loading the same file twice, or
loading a file that overlaps with rows already here, is always safe: a row
this DB doesn't have yet is inserted, a row it already has is refreshed to
match the file's version.

Defaults to a dry run that only reports what would change.

Usage:
    python -m scripts.load_resolution_rules resolution_rules_export.json
    python -m scripts.load_resolution_rules resolution_rules_export.json --commit
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from psycopg import Connection

from ingestion.config import get_ingestion_settings
from ingestion.datastores import DatastoreClients
from ingestion.tecdoc.resolution_migrations import (
    TECDOC_RESOLUTION_RULES_TABLE,
    run_tecdoc_resolution_migrations,
)

_UPSERT_SINGLE_TARGET = f"""
    INSERT INTO {TECDOC_RESOLUTION_RULES_TABLE}
        (canonical_field, source_system, comparison_key, source_term, key_table,
         decision, canonical_value, relation, note, reviewed_by, updated_at)
    VALUES (%(canonical_field)s, %(source_system)s, %(comparison_key)s, %(source_term)s,
            %(key_table)s, %(decision)s, %(canonical_value)s, %(relation)s, %(note)s,
            %(reviewed_by)s, now())
    ON CONFLICT (canonical_field, source_system, comparison_key) WHERE relation <> 'compatible'
    DO UPDATE SET
        source_term = EXCLUDED.source_term,
        key_table = EXCLUDED.key_table,
        decision = EXCLUDED.decision,
        canonical_value = EXCLUDED.canonical_value,
        note = EXCLUDED.note,
        reviewed_by = EXCLUDED.reviewed_by,
        updated_at = now()
"""

_UPSERT_COMPATIBLE = f"""
    INSERT INTO {TECDOC_RESOLUTION_RULES_TABLE}
        (canonical_field, source_system, comparison_key, source_term, key_table,
         decision, canonical_value, relation, support, note, reviewed_by, updated_at)
    VALUES (%(canonical_field)s, %(source_system)s, %(comparison_key)s, %(source_term)s,
            %(key_table)s, %(decision)s, %(canonical_value)s, %(relation)s, %(support)s,
            %(note)s, %(reviewed_by)s, now())
    ON CONFLICT (canonical_field, source_system, comparison_key, canonical_value)
        WHERE relation = 'compatible'
    DO UPDATE SET
        source_term = EXCLUDED.source_term,
        key_table = EXCLUDED.key_table,
        support = EXCLUDED.support,
        note = EXCLUDED.note,
        reviewed_by = EXCLUDED.reviewed_by,
        updated_at = now()
"""


def load_rules(
    connection: Connection, rules: list[dict[str, Any]], *, commit: bool
) -> dict[str, int]:
    run_tecdoc_resolution_migrations(connection)
    counts = {"compatible": 0, "single_target": 0}
    with connection.cursor() as cursor:
        for rule in rules:
            statement = (
                _UPSERT_COMPATIBLE if rule["relation"] == "compatible" else _UPSERT_SINGLE_TARGET
            )
            key = "compatible" if rule["relation"] == "compatible" else "single_target"
            if commit:
                cursor.execute(statement, rule)
            counts[key] += 1
    if commit:
        connection.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="JSON file written by export_resolution_rules.py")
    parser.add_argument(
        "--commit", action="store_true",
        help="Actually write. Omitted means dry run (report only).",
    )
    args = parser.parse_args()

    payload = json.loads(Path(args.snapshot).read_text())
    rules = payload["rules"]

    settings = get_ingestion_settings()
    datastores = DatastoreClients.from_settings(settings)
    with datastores.postgres.connect() as connection:
        counts = load_rules(connection, rules, commit=args.commit)

    verb = "Wrote" if args.commit else "Would write"
    print(f"{verb}: {counts['single_target']} single-target, {counts['compatible']} compatible")
    if not args.commit:
        print("Dry run -- pass --commit to actually write.")


if __name__ == "__main__":
    main()
