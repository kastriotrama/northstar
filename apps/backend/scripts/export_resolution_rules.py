"""Export every row of `core.tecdoc_resolution_rules` to a portable JSON file.

The DB is always the source of truth -- this file is never read by the
application, only by `load_resolution_rules.py`. Its only job is to let a
rule added in one environment (online) reach another (local, CI, a fresh
dev DB) without hand-retyping it: export here, commit the file, `load` it
there.

Only the natural-key columns are written (not the surrogate `id`, which is
per-database and meaningless elsewhere). Re-exporting is always safe -- it
just overwrites the file with the table's current contents.

Usage:
    python -m scripts.export_resolution_rules --out resolution_rules_export.json
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg import Connection

from ingestion.config import get_ingestion_settings
from ingestion.datastores import DatastoreClients
from ingestion.tecdoc.resolution_migrations import (
    TECDOC_RESOLUTION_RULES_TABLE,
    run_tecdoc_resolution_migrations,
)

#: Everything but the surrogate `id` -- the natural key
#: (canonical_field, source_system, comparison_key[, canonical_value]) is
#: what makes a row identifiable across databases, not the local sequence.
_COLUMNS = (
    "canonical_field", "comparison_key", "source_term", "key_table", "decision",
    "canonical_value", "note", "reviewed_by", "source_system", "relation", "support",
)


def export_rules(connection: Connection) -> list[dict[str, Any]]:
    run_tecdoc_resolution_migrations(connection)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM {TECDOC_RESOLUTION_RULES_TABLE} "
            "ORDER BY canonical_field, source_system, comparison_key, canonical_value"
        )
        rows = cursor.fetchall()
    return [dict(zip(_COLUMNS, row, strict=True)) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default="resolution_rules_export.json",
        help="Path to write the JSON snapshot to.",
    )
    args = parser.parse_args()

    settings = get_ingestion_settings()
    datastores = DatastoreClients.from_settings(settings)
    with datastores.postgres.connect() as connection:
        rules = export_rules(connection)

    payload = {"exported_at": datetime.now(UTC).isoformat(), "rules": rules}
    Path(args.out).write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(f"Wrote {len(rules)} rule(s) to {args.out}")


if __name__ == "__main__":
    main()
