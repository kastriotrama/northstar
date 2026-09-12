from scripts.export_resolution_rules import _COLUMNS, export_rules

_ROW = (
    "fuel", "ELECTRICITY", "electricity", None, "accepted", "electric", "",
    "pytest", "transportstyrelsen", "equivalent", None,
)


class _FakeCursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows
        self.executed: list[str] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> None:
        self.executed.append(statement)

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows


class _FakeConnection:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._cursor = _FakeCursor(rows)
        self.commits = 0

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1


def test_exports_every_row_keyed_by_its_natural_columns() -> None:
    connection = _FakeConnection([_ROW])

    rules = export_rules(connection)  # type: ignore[arg-type]

    assert rules == [dict(zip(_COLUMNS, _ROW, strict=True))]


def test_never_exports_the_surrogate_id() -> None:
    assert "id" not in _COLUMNS


def test_no_rows_yields_an_empty_list() -> None:
    connection = _FakeConnection([])

    assert export_rules(connection) == []  # type: ignore[arg-type]
