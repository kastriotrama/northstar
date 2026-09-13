"""Export every reviewed rule -- TS, TecDoc, and policy -- into one JSON file.

A single-file wrapper around the three existing per-table exporters, so a
teammate only has to hand over (and import) one file instead of three:

  - `rule_policy`: `export_rule_policy.py` -- the active TS catalog decisions
    and manufacturer-entity rulings layered on top of the hardcoded base set.
  - `tecdoc_resolution_rules`: `export_resolution_rules.py` -- rules a
    reviewer authored live against one TecDoc value.
  - `ts_resolution_rules`: `export_ts_resolution_rules.py` -- IF/THEN rules
    authored from the `/ts-data` resolve panel.

Each section keeps the exact shape its own exporter produces, so
`import_all_rules.py` (or the standalone `load_*` scripts) can consume it
unchanged.

Usage:
    python -m scripts.export_all_rules --out rules_export.json
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from ingestion.config import get_ingestion_settings
from ingestion.datastores import DatastoreClients
from scripts.export_resolution_rules import export_rules as export_tecdoc_resolution_rules
from scripts.export_rule_policy import export_overrides
from scripts.export_ts_resolution_rules import export_rules as export_ts_resolution_rules


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default="rules_export.json",
        help="Path to write the combined JSON snapshot to.",
    )
    args = parser.parse_args()

    settings = get_ingestion_settings()
    datastores = DatastoreClients.from_settings(settings)
    with datastores.postgres.connect() as connection:
        source_version, base_rule_version, overrides = export_overrides(connection)
        tecdoc_resolution_rules = export_tecdoc_resolution_rules(connection)
        ts_resolution_rules = export_ts_resolution_rules(connection)

    payload = {
        "exported_at": datetime.now(UTC).isoformat(),
        "rule_policy": {
            "source_version": source_version,
            "base_rule_version": base_rule_version,
            "overrides": overrides,
        },
        "tecdoc_resolution_rules": tecdoc_resolution_rules,
        "ts_resolution_rules": ts_resolution_rules,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(f"Wrote {args.out}:")
    print(f"  policy overrides:       {len(overrides)}")
    print(f"  tecdoc resolution rules: {len(tecdoc_resolution_rules)}")
    print(f"  ts resolution rules:     {len(ts_resolution_rules)}")


if __name__ == "__main__":
    main()
