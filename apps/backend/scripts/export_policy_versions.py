"""Export every row of `core.translation_rule_versions` to portable JSON.

The counterpart to `export_resolution_rules.py`/`export_ts_resolution_rules.py`,
for the third rule source the `/rules` catalog counts under `policy_total`:
free-standing overrides (a VIN+brand correction, a special-vehicle safety
policy, a manufacturer-match policy) plus per-rule_id decisions, all bundled
into one immutable, activated `overrides` blob per version -- see
`api/app/features/rule_review/service.py`'s own comment on why these are
listed separately from the 1.26k catalog decisions.

The table is append-only (`version` is its primary key and an immutability
trigger forbids UPDATE/DELETE on it), so exporting it is simpler than
TecDoc's: there is no edit-collision case to detect, only "does the other
database already have this version." `activated_at` rides along so
`load_policy_versions.py` can insert it verbatim rather than stamping a new
one -- `fetch_active_version` orders by it, and a version reactivated under
today's date would jump the queue ahead of versions actually activated more
recently elsewhere.

Usage:
    python -m scripts.export_policy_versions --out policy_versions_export.json
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
from ingestion.normalization_migrations import TRANSLATION_RULE_VERSIONS_TABLE

_COLUMNS = ("version", "base_rule_version", "overrides", "activation_note", "activated_at")


def export_versions(connection: Connection) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM {TRANSLATION_RULE_VERSIONS_TABLE} "
            "ORDER BY activated_at, version"
        )
        rows = cursor.fetchall()
    return [dict(zip(_COLUMNS, row, strict=True)) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default="policy_versions_export.json",
        help="Path to write the JSON snapshot to.",
    )
    args = parser.parse_args()

    settings = get_ingestion_settings()
    datastores = DatastoreClients.from_settings(settings)
    with datastores.postgres.connect() as connection:
        versions = export_versions(connection)

    payload = {"exported_at": datetime.now(UTC).isoformat(), "versions": versions}
    Path(args.out).write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(f"Wrote {len(versions)} version(s) to {args.out}")


if __name__ == "__main__":
    main()
