from collections.abc import Iterator

import pytest
from neo4j import Driver, GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from api.app.core.settings import get_settings
from ingestion.graph_migrations import (
    GRAPH_MIGRATION_STATEMENTS,
    NODE_LABELS_BY_PREFIX,
    fetch_constraint_definitions,
    fetch_index_definitions,
    run_graph_migrations,
)
from northstar.alias_identity import build_assertion_identity
from northstar.node_ids import NodeIdPrefix


@pytest.fixture(scope="module")
def graph_driver() -> Iterator[Driver]:
    settings = get_settings()
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )
    try:
        driver.verify_connectivity()
    except (Neo4jError, ServiceUnavailable):
        driver.close()
        if settings.environment == "test":
            raise
        pytest.skip("Neo4j is unavailable; start it with docker compose up -d neo4j")

    yield driver
    driver.close()


def test_migration_runs_twice_and_creates_all_named_schema_objects(
    graph_driver: Driver,
) -> None:
    first_applied = run_graph_migrations(graph_driver)
    second_applied = run_graph_migrations(graph_driver)

    assert first_applied == second_applied
    assert len(first_applied) == len(GRAPH_MIGRATION_STATEMENTS)

    constraint_definitions = fetch_constraint_definitions(graph_driver)
    index_definitions = fetch_index_definitions(graph_driver)

    for statement in GRAPH_MIGRATION_STATEMENTS:
        definitions = (
            constraint_definitions if statement.kind == "constraint" else index_definitions
        )
        actual = definitions[statement.name]
        assert actual.labels_or_types == (statement.label,)
        assert actual.properties == statement.properties
        assert actual.schema_type == statement.schema_type


@pytest.mark.parametrize(
    ("prefix", "label"),
    list(NODE_LABELS_BY_PREFIX.items()),
)
def test_id_constraint_rejects_duplicates_for_every_label(
    graph_driver: Driver,
    prefix: NodeIdPrefix,
    label: str,
) -> None:
    run_graph_migrations(graph_driver)
    node_id = f"{prefix.value}-{'0' * 26}"
    create = f"CREATE (n:{label}:Scrum15IdFixture {{id: $id}})"
    cleanup = "MATCH (n:Scrum15IdFixture) DETACH DELETE n"

    with graph_driver.session() as session:
        session.run(cleanup).consume()
        session.run(create, id=node_id).consume()
        try:
            with pytest.raises(Neo4jError):
                session.run(create, id=node_id).consume()
        finally:
            session.run(cleanup).consume()


def test_alias_identity_constraint_rejects_duplicates(graph_driver: Driver) -> None:
    run_graph_migrations(graph_driver)

    assertion_identity = build_assertion_identity("scrum15", "duplicate:check")
    create = "CREATE (:Alias:Scrum15Fixture {id: $id, assertion_identity: $identity})"
    cleanup = "MATCH (n:Scrum15Fixture) DETACH DELETE n"

    with graph_driver.session() as session:
        session.run(cleanup).consume()
        session.run(
            create,
            id=f"ALI-{'1' * 26}",
            identity=assertion_identity,
        ).consume()
        try:
            with pytest.raises(Neo4jError):
                session.run(
                    create,
                    id=f"ALI-{'2' * 26}",
                    identity=assertion_identity,
                ).consume()
        finally:
            session.run(cleanup).consume()
