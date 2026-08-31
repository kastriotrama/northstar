from datetime import UTC, datetime
from pathlib import Path

import pytest

from ingestion.normalization_bundle import (
    NormalizationBundleError,
    load_normalization_bundle,
)

FIXTURE = (
    Path(__file__).parents[2] / "fixtures" / "normalization_bundle_minimal.xlsx"
)


def test_bundle_loader_validates_complete_portable_contract(current_normalization_bundle: Path) -> None:
    bundle = load_normalization_bundle(current_normalization_bundle)

    assert bundle.source_batch_id == "normalization-bundle-fixture-v1"
    assert len(bundle.raw_records) == 1
    assert len(bundle.expected_results) == 1
    assert bundle.rule_version.version == "ts-review-fixture-v1"
    assert bundle.rule_version.base_rule_version == "ts-translation-v4"
    assert bundle.translation_rule_count == 99
    assert bundle.base_manufacturer_count == 184
    assert bundle.effective_manufacturer_count == 184
    assert bundle.manufacturer_override_count == 0
    assert bundle.policy_override_count == 0
    assert bundle.raw_records[0].ingested_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert bundle.rule_version.activated_at == datetime(2026, 1, 1, tzinfo=UTC)


def test_bundle_loader_rejects_a_missing_file() -> None:
    with pytest.raises(NormalizationBundleError, match="does not exist"):
        load_normalization_bundle("missing-normalization-bundle.xlsx")


def test_historical_bundle_is_not_silently_reprocessed_under_new_pipeline() -> None:
    with pytest.raises(NormalizationBundleError, match="incompatible pipeline version"):
        load_normalization_bundle(FIXTURE)
