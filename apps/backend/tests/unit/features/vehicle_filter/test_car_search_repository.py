from contextlib import contextmanager
from typing import Any

from api.app.features.vehicle_filter.repository import VehicleFilterRepository
from ingestion.vehicle_facts_migrations import RESOLVABLE_FIELDS


class _Cursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows
        self.executed: list[tuple[str, list[Any]]] = []

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, parameters: list[Any]) -> None:
        self.executed.append((sql, list(parameters)))

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def fetchone(self) -> tuple[Any, ...]:
        return (7,)


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def cursor(self) -> _Cursor:
        return self._cursor


def _repository(cursor: _Cursor) -> VehicleFilterRepository:
    @contextmanager
    def factory():
        yield _Connection(cursor)

    return VehicleFilterRepository(factory)


def _row() -> tuple[Any, ...]:
    values: list[Any] = [12, "AB12345", "VIN123"]
    for field in RESOLVABLE_FIELDS:
        # power_kw is the one field a rule supplied.
        values.extend([110 if field == "power_kw" else "X", field == "power_kw"])
    values.extend(["diesel", "automatic", "Euro 6", "resolved"])
    return tuple(values)


def test_search_page_maps_canonical_values_and_rule_filled_fields() -> None:
    cursor = _Cursor([_row()])

    rows = _repository(cursor).search_page([], "volvo", cursor_id=0, limit=50)

    assert rows == [
        {
            "source_record_id": 12,
            "plate": "AB12345",
            "vin": "VIN123",
            "manufacturer": "X",
            "model_family": "X",
            "drive_type": "X",
            "bodywork_form": "X",
            "engine_code": "X",
            "power_kw": 110,
            "displacement_cc": "X",
            "production_year": "X",
            "rule_filled": ["power_kw"],
            "fuel": "diesel",
            "transmission": "automatic",
            "euro_class": "Euro 6",
            "norm_status": "resolved",
        }
    ]
    sql, parameters = cursor.executed[0]
    assert "ILIKE" in sql
    assert parameters[-2:] == [0, 50]


def test_search_without_text_or_conditions_matches_everything() -> None:
    cursor = _Cursor([])

    _repository(cursor).search_page([], "", cursor_id=5, limit=10)

    sql, parameters = cursor.executed[0]
    assert "WHERE true AND source_record_id > %s" in sql
    assert parameters == [5, 10]


def test_search_count_uses_the_same_predicate() -> None:
    cursor = _Cursor([])

    assert _repository(cursor).search_count([], "volvo") == 7
    assert "ILIKE" in cursor.executed[0][0]
