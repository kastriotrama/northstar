from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import Connection

from ingestion.config import get_ingestion_settings
from ingestion.ledger_migrations import run_ledger_migrations
from ingestion.tecdoc.dat_extraction import (
    EngineAllocation,
    EngineApplicability,
    TecDocHierarchyRecord,
)
from ingestion.tecdoc.hierarchy_persistence import persist_engine_relationship_candidates
from ingestion.tecdoc.canonical_promotion import prepare_canonical_promotions
from ingestion.tecdoc.migrations import run_tecdoc_migrations
from ingestion.tecdoc.models import TecDocVehicleRow
from ingestion.tecdoc.service import ingest_tecdoc_vehicle_tree
from ingestion.tecdoc.repository import register_batch
from api.app.features.tecdoc_review.repository import TecDocReviewRepository


@pytest.fixture(scope="module")
def pg_connection() -> Iterator[Connection]:
    try:
        connection = psycopg.connect(get_ingestion_settings().database_url)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is unavailable; start it with docker compose up -d postgres")
    yield connection
    connection.close()


def test_tecdoc_batch_is_traceable_and_repeatable(pg_connection: Connection) -> None:
    run_ledger_migrations(pg_connection)
    run_tecdoc_migrations(pg_connection)
    batch_id = f"tecdoc-integration-{uuid4()}"
    row = TecDocVehicleRow(
        ktype_id="12345",
        manufacturer_id="5",
        manufacturer_name="Volvo",
        model_id="50",
        model_name="XC60",
        variant_id=f"variant-{uuid4()}",
        variant_name="D4 AWD",
        year_from=2018,
        source_row_refs=("120:12345", "100:5", "110:50"),
    )
    arguments = {
        "rows": (row,),
        "batch_id": batch_id,
        "source_version": "0326",
        "format_version": "2.70",
        "license_reference": None,
        "source_path": "/licensed/REFERENCE_DATA_0326",
        "source_checksum": "a" * 64,
    }

    first = ingest_tecdoc_vehicle_tree(pg_connection, **arguments)  # type: ignore[arg-type]
    second = ingest_tecdoc_vehicle_tree(pg_connection, **arguments)  # type: ignore[arg-type]

    assert first.source_rows == first.unique_ktypes == 1
    assert first.candidates_written == 4
    assert first.ledger_entries_written == 4
    assert second.candidates_written == 0
    assert second.ledger_entries_written == 4
    with pg_connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, source_version, source_row_count, license_reference "
            "FROM core.tecdoc_source_batches "
            "WHERE batch_id=%s",
            (batch_id,),
        )
        assert cursor.fetchone() == ("completed", "0326", 1, "not_provided")
        cursor.execute(
            "SELECT source_row_refs FROM core.tecdoc_canonical_candidates "
            "WHERE batch_id=%s AND entity_type='alias'",
            (batch_id,),
        )
        assert cursor.fetchone() == (["100:5", "110:50", "120:12345"],)


def test_multiple_engines_persist_as_candidates_without_graph_flattening(
    pg_connection: Connection,
) -> None:
    run_tecdoc_migrations(pg_connection)
    batch_id = f"tecdoc-relationship-{uuid4()}"
    register_batch(
        pg_connection,
        batch_id=batch_id,
        source_version="0326",
        format_version="2.70",
        license_reference=None,
        source_path="/licensed/REFERENCE_DATA_0326",
        source_checksum="b" * 64,
        source_row_count=1,
    )
    applicability = EngineApplicability("001", None, None, None, False, "125:1")
    engines = tuple(
        EngineAllocation(
            engine_id=engine_id,
            engine_code=engine_code,
            manufacturer_id="000005",
            fuel_type_code="001",
            displacement_cc_from=1969,
            displacement_cc_to=1969,
            deleted=False,
            applicability=(applicability,),
            engine_source_row_ref=source_ref,
        )
        for engine_id, engine_code, source_ref in (
            ("00001", "D4204T14", "155:1"),
            ("00002", "B4204T35", "155:2"),
        )
    )
    hierarchy = TecDocHierarchyRecord(
        manufacturer_id="000005",
        manufacturer_name="VOLVO",
        manufacturer_groups=("PC",),
        model_id="00050",
        model_name="XC60",
        ktype_id=f"ktype-{uuid4()}",
        ktype_name="D4 AWD",
        year_from="201801",
        year_to=None,
        power_kw=140,
        displacement_cc=1969,
        fuel_type_code="001",
        drive_type_code="004",
        transmission_type_code="002",
        body_type_code="006",
        engines=engines,
        source_row_refs=("100:1", "110:1", "120:1"),
    )

    first = persist_engine_relationship_candidates(
        pg_connection, batch_id=batch_id, records=(hierarchy,)
    )
    second = persist_engine_relationship_candidates(
        pg_connection, batch_id=batch_id, records=(hierarchy,)
    )

    assert first.distinct_relationships == first.relationships_written == 2
    assert first.ambiguous_ktypes == 1
    assert second.relationships_written == 0
    with pg_connection.cursor() as cursor:
        cursor.execute(
            "SELECT to_source_key, evidence->>'engine_source_row_ref' "
            "FROM core.tecdoc_candidate_relationships WHERE batch_id=%s "
            "ORDER BY to_source_key",
            (batch_id,),
        )
        assert cursor.fetchall() == [
            ("engine:00001", "155:1"),
            ("engine:00002", "155:2"),
        ]


