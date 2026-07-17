import pytest

from ingestion.staging_loaders import copy_raw_records, count_batch_rows


def test_copy_raw_records_rejects_table_outside_allow_list() -> None:
    with pytest.raises(ValueError, match="not an allowed staging table"):
        copy_raw_records(
            connection=None,  # type: ignore[arg-type]
            table="staging.not_a_real_table",
            source_batch_id="batch-1",
            records=[],
        )


def test_copy_raw_records_validates_before_touching_connection() -> None:
    # A None connection would raise AttributeError if the loader tried to
    # use it; validation must happen first so this raises ValueError instead.
    with pytest.raises(ValueError):
        copy_raw_records(
            connection=None,  # type: ignore[arg-type]
            table="staging.also_not_real",
            source_batch_id="batch-1",
            records=[{"a": 1}],
        )


def test_count_batch_rows_rejects_table_outside_allow_list() -> None:
    with pytest.raises(ValueError, match="not an allowed staging table"):
        count_batch_rows(
            connection=None,  # type: ignore[arg-type]
            table="staging.not_a_real_table",
            source_batch_id="batch-1",
        )
