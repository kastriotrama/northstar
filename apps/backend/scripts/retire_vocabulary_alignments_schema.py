"""Drop the retired core.vocabulary_alignments schema. Destructive; run once, by hand.

`core.vocabulary_alignments` / `core.vocabulary_alignment_versions` /
`core.vocabulary_alignment_drafts` are superseded by the live
`core.tecdoc_resolution_rules` table (`source_system`/`relation`/`support`
columns -- see `ingestion/tecdoc/resolution_migrations.py` and
`ingestion/vocabulary_alignment.py`). This is the explicit, separate teardown
step the migration plan calls for: run only after `scripts/
port_vocabulary_alignment_seed.py` has ported the seed rows and the matching
pipeline has been confirmed to work against the new table.

Nothing here runs automatically -- it is not wired into any CLI command or
startup migration, and defaults to a dry run that only reports what exists.

Usage:
    python -m scripts.retire_vocabulary_alignments_schema            # dry run
    python -m scripts.retire_vocabulary_alignments_schema --commit   # drops it
"""

from __future__ import annotations

import argparse

from psycopg import Connection

from ingestion.config import get_ingestion_settings
from ingestion.datastores import DatastoreClients

_TABLES = (
    # Drop order matters: vocabulary_alignments FK-references
    # vocabulary_alignment_versions, so it must go first. CASCADE takes each
    # table's own triggers with it; the trigger *functions* are separate
    # objects and are dropped explicitly below.
    "core.vocabulary_alignments",
    "core.vocabulary_alignment_versions",
    "core.vocabulary_alignment_drafts",
)

_FUNCTIONS = (
    "core.reject_vocabulary_alignment_mutation",
    "core.guard_vocabulary_alignment_seal",
    "core.guard_vocabulary_version",
)


def _existing_tables(connection: Connection) -> list[str]:
    existing: list[str] = []
    with connection.cursor() as cursor:
        for table in _TABLES:
            cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
            row = cursor.fetchone()
            if row and row[0]:
                existing.append(table)
    return existing


def retire_schema(connection: Connection, *, commit: bool) -> dict[str, list[str]]:
    """Report (dry run) or drop (commit) the retired vocabulary schema."""

    existing = _existing_tables(connection)
    if not commit:
        return {"would_drop_tables": existing, "would_drop_functions": list(_FUNCTIONS)}

    with connection.cursor() as cursor:
        for table in _TABLES:
            cursor.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        for function in _FUNCTIONS:
            cursor.execute(f"DROP FUNCTION IF EXISTS {function}() CASCADE")
    connection.commit()
    return {"dropped_tables": existing, "dropped_functions": list(_FUNCTIONS)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually drop the schema. Omitted means dry run (report only).",
    )
    args = parser.parse_args()

    settings = get_ingestion_settings()
    datastores = DatastoreClients.from_settings(settings)
    with datastores.postgres.connect() as connection:
        result = retire_schema(connection, commit=args.commit)
    print(result)


if __name__ == "__main__":
    main()
