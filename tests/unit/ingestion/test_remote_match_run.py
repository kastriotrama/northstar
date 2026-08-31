from ingestion.tecdoc.remote_match_run import PASSENGER_FILTER_SQL, _fetch_local_raw_page


def test_remote_filter_is_passenger_only() -> None:
    assert "IN ('M1', 'M1G')" in PASSENGER_FILTER_SQL
    assert "vehicle_type" in PASSENGER_FILTER_SQL
    assert "IS NULL" in PASSENGER_FILTER_SQL


def test_remote_runner_module_never_contains_write_sql() -> None:
    normalized = PASSENGER_FILTER_SQL.upper()
    for keyword in ("INSERT", "UPDATE", "DELETE", "COPY"):
        assert keyword not in normalized


class Cursor:
    def __init__(self) -> None:
        self.query = ""
        self.parameters = ()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, query, parameters) -> None:
        self.query = query
        self.parameters = parameters

    def fetchall(self):
        return ((101, {"brand": "Volvo"}),)


class Connection:
    def __init__(self) -> None:
        self.test_cursor = Cursor()

    def cursor(self):
        return self.test_cursor


def test_local_raw_page_is_pinned_ordered_and_read_only() -> None:
    connection = Connection()

    rows = _fetch_local_raw_page(
        connection,
        source_batch_prefix="passenger-v1-part-",
        after_id=100,
        limit=25_000,
    )

    assert rows == ((101, {"brand": "Volvo"}),)
    assert "ORDER BY id" in connection.test_cursor.query
    assert connection.test_cursor.parameters == ("passenger-v1-part-%", 100, 25_000)
    for keyword in ("INSERT", "UPDATE", "DELETE", "COPY"):
        assert keyword not in connection.test_cursor.query.upper()
