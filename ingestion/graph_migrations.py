"""Idempotent Neo4j constraint and index migrations (SCRUM-15).

Statement names are stable public contract: they appear in
docs/graph-schema-design.md §8 and are asserted by the doc contract tests.
Every statement uses IF NOT EXISTS so the migration can run repeatedly.

Neo4j Community edition does not support composite uniqueness (node key)
constraints. Alias identity over (source_system, source_assertion_key) is
therefore enforced through the writer-computed single property
`assertion_identity` under a plain uniqueness constraint; composite lookup
indexes (which Community does support) serve the resolve entry points.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from neo4j import Driver

from northstar.node_ids import NodeIdPrefix

NODE_LABELS_BY_PREFIX: dict[NodeIdPrefix, str] = {
    NodeIdPrefix.MANUFACTURER: "Manufacturer",
    NodeIdPrefix.MODEL_FAMILY: "ModelFamily",
    NodeIdPrefix.PLATFORM: "Platform",
    NodeIdPrefix.ENGINE: "Engine",
    NodeIdPrefix.TRANSMISSION: "Transmission",
    NodeIdPrefix.BODY_TYPE: "BodyType",
    NodeIdPrefix.VEHICLE_VARIANT: "VehicleVariant",
    NodeIdPrefix.ALIAS: "Alias",
}


@dataclass(frozen=True)
class GraphMigrationStatement:
    """One named, idempotent schema statement."""

    name: str
    kind: Literal["constraint", "index"]
    cypher: str


def _snake_case(label: str) -> str:
    characters: list[str] = []
    for index, character in enumerate(label):
        if character.isupper() and index > 0:
            characters.append("_")
        characters.append(character.lower())
    return "".join(characters)


def _node_id_constraint(label: str) -> GraphMigrationStatement:
    name = f"{_snake_case(label)}_id_unique"
    return GraphMigrationStatement(
        name=name,
        kind="constraint",
        cypher=(
            f"CREATE CONSTRAINT {name} IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
        ),
    )


NODE_ID_CONSTRAINTS: tuple[GraphMigrationStatement, ...] = tuple(
    _node_id_constraint(label) for label in NODE_LABELS_BY_PREFIX.values()
)

ALIAS_IDENTITY_CONSTRAINT = GraphMigrationStatement(
    name="alias_assertion_identity_unique",
    kind="constraint",
    cypher=(
        "CREATE CONSTRAINT alias_assertion_identity_unique IF NOT EXISTS "
        "FOR (n:Alias) REQUIRE n.assertion_identity IS UNIQUE"
    ),
)

LOOKUP_INDEXES: tuple[GraphMigrationStatement, ...] = (
    GraphMigrationStatement(
        name="alias_resolve_entry",
        kind="index",
        cypher=(
            "CREATE INDEX alias_resolve_entry IF NOT EXISTS "
            "FOR (n:Alias) ON (n.source_system, n.alias_type, n.alias_text)"
        ),
    ),
    GraphMigrationStatement(
        name="alias_text_lookup",
        kind="index",
        cypher=(
            "CREATE INDEX alias_text_lookup IF NOT EXISTS "
            "FOR (n:Alias) ON (n.alias_text)"
        ),
    ),
    GraphMigrationStatement(
        name="model_family_canonical_name_lookup",
        kind="index",
        cypher=(
            "CREATE INDEX model_family_canonical_name_lookup IF NOT EXISTS "
            "FOR (n:ModelFamily) ON (n.canonical_name)"
        ),
    ),
    GraphMigrationStatement(
        name="manufacturer_canonical_name_lookup",
        kind="index",
        cypher=(
            "CREATE INDEX manufacturer_canonical_name_lookup IF NOT EXISTS "
            "FOR (n:Manufacturer) ON (n.canonical_name)"
        ),
    ),
)

GRAPH_MIGRATION_STATEMENTS: tuple[GraphMigrationStatement, ...] = (
    *NODE_ID_CONSTRAINTS,
    ALIAS_IDENTITY_CONSTRAINT,
    *LOOKUP_INDEXES,
)


def run_graph_migrations(driver: Driver) -> tuple[str, ...]:
    """Apply every migration statement; return applied names in order."""

    with driver.session() as session:
        for statement in GRAPH_MIGRATION_STATEMENTS:
            session.run(statement.cypher).consume()
    return tuple(statement.name for statement in GRAPH_MIGRATION_STATEMENTS)


def fetch_constraint_names(driver: Driver) -> set[str]:
    with driver.session() as session:
        records = session.run("SHOW CONSTRAINTS YIELD name RETURN name").data()
    return {record["name"] for record in records}


def fetch_index_names(driver: Driver) -> set[str]:
    with driver.session() as session:
        records = session.run("SHOW INDEXES YIELD name RETURN name").data()
    return {record["name"] for record in records}
