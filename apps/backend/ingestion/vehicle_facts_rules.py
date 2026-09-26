"""Apply and retire resolution rules across the whole projection.

Rules used to be welded to one match-chunk build, so a rule could only ever
reach the rows in that build -- 226,529 of 7,255,433. The predicate compiled
here runs against the projection instead, which is every car.

That changes what applying a rule costs. `brand = VOLVO -> drive_type` covers
232,136 rows rather than the couple of hundred a build-scoped rule reached, so
application is a batched, resumable job: each batch writes the ledger and the
projection overlay in one transaction, and reports a cursor. A synchronous
request would hold a transaction open across millions of rows and roll the whole
thing back on any interruption.

A rule normally only fills gaps, which is what keeps it from trampling a value
normalization already derived. An *override* rule is the deliberate exception:
the registry says an XC40 is an estate, a reviewer knows it is an SUV, and
saying so has to reach rows that already carry the wrong answer. It selects on
"the effective value is not what I am asserting" instead of "nothing is there",
supersedes whatever resolution those rows carried, and writes its own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection

from ingestion.match_chunk_migrations import MATCH_FIELD_RESOLUTIONS_TABLE
from ingestion.vehicle_facts_migrations import (
    NORMALIZED_INTEGER_FIELDS,
    RESOLVABLE_FIELDS,
    VEHICLE_FACTS_TABLE,
    effective_value,
    unresolved_predicate,
)
from ingestion.vehicle_facts_query import CompiledPredicate

DEFAULT_BATCH_SIZE = 50_000

# Applying a rule is bookkept as an ingest job run, reusing the table that
# already models exactly this: a long unit of work with a status, a progress
# count, and a terminal error. `batch_id` carries the rule and one run's own id,
# so a rule can be run again later while two runs of it can never overlap.
APPLY_JOB_NAME = "apply-resolution-rule"

_INTEGER_FIELDS: frozenset[str] = frozenset(NORMALIZED_INTEGER_FIELDS)

# Called inside a batch's transaction, before it commits, with what the batch
# resolved: (connection, rule_id, target_field, target_value, after_id, through_id).
# Production passes `vehicle_core_review.sync_applied_review`, so the NorthStar
# vehicles receive the batch in the same transaction as the TS projection.
BatchSync = Callable[..., Any]
RetireSync = Callable[..., Any]


@dataclass(frozen=True)
class ApplyProgress:
    """One batch's outcome, and where the next one resumes."""

    rows_written: int
    cursor: int
    exhausted: bool


@dataclass(frozen=True)
class ApplySummary:
    rows_written: int
    batches: int
    cursor: int


def _overlay_value(target_field: str) -> str:
    """The projection column keeps the rule's value in the field's own type."""

    if target_field in _INTEGER_FIELDS:
        return "(CASE WHEN %s ~ '^-?[0-9]+$' THEN (%s)::bigint END)::int"
    return "%s"


def _target_predicate(target_field: str, *, override: bool) -> str:
    """Which rows this rule still has work to do on.

    Filling a gap means "nothing is there". Overriding means "what is there is
    not what I am asserting" -- which also makes a re-run a no-op once the rows
    agree with the rule, so both modes stay idempotent.
    """

    if not override:
        return unresolved_predicate(target_field)
    return f"{effective_value(target_field, cast='text')} IS DISTINCT FROM %s"


