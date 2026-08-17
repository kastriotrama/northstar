"""Stream remote passenger cars through local normalization with bounded disk use."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from ingestion.active_rules import load_active_rules
from ingestion.normalization_service import normalize_batch

PASSENGER_FILTER_SQL = """
(
    upper(trim(coalesce(eu_category, ''))) IN ('M1', 'M1G')
    OR (
        nullif(trim(eu_category), '') IS NULL
        AND upper(trim(coalesce(vehicle_type, ''))) = 'PB'
    )
)
"""

CHECKPOINT_TABLE = "core.remote_passenger_import_parts"


@dataclass(frozen=True)
class ImportPart:
    part_number: int
    batch_id: str
    first_plate: str
    last_plate: str
    source_count: int


def batch_id_for(prefix: str, part_number: int) -> str:
    if part_number < 1:
        raise ValueError("part_number must be positive")
    return f"{prefix}-part-{part_number:03d}"


def _required_url(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be set")
    return value


def _ensure_checkpoint_table(connection: Connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {CHECKPOINT_TABLE} (
                import_prefix TEXT NOT NULL,
                part_number INTEGER NOT NULL CHECK (part_number > 0),
                batch_id TEXT NOT NULL UNIQUE,
                first_plate TEXT NOT NULL,
                last_plate TEXT NOT NULL,
                source_count INTEGER NOT NULL CHECK (source_count > 0),
                resolved INTEGER NOT NULL CHECK (resolved >= 0),
                provisional INTEGER NOT NULL CHECK (provisional >= 0),
                review_required INTEGER NOT NULL CHECK (review_required >= 0),
                failed INTEGER NOT NULL CHECK (failed >= 0),
                completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (import_prefix, part_number)
            )
            """
        )
    connection.commit()


def _resume_position(connection: Connection, prefix: str) -> tuple[int, str]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT part_number, last_plate FROM {CHECKPOINT_TABLE} "
            "WHERE import_prefix = %s ORDER BY part_number DESC LIMIT 1",
            (prefix,),
        )
        row = cursor.fetchone()
    return (1, "") if row is None else (int(row[0]) + 1, str(row[1]))


def _fetch_remote_part(
    remote_url: str,
    *,
    after_plate: str,
    limit: int,
) -> list[dict[str, object]]:
    with psycopg.connect(remote_url) as connection, connection.cursor(
        row_factory=dict_row
    ) as cursor:
        cursor.execute(
            f"SELECT * FROM public.swedish_vehicles WHERE {PASSENGER_FILTER_SQL} "
            "AND plate > %s ORDER BY plate LIMIT %s",
            (after_plate, limit),
        )
        return [dict(row) for row in cursor.fetchall()]


def _stage_part(
    connection: Connection,
    *,
    batch_id: str,
    records: Sequence[dict[str, object]],
) -> None:
    with connection.cursor() as cursor, cursor.copy(
        "COPY staging.transportstyrelsen_raw (source_batch_id, raw_record) FROM STDIN"
    ) as copy:
        for record in records:
            copy.write_row((batch_id, json.dumps(record, default=str)))
    connection.commit()


def _staged_count(connection: Connection, batch_id: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM staging.transportstyrelsen_raw WHERE source_batch_id = %s",
            (batch_id,),
        )
        row = cursor.fetchone()
    return int(row[0]) if row is not None else 0


def _store_checkpoint_and_prune(
    connection: Connection,
    *,
    prefix: str,
    part: ImportPart,
    resolved: int,
    provisional: int,
    review_required: int,
    failed: int,
    retain_raw: bool = False,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {CHECKPOINT_TABLE} "
            "(import_prefix, part_number, batch_id, first_plate, last_plate, source_count, "
            "resolved, provisional, review_required, failed) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                prefix,
                part.part_number,
                part.batch_id,
                part.first_plate,
                part.last_plate,
                part.source_count,
                resolved,
                provisional,
                review_required,
                failed,
            ),
        )
        cursor.execute(
            "DELETE FROM core.normalization_results "
            "WHERE source_batch_id = %s AND status NOT IN ('review_required', 'failed')",
            (part.batch_id,),
        )
        if not retain_raw:
            cursor.execute(
                "DELETE FROM staging.transportstyrelsen_raw AS raw "
                "WHERE source_batch_id = %s AND NOT EXISTS ("
                "SELECT 1 FROM core.normalization_results AS result "
                "WHERE result.source_record_id = raw.id AND result.source_batch_id = %s)",
                (part.batch_id, part.batch_id),
            )
    connection.commit()


def run(*, prefix: str, batch_size: int, retain_raw: bool = False) -> None:
    if not 1 <= batch_size <= 100_000:
        raise ValueError("batch_size must be between 1 and 100000")

    remote_url = _required_url("REMOTE_DATABASE_URL")
    local_url = _required_url("DATABASE_URL")
    with psycopg.connect(local_url) as local:
        _ensure_checkpoint_table(local)
        rule_set, manufacturer_rules = load_active_rules(local)
        part_number, after_plate = _resume_position(local, prefix)

        while True:
            records = _fetch_remote_part(
                remote_url,
                after_plate=after_plate,
                limit=batch_size,
            )
            if not records:
                break
            plates = [str(record["plate"]) for record in records]
            batch_id = batch_id_for(prefix, part_number)
            staged_count = _staged_count(local, batch_id)
            if staged_count == 0:
                _stage_part(local, batch_id=batch_id, records=records)
            elif staged_count != len(records):
                raise RuntimeError(
                    f"partial staging batch {batch_id}: expected {len(records)}, "
                    f"found {staged_count}"
                )
            summary = normalize_batch(
                local,
                batch_id=batch_id,
                page_size=5000,
                rule_set=rule_set,
                manufacturer_entity_rules=manufacturer_rules,
            )
            part = ImportPart(
                part_number=part_number,
                batch_id=batch_id,
                first_plate=plates[0],
                last_plate=plates[-1],
                source_count=len(records),
            )
            _store_checkpoint_and_prune(
                local,
                prefix=prefix,
                part=part,
                resolved=summary.resolved,
                provisional=summary.provisional,
                review_required=summary.review_required,
                failed=summary.failed,
                retain_raw=retain_raw,
            )
            print(
                json.dumps(
                    {
                        "part": part_number,
                        "last_plate": plates[-1],
                        "processed": summary.processed,
                        "resolved": summary.resolved,
                        "provisional": summary.provisional,
                        "review_required": summary.review_required,
                        "failed": summary.failed,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            after_plate = plates[-1]
            part_number += 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--prefix",
        default="normalization-remote-passenger-6515471",
    )
    parser.add_argument("--batch-size", type=int, default=25_000)
    parser.add_argument(
        "--retain-raw",
        action="store_true",
        help="Retain every staged raw passenger row while pruning non-review results.",
    )
    args = parser.parse_args()
    run(prefix=args.prefix, batch_size=args.batch_size, retain_raw=args.retain_raw)


if __name__ == "__main__":
    main()
