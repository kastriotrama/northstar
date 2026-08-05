from ingestion.normalization_migrations import NORMALIZATION_MIGRATIONS
from ingestion.normalization_repository import normalization_uuid, review_uuid


def test_normalization_identity_changes_with_pipeline_version() -> None:
    first = normalization_uuid(42, "mapping-v1", "rules-v1", "pipeline-v1")
    second = normalization_uuid(42, "mapping-v1", "rules-v1", "pipeline-v2")
    first_review = review_uuid(42, "mapping-v1", "rules-v1", "pipeline-v1")
    second_review = review_uuid(42, "mapping-v1", "rules-v1", "pipeline-v2")

    assert first != second
    assert first_review != second_review


def test_migrations_upgrade_legacy_identity_to_include_pipeline_version() -> None:
    statements = dict(NORMALIZATION_MIGRATIONS)

    assert "pipeline_version TEXT NOT NULL" in statements["create_normalization_results_table"]
    assert (
        "ADD COLUMN IF NOT EXISTS pipeline_version"
        in statements["add_normalization_pipeline_version"]
    )
    assert "DROP CONSTRAINT" in statements["drop_legacy_normalization_source_version_constraint"]
    assert (
        "normalization_results_source_version_key"
        in statements["create_normalization_source_version_constraint"]
    )


def test_migrations_store_drafts_and_protect_activated_rule_versions() -> None:
    statements = dict(NORMALIZATION_MIGRATIONS)

    assert "translation_rule_drafts" in statements["create_translation_rule_drafts_table"]
    assert "change_note TEXT NOT NULL" in statements["create_translation_rule_drafts_table"]
    assert "translation_rule_versions" in statements["create_translation_rule_versions_table"]
    assert "overrides JSONB NOT NULL" in statements["create_translation_rule_versions_table"]
    assert "manufacturer_entity_drafts" in statements["create_manufacturer_entity_drafts_table"]
    assert "bodybuilder_converter" in statements["create_manufacturer_entity_drafts_table"]
    assert "BEFORE UPDATE OR DELETE" in statements["protect_translation_rule_versions"]
