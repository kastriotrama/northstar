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
match the file's version -- *unless* this DB's own copy was edited more
recently than the one arriving. That comparison is what makes this safe to
run in both directions between two live databases (a local DB and the online
one): an older edit arriving after a newer local one is skipped, not applied,
and reported back as a conflict rather than silently overwriting someone's
more recent ruling. A snapshot exported before `updated_at` was added has
none to compare, and always applies (the old, unconditional behaviour).

Defaults to a dry run that only reports what would change. Dry run does not
detect conflicts (that needs the current row's timestamp, which only a real
write path checks) -- it is a cheap preview of volume, not a full simulation.

Usage:
    python -m scripts.load_resolution_rules resolution_rules_export.json
    python -m scripts.load_resolution_rules resolution_rules_export.json --commit
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
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
            %(reviewed_by)s, COALESCE(%(updated_at)s, now()))
    ON CONFLICT (canonical_field, source_system, comparison_key) WHERE relation <> 'compatible'
    DO UPDATE SET
        source_term = EXCLUDED.source_term,
        key_table = EXCLUDED.key_table,
        decision = EXCLUDED.decision,
        canonical_value = EXCLUDED.canonical_value,
        note = EXCLUDED.note,
        reviewed_by = EXCLUDED.reviewed_by,
        updated_at = EXCLUDED.updated_at
    WHERE {TECDOC_RESOLUTION_RULES_TABLE}.updated_at <= EXCLUDED.updated_at
    RETURNING id
"""

_UPSERT_COMPATIBLE = f"""
    INSERT INTO {TECDOC_RESOLUTION_RULES_TABLE}
        (canonical_field, source_system, comparison_key, source_term, key_table,
         decision, canonical_value, relation, support, note, reviewed_by, updated_at)
    VALUES (%(canonical_field)s, %(source_system)s, %(comparison_key)s, %(source_term)s,
            %(key_table)s, %(decision)s, %(canonical_value)s, %(relation)s, %(support)s,
            %(note)s, %(reviewed_by)s, COALESCE(%(updated_at)s, now()))
    ON CONFLICT (canonical_field, source_system, comparison_key, canonical_value)
        WHERE relation = 'compatible'
    DO UPDATE SET
        source_term = EXCLUDED.source_term,
        key_table = EXCLUDED.key_table,
        support = EXCLUDED.support,
        note = EXCLUDED.note,
        reviewed_by = EXCLUDED.reviewed_by,
        updated_at = EXCLUDED.updated_at
    WHERE {TECDOC_RESOLUTION_RULES_TABLE}.updated_at <= EXCLUDED.updated_at
    RETURNING id
"""


@dataclass(frozen=True)
class TecDocRuleLoadResult:
    single_target: int
    compatible: int
    #: Rows this DB's own copy was newer than -- left untouched, not
    #: overwritten. Each entry carries the natural key so a reviewer can look
    #: the row up and decide by hand which version should actually stand.
    conflicts: tuple[dict[str, Any], ...] = ()


def _parsed_updated_at(rule: dict[str, Any]) -> dict[str, Any]:
    """A copy of `rule` with `updated_at` as a real `datetime`.

    The JSON round trip (file or HTTP) only ever carries an ISO string or
    nothing; psycopg needs an actual `datetime` to bind against a
    `timestamptz` parameter; a bare string has no implicit cast in an INSERT.
    """

    raw = rule.get("updated_at")
    if raw is None or isinstance(raw, datetime):
        return rule
    return {**rule, "updated_at": datetime.fromisoformat(str(raw))}


def load_rules(
    connection: Connection, rules: list[dict[str, Any]], *, commit: bool
) -> TecDocRuleLoadResult:
    run_tecdoc_resolution_migrations(connection)
    single_target = 0
    compatible = 0
    conflicts: list[dict[str, Any]] = []
    with connection.cursor() as cursor:
        for rule in rules:
            is_compatible = rule["relation"] == "compatible"
            if not commit:
                # Dry run never touches the table, so there is nothing to
                # compare a timestamp against -- report volume only.
                compatible += is_compatible
                single_target += not is_compatible
                continue
            statement = _UPSERT_COMPATIBLE if is_compatible else _UPSERT_SINGLE_TARGET
            cursor.execute(statement, _parsed_updated_at(rule))
            if cursor.fetchone() is not None:
                compatible += is_compatible
                single_target += not is_compatible
            else:
                conflicts.append(
                    {
                        "canonical_field": rule["canonical_field"],
                        "source_system": rule["source_system"],
                        "comparison_key": rule["comparison_key"],
                        "canonical_value": rule.get("canonical_value"),
                        "incoming_updated_at": rule.get("updated_at"),
                    }
                )
    if commit:
        connection.commit()
    return TecDocRuleLoadResult(
        single_target=single_target, compatible=compatible, conflicts=tuple(conflicts)
    )


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
        result = load_rules(connection, rules, commit=args.commit)

    verb = "Wrote" if args.commit else "Would write"
    print(f"{verb}: {result.single_target} single-target, {result.compatible} compatible")
    if result.conflicts:
        print(f"Skipped {len(result.conflicts)} row(s) with a newer local edit:")
        for conflict in result.conflicts:
            print(f"  {conflict}")
    if not args.commit:
        print("Dry run -- pass --commit to actually write.")


if __name__ == "__main__":
    main()