def test_only_complete_unambiguous_ktype_prepares_canonical_nodes(
    pg_connection: Connection,
) -> None:
    run_tecdoc_migrations(pg_connection)
    batch_id = f"tecdoc-promotion-{uuid4()}"
    register_batch(
        pg_connection,
        batch_id=batch_id,
        source_version="0326",
        format_version="2.70",
        license_reference=None,
        source_path="/licensed/REFERENCE_DATA_0326",
        source_checksum="c" * 64,
        source_row_count=3,
    )
    applicability = EngineApplicability("001", None, None, None, False, "125:1")

    def engine(engine_id: str) -> EngineAllocation:
        return EngineAllocation(
            engine_id=engine_id,
            engine_code=f"ENGINE-{engine_id}",
            manufacturer_id="000005",
            fuel_type_code="001",
            displacement_cc_from=1969,
            displacement_cc_to=1969,
            deleted=False,
            applicability=(applicability,),
            engine_source_row_ref=f"155:{engine_id}",
        )

    def ranged_engine(engine_id: str) -> EngineAllocation:
        value = engine(engine_id)
        return EngineAllocation(
            engine_id=value.engine_id,
            engine_code=value.engine_code,
            manufacturer_id=value.manufacturer_id,
            fuel_type_code=value.fuel_type_code,
            displacement_cc_from=None,
            displacement_cc_to=None,
            deleted=value.deleted,
            applicability=value.applicability,
            engine_source_row_ref=value.engine_source_row_ref,
        )

    def hierarchy(ktype_id: str, engines: tuple[EngineAllocation, ...]) -> TecDocHierarchyRecord:
        return TecDocHierarchyRecord(
            manufacturer_id="000005", manufacturer_name="VOLVO", manufacturer_groups=("PC",),
            model_id="00050", model_name="XC60", ktype_id=ktype_id, ktype_name="D4 AWD",
            year_from="201801", year_to=None, power_kw=140, displacement_cc=1969,
            fuel_type_code="001", drive_type_code="004", transmission_type_code="002",
            body_type_code="006", engines=engines,
            source_row_refs=("100:1", "110:1", f"120:{ktype_id}"),
        )

    unique_id = f"unique-{uuid4()}"
    ambiguous_id = f"ambiguous-{uuid4()}"
    consensus_id = f"consensus-{uuid4()}"
    records = (
        hierarchy(unique_id, (engine("00001"),)),
        hierarchy(ambiguous_id, (engine("00001"), engine("00002"))),
        hierarchy(consensus_id, (ranged_engine("00003"),)),
    )

    first = prepare_canonical_promotions(
        pg_connection, batch_id=batch_id, records=records, engine_fuels={"001": "petrol"},
        complete_source=True,
    )
    second = prepare_canonical_promotions(
        pg_connection, batch_id=batch_id, records=records, engine_fuels={"001": "petrol"},
        complete_source=True,
    )

    assert len(first.promotions) == 2
    assert first.promotions[0].alias_text == unique_id
    assert first.promotions[1].displacement_source == "table_120_complete_source_consensus"
    assert first.skipped_by_reason == {"engine_ambiguous": 1}
    assert first.candidates_written == 8
    assert second.candidates_written == 0
    with pg_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT attributes
            FROM core.tecdoc_canonical_candidates
            WHERE batch_id = %s AND entity_type = 'vehicle_variant'
            ORDER BY source_key
            LIMIT 1
            """,
            (batch_id,),
        )
        attributes = cursor.fetchone()[0]
    assert attributes["manufacturer_source_key"] == "manufacturer:000005"
    assert attributes["model_family_source_key"] == "model:00050"
    assert attributes["hierarchy_link_status"] == "awaiting_platform_mapping"
    assert attributes["engine_source_key"] == "engine:00003"

    class ExistingConnection:
        def __enter__(self):
            return pg_connection

        def __exit__(self, *_args):
            return False

    repository = TecDocReviewRepository(lambda: ExistingConnection())
    total, promoted = repository.fetch_vehicles(
        batch_id=batch_id, query="VOLVO", limit=10, offset=0
    )
    assert total == 2
    assert promoted[0]["manufacturer_attributes"]["canonical_name"] == "VOLVO"
    assert promoted[0]["engine_attributes"]["engine_code"] == "ENGINE-00003"
