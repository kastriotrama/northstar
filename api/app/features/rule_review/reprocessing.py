from __future__ import annotations

from api.app.features.normalization_review.repository import ConnectionFactory
from ingestion.job_bookkeeping_migrations import run_job_bookkeeping_migrations
from ingestion.normalization_migrations import run_normalization_migrations
from ingestion.normalization_repository import NormalizationSummary, summarize_batch
from ingestion.normalization_rules import ManufacturerEntityRules
from ingestion.normalization_service import normalize_batch
from ingestion.review_queue_migrations import run_review_queue_migrations
from ingestion.staging_migrations import run_staging_migrations
from ingestion.translation_dictionaries import TranslationRuleSet


class RuleReprocessingAdapter:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def reprocess(
        self,
        *,
        source_batch_id: str,
        new_batch_id: str,
        rule_set: TranslationRuleSet,
        manufacturer_entity_rules: ManufacturerEntityRules,
    ) -> tuple[NormalizationSummary, NormalizationSummary]:
        with self._connection_factory() as connection:
            run_staging_migrations(connection)
            run_review_queue_migrations(connection)
            run_job_bookkeeping_migrations(connection)
            run_normalization_migrations(connection)
            before = summarize_batch(connection, source_batch_id)
            if before.processed == 0:
                raise ValueError("source_batch_not_found_or_not_normalized")
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM staging.transportstyrelsen_raw "
                    "WHERE source_batch_id = %s",
                    (source_batch_id,),
                )
                count_row = cursor.fetchone()
                source_count = int(count_row[0]) if count_row is not None else 0
                if not 1 <= source_count <= 1000:
                    raise ValueError("reprocess_batch_size_must_be_between_1_and_1000")
                cursor.execute(
                    "SELECT 1 FROM staging.transportstyrelsen_raw "
                    "WHERE source_batch_id = %s LIMIT 1",
                    (new_batch_id,),
                )
                if cursor.fetchone() is not None:
                    raise ValueError("reprocess_batch_already_exists")
                cursor.execute(
                    "INSERT INTO staging.transportstyrelsen_raw (source_batch_id, raw_record) "
                    "SELECT %s, raw_record FROM staging.transportstyrelsen_raw "
                    "WHERE source_batch_id = %s ORDER BY id",
                    (new_batch_id, source_batch_id),
                )
            connection.commit()
            after = normalize_batch(
                connection,
                batch_id=new_batch_id,
                rule_set=rule_set,
                manufacturer_entity_rules=manufacturer_entity_rules,
            )
        return before, after
