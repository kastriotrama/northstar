import pytest

from ingestion.tecdoc.graph_writer import (
    GraphRelationshipConflictError,
    ResolvedEngineRelationship,
    _PROMOTION_QUERY,
    _VEHICLE_FACTS_PROMOTION_QUERY,
    write_resolved_engine_relationships,
)
from northstar.node_ids import NodeIdGenerator


class FakeResult:
    def __init__(self, written: int) -> None:
        self._written = written

    def single(self) -> dict[str, int]:
        return {"written": self._written}


class FakeTransaction:
    def __init__(self, written: int) -> None:
        self.written = written
        self.rows: list[dict[str, object]] = []

    def run(self, query: str, *, rows: list[dict[str, object]]) -> FakeResult:
        assert "MERGE (variant)-[relationship:USES_ENGINE]->(engine)" in query
        self.rows = rows
        return FakeResult(self.written)


class FakeSession:
    def __init__(self, transaction: FakeTransaction) -> None:
        self.transaction = transaction

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute_write(self, callback: object, rows: list[dict[str, object]]) -> int:
        return callback(self.transaction, rows)  # type: ignore[operator]


class FakeDriver:
    def __init__(self, written: int) -> None:
        self.transaction = FakeTransaction(written)

    def session(self) -> FakeSession:
        return FakeSession(self.transaction)


def ids() -> tuple[str, str, str]:
    generator = NodeIdGenerator(clock_ms=lambda: 1, entropy=lambda _: b"\x01" * 10)
    variant = generator.mint("VEH")
    engine_one = generator.mint("ENG")
    engine_two = NodeIdGenerator(
        clock_ms=lambda: 1, entropy=lambda _: b"\x02" * 10
    ).mint("ENG")
    return variant, engine_one, engine_two


def test_writes_one_resolved_relationship_idempotently() -> None:
    variant, engine, _ = ids()
    driver = FakeDriver(written=1)
    relationship = ResolvedEngineRelationship(variant, engine, 140, "ktype:1:engine:2")

    assert write_resolved_engine_relationships(driver, (relationship,)) == 1  # type: ignore[arg-type]
    assert driver.transaction.rows[0]["power_kw"] == 140


def test_both_promotion_paths_link_variant_directly_to_model_family() -> None:
    for query in (_PROMOTION_QUERY, _VEHICLE_FACTS_PROMOTION_QUERY):
        assert "MERGE (variant)-[:VARIANT_OF]->(family)" in query
        assert "MERGE (variant)-[:BUILT_ON]" not in query
        assert "MERGE (variant)-[:HAS_BODY]->(bodywork)" in query
        assert "MERGE (variant)-[:USES_TRANSMISSION]->(transmission)" in query


def test_rejects_multiple_engines_for_one_canonical_variant_before_writing() -> None:
    variant, engine_one, engine_two = ids()
    driver = FakeDriver(written=2)
    relationships = (
        ResolvedEngineRelationship(variant, engine_one, 100, "one"),
        ResolvedEngineRelationship(variant, engine_two, 110, "two"),
    )

    with pytest.raises(GraphRelationshipConflictError, match="multiple resolved engines"):
        write_resolved_engine_relationships(driver, relationships)  # type: ignore[arg-type]
    assert driver.transaction.rows == []


def test_rolls_back_transaction_when_nodes_are_missing_or_conflicting() -> None:
    variant, engine, _ = ids()
    driver = FakeDriver(written=0)
    relationship = ResolvedEngineRelationship(variant, engine, None, "one")

    with pytest.raises(GraphRelationshipConflictError, match="node is missing"):
        write_resolved_engine_relationships(driver, (relationship,))  # type: ignore[arg-type]
