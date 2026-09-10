"""Export `core.match_resolution_rules`' active definitions to portable JSON.

The counterpart to `export_resolution_rules.py`, for TS's own IF/THEN rules
(authored from the `/ts-data` resolve panel) rather than TecDoc's live
resolution table.

Only the environment-independent *definition* survives the round trip --
`source_field`, `source_value`, `target_field`, `target_value`, `conditions`,
`author`, `note`. `rule_id` and `build_id` are dropped: a build is a
particular ingest run's own UUID, meaningless in another database, and the
table's own immutability trigger would reject writing a foreign one anyway.
`matched_rows`/`would_resolve`/`already_resolved` are dropped too -- they are
a snapshot of one build's population, not a fact about the rule itself;
`load_ts_resolution_rules.py` recomputes them fresh against whichever build
it's run against, exactly as saving a brand new rule always does.

Retired rules are not exported -- they were turned off on purpose, and
recreating them elsewhere would silently turn that decision back on.

Usage:
    python -m scripts.export_ts_resolution_rules --out ts_resolution_rules_export.json
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
from ingestion.match_chunk_migrations import MATCH_RESOLUTION_RULES_TABLE

_COLUMNS = (
    "source_field", "source_value", "target_field", "target_value",
    "conditions", "author", "note",
)


def export_rules(connection: Connection) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM {MATCH_RESOLUTION_RULES_TABLE} "
            "WHERE status <> 'retired' ORDER BY created_at"
        )
        rows = cursor.fetchall()
    return [dict(zip(_COLUMNS, row, strict=True)) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default="ts_resolution_rules_export.json",
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
