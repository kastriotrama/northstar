from collections.abc import Iterator

import pytest
from neo4j import Driver, GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from api.app.core.settings import get_settings
from ingestion.graph_migrations import (
    GRAPH_MIGRATION_STATEMENTS,
    fetch_constraint_names,
    fetch_index_names,
    run_graph_migrations,
)


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

    constraint_names = fetch_constraint_names(graph_driver)
    index_names = fetch_index_names(graph_driver)

    for statement in GRAPH_MIGRATION_STATEMENTS:
        if statement.kind == "constraint":
            assert statement.name in constraint_names, statement.name
        else:
            assert statement.name in index_names, statement.name


def test_alias_identity_constraint_rejects_duplicates(graph_driver: Driver) -> None:
    run_graph_migrations(graph_driver)

    create = (
        "CREATE (:Alias:Scrum15Fixture {id: $id, "
        "assertion_identity: 'scrum15:duplicate-check'})"
    )
    cleanup = "MATCH (n:Scrum15Fixture) DETACH DELETE n"

    with graph_driver.session() as session:
        session.run(cleanup).consume()
        session.run(create, id="ALI-SCRUM15FIXTUREAAAAAAAAA").consume()
        try:
            with pytest.raises(Neo4jError):
                session.run(create, id="ALI-SCRUM15FIXTUREBBBBBBBBB").consume()
        finally:
            session.run(cleanup).consume()
