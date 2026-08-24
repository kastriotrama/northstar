"""Checkpointed, injected execution loop for write-free full matching audits."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from psycopg import Connection

from ingestion.match_run_repository import (
    MatchRunCounts,
    MatchRunMode,
    MatchRunPins,
    append_match_checkpoint,
    claim_match_run,
    complete_match_run,
)

MatchTerminal = Literal[
    "resolved",
    "provisional",
    "review_required",
    "unmatched",
    "hard_conflict",
    "normalization_review",
    "policy_excluded",
    "failed",
]


@dataclass(frozen=True)
class MatchSourceRecord:
    source_record_id: int
    payload: dict[str, object]

    def __post_init__(self) -> None:
        if self.source_record_id < 1:
            raise ValueError("source_record_id must be positive")


FetchPage = Callable[[int, int], Sequence[MatchSourceRecord]]
EvaluateRecord = Callable[[MatchSourceRecord], MatchTerminal]


def run_dry_match_audit(
    connection: Connection,
    *,
    pins: MatchRunPins,
    fetch_page: FetchPage,
    evaluate_record: EvaluateRecord,
    page_size: int = 25_000,
) -> MatchRunCounts:
    """Evaluate a pinned cohort without allowing decision or graph persistence."""

    if pins.mode is not MatchRunMode.DRY_RUN:
        raise ValueError("run_dry_match_audit requires dry_run mode")
    if not 1 <= page_size <= 100_000:
        raise ValueError("page_size must be between 1 and 100000")
    progress = claim_match_run(connection, pins)
    connection.commit()
    counts = progress.counts
    after_id = progress.last_source_record_id
    batch_number = progress.last_batch_number
    while True:
        page = tuple(fetch_page(after_id, page_size))
        if not page:
            break
        ids = tuple(record.source_record_id for record in page)
        if ids != tuple(sorted(set(ids))) or ids[0] <= after_id:
            raise ValueError("source page ids must be unique, ascending and after checkpoint")
        for record in page:
            terminal = evaluate_record(record)
            counts = replace(counts, **{terminal: getattr(counts, terminal) + 1})
        batch_number += 1
        after_id = ids[-1]
        append_match_checkpoint(
            connection,
            operation_id=pins.operation_id,
            batch_number=batch_number,
            last_source_record_id=after_id,
            counts=counts,
        )
        connection.commit()
    if counts.processed != pins.expected_source_rows:
        raise ValueError(
            f"source accounting mismatch: expected {pins.expected_source_rows}, "
            f"processed {counts.processed}"
        )
    complete_match_run(connection, pins.operation_id)
    connection.commit()
    return counts
