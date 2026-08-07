from collections.abc import Iterator
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.confidence_routing import ConfidenceRouter
from ingestion.confidence_routing_migrations import (
    MATCH_ROUTING_TABLE,
    run_confidence_routing_migrations,
)
from ingestion.confidence_routing_repository import (
    fetch_batch_routing_decisions,
    store_routing_decision,
)
from ingestion.config import get_ingestion_settings
from ingestion.fuzzy_matching import (
    FuzzyMatchConfig,
    FuzzyVehicleMatcher,
    ManufacturerCandidateIndex,
    VehicleCandidate,
    VehicleMatchQuery,
)
from ingestion.staging_loaders import copy_raw_records
from ingestion.staging_migrations import run_staging_migrations


@pytest.fixture
def pg_connection() -> Iterator[Connection]:
    settings = get_ingestion_settings()
    try:
        connection = psycopg.connect(settings.database_url)
    except psycopg.OperationalError:
        if settings.environment == "test":
            raise
        pytest.skip("PostgreSQL is unavailable; start it with docker compose up -d postgres")
        return
    run_staging_migrations(connection)
    run_confidence_routing_migrations(connection)
    yield connection
    connection.close()


def test_routing_decision_persists_trace_alternatives_and_idempotent_retry(
    pg_connection: Connection,
) -> None:
    batch_id = f"scrum93-test-{uuid4()}"
    try:
        copy_raw_records(
            pg_connection,
            table="staging.transportstyrelsen_raw",
            source_batch_id=batch_id,
            expected_source_count=1,
            records=[{"manufacturer": "Volvo", "model": "XC90"}],
        )
        with pg_connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM staging.transportstyrelsen_raw WHERE source_batch_id = %s",
                (batch_id,),
            )
            source_record_id = int(cursor.fetchone()[0])
        candidates = (
            VehicleCandidate("KTYPE-100", "Volvo", "XC90"),
            VehicleCandidate("KTYPE-101", "Volvo", "XC90 Recharge"),
        )
        match = FuzzyVehicleMatcher(
            ManufacturerCandidateIndex(candidates),
            FuzzyMatchConfig(candidate_threshold=0.30),
        ).match(VehicleMatchQuery(manufacturer="Volvo", model="XC90"))
        decision = ConfidenceRouter().route(match)

        first_id = store_routing_decision(
            pg_connection,
            source_system="Transportstyrelsen",
            source_batch_id=batch_id,
            source_table="staging.transportstyrelsen_raw",
            source_record_id=source_record_id,
            candidate_catalog_version="tecdoc-test-v1",
            decision=decision,
        )
        retry_id = store_routing_decision(
            pg_connection,
            source_system="Transportstyrelsen",
            source_batch_id=batch_id,
            source_table="staging.transportstyrelsen_raw",
            source_record_id=source_record_id,
            candidate_catalog_version="tecdoc-test-v1",
            decision=decision,
        )
        pg_connection.commit()
        stored = fetch_batch_routing_decisions(pg_connection, batch_id)

        assert retry_id == first_id
        assert len(stored) == 1
        assert stored[0]["route"] == "resolved"
        payload = stored[0]["decision_payload"]
        assert len(payload["decision_trace"]) == 5
        assert [
            candidate["candidate_reference"] for candidate in payload["alternative_candidates"]
        ] == ["KTYPE-100", "KTYPE-101"]
        assert payload["policy_version"] == "confidence-routing-v1"

        with pytest.raises(ValueError, match="different payload"):
            store_routing_decision(
                pg_connection,
                source_system="Transportstyrelsen",
                source_batch_id=batch_id,
                source_table="staging.transportstyrelsen_raw",
                source_record_id=source_record_id,
                candidate_catalog_version="tecdoc-test-v1",
                decision=replace(decision, confidence=0.99),
            )
        pg_connection.rollback()
    finally:
        with pg_connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {MATCH_ROUTING_TABLE} WHERE source_batch_id = %s",
                (batch_id,),
            )
            cursor.execute(
                "DELETE FROM staging.transportstyrelsen_raw WHERE source_batch_id = %s",
                (batch_id,),
            )
        pg_connection.commit()


def test_routing_writer_rejects_missing_source_record(pg_connection: Connection) -> None:
    match = FuzzyVehicleMatcher(
        ManufacturerCandidateIndex((VehicleCandidate("KTYPE-1", "Volvo", "V60"),))
    ).match(VehicleMatchQuery(manufacturer="Volvo", model="V60"))
    decision = ConfidenceRouter().route(match)

    with pytest.raises(ValueError, match="does not exist"):
        store_routing_decision(
            pg_connection,
            source_system="Transportstyrelsen",
            source_batch_id="missing-batch",
            source_table="staging.transportstyrelsen_raw",
            source_record_id=999_999_999,
            candidate_catalog_version="tecdoc-test-v1",
            decision=decision,
        )
    pg_connection.rollback()


@pytest.mark.parametrize(
    ("route", "selected_candidate", "payload"),
    [
        (
            "resolved",
            None,
            {
                "route": "resolved",
                "policy_version": "confidence-routing-v1",
                "decision_trace": [],
                "alternative_candidates": [],
            },
        ),
        (
            "review_required",
            None,
            {
                "route": "review_required",
                "policy_version": "confidence-routing-v1",
            },
        ),
    ],
)
def test_database_rejects_invalid_selection_and_payload_shapes(
    pg_connection: Connection,
    route: str,
    selected_candidate: str | None,
    payload: dict[str, object],
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), pg_connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {MATCH_ROUTING_TABLE} "
            "(decision_id, source_system, source_batch_id, source_table, source_record_id, "
            "candidate_catalog_version, policy_version, route, confidence, "
            "selected_candidate_reference, decision_payload) "
            "VALUES (%s, 'Transportstyrelsen', 'constraint-test', "
            "'staging.transportstyrelsen_raw', 1, 'catalog-v1', "
            "'confidence-routing-v1', %s, 0.95, %s, %s)",
            (uuid4(), route, selected_candidate, Jsonb(payload)),
        )
    pg_connection.rollback()
