from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from api.app.features.match_review.repository import MatchReviewRepository
from ingestion.config import get_ingestion_settings
from ingestion.match_run_migrations import (
    MATCH_RUN_BLOCKER_COUNTS_TABLE,
    MATCH_RUNS_TABLE,
    run_match_run_migrations,
)
from ingestion.review_queue import CandidateMatch, enqueue_review_item
from ingestion.review_queue_migrations import REVIEW_QUEUE_TABLE, run_review_queue_migrations
from ingestion.staging_migrations import run_staging_migrations


@pytest.fixture
def match_review_fixture() -> Iterator[tuple[MatchReviewRepository, str, int, int]]:
    settings = get_ingestion_settings()
    try:
        connection = psycopg.connect(settings.database_url)
    except psycopg.OperationalError:
        if settings.environment == "test":
            raise
        pytest.skip("PostgreSQL is unavailable; start it with docker compose up -d postgres")
    run_staging_migrations(connection)
    run_match_run_migrations(connection)
    run_review_queue_migrations(connection)
    operation_id = str(uuid4())
    batch_id = f"match-review-test-{operation_id}"
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO staging.transportstyrelsen_raw (source_batch_id, raw_record) "
            "VALUES (%s,%s) RETURNING id",
            (batch_id, Jsonb({"plate": "TEST-REVIEW", "brand": "VOLVO", "model": "V60"})),
        )
        source_id = int(cursor.fetchone()[0])
        cursor.execute(
            f"INSERT INTO {MATCH_RUNS_TABLE} (operation_id,source_system,source_version,"
            "source_batch_prefix,expected_source_rows,normalization_rule_version,"
            "candidate_catalog_version,policy_version,code_revision,mode,status,"
            "last_batch_number,last_source_record_id,last_source_cursor,processed,"
            "review_required,finished_at) VALUES (%s,'TS','test',%s,1,'rules','catalog',"
            "'policy','revision','dry_run','completed',1,%s,%s,1,1,now())",
            (operation_id, batch_id, source_id, str(source_id)),
        )
        cursor.execute(
            f"INSERT INTO {MATCH_RUN_BLOCKER_COUNTS_TABLE} "
            "(operation_id,blocker_category,occurrence_count) VALUES (%s,%s,1)",
            (operation_id, "bodywork_conflict"),
        )
    review_id = uuid4()
    item_id = enqueue_review_item(
        connection,
        review_id=review_id,
        source_system="Transportstyrelsen",
        source_batch_id=batch_id,
        source_table="staging.transportstyrelsen_raw",
        source_record_id=source_id,
        reason_code="ts_tecdoc_match_blocker:bodywork_conflict",
        reason_detail="context_conflict:bodywork",
        target_entity_type=f"ts_tecdoc_match:{operation_id}",
        candidate_matches=(
            CandidateMatch("0001", "TecDocKType", 0.91, {"model": "V60"}),
        ),
        confidence=0.91,
    )
    connection.commit()
    connection.close()
    repository = MatchReviewRepository(lambda: psycopg.connect(settings.database_url))
    yield repository, operation_id, item_id, source_id
    with psycopg.connect(settings.database_url) as cleanup, cleanup.cursor() as cursor:
        cursor.execute(f"DELETE FROM {REVIEW_QUEUE_TABLE} WHERE review_id=%s", (review_id,))
        cursor.execute(
            f"DELETE FROM {MATCH_RUN_BLOCKER_COUNTS_TABLE} WHERE operation_id=%s",
            (operation_id,),
        )
        cursor.execute(f"DELETE FROM {MATCH_RUNS_TABLE} WHERE operation_id=%s", (operation_id,))
        cursor.execute("DELETE FROM staging.transportstyrelsen_raw WHERE id=%s", (source_id,))


def test_repository_reads_progress_and_persists_locked_review_decision(
    match_review_fixture: tuple[MatchReviewRepository, str, int, int],
) -> None:
    repository, operation_id, item_id, _ = match_review_fixture
    run = repository.fetch_run(operation_id)
    assert run is not None and run["processed"] == 1
    assert repository.fetch_blocker_counts(operation_id) == {"bodywork_conflict": 1}
    total, items = repository.fetch_items(
        operation_id=operation_id,
        category="bodywork_conflict",
        status="pending",
        limit=10,
        offset=0,
    )
    assert total == 1 and items[0]["source_evidence"]["plate"] == "TEST-REVIEW"

    decided = repository.decide(
        operation_id=operation_id,
        item_id=item_id,
        action="accept_top_candidate",
        reviewer="Integration reviewer",
        reason="Bodywork evidence independently confirmed",
        selected_candidate_reference=None,
        scope="vehicle_only",
    )
    assert decided["resolution"]["selected_candidate_reference"] == "0001"
    assert decided["resolution"]["graph_write"] is False

    with pytest.raises(ValueError, match="terminal review item already has"):
        repository.decide(
            operation_id=operation_id,
            item_id=item_id,
            action="keep_unresolved",
            reviewer="Second reviewer",
            reason="Attempted second decision",
            selected_candidate_reference=None,
            scope="vehicle_only",
        )
