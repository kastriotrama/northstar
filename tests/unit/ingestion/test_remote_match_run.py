from ingestion.tecdoc.remote_match_run import PASSENGER_FILTER_SQL


def test_remote_filter_is_passenger_only() -> None:
    assert "IN ('M1', 'M1G')" in PASSENGER_FILTER_SQL
    assert "vehicle_type" in PASSENGER_FILTER_SQL
    assert "IS NULL" in PASSENGER_FILTER_SQL


def test_remote_runner_module_never_contains_write_sql() -> None:
    normalized = PASSENGER_FILTER_SQL.upper()
    for keyword in ("INSERT", "UPDATE", "DELETE", "COPY"):
        assert keyword not in normalized
