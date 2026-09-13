"""Export the active `core.translation_rule_versions` overrides to portable JSON.

The "policy" layer, in rule-review terms: every human decision (accepted or
proposed catalog value, plus manufacturer-entity ruling) that has been
activated on top of the hardcoded base rule set. `translation_rule_versions`
is insert-only and each row's `overrides` already accumulates everything
activated before it (see `RuleReviewService.activate`), so exporting just the
latest row's `overrides` is exporting the whole policy, not one diff of it.

The base rule set itself (`ingestion.translation_dictionaries`) is not
exported -- it ships with the code, so both databases already have it.

Usage:
    python -m scripts.export_rule_policy --out rule_policy_export.json
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
from ingestion.normalization_migrations import (
    TRANSLATION_RULE_VERSIONS_TABLE,
    run_normalization_migrations,
)


def export_overrides(connection: Connection) -> tuple[str, str, dict[str, Any]]:
    run_normalization_migrations(connection)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT version, base_rule_version, overrides "
            f"FROM {TRANSLATION_RULE_VERSIONS_TABLE} "
            "ORDER BY activated_at DESC, version DESC LIMIT 1"
        )
        row = cursor.fetchone()
    if row is None:
        return "", "", {}
    return str(row[0]), str(row[1]), dict(row[2])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default="rule_policy_export.json",
        help="Path to write the JSON snapshot to.",
    )
    args = parser.parse_args()

    settings = get_ingestion_settings()
    datastores = DatastoreClients.from_settings(settings)
    with datastores.postgres.connect() as connection:
        source_version, base_rule_version, overrides = export_overrides(connection)

    payload = {
        "exported_at": datetime.now(UTC).isoformat(),
        "source_version": source_version,
        "base_rule_version": base_rule_version,
        "overrides": overrides,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(f"Wrote {len(overrides)} policy override(s) to {args.out}")


if __name__ == "__main__":
    main()
