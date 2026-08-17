from inspect import signature

import pytest

from scripts.import_remote_passenger_reviews import batch_id_for, run


def test_batch_id_is_stable_and_zero_padded() -> None:
    assert batch_id_for("normalization-remote-passenger", 7) == (
        "normalization-remote-passenger-part-007"
    )


def test_batch_id_rejects_non_positive_parts() -> None:
    with pytest.raises(ValueError, match="part_number must be positive"):
        batch_id_for("normalization-remote-passenger", 0)


def test_full_restore_retains_raw_only_when_explicitly_requested() -> None:
    parameter = signature(run).parameters["retain_raw"]

    assert parameter.default is False
    assert parameter.kind.name == "KEYWORD_ONLY"
