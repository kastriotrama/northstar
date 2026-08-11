"""Safe promotion of resolved TecDoc engine relationships to Neo4j."""

from __future__ import annotations

from dataclasses import dataclass

from neo4j import Driver, ManagedTransaction

from northstar.node_ids import is_valid_node_id


@dataclass(frozen=True)
class ResolvedEngineRelationship:
    variant_node_id: str
    engine_node_id: str
    power_kw: int | None
    source_assertion_key: str


class GraphRelationshipConflictError(RuntimeError):
    """Raised when promotion would violate singular USES_ENGINE cardinality."""


_WRITE_QUERY = """
UNWIND $rows AS row
MATCH (variant:VehicleVariant {id: row.variant_node_id})
MATCH (engine:Engine {id: row.engine_node_id})
OPTIONAL MATCH (variant)-[:USES_ENGINE]->(other:Engine)
WITH row, variant, engine, collect(other.id) AS existing_engine_ids
WHERE size(existing_engine_ids) = 0 OR existing_engine_ids = [engine.id]
MERGE (variant)-[relationship:USES_ENGINE]->(engine)
SET relationship.power_kw = row.power_kw,
    relationship.source_system = 'tecdoc',
    relationship.source_assertion_key = row.source_assertion_key
RETURN count(relationship) AS written
"""


def _write_transaction(
    transaction: ManagedTransaction,
    rows: list[dict[str, object]],
) -> int:
    record = transaction.run(_WRITE_QUERY, rows=rows).single()
    written = 0 if record is None else int(record["written"])
    if written != len(rows):
        raise GraphRelationshipConflictError(
            "Neo4j promotion stopped: a node is missing or a variant already uses another engine"
        )
    return written


def write_resolved_engine_relationships(
    driver: Driver,
    relationships: tuple[ResolvedEngineRelationship, ...],
) -> int:
    """Promote only unambiguous one-engine variants in one rollback-safe transaction."""

    variant_targets: dict[str, str] = {}
    rows: list[dict[str, object]] = []
    for relationship in relationships:
        if not is_valid_node_id(relationship.variant_node_id):
            raise ValueError("variant_node_id must be a canonical NorthStar ID")
        if not is_valid_node_id(relationship.engine_node_id):
            raise ValueError("engine_node_id must be a canonical NorthStar ID")
        existing = variant_targets.setdefault(
            relationship.variant_node_id, relationship.engine_node_id
        )
        if existing != relationship.engine_node_id:
            raise GraphRelationshipConflictError(
                f"Variant {relationship.variant_node_id} has multiple resolved engines"
            )
        rows.append(
            {
                "variant_node_id": relationship.variant_node_id,
                "engine_node_id": relationship.engine_node_id,
                "power_kw": relationship.power_kw,
                "source_assertion_key": relationship.source_assertion_key,
            }
        )
    if not rows:
        return 0
    with driver.session() as session:
        return session.execute_write(_write_transaction, rows)
