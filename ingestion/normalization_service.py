"""Retry-safe Transportstyrelsen normalization orchestration."""

from __future__ import annotations

from dataclasses import replace

from psycopg import Connection

from ingestion.job_bookkeeping import claim_job_run, complete_job_run, fail_job_run
from ingestion.normalization_repository import (
    NormalizationSummary,
    count_staged_records,
    fetch_staged_records,
    review_uuid,
    store_normalization_result,
    summarize_batch,
)
from ingestion.normalization_rules import (
    MAPPING_VERSION,
    PIPELINE_VERSION,
    RULE_SET,
    RULE_VERSION,
    ManufacturerEntityRules,
    normalize_ts_record,
)
from ingestion.review_queue import CandidateMatch, enqueue_review_item
from ingestion.translation_dictionaries import TranslationRuleSet

JOB_NAME = "normalize"


def normalize_batch(
    connection: Connection,
    *,
    batch_id: str,
    page_size: int = 500,
    rule_set: TranslationRuleSet | None = None,
    manufacturer_entity_rules: ManufacturerEntityRules | None = None,
) -> NormalizationSummary:
    """Normalize one complete staging batch and persist safe decisions."""

    batch_id = batch_id.strip()
    if not batch_id:
        raise ValueError("batch_id must not be empty")
    claim = claim_job_run(connection, job_name=JOB_NAME, batch_id=batch_id)
    connection.commit()
    if not claim.should_execute:
        return replace(summarize_batch(connection, batch_id), already_completed=True)

    processed = succeeded = failed = 0
    try:
        source_count = count_staged_records(connection, batch_id)
        if source_count == 0:
            raise ValueError("source_batch_not_found_or_empty")
        after_id = 0
        while True:
            records = fetch_staged_records(
                connection,
                batch_id=batch_id,
                after_id=after_id,
                limit=page_size,
            )
            if not records:
                break
            for record in records:
                outcome = normalize_ts_record(
                    record.raw_record,
                    rule_set=rule_set or RULE_SET,
                    manufacturer_entity_rules=manufacturer_entity_rules,
                )
                store_normalization_result(
                    connection,
                    record=record,
                    mapping_version=MAPPING_VERSION,
                    rule_version=(rule_set.version if rule_set is not None else RULE_VERSION),
                    outcome=outcome,
                )
                if outcome.status in {"review_required", "failed"}:
                    candidates = tuple(
                        CandidateMatch(
                            candidate_reference=rule_id,
                            candidate_type="translation_rule",
                            confidence=outcome.confidence,
                            evidence={
                                "mapping_version": MAPPING_VERSION,
                                "pipeline_version": PIPELINE_VERSION,
                            },
                        )
                        for rule_id in outcome.candidate_rule_ids
                    )
                    enqueue_review_item(
                        connection,
                        review_id=review_uuid(
                            record.id,
                            MAPPING_VERSION,
                            rule_set.version if rule_set is not None else RULE_VERSION,
                            PIPELINE_VERSION,
                        ),
                        source_system="Transportstyrelsen",
                        source_batch_id=batch_id,
                        source_table="staging.transportstyrelsen_raw",
                        source_record_id=record.id,
                        reason_code=(
                            "normalization_failed"
                            if outcome.status == "failed"
                            else "normalization_review_required"
                        ),
                        reason_detail=",".join(outcome.review_reasons),
                        target_entity_type="vehicle",
                        candidate_matches=candidates,
                        confidence=outcome.confidence,
                    )
                processed += 1
                if outcome.status == "failed":
                    failed += 1
                else:
                    succeeded += 1
            connection.commit()
            after_id = records[-1].id
        if processed != source_count:
            raise RuntimeError("source_count_changed_during_normalization")
        complete_job_run(
            connection,
            claim.job_run.id,
            records_processed=processed,
            records_succeeded=succeeded,
            records_failed=failed,
        )
        connection.commit()
        return summarize_batch(connection, batch_id)
    except Exception as error:
        connection.rollback()
        fail_job_run(
            connection,
            claim.job_run.id,
            records_processed=processed,
            records_succeeded=succeeded,
            records_failed=failed,
            error_code=type(error).__name__,
            error_summary=f"Normalization stopped safely ({type(error).__name__})",
        )
        connection.commit()
        raise