def apply_rule_batch(
    connection: Connection,
    *,
    rule_id: UUID,
    build_id: UUID,
    predicate: CompiledPredicate,
    target_field: str,
    target_value: str,
    after_id: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
    override: bool = False,
    on_batch: BatchSync | None = None,
) -> ApplyProgress:
    """Resolve one batch of the cars a rule covers.

    The ledger insert and the projection overlay happen in one transaction, so
    the two can never disagree about what a rule resolved. Rows that already
    carry a value are excluded by the predicate itself, which is what keeps a
    rule from overwriting a decision normalization already made; `ON CONFLICT DO
    NOTHING` then makes a re-run idempotent rather than merely harmless.

    An override rule takes the rows the ordinary one skips. Its batch is
    superseded in the ledger first, as its own statement: the active-resolution
    index is partial on `superseded_at IS NULL`, and an insert sharing a
    statement with the update that vacates it is checked against the snapshot
    the update started from, so the two must not be the same statement.
    """

    if target_field not in RESOLVABLE_FIELDS:
        raise ValueError(f"{target_field!r} is not a resolvable field")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    overlay = _overlay_value(target_field)
    overlay_parameters: list[Any] = (
        [target_value, target_value] if target_field in _INTEGER_FIELDS else [target_value]
    )
    target_clause = _target_predicate(target_field, override=override)
    target_parameters: list[Any] = [target_value] if override else []
    batch_sql = f"""
        SELECT source_record_id
        FROM {VEHICLE_FACTS_TABLE}
        WHERE {predicate.sql}
          AND ({target_clause})
          AND source_record_id > %s
        ORDER BY source_record_id
        LIMIT %s
    """
    batch_parameters = [
        *predicate.parameters,
        *target_parameters,
        after_id,
        batch_size,
    ]

    with connection.cursor() as cursor:
        if override:
            cursor.execute(
                f"""
                WITH batch AS ({batch_sql})
                UPDATE {MATCH_FIELD_RESOLUTIONS_TABLE} AS fr
                SET superseded_at = now()
                FROM batch
                WHERE fr.source_record_id = batch.source_record_id
                  AND fr.target_field = %s
                  AND fr.superseded_at IS NULL
                """,
                [*batch_parameters, target_field],
            )
        cursor.execute(
            f"""
            WITH batch AS ({batch_sql}),
            ledger AS (
                INSERT INTO {MATCH_FIELD_RESOLUTIONS_TABLE}
                    (rule_id, build_id, source_record_id, target_field, target_value)
                SELECT %s, %s, source_record_id, %s, %s FROM batch
                ON CONFLICT DO NOTHING
                RETURNING source_record_id
            ),
            overlay AS (
                UPDATE {VEHICLE_FACTS_TABLE} AS vf
                SET r_{target_field} = {overlay}
                FROM batch
                WHERE vf.source_record_id = batch.source_record_id
                RETURNING vf.source_record_id
            )
            SELECT
                (SELECT count(*) FROM ledger)::bigint,
                (SELECT coalesce(max(source_record_id), %s) FROM batch)::bigint,
                (SELECT count(*) FROM batch)::bigint
            """,
            [
                *batch_parameters,
                rule_id,
                build_id,
                target_field,
                target_value,
                *overlay_parameters,
                after_id,
            ],
        )
        row = cursor.fetchone()
    written = int(row[0]) if row else 0
    cursor_position = int(row[1]) if row else after_id
    scanned = int(row[2]) if row else 0
    if on_batch is not None:
        # Same transaction: the TS projection and the vehicles can never disagree
        # about what a rule said.
        on_batch(
            connection,
            rule_id=rule_id,
            target_field=target_field,
            target_value=target_value,
            after_id=after_id,
            through_id=cursor_position,
        )
    connection.commit()
    return ApplyProgress(
        rows_written=written, cursor=cursor_position, exhausted=scanned < batch_size
    )


def apply_rule(
    connection: Connection,
    *,
    rule_id: UUID,
    build_id: UUID,
    predicate: CompiledPredicate,
    target_field: str,
    target_value: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    after_id: int = 0,
    override: bool = False,
    progress: Any = None,
    on_batch: BatchSync | None = None,
) -> ApplySummary:
    """Run a rule to completion, one committed batch at a time."""

    cursor_position = after_id
    rows_written = 0
    batches = 0

    while True:
        step = apply_rule_batch(
            connection,
            rule_id=rule_id,
            build_id=build_id,
            predicate=predicate,
            target_field=target_field,
            target_value=target_value,
            after_id=cursor_position,
            batch_size=batch_size,
            override=override,
            on_batch=on_batch,
        )
        rows_written += step.rows_written
        cursor_position = step.cursor
        batches += 1
        if progress is not None:
            progress(rows_written, cursor_position)
        if step.exhausted:
            break

    return ApplySummary(
        rows_written=rows_written, batches=batches, cursor=cursor_position
    )


def retire_rule(
    connection: Connection,
    *,
    rule_id: UUID,
    target_field: str,
    on_retire: RetireSync | None = None,
) -> int:
    """Supersede everything a rule wrote and clear its overlay.

    The ledger rows are marked superseded rather than deleted -- it is an
    append-only record of what was asserted and when -- while the projection
    column is cleared so the cars reappear as unresolved immediately. Who
    retired it is recorded on the rule, not on each of its resolutions: the
    table's trigger permits `superseded_at` to change and nothing else.

    Retiring an override rule returns its cars to what normalization derived,
    not to whatever earlier rule the override displaced: the overlay is cleared,
    and a superseded resolution stays superseded. Re-running that earlier rule
    is how it comes back, which is also the only way it stays a decision
    somebody made rather than one that reappeared on its own.
    """

    if target_field not in RESOLVABLE_FIELDS:
        raise ValueError(f"{target_field!r} is not a resolvable field")

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH superseded AS (
                UPDATE {MATCH_FIELD_RESOLUTIONS_TABLE}
                SET superseded_at = now()
                WHERE rule_id = %s AND superseded_at IS NULL
                RETURNING source_record_id
            ),
            cleared AS (
                UPDATE {VEHICLE_FACTS_TABLE} AS vf
                SET r_{target_field} = NULL
                FROM superseded
                WHERE vf.source_record_id = superseded.source_record_id
                RETURNING vf.source_record_id
            )
            SELECT (SELECT count(*) FROM superseded)::bigint
            """,
            [rule_id],
        )
        row = cursor.fetchone()
    if on_retire is not None:
        on_retire(connection, rule_id=rule_id, target_field=target_field)
    connection.commit()
    return int(row[0]) if row else 0
