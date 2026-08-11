from collections.abc import Iterator

import pytest
from neo4j import Driver, GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from api.app.core.settings import get_settings
from ingestion.tecdoc.graph_writer import (
    GraphRelationshipConflictError,
    ResolvedEngineRelationship,
    write_resolved_engine_relationships,
)
from northstar.node_ids import mint_node_id


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
        pytest.skip("Neo4j is unavailable; start it with docker compose up -d neo4j")
    yield driver
    with driver.session() as session:
        session.run("MATCH (n:TecDocWriterFixture) DETACH DELETE n").consume()
    driver.close()


def test_resolved_engine_relationship_is_idempotent_and_conflict_safe(
    graph_driver: Driver,
) -> None:
    variant_id = mint_node_id("VEH")
    engine_id = mint_node_id("ENG")
    conflicting_engine_id = mint_node_id("ENG")
    with graph_driver.session() as session:
        session.run(
            "CREATE (:VehicleVariant:TecDocWriterFixture {id:$variant_id}), "
            "(:Engine:TecDocWriterFixture {id:$engine_id}), "
            "(:Engine:TecDocWriterFixture {id:$conflicting_engine_id})",
            variant_id=variant_id,
            engine_id=engine_id,
            conflicting_engine_id=conflicting_engine_id,
        ).consume()
    relationship = ResolvedEngineRelationship(
        variant_id, engine_id, 140, "ktype:1:engine:1"
    )

    assert write_resolved_engine_relationships(graph_driver, (relationship,)) == 1
    assert write_resolved_engine_relationships(graph_driver, (relationship,)) == 1
    with graph_driver.session() as session:
        count = session.run(
            "MATCH (:VehicleVariant {id:$variant_id})-[r:USES_ENGINE]->(:Engine) "
            "RETURN count(r) AS count",
            variant_id=variant_id,
        ).single(strict=True)["count"]
    assert count == 1

    conflict = ResolvedEngineRelationship(
        variant_id, conflicting_engine_id, 150, "ktype:1:engine:2"
    )
    with pytest.raises(GraphRelationshipConflictError):
        write_resolved_engine_relationships(graph_driver, (conflict,))
    with graph_driver.session() as session:
        target = session.run(
            "MATCH (:VehicleVariant {id:$variant_id})-[:USES_ENGINE]->(e:Engine) "
            "RETURN e.id AS id",
            variant_id=variant_id,
        ).single(strict=True)["id"]
    assert target == engine_id
