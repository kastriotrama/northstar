from inspect import signature
from unittest.mock import MagicMock

import pytest

from scripts.import_remote_passenger_reviews import (
    EXPECTED_PASSENGER_COUNT,
    batch_id_for,
    recover_stale_part,
    run,
    verify_remote_source_count,
)


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


def test_remote_source_count_must_match_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = MagicMock()
    cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (EXPECTED_PASSENGER_COUNT - 1,)
    monkeypatch.setattr(
        "scripts.import_remote_passenger_reviews.psycopg.connect",
        MagicMock(return_value=connection),
    )

    with pytest.raises(RuntimeError, match="source count mismatch"):
        verify_remote_source_count("postgresql://source", EXPECTED_PASSENGER_COUNT)


def test_stale_recovery_removes_only_next_uncheckpointed_part() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [(47, "EJY75U"), ("running",)]

    recovered = recover_stale_part(connection, "full-import")

    assert recovered == "full-import-part-048"
    deleted_batches = [
        call.args[1]
        for call in cursor.execute.call_args_list
        if call.args and str(call.args[0]).startswith("DELETE")
    ]
    assert deleted_batches == [
        ("full-import-part-048",),
        ("full-import-part-048",),
        ("full-import-part-048",),
        ("full-import-part-048",),
    ]
    connection.commit.assert_called_once_with()
