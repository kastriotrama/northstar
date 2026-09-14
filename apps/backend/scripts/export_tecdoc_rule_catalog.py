"""Export every row of `core.tecdoc_rule_versions` + `core.tecdoc_rules` to JSON.

The counterpart to `export_policy_versions.py`, for the sealed, generated
TecDoc rule catalog `generate-tecdoc-rules` writes (`tecdoc_total` on the
`/rules` page) -- a different mechanism from both `core.tecdoc_resolution_rules`
(reviewer gap rulings, already synced by `RulesBundleService`) and
`core.translation_rule_versions` (the policy overlay). Two databases can end
up sealing different-named versions of this catalog independently (e.g.
`tecdoc-rules-v1` locally vs. `tecdoc-0326-canonical-scan-v1-20260907` in
production) with no way to reconcile them short of hand-retyping.

Both tables are append-only (`tecdoc_rule_versions.sealed` is guarded by a
trigger; `tecdoc_rules` has no immutability trigger of its own but is always
written as a whole sealed batch per version), so exporting/importing is the
same "insert only what's missing, keyed by rule_version" shape as
`export_policy_versions.py` -- no edit-collision case, just "does the other
database already have this version."

Usage:
    python -m scripts.export_tecdoc_rule_catalog --out tecdoc_rule_catalog_export.json
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
from ingestion.tecdoc.canonical_rule_migrations import (
    TECDOC_RULE_VERSIONS_TABLE,
    TECDOC_RULES_TABLE,
    run_tecdoc_rule_migrations,
)

_VERSION_COLUMNS = (
    "rule_version", "tecdoc_release", "source_note", "generated_by",
    "rule_count", "content_fingerprint", "sealed", "generated_at",
)
_RULE_COLUMNS = (
    "rule_version", "rule_id", "area", "entity_type", "source_field", "source_term",
    "key_table", "canonical_field", "canonical_value", "decision", "derivation",
    "support", "evidence", "created_at",
)


def export_catalog(connection: Connection) -> dict[str, list[dict[str, Any]]]:
    run_tecdoc_rule_migrations(connection)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {', '.join(_VERSION_COLUMNS)} FROM {TECDOC_RULE_VERSIONS_TABLE} "
            "WHERE sealed ORDER BY generated_at, rule_version"
        )
        versions = [dict(zip(_VERSION_COLUMNS, row, strict=True)) for row in cursor.fetchall()]
        cursor.execute(
            f"SELECT {', '.join(_RULE_COLUMNS)} FROM {TECDOC_RULES_TABLE} "
            "WHERE rule_version = ANY(%s) ORDER BY rule_version, rule_id",
            ([v["rule_version"] for v in versions],),
        )
        rules = [dict(zip(_RULE_COLUMNS, row, strict=True)) for row in cursor.fetchall()]
    return {"tecdoc_rule_versions": versions, "tecdoc_rules": rules}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default="tecdoc_rule_catalog_export.json",
        help="Path to write the JSON snapshot to.",
    )
    args = parser.parse_args()

    settings = get_ingestion_settings()
    datastores = DatastoreClients.from_settings(settings)
    with datastores.postgres.connect() as connection:
        catalog = export_catalog(connection)

    payload = {"exported_at": datetime.now(UTC).isoformat(), **catalog}
    Path(args.out).write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(
        f"Wrote {len(catalog['tecdoc_rule_versions'])} version(s), "
        f"{len(catalog['tecdoc_rules'])} rule(s) to {args.out}"
    )


if __name__ == "__main__":
    main()
