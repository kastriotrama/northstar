import pytest

from scripts.import_remote_passenger_reviews import batch_id_for


def test_batch_id_is_stable_and_zero_padded() -> None:
    assert batch_id_for("normalization-remote-passenger", 7) == (
        "normalization-remote-passenger-part-007"
    )


def test_batch_id_rejects_non_positive_parts() -> None:
    with pytest.raises(ValueError, match="part_number must be positive"):
        batch_id_for("normalization-remote-passenger", 0)
