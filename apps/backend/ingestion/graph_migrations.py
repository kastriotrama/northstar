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
    NodeIdPrefix.FUEL_TYPE: "FuelType",
    NodeIdPrefix.VEHICLE_VARIANT: "VehicleVariant",
    NodeIdPrefix.ALIAS: "Alias",
}


@dataclass(frozen=True)
class GraphMigrationStatement:
    """One named, idempotent schema statement."""

    name: str
    kind: Literal["constraint", "index"]
    label: str
    properties: tuple[str, ...]
    schema_type: Literal["UNIQUENESS", "RANGE"]
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
        label=label,
        properties=("id",),
        schema_type="UNIQUENESS",
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
    label="Alias",
    properties=("assertion_identity",),
    schema_type="UNIQUENESS",
    cypher=(
        "CREATE CONSTRAINT alias_assertion_identity_unique IF NOT EXISTS "
        "FOR (n:Alias) REQUIRE n.assertion_identity IS UNIQUE"
    ),
)

LOOKUP_INDEXES: tuple[GraphMigrationStatement, ...] = (
    GraphMigrationStatement(
        name="alias_resolve_entry",
        kind="index",
        label="Alias",
        properties=("source_system", "alias_type", "alias_text"),
        schema_type="RANGE",
        cypher=(
            "CREATE INDEX alias_resolve_entry IF NOT EXISTS "
            "FOR (n:Alias) ON (n.source_system, n.alias_type, n.alias_text)"
        ),
    ),
    GraphMigrationStatement(
        name="alias_text_lookup",
        kind="index",
        label="Alias",
        properties=("alias_text",),
        schema_type="RANGE",
        cypher=(
            "CREATE INDEX alias_text_lookup IF NOT EXISTS "
            "FOR (n:Alias) ON (n.alias_text)"
        ),
    ),
    GraphMigrationStatement(
        name="model_family_canonical_name_lookup",
        kind="index",
        label="ModelFamily",
        properties=("canonical_name",),
        schema_type="RANGE",
        cypher=(
            "CREATE INDEX model_family_canonical_name_lookup IF NOT EXISTS "
            "FOR (n:ModelFamily) ON (n.canonical_name)"
        ),
    ),
    GraphMigrationStatement(
        name="manufacturer_canonical_name_lookup",
        kind="index",
        label="Manufacturer",
        properties=("canonical_name",),
        schema_type="RANGE",
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


@dataclass(frozen=True)
class GraphSchemaObject:
    """Relevant database metadata for one constraint or index."""

    name: str
    labels_or_types: tuple[str, ...]
    properties: tuple[str, ...]
    schema_type: str


def fetch_constraint_definitions(driver: Driver) -> dict[str, GraphSchemaObject]:
    names = [
        statement.name
        for statement in GRAPH_MIGRATION_STATEMENTS
        if statement.kind == "constraint"
    ]
    with driver.session() as session:
        records = session.run(
            "SHOW CONSTRAINTS "
            "YIELD name, labelsOrTypes, properties, type "
            "WHERE name IN $names "
            "RETURN name, labelsOrTypes, properties, type",
            names=names,
        ).data()
    return _schema_objects_by_name(records)


def fetch_index_definitions(driver: Driver) -> dict[str, GraphSchemaObject]:
    names = [
        statement.name
        for statement in GRAPH_MIGRATION_STATEMENTS
        if statement.kind == "index"
    ]
    with driver.session() as session:
        records = session.run(
            "SHOW INDEXES "
            "YIELD name, labelsOrTypes, properties, type "
            "WHERE name IN $names "
            "RETURN name, labelsOrTypes, properties, type",
            names=names,
        ).data()
    return _schema_objects_by_name(records)


def _schema_objects_by_name(
    records: list[dict[str, object]],
) -> dict[str, GraphSchemaObject]:
    objects: dict[str, GraphSchemaObject] = {}
    for record in records:
        name = record["name"]
        labels_or_types = record["labelsOrTypes"]
        properties = record["properties"]
        schema_type = record["type"]
        if (
            not isinstance(name, str)
            or not isinstance(labels_or_types, list)
            or not all(isinstance(value, str) for value in labels_or_types)
            or not isinstance(properties, list)
            or not all(isinstance(value, str) for value in properties)
            or not isinstance(schema_type, str)
        ):
            raise TypeError("Neo4j returned invalid schema metadata")
        objects[name] = GraphSchemaObject(
            name=name,
            labels_or_types=tuple(labels_or_types),
            properties=tuple(properties),
            schema_type=schema_type,
        )
    return objects
