"""One-time port of the retired core.vocabulary_alignments seed rows.

`core.vocabulary_alignments` (versioned, sealed) is retired in favor of the
same live `core.tecdoc_resolution_rules` table TecDoc's own value resolution
already uses, extended with `source_system`/`relation`/`support` -- see
`ingestion/tecdoc/resolution_migrations.py` and `ingestion/vocabulary_alignment.py`.

This ports the five rows `ingestion/vocabulary_seed.py` used to seed:
electricity/electric and methane/cng (equivalent), and ethanol/petrol,
2wd/fwd, 2wd/rwd (compatible -- broader-than, never scored as a match). Run
once per database; every insert is idempotent (ON CONFLICT DO UPDATE), so
re-running is harmless.

Usage: python -m scripts.port_vocabulary_alignment_seed --reviewed-by <you>
"""

from __future__ import annotations

import argparse

from ingestion.config import get_ingestion_settings
from ingestion.datastores import DatastoreClients
from ingestion.tecdoc.canonical_rule_proposals import comparison_key
from ingestion.tecdoc.resolution_migrations import (
    TECDOC_RESOLUTION_RULES_TABLE,
    run_tecdoc_resolution_migrations,
)

_DRIVE_FLAG_NOTE = (
    "The registry's is_4wd flag only asserts 'not all-wheel-drive'; it cannot "
    "distinguish front- from rear-wheel drive. TecDoc's {} is one of two "
    "possibilities, never confirmed by the flag alone."
)

_EQUIVALENT_ROWS = (
    # (canonical_field, ts_term, canonical_value, note)
    (
        "fuel", "electricity", "electric",
        (
            "Same concept, different canonicalisation. TecDoc KT-088 label "
            "'Electric' becomes 'electric'; the registry rules produce "
            "'electricity'. Disjoint sets, so no electric vehicle could "
            "intersect any of the 551 electric KTypes."
        ),
    ),
    ("fuel", "methane", "cng", "TecDoc folds Natural Gas and Biogas into cng."),
)

_COMPATIBLE_ROWS = (
    # (canonical_field, ts_term, canonical_value, support, note)
    (
        "fuel", "ethanol", "petrol", 627,
        (
            "TecDoc folds E85/E10/E5 blends into petrol. A granularity "
            "difference rather than a synonym, so it must score neutral and "
            "never as agreement."
        ),
    ),
    ("drive", "2wd", "fwd", 744197, _DRIVE_FLAG_NOTE.format("fwd")),
    ("drive", "2wd", "rwd", 744197, _DRIVE_FLAG_NOTE.format("rwd")),
)


def port_seed(connection, *, reviewed_by: str) -> dict[str, int]:
    run_tecdoc_resolution_migrations(connection)
    equivalent_written = 0
    with connection.cursor() as cursor:
        for canonical_field, ts_term, canonical_value, note in _EQUIVALENT_ROWS:
            cursor.execute(
                f"""
                INSERT INTO {TECDOC_RESOLUTION_RULES_TABLE}
                    (canonical_field, source_system, comparison_key, source_term,
                     decision, canonical_value, relation, note, reviewed_by, updated_at)
                VALUES (%s, 'transportstyrelsen', %s, %s, 'accepted', %s, 'equivalent',
                        %s, %s, now())
                ON CONFLICT (canonical_field, source_system, comparison_key)
                    WHERE relation <> 'compatible' DO UPDATE SET
                    source_term = EXCLUDED.source_term,
                    canonical_value = EXCLUDED.canonical_value,
                    relation = EXCLUDED.relation,
                    note = EXCLUDED.note,
                    reviewed_by = EXCLUDED.reviewed_by,
                    updated_at = now()
                """,
                (
                    canonical_field,
                    comparison_key(ts_term),
                    ts_term,
                    canonical_value,
                    note,
                    reviewed_by,
                ),
            )
            equivalent_written += cursor.rowcount

        compatible_written = 0
        for canonical_field, ts_term, canonical_value, support, note in _COMPATIBLE_ROWS:
            cursor.execute(
                f"""
                INSERT INTO {TECDOC_RESOLUTION_RULES_TABLE}
                    (canonical_field, source_system, comparison_key, source_term, decision,
                     canonical_value, relation, support, note, reviewed_by, updated_at)
                VALUES (%s, 'transportstyrelsen', %s, %s, 'accepted', %s, 'compatible', %s,
                        %s, %s, now())
                ON CONFLICT (canonical_field, source_system, comparison_key, canonical_value)
                    WHERE relation = 'compatible' DO UPDATE SET
                    source_term = EXCLUDED.source_term,
                    support = EXCLUDED.support,
                    note = EXCLUDED.note,
                    reviewed_by = EXCLUDED.reviewed_by,
                    updated_at = now()
                """,
                (
                    canonical_field,
                    comparison_key(ts_term),
                    ts_term,
                    canonical_value,
                    support,
                    note,
                    reviewed_by,
                ),
            )
            compatible_written += cursor.rowcount
    connection.commit()
    return {"equivalent": equivalent_written, "compatible": compatible_written}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reviewed-by", required=True, help="Actor accountable for these rows."
    )
    args = parser.parse_args()

    settings = get_ingestion_settings()
    datastores = DatastoreClients.from_settings(settings)
    with datastores.postgres.connect() as connection:
        result = port_seed(connection, reviewed_by=args.reviewed_by)
    print(result)


if __name__ == "__main__":
    main()
