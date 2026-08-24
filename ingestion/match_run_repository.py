"""Version-pinned run claims and monotonic checkpoints for full matching runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.match_run_migrations import (
    MATCH_RUN_CHECKPOINTS_TABLE,
    MATCH_RUN_REASON_COUNTS_TABLE,
    MATCH_RUNS_TABLE,
)


class MatchRunMode(StrEnum):
    DRY_RUN = "dry_run"
    PERSIST = "persist"


@dataclass(frozen=True)
class MatchRunPins:
    operation_id: UUID
    source_system: str
    source_version: str
    source_batch_prefix: str
    expected_source_rows: int
    normalization_rule_version: str
    candidate_catalog_version: str
    policy_version: str
    code_revision: str
    mode: MatchRunMode = MatchRunMode.DRY_RUN

    def __post_init__(self) -> None:
        text = (
            self.source_system,
            self.source_version,
            self.source_batch_prefix,
            self.normalization_rule_version,
            self.candidate_catalog_version,
            self.policy_version,
            self.code_revision,
        )
        if any(not value.strip() for value in text):
            raise ValueError("match-run pinned text values must not be empty")
        if self.expected_source_rows < 1:
            raise ValueError("expected_source_rows must be positive")


@dataclass(frozen=True)
class MatchRunCounts:
    resolved: int = 0
    provisional: int = 0
    review_required: int = 0
    unmatched: int = 0
    hard_conflict: int = 0
    normalization_review: int = 0
    policy_excluded: int = 0
    failed: int = 0

    def __post_init__(self) -> None:
        if min(self.as_dict().values()) < 0:
            raise ValueError("match-run counts must not be negative")

    @property
    def processed(self) -> int:
        return sum(self.as_dict().values())

    def as_dict(self) -> dict[str, int]:
        return {
            "resolved": self.resolved,
            "provisional": self.provisional,
            "review_required": self.review_required,
            "unmatched": self.unmatched,
            "hard_conflict": self.hard_conflict,
            "normalization_review": self.normalization_review,
            "policy_excluded": self.policy_excluded,
            "failed": self.failed,
        }


@dataclass(frozen=True)
class MatchRunProgress:
    pins: MatchRunPins
    last_batch_number: int
    last_source_record_id: int
    last_source_cursor: str
    counts: MatchRunCounts
    created: bool


def claim_match_run(connection: Connection, pins: MatchRunPins) -> MatchRunProgress:
    """Create a run or resume it only when every immutable pin agrees."""

    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {MATCH_RUNS_TABLE} (operation_id, source_system, source_version, "
            "source_batch_prefix, expected_source_rows, normalization_rule_version, "
            "candidate_catalog_version, policy_version, code_revision, mode) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (operation_id) DO NOTHING",
            (
                pins.operation_id,
                pins.source_system.strip(),
                pins.source_version.strip(),
                pins.source_batch_prefix.strip(),
                pins.expected_source_rows,
                pins.normalization_rule_version.strip(),
                pins.candidate_catalog_version.strip(),
                pins.policy_version.strip(),
                pins.code_revision.strip(),
                pins.mode.value,
            ),
        )
        created = cursor.rowcount == 1
        cursor.execute(
            f"SELECT source_system, source_version, source_batch_prefix, "
            "expected_source_rows, normalization_rule_version, candidate_catalog_version, "
            "policy_version, code_revision, mode, status, last_batch_number, "
            f"last_source_record_id, last_source_cursor, resolved, provisional, review_required, unmatched, "
            f"hard_conflict, normalization_review, policy_excluded, failed "
            f"FROM {MATCH_RUNS_TABLE} WHERE operation_id = %s FOR UPDATE",
            (pins.operation_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("match run disappeared during claim")
    actual = (
        tuple(str(value) for value in row[:3])
        + (int(row[3]),)
        + tuple(str(value) for value in row[4:9])
    )
    expected = (
        pins.source_system.strip(),
        pins.source_version.strip(),
        pins.source_batch_prefix.strip(),
        pins.expected_source_rows,
        pins.normalization_rule_version.strip(),
        pins.candidate_catalog_version.strip(),
        pins.policy_version.strip(),
        pins.code_revision.strip(),
        pins.mode.value,
    )
    if actual != expected:
        raise ValueError("operation_id already exists with different pinned inputs")
    if str(row[9]) != "running":
        raise ValueError(f"cannot resume match run with status {row[9]}")
    counts = MatchRunCounts(*map(int, row[13:21]))
    return MatchRunProgress(pins, int(row[10]), int(row[11]), str(row[12]), counts, created)


def append_match_checkpoint(
    connection: Connection,
    *,
    operation_id: UUID,
    batch_number: int,
    last_source_record_id: int,
    last_source_cursor: str | None = None,
    counts: MatchRunCounts,
) -> None:
    """Append one cumulative checkpoint; retries must be identical and monotonic."""

    cursor_value = (last_source_cursor or str(last_source_record_id)).strip()
    if batch_number < 1 or last_source_record_id < 1 or counts.processed < 1 or not cursor_value:
        raise ValueError("checkpoint batch, source id and processed count must be positive")
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT last_batch_number, last_source_record_id, last_source_cursor, expected_source_rows, "
            f"resolved, provisional, review_required, unmatched, hard_conflict, "
            f"normalization_review, policy_excluded, failed FROM {MATCH_RUNS_TABLE} "
            "WHERE operation_id = %s AND status = 'running' FOR UPDATE",
            (operation_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError("running match run does not exist")
        previous_batch, previous_source = map(int, row[:2])
        previous_cursor, expected_rows = str(row[2]), int(row[3])
        previous = MatchRunCounts(*map(int, row[4:12]))
        if batch_number <= previous_batch:
            cursor.execute(
                f"SELECT last_source_record_id, last_source_cursor, processed, counters "
                f"FROM {MATCH_RUN_CHECKPOINTS_TABLE} "
                "WHERE operation_id = %s AND batch_number = %s",
                (operation_id, batch_number),
            )
            existing = cursor.fetchone()
            requested = (last_source_record_id, cursor_value, counts.processed, counts.as_dict())
            actual = (
                None
                if existing is None
                else (int(existing[0]), str(existing[1]), int(existing[2]), dict(existing[3]))
            )
            if actual != requested:
                raise ValueError("checkpoint retry differs from the immutable checkpoint")
            return
        if batch_number != previous_batch + 1 or last_source_record_id <= previous_source:
            raise ValueError("checkpoint must advance batch and source position exactly once")
        if previous_cursor and cursor_value <= previous_cursor:
            raise ValueError("checkpoint source cursor must advance")
        if counts.processed > expected_rows:
            raise ValueError("checkpoint exceeds expected source rows")
        if any(counts.as_dict()[key] < value for key, value in previous.as_dict().items()):
            raise ValueError("checkpoint counters must be monotonic")
        cursor.execute(
            f"INSERT INTO {MATCH_RUN_CHECKPOINTS_TABLE} "
            "(operation_id, batch_number, last_source_record_id, last_source_cursor, processed, counters) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                operation_id,
                batch_number,
                last_source_record_id,
                cursor_value,
                counts.processed,
                Jsonb(counts.as_dict()),
            ),
        )
        values = counts.as_dict()
        cursor.execute(
            f"UPDATE {MATCH_RUNS_TABLE} SET last_batch_number=%s, last_source_record_id=%s, last_source_cursor=%s, "
            "processed=%s, resolved=%s, provisional=%s, review_required=%s, unmatched=%s, "
            "hard_conflict=%s, normalization_review=%s, policy_excluded=%s, failed=%s, "
            "updated_at=now() WHERE operation_id=%s",
            (
                batch_number,
                last_source_record_id,
                cursor_value,
                counts.processed,
                *values.values(),
                operation_id,
            ),
        )


def complete_match_run(connection: Connection, operation_id: UUID) -> None:
    """Complete only a fully accounted run; identical completion is a no-op."""

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT status, processed, expected_source_rows FROM {MATCH_RUNS_TABLE} "
            "WHERE operation_id = %s FOR UPDATE",
            (operation_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError("match run does not exist")
        if str(row[0]) == "completed":
            return
        if str(row[0]) != "running" or int(row[1]) != int(row[2]):
            raise ValueError("match run cannot complete before every source row is accounted")
        cursor.execute(
            f"UPDATE {MATCH_RUNS_TABLE} SET status='completed', finished_at=now(), "
            "updated_at=now() WHERE operation_id=%s",
            (operation_id,),
        )


def increment_match_run_reason_counts(
    connection: Connection,
    *,
    operation_id: UUID,
    reason_counts: dict[str, int],
) -> None:
    """Add one uncommitted batch of sanitized reason aggregates."""

    normalized = {
        reason.strip(): count
        for reason, count in reason_counts.items()
        if reason.strip() and count > 0
    }
    if len(normalized) != len(reason_counts) or any(
        not isinstance(count, int) for count in reason_counts.values()
    ):
        raise ValueError("reason counts require unique non-empty codes and positive integers")
    if not normalized:
        return
    with connection.cursor() as cursor:
        cursor.executemany(
            f"INSERT INTO {MATCH_RUN_REASON_COUNTS_TABLE} "
            "(operation_id, reason_code, occurrence_count) VALUES (%s, %s, %s) "
            "ON CONFLICT (operation_id, reason_code) DO UPDATE SET "
            f"occurrence_count = {MATCH_RUN_REASON_COUNTS_TABLE}.occurrence_count "
            "+ EXCLUDED.occurrence_count, updated_at=now()",
            ((operation_id, reason, count) for reason, count in sorted(normalized.items())),
        )
