"""Disk-bounded remote TS normalization and TecDoc dry-run matching."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row

from ingestion.match_run_repository import (
    MatchRunCounts,
    MatchRunMode,
    MatchRunPins,
    append_match_checkpoint,
    claim_match_run,
    complete_match_run,
    increment_match_run_blocker_counts,
    increment_match_run_reason_counts,
)
from ingestion.match_pattern_inventory import observe_match_pattern, upsert_match_pattern_inventory
from ingestion.match_run_service import MatchSourceRecord
from ingestion.normalization_rules import ManufacturerEntityRules, normalize_ts_record
from ingestion.tecdoc.blocker_review import classify_match_blocker
from ingestion.tecdoc.match_run_adapters import MatchEvaluation, TecDocDryRunEvaluator
from ingestion.translation_dictionaries import TranslationRuleSet

PASSENGER_FILTER_SQL = """
(
    upper(trim(coalesce(eu_category, ''))) IN ('M1', 'M1G')
    OR (
        nullif(trim(eu_category), '') IS NULL
        AND upper(trim(coalesce(vehicle_type, ''))) = 'PB'
    )
)
"""


def run_remote_dry_match_audit(
    local: Connection,
    remote: Connection,
    *,
    pins: MatchRunPins,
    rule_set: TranslationRuleSet,
    manufacturer_rules: ManufacturerEntityRules,
    evaluator: TecDocDryRunEvaluator,
    batch_size: int = 25_000,
    max_batches: int | None = None,
) -> MatchRunCounts:
    """Stream remote rows by plate, retaining only local cumulative checkpoints."""

    if pins.mode is not MatchRunMode.DRY_RUN:
        raise ValueError("remote audit requires dry_run mode")
    if rule_set.version != pins.normalization_rule_version:
        raise ValueError("active normalization rule version differs from pinned version")
    if not 1 <= batch_size <= 100_000:
        raise ValueError("batch_size must be between 1 and 100000")
    if max_batches is not None and max_batches < 1:
        raise ValueError("max_batches must be positive")
    progress = claim_match_run(local, pins)
    local.commit()
    counts = progress.counts
    after_plate = progress.last_source_cursor
    batch_number = progress.last_batch_number
    while True:
        records = _fetch_remote_page(remote, after_plate=after_plate, limit=batch_size)
        if not records:
            break
        plates = tuple(str(record["plate"]) for record in records)
        if plates != tuple(sorted(set(plates))) or (after_plate and plates[0] <= after_plate):
            raise ValueError("remote plates must be unique, ascending and after checkpoint")
        batch_reason_counts: Counter[str] = Counter()
        batch_blocker_counts: Counter[str] = Counter()
        batch_patterns = []
        for raw in records:
            source_record_id = counts.processed + 1
            evaluation = _evaluate_raw_record(
                raw,
                source_record_id=source_record_id,
                rule_set=rule_set,
                manufacturer_rules=manufacturer_rules,
                evaluator=evaluator,
            )
            terminal = evaluation.terminal
            batch_reason_counts.update(evaluation.reason_codes)
            if category := classify_match_blocker(evaluation):
                batch_blocker_counts[category.code] += 1
            if observation := observe_match_pattern(raw, evaluation):
                batch_patterns.append(observation)
            counts = replace(counts, **{terminal: getattr(counts, terminal) + 1})
        batch_number += 1
        after_plate = plates[-1]
        append_match_checkpoint(
            local,
            operation_id=pins.operation_id,
            batch_number=batch_number,
            last_source_record_id=counts.processed,
            last_source_cursor=after_plate,
            counts=counts,
        )
        increment_match_run_reason_counts(
            local,
            operation_id=pins.operation_id,
            reason_counts=dict(batch_reason_counts),
        )
        increment_match_run_blocker_counts(
            local,
            operation_id=pins.operation_id,
            blocker_counts=dict(batch_blocker_counts),
        )
        upsert_match_pattern_inventory(
            local,
            operation_id=pins.operation_id,
            batch_number=batch_number,
            observations=batch_patterns,
            source_record_id=counts.processed,
        )
        local.commit()
        if max_batches is not None and batch_number - progress.last_batch_number >= max_batches:
            return counts
    if counts.processed != pins.expected_source_rows:
        raise ValueError(
            f"source accounting mismatch: expected {pins.expected_source_rows}, "
            f"processed {counts.processed}"
        )
    complete_match_run(local, pins.operation_id)
    local.commit()
    return counts


def run_local_raw_dry_match_audit(
    connection: Connection,
    *,
    pins: MatchRunPins,
    rule_set: TranslationRuleSet,
    manufacturer_rules: ManufacturerEntityRules,
    evaluator: TecDocDryRunEvaluator,
    batch_size: int = 25_000,
    max_batches: int | None = None,
) -> MatchRunCounts:
    """Resume a full audit from retained local raw rows without another source DB."""

    if pins.mode is not MatchRunMode.DRY_RUN:
        raise ValueError("local raw audit requires dry_run mode")
    if rule_set.version != pins.normalization_rule_version:
        raise ValueError("active normalization rule version differs from pinned version")
    if not 1 <= batch_size <= 100_000:
        raise ValueError("batch_size must be between 1 and 100000")
    if max_batches is not None and max_batches < 1:
        raise ValueError("max_batches must be positive")
    progress = claim_match_run(connection, pins)
    connection.commit()
    counts = progress.counts
    after_id = progress.last_source_record_id
    batch_number = progress.last_batch_number
    while True:
        records = _fetch_local_raw_page(
            connection,
            source_batch_prefix=pins.source_batch_prefix,
            after_id=after_id,
            limit=batch_size,
        )
        if not records:
            break
        ids = tuple(source_id for source_id, _ in records)
        if ids != tuple(sorted(set(ids))) or ids[0] <= after_id:
            raise ValueError("local raw ids must be unique, ascending and after checkpoint")
        batch_reason_counts: Counter[str] = Counter()
        batch_blocker_counts: Counter[str] = Counter()
        batch_patterns = []
        for source_id, raw in records:
            evaluation = _evaluate_raw_record(
                raw,
                source_record_id=source_id,
                rule_set=rule_set,
                manufacturer_rules=manufacturer_rules,
                evaluator=evaluator,
            )
            batch_reason_counts.update(evaluation.reason_codes)
            if category := classify_match_blocker(evaluation):
                batch_blocker_counts[category.code] += 1
            if observation := observe_match_pattern(raw, evaluation):
                batch_patterns.append(observation)
            counts = replace(
                counts,
                **{evaluation.terminal: getattr(counts, evaluation.terminal) + 1},
            )
        batch_number += 1
        after_id = ids[-1]
        append_match_checkpoint(
            connection,
            operation_id=pins.operation_id,
            batch_number=batch_number,
            last_source_record_id=after_id,
            counts=counts,
        )
        increment_match_run_reason_counts(
            connection,
            operation_id=pins.operation_id,
            reason_counts=dict(batch_reason_counts),
        )
        increment_match_run_blocker_counts(
            connection,
            operation_id=pins.operation_id,
            blocker_counts=dict(batch_blocker_counts),
        )
        upsert_match_pattern_inventory(
            connection,
            operation_id=pins.operation_id,
            batch_number=batch_number,
            observations=batch_patterns,
            source_record_id=ids[-1],
        )
        connection.commit()
        if max_batches is not None and batch_number - progress.last_batch_number >= max_batches:
            return counts
    if counts.processed != pins.expected_source_rows:
        raise ValueError(
            f"source accounting mismatch: expected {pins.expected_source_rows}, "
            f"processed {counts.processed}"
        )
    complete_match_run(connection, pins.operation_id)
    connection.commit()
    return counts


def _fetch_remote_page(
    connection: Connection,
    *,
    after_plate: str,
    limit: int,
) -> tuple[dict[str, Any], ...]:
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            f"SELECT * FROM public.swedish_vehicles WHERE {PASSENGER_FILTER_SQL} "
            "AND plate > %s ORDER BY plate LIMIT %s",
            (after_plate, limit),
        )
        return tuple(dict(row) for row in cursor.fetchall())


def _fetch_local_raw_page(
    connection: Connection,
    *,
    source_batch_prefix: str,
    after_id: int,
    limit: int,
) -> tuple[tuple[int, dict[str, Any]], ...]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, raw_record FROM staging.transportstyrelsen_raw "
            "WHERE source_batch_id LIKE %s AND id > %s ORDER BY id LIMIT %s",
            (f"{source_batch_prefix}%", after_id, limit),
        )
        return tuple((int(row[0]), dict(row[1])) for row in cursor.fetchall())


def _evaluate_raw_record(
    raw: dict[str, Any],
    *,
    source_record_id: int,
    rule_set: TranslationRuleSet,
    manufacturer_rules: ManufacturerEntityRules,
    evaluator: TecDocDryRunEvaluator,
) -> MatchEvaluation:
    outcome = normalize_ts_record(
        raw,
        rule_set=rule_set,
        manufacturer_entity_rules=manufacturer_rules,
    )
    return evaluator.evaluate(
        MatchSourceRecord(
            source_record_id,
            {
                "normalization_status": outcome.status,
                "normalized": outcome.normalized,
                "candidates": outcome.candidates,
                "review_reasons": list(outcome.review_reasons),
                "source_evidence": {
                    field_name: raw.get(field_name)
                    for field_name in (
                        "body_code",
                        "is_4wd",
                        "brand",
                        "model",
                        "variant",
                        "version",
                        "model_no",
                        "type_text",
                        "eeg_type_approval",
                    )
                },
            },
        )
    )
