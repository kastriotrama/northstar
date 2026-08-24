from collections.abc import Iterator
from contextlib import nullcontext
from uuid import uuid4

import psycopg
import pytest
from psycopg import Connection
from psycopg.types.json import Jsonb

from api.app.features.normalization_review.repository import NormalizationReviewRepository
from api.app.features.normalization_review.schemas import NormalizationReviewFilters
from ingestion.config import get_ingestion_settings
from ingestion.normalization_migrations import (
    NORMALIZATION_RESULTS_TABLE,
    run_normalization_migrations,
)
from ingestion.staging_migrations import run_staging_migrations


@pytest.fixture
def review_connection() -> Iterator[Connection]:
    settings = get_ingestion_settings()
    try:
        connection = psycopg.connect(settings.database_url)
    except psycopg.OperationalError:
        if settings.environment == "test":
            raise
        pytest.skip("PostgreSQL is unavailable; start it with docker compose up -d postgres")
        return
    run_normalization_migrations(connection)
    run_staging_migrations(connection)
    yield connection
    connection.close()


def _insert_result(
    connection: Connection,
    *,
    batch_id: str,
    source_record_id: int,
    status: str,
    manufacturer: str,
    model: str,
    bodywork: str,
    fuels: list[str],
    brand: str,
) -> None:
    payload = {
        "normalized": {
            "manufacturer": manufacturer,
            "bodywork_form": bodywork,
            "energy_sources": fuels,
            "transmission_type": "automatic",
        },
        "candidates": {"model_family": model},
        "decision_trace": [{"sequence": 1, "field": "bodywork_form"}],
        "rule_matches": [{"rule_id": "BDY-110"}],
    }
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO staging.transportstyrelsen_raw (id, source_batch_id, raw_record) "
            "VALUES (%s, %s, %s)",
            (source_record_id, batch_id, Jsonb({"brand": brand})),
        )
        cursor.execute(
            f"""
            INSERT INTO {NORMALIZATION_RESULTS_TABLE} (
                normalization_id, source_system, source_batch_id, source_table,
                source_record_id, mapping_version, rule_version, pipeline_version,
                status, normalized_payload, applied_rule_ids, review_reasons, confidence
            ) VALUES (%s, 'Transportstyrelsen', %s, 'staging.transportstyrelsen_raw',
                %s, 'test-map', 'ts-translation-v4', 'normalization-pipeline-v3',
                %s, %s, ARRAY['BDY-110'], ARRAY[]::TEXT[], 0.8)
            """,
            (uuid4(), batch_id, source_record_id, status, Jsonb(payload)),
        )
    connection.commit()


def test_repository_searches_filters_summarizes_and_builds_facets(
    review_connection: Connection,
) -> None:
    batch_id = f"review-screen-{uuid4()}"
    _insert_result(
        review_connection,
        batch_id=batch_id,
        source_record_id=910001,
        status="provisional",
        manufacturer="Volvo",
        model="V60",
        bodywork="estate",
        fuels=["petrol", "electricity"],
        brand="VOLVO SOURCE BRAND",
    )
    _insert_result(
        review_connection,
        batch_id=batch_id,
        source_record_id=910002,
        status="review_required",
        manufacturer="Ford",
        model="Transit",
        bodywork="van",
        fuels=["diesel"],
        brand="FORD SOURCE BRAND",
    )
    repository = NormalizationReviewRepository(lambda: nullcontext(review_connection))
    try:
        filtered_total, rows = repository.fetch_page(
            batch_id=batch_id,
            filters=NormalizationReviewFilters(query="V60", fuel="petrol"),
        )
        summary = repository.fetch_summary(batch_id=batch_id)
        facets = repository.fetch_facets(batch_id=batch_id)

        assert filtered_total == 1
        assert rows[0]["source_record_id"] == 910001
        assert rows[0]["source_brand"] == "VOLVO SOURCE BRAND"
        brand_total, brand_rows = repository.fetch_page(
            batch_id=batch_id,
            filters=NormalizationReviewFilters(query="VOLVO SOURCE BRAND"),
        )
        assert brand_total == 1
        assert brand_rows[0]["source_record_id"] == 910001
        assert summary == {
            "resolved": 0,
            "provisional": 1,
            "review_required": 1,
            "failed": 0,
            "total": 2,
        }
        assert facets["manufacturers"] == ["Ford", "Volvo"]
        assert facets["bodywork_forms"] == ["estate", "van"]
        assert facets["fuels"] == ["diesel", "electricity", "petrol"]
        assert facets["transmissions"] == ["automatic"]
    finally:
        with review_connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {NORMALIZATION_RESULTS_TABLE} WHERE source_batch_id = %s",
                (batch_id,),
            )
            cursor.execute(
                "DELETE FROM staging.transportstyrelsen_raw WHERE source_batch_id = %s",
                (batch_id,),
            )
        review_connection.commit()


def test_repository_aggregates_a_multi_part_import_as_one_cohort(
    review_connection: Connection,
) -> None:
    prefix = f"review-cohort-{uuid4()}"
    part_one = f"{prefix}-part-001"
    part_two = f"{prefix}-part-002"
    _insert_result(
        review_connection,
        batch_id=part_one,
        source_record_id=920001,
        status="resolved",
        manufacturer="Volvo",
        model="V60",
        bodywork="estate",
        fuels=["petrol"],
        brand="VOLVO",
    )
    _insert_result(
        review_connection,
        batch_id=part_two,
        source_record_id=920002,
        status="review_required",
        manufacturer="Ford",
        model="Focus",
        bodywork="hatchback",
        fuels=["diesel"],
        brand="FORD",
    )
    repository = NormalizationReviewRepository(lambda: nullcontext(review_connection))
    cohort_id = f"{prefix}-all-parts"
    try:
        filtered_total, rows = repository.fetch_page(
            batch_id=cohort_id,
            filters=NormalizationReviewFilters(),
        )

        assert filtered_total == 2
        assert {row["source_batch_id"] for row in rows} == {part_one, part_two}
        assert repository.fetch_summary(batch_id=cohort_id) == {
            "resolved": 1,
            "provisional": 0,
            "review_required": 1,
            "failed": 0,
            "total": 2,
        }
        assert repository.fetch_facets(batch_id=cohort_id)["manufacturers"] == [
            "Ford",
            "Volvo",
        ]
    finally:
        with review_connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {NORMALIZATION_RESULTS_TABLE} WHERE source_batch_id IN (%s, %s)",
                (part_one, part_two),
            )
            cursor.execute(
                "DELETE FROM staging.transportstyrelsen_raw WHERE source_batch_id IN (%s, %s)",
                (part_one, part_two),
            )
        review_connection.commit()
