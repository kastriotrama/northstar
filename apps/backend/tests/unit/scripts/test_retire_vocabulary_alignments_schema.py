from scripts.retire_vocabulary_alignments_schema import _FUNCTIONS, _TABLES, retire_schema


class _FakeCursor:
    def __init__(self, existing: set[str]) -> None:
        self._existing = existing
        self.executed: list[str] = []
        self._last_regclass_table: str | None = None

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> None:
        self.executed.append(statement)
        if "to_regclass" in statement:
            self._last_regclass_table = str(parameters[0])

    def fetchone(self) -> tuple[bool] | None:
        return (self._last_regclass_table in self._existing,)


class _FakeConnection:
    def __init__(self, existing: set[str]) -> None:
        self._cursor = _FakeCursor(existing)
        self.commits = 0

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    @property
    def executed(self) -> list[str]:
        return self._cursor.executed


def test_dry_run_reports_only_the_tables_that_still_exist() -> None:
    connection = _FakeConnection({"core.vocabulary_alignments"})

    result = retire_schema(connection, commit=False)  # type: ignore[arg-type]

    assert result == {
        "would_drop_tables": ["core.vocabulary_alignments"],
        "would_drop_functions": list(_FUNCTIONS),
    }
    assert connection.commits == 0
    assert not any("DROP" in statement for statement in connection.executed)


def test_commit_drops_every_table_and_function_with_cascade() -> None:
    connection = _FakeConnection(set(_TABLES))

    result = retire_schema(connection, commit=True)  # type: ignore[arg-type]

    assert result["dropped_tables"] == list(_TABLES)
    assert result["dropped_functions"] == list(_FUNCTIONS)
    assert connection.commits == 1
    for table in _TABLES:
        assert f"DROP TABLE IF EXISTS {table} CASCADE" in connection.executed
    for function in _FUNCTIONS:
        assert f"DROP FUNCTION IF EXISTS {function}() CASCADE" in connection.executed


def test_commit_drops_vocabulary_alignments_before_its_version_table() -> None:
    """`vocabulary_alignments` FK-references `vocabulary_alignment_versions`,
    so it must be dropped first or CASCADE has to do more than expected."""

    connection = _FakeConnection(set(_TABLES))

    retire_schema(connection, commit=True)  # type: ignore[arg-type]

    drops = [s for s in connection.executed if s.startswith("DROP TABLE")]
    assert drops.index("DROP TABLE IF EXISTS core.vocabulary_alignments CASCADE") < drops.index(
        "DROP TABLE IF EXISTS core.vocabulary_alignment_versions CASCADE"
    )
