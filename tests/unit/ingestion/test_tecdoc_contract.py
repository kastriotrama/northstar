from ingestion.staging_migrations import ALLOWED_STAGING_TABLES, TECDOC_ENTITY_NAMES
from ingestion.tecdoc.extraction import VEHICLE_TREE_COLUMNS, extract_vehicle_tree
from ingestion.tecdoc.migrations import TECDOC_MIGRATION_STATEMENTS


def test_all_tecdoc_entities_have_versioned_raw_staging_tables() -> None:
    assert {
        f"staging.tecdoc_{entity}" for entity in TECDOC_ENTITY_NAMES
    }.issubset(ALLOWED_STAGING_TABLES)


def test_vehicle_tree_contract_contains_required_source_keys_and_components() -> None:
    assert {
        "ktype_id", "manufacturer_id", "model_id", "variant_id",
        "engine_id", "transmission_id", "bodywork_id", "source_row_refs",
    }.issubset(VEHICLE_TREE_COLUMNS)


def test_extractor_rejects_unsafe_source_schema_before_database_access() -> None:
    try:
        tuple(extract_vehicle_tree(None, source_schema="tecdoc_source; DROP SCHEMA core"))  # type: ignore[arg-type]
    except ValueError as error:
        assert "lowercase PostgreSQL identifier" in str(error)
    else:
        raise AssertionError("unsafe schema was accepted")


def test_tecdoc_storage_covers_batch_identity_and_candidates() -> None:
    names = {name for name, _ in TECDOC_MIGRATION_STATEMENTS}
    assert names == {
        "create_core_schema",
        "create_tecdoc_source_batches",
        "create_tecdoc_identity_registry",
        "create_tecdoc_canonical_candidates",
    }
    sql = " ".join(statement for _, statement in TECDOC_MIGRATION_STATEMENTS)
    assert "source_checksum" in sql
    assert "PRIMARY KEY (entity_type, source_key)" in sql
    assert "source_row_refs" in sql
