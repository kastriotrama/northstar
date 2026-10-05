"""Read the tyre sizes of stopped records again, and nothing else.

A record whose tyre size an older parser could not read normalized to review
(`tyre_size_unrecognized`), and a record in review never reaches the matcher --
although the matcher does not use tyre sizes. The parser has since learned the
notations it rejected. Normalizing those records again would also apply every
other change the pipeline has seen since; this step applies the tyre parser
alone:

- it takes each such record's latest normalization result,
- runs today's tyre step on the record's raw tyre texts,
- and, when every tyre now reads, appends a new result that is the old one with
  its tyre keys replaced, the tyre reason removed and the status that follows.

Every other normalized value, candidate and reason is carried over unchanged. A
record whose tyre text still does not read (a typo) is left as it is. The new
row is tagged `<old pipeline>+tyres-<parser version>`, so it is told apart from
a full normalization and a second run writes nothing.

Afterwards the vehicles those records created are refreshed through the same
path the backfill uses, and the registry screen's status column is brought in
line. Sync, PostgreSQL only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.normalization_migrations import NORMALIZATION_RESULTS_TABLE
from ingestion.normalization_repository import normalization_uuid
from ingestion.normalization_rules import (
    PIPELINE_VERSION,
    TYRE_REVIEW_REASON,
    outcome_status,
    read_tyres,
)
from ingestion.vehicle_core_fields import normalize_plate
from ingestion.vehicle_core_migrations import VEHICLES_TABLE
from ingestion.vehicle_core_store import current_owners
from ingestion.vehicle_core_ts import refresh_vehicle_core_records
from ingestion.vehicle_facts import STAGING_TABLE
from ingestion.vehicle_facts_migrations import VEHICLE_FACTS_TABLE

#: Marks a result this step wrote; also what makes a second run a no-op.
_STEP = "+tyres-"
#: What the tyre step owns in a result: a re-read replaces exactly these and nothing else
#: (the `writes` of the catalog's tyre transform, and the texts it parks as candidates).
TYRE_NORMALIZED_KEYS: tuple[str, ...] = ("tyre_front", "tyre_rear", "tyre_staggered", "rim_diameter_in")
TYRE_SOURCE_FIELDS: tuple[str, ...] = ("tyre_front", "tyre_rear")


def reparse_label(pipeline_version: str) -> str:
    """The pipeline label of a result whose tyres were read again by today's parser."""

    return f"{pipeline_version}{_STEP}{PIPELINE_VERSION.rsplit('-', 1)[-1]}"


@dataclass(frozen=True)
class RereadResult:
    """One record's result with only its tyre part redone."""

    payload: dict[str, Any]
    review_reasons: tuple[str, ...]
    status: str
    confidence: float


def reread(
    payload: Mapping[str, Any], review_reasons: Sequence[str], raw_record: Mapping[str, Any]
) -> RereadResult | None:
    """The result with today's tyre reading, or None when a tyre still does not read.

    Pure. Only the tyre step's own keys change; the status follows from what is
    then left open, by the pipeline's own rule.
    """

    normalized_tyres, candidate_tyres, tyre_reasons = read_tyres(raw_record)
    if TYRE_REVIEW_REASON in tyre_reasons:
        return None
    normalized = {
        key: value
        for key, value in dict(payload.get("normalized") or {}).items()
        if key not in TYRE_NORMALIZED_KEYS
    }
    normalized.update(normalized_tyres)
    candidates = {
        key: value
        for key, value in dict(payload.get("candidates") or {}).items()
        if key not in TYRE_SOURCE_FIELDS
    }
    candidates.update(candidate_tyres)
    reasons = tuple(reason for reason in review_reasons if reason != TYRE_REVIEW_REASON)
    status, confidence = outcome_status(reasons, candidates, normalized)
    updated = dict(payload)
    updated["normalized"] = normalized
    updated["candidates"] = candidates
    updated["confidence"] = confidence
    return RereadResult(updated, reasons, status, confidence)


@dataclass
class TyreReparseSummary:
    #: Records whose latest result carries the tyre reason.
    stopped: int = 0
    #: Of those, records whose tyre texts all read now.
    readable: int = 0
    #: Results written (0 in a dry run).
    written: int = 0
    #: Records left as they are: a tyre text still does not read.
    still_unread: int = 0
    #: Vehicles refreshed from the rewritten records.
    vehicles_refreshed: int = 0
    #: The status the rewritten records end in.
    statuses: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "stopped": self.stopped,
            "readable": self.readable,
            "written": self.written,
            "still_unread": self.still_unread,
            "vehicles_refreshed": self.vehicles_refreshed,
            "statuses": dict(sorted(self.statuses.items())),
        }


