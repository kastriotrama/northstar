from scripts.load_resolution_rules import load_rules

_EQUIVALENT_RULE = {
    "canonical_field": "fuel", "comparison_key": "ELECTRICITY", "source_term": "electricity",
    "key_table": None, "decision": "accepted", "canonical_value": "electric", "note": "",
    "reviewed_by": "pytest", "source_system": "transportstyrelsen", "relation": "equivalent",
    "support": None,
}

_COMPATIBLE_RULE = {
    "canonical_field": "drive", "comparison_key": "2WD", "source_term": "2wd",
    "key_table": None, "decision": "accepted", "canonical_value": "fwd", "note": "",
    "reviewed_by": "pytest", "source_system": "transportstyrelsen", "relation": "compatible",
    "support": 744197,
}


class _FakeCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, object]]] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, parameters: dict[str, object] | tuple = ()) -> None:
        self.executed.append((statement, parameters if isinstance(parameters, dict) else {}))


class _FakeConnection:
    def __init__(self) -> None:
        self._cursor = _FakeCursor()
        self.commits = 0

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    @property
    def executed(self) -> list[tuple[str, dict[str, object]]]:
        return self._cursor.executed


def test_dry_run_reports_counts_but_writes_nothing() -> None:
    connection = _FakeConnection()

    counts = load_rules(
        connection, [_EQUIVALENT_RULE, _COMPATIBLE_RULE], commit=False  # type: ignore[arg-type]
    )

    assert counts == {"single_target": 1, "compatible": 1}
    # One commit from the (idempotent, DDL-only) schema migration step, which
    # always runs; zero from actually writing rule rows, since commit=False.
    assert connection.commits == 1
    assert not any("INSERT INTO" in statement for statement, _ in connection.executed)


def test_commit_writes_an_equivalent_rule_through_the_single_target_upsert() -> None:
    connection = _FakeConnection()

    load_rules(connection, [_EQUIVALENT_RULE], commit=True)  # type: ignore[arg-type]

    writes = [(s, p) for s, p in connection.executed if "INSERT INTO" in s]
    assert len(writes) == 1
    statement, params = writes[0]
    assert "relation <> 'compatible'" in statement
    assert params["canonical_value"] == "electric"
    # Schema migration commits once, the write commits once more.
    assert connection.commits == 2


def test_commit_writes_a_compatible_rule_through_the_pair_upsert() -> None:
    connection = _FakeConnection()

    load_rules(connection, [_COMPATIBLE_RULE], commit=True)  # type: ignore[arg-type]

    writes = [(s, p) for s, p in connection.executed if "INSERT INTO" in s]
    assert len(writes) == 1
    statement, params = writes[0]
    assert "relation = 'compatible'" in statement
    assert params["support"] == 744197
