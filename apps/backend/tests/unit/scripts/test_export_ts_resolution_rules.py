from scripts.export_ts_resolution_rules import _COLUMNS, export_rules

_ROW = ("brand", "BMW", "drive_type", "fwd", [{"field": "brand", "value": "BMW"}], "pytest", None)


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

    def cursor(self) -> _FakeCursor:
        return self._cursor


def test_exports_every_active_row_keyed_by_its_definition_columns() -> None:
    connection = _FakeConnection([_ROW])

    rules = export_rules(connection)  # type: ignore[arg-type]

    assert rules == [dict(zip(_COLUMNS, _ROW, strict=True))]


def test_only_reads_non_retired_rules() -> None:
    connection = _FakeConnection([_ROW])

    export_rules(connection)  # type: ignore[arg-type]

    assert "status <> 'retired'" in connection._cursor.executed[0]


def test_never_exports_rule_id_build_id_or_population_counts() -> None:
    for field in ("rule_id", "build_id", "matched_rows", "would_resolve", "already_resolved"):
        assert field not in _COLUMNS