_PAGE_STATEMENT = f"""
    SELECT latest.source_record_id, latest.source_system, latest.source_batch_id,
           latest.mapping_version, latest.rule_version, latest.pipeline_version,
           latest.normalized_payload, latest.applied_rule_ids, latest.review_reasons,
           raw.raw_record
    FROM (
        SELECT DISTINCT ON (source_record_id) *
        FROM {NORMALIZATION_RESULTS_TABLE}
        WHERE source_table = %s AND source_record_id > %s
        ORDER BY source_record_id, updated_at DESC, id DESC
    ) AS latest
    JOIN {STAGING_TABLE} AS raw ON raw.id = latest.source_record_id
    WHERE latest.review_reasons @> ARRAY[%s]
      AND position(%s in latest.pipeline_version) = 0
    ORDER BY latest.source_record_id
    LIMIT %s
"""


def reparse_tyre_sizes(
    connection: Connection[Any], *, dry_run: bool = True, page_size: int = 2000
) -> TyreReparseSummary:
    """Re-read the tyre sizes of every record stopped for them. Commits per page.

    A dry run reads and counts and writes nothing. Idempotent: a result this step
    wrote is not picked up again, and the result id is derived from the record and
    the step's label.
    """

    summary = TyreReparseSummary()
    after = 0
    while True:
        with connection.cursor() as cursor:
            cursor.execute(
                _PAGE_STATEMENT, (STAGING_TABLE, after, TYRE_REVIEW_REASON, _STEP, page_size)
            )
            rows = cursor.fetchall()
        if not rows:
            break
        after = int(rows[-1][0])
        rewritten: dict[int, RereadResult] = {}
        for row in rows:
            record_id = int(row[0])
            summary.stopped += 1
            result = reread(dict(row[6] or {}), list(row[8] or []), dict(row[9] or {}))
            if result is None:
                summary.still_unread += 1
                continue
            summary.readable += 1
            summary.statuses[result.status] = summary.statuses.get(result.status, 0) + 1
            if dry_run:
                continue
            label = reparse_label(str(row[5]))
            payload = dict(result.payload)
            payload["pipeline_version"] = label
            with connection.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {NORMALIZATION_RESULTS_TABLE} "
                    "(normalization_id, source_system, source_batch_id, source_table, "
                    "source_record_id, mapping_version, rule_version, pipeline_version, status, "
                    "normalized_payload, applied_rule_ids, review_reasons, confidence) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (normalization_id) DO NOTHING",
                    (
                        normalization_uuid(record_id, str(row[3]), str(row[4]), label),
                        row[1],
                        row[2],
                        STAGING_TABLE,
                        record_id,
                        row[3],
                        row[4],
                        label,
                        result.status,
                        Jsonb(payload),
                        list(row[7] or []),
                        list(result.review_reasons),
                        result.confidence,
                    ),
                )
                summary.written += cursor.rowcount
            rewritten[record_id] = result
        if rewritten:
            _sync_registry_status(connection, rewritten)
            connection.commit()
            summary.vehicles_refreshed += _refresh_vehicles(connection, sorted(rewritten))
    return summary


def _sync_registry_status(connection: Connection[Any], rewritten: Mapping[int, RereadResult]) -> None:
    """Bring the registry screen's status column in line with the new results."""

    by_status: dict[str, list[int]] = {}
    for record_id, result in rewritten.items():
        by_status.setdefault(result.status, []).append(record_id)
    with connection.cursor() as cursor:
        for status, record_ids in by_status.items():
            cursor.execute(
                f"UPDATE {VEHICLE_FACTS_TABLE} SET norm_status = %s "
                "WHERE source_record_id = ANY(%s) AND norm_status IS DISTINCT FROM %s",
                (status, record_ids, status),
            )


def _refresh_vehicles(connection: Connection[Any], record_ids: Sequence[int]) -> int:
    """Refresh the vehicles these records created; a record without one mints nothing.

    A vehicle whose plate another vehicle holds today is left as it is. Its
    record still names the plate, so merging the record again would open the
    plate on it a second time, which the database refuses (one current holder
    per plate). Such a vehicle lost its plate to a newer one and is out of the
    register; its record's new result is stored all the same, and matching reads
    the result, not the vehicle's copy of the status.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT vehicle.ts_record_id, vehicle.vehicle_id, raw.raw_record->>'plate' "
            f"FROM {VEHICLES_TABLE} AS vehicle "
            f"JOIN {STAGING_TABLE} AS raw ON raw.id = vehicle.ts_record_id "
            "WHERE vehicle.ts_record_id = ANY(%s)",
            (list(record_ids),),
        )
        rows = [(int(row[0]), str(row[1]), normalize_plate(row[2])) for row in cursor.fetchall()]
    owners = current_owners(connection, "plate", [plate for _, _, plate in rows if plate])
    linked = sorted(
        {
            record_id
            for record_id, vehicle_id, plate in rows
            if not plate or owners.get(plate, vehicle_id) == vehicle_id
        }
    )
    if not linked:
        return 0
    refresh_vehicle_core_records(connection, linked)
    return len(linked)
