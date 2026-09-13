from typing import Self

from scripts.load_resolution_rules import load_rules

_STALE_RULE = {
    
        "canonical_field": "fuel", "comparison_key": "ELECTRICITY", "source_term": "electricity",
        "key_table": None, "decision": "accepted", "canonical_value": "electric", "note": "",
        "reviewed_by": "pytest", "source_system": "transportstyrelsen", "relation": "equivalent",
        "support": None
    ,
    "updated_at": "2020-01-01T00:00:00+00:00",
}

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
    def __init__(self, *, applies: bool = True) -> None:
        self.executed: list[tuple[str, dict[str, object]]] = []
        self._applies = applies

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, parameters: dict[str, object] | tuple = ()) -> None:
        self.executed.append((statement, parameters if isinstance(parameters, dict) else {}))

    def fetchone(self) -> tuple[int] | None:
        # RETURNING id: a real upsert returns a row when its WHERE clause let
        # the write through, and none when a newer local edit blocked it --
        # `applies=False` is how a test stands in for that second case.
        return (1,) if self._applies else None


class _FakeConnection:
    def __init__(self, *, applies: bool = True) -> None:
        self._cursor = _FakeCursor(applies=applies)
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

    result = load_rules(
        connection, [_EQUIVALENT_RULE, _COMPATIBLE_RULE], commit=False  # type: ignore[arg-type]
    )

    assert result.single_target == 1
    assert result.compatible == 1
    assert result.conflicts == ()
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


def test_a_row_blocked_by_a_newer_local_edit_is_reported_as_a_conflict_not_applied() -> None:
    # `applies=False` stands in for the real upsert's WHERE clause finding
    # this database's own `updated_at` newer than the incoming row's --
    # the write still executes (it always does, unconditionally), but the
    # database itself decided not to apply it.
    connection = _FakeConnection(applies=False)

    result = load_rules(connection, [_STALE_RULE], commit=True)  # type: ignore[arg-type]

    assert result.single_target == 0
    assert result.compatible == 0
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict["canonical_field"] == "fuel"
    assert conflict["comparison_key"] == "ELECTRICITY"
    assert conflict["incoming_updated_at"] == "2020-01-01T00:00:00+00:00"
