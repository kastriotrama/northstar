from collections.abc import Iterator

import pytest
from neo4j import Driver, GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from api.app.core.settings import get_settings
from ingestion.tecdoc.canonical_promotion import CanonicalPromotion
from ingestion.tecdoc.graph_writer import (
    GraphRelationshipConflictError,
    ResolvedEngineRelationship,
    promote_canonical_vehicles,
    write_resolved_engine_relationships,
)
from northstar.alias_identity import build_assertion_identity
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
    relationship = ResolvedEngineRelationship(variant_id, engine_id, 140, "ktype:1:engine:1")

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
            "MATCH (:VehicleVariant {id:$variant_id})-[:USES_ENGINE]->(e:Engine) RETURN e.id AS id",
            variant_id=variant_id,
        ).single(strict=True)["id"]
    assert target == engine_id


def test_promotes_complete_ktype_hierarchy_idempotently(graph_driver: Driver) -> None:
    manufacturer_id = mint_node_id("MFR")
    family_id = mint_node_id("FAM")
    variant_id = mint_node_id("VEH")
    engine_id = mint_node_id("ENG")
    transmission_id = mint_node_id("TRN")
    bodywork_id = mint_node_id("BDY")
    alias_id = mint_node_id("ALI")
    assertion_key = f"ktype:{variant_id}"
    promotion = CanonicalPromotion(
        manufacturer_id=manufacturer_id,
        manufacturer_name="VOLVO",
        model_family_id=family_id,
        model_family_name="XC60",
        variant_id=variant_id,
        year_from=2018,
        year_to=None,
        engine_id=engine_id,
        engine_code="D4204T14",
        displacement_cc=1969,
        displacement_source="table_155_exact",
        fuel_type="diesel",
        tecdoc_fuel_code="002",
        tecdoc_engine_type_code="002",
        engine_link_status="linked",
        power_kw=140,
        alias_id=alias_id,
        alias_text="12345",
        source_record_key="ktype:12345",
        source_assertion_key=assertion_key,
        assertion_identity=build_assertion_identity("tecdoc", assertion_key),
        bodywork_id=bodywork_id,
        bodywork_code="027",
        transmission_id=transmission_id,
        transmission_code="TG-81SC",
        transmission_type_code="002",
        transmission_speeds=8,
        bodywork_name="sedan",
        bodywork_official_label="Saloon",
        transmission_type_name="Fully Automatic",
    )
    try:
        assert promote_canonical_vehicles(graph_driver, (promotion,)) == 1
        assert promote_canonical_vehicles(graph_driver, (promotion,)) == 1
        with graph_driver.session() as session:
            record = session.run(
                "MATCH (a:Alias {id:$alias_id})-[:REFERS_TO]->(v:VehicleVariant) "
                "MATCH (v)-[:VARIANT_OF]->(f:ModelFamily) "
                "MATCH (v)-[:USES_ENGINE]->(e:Engine) "
                "MATCH (v)-[:USES_TRANSMISSION]->(t:Transmission) "
                "MATCH (v)-[:HAS_BODY]->(b:BodyType) "
                "MATCH (f)-[:MADE_BY]->(m:Manufacturer) "
                "RETURN a.alias_text AS alias, v.id AS variant, e.id AS engine, "
                "f.id AS family, m.id AS manufacturer, t.id AS transmission, "
                "t.speeds AS speeds, t.transmission_type_name AS transmission_type, "
                "b.id AS bodywork, b.tecdoc_body_type_code AS body_code, "
                "b.canonical_name AS body_name",
                alias_id=alias_id,
                family_id=family_id,
            ).single(strict=True)
            model_family_links = session.run(
                "MATCH (:VehicleVariant {id:$variant_id})-[r:VARIANT_OF]->"
                "(:ModelFamily {id:$family_id}) RETURN count(r) AS count",
                variant_id=variant_id,
                family_id=family_id,
            ).single(strict=True)["count"]
        assert dict(record) == {
            "alias": "12345",
            "variant": variant_id,
            "engine": engine_id,
            "family": family_id,
            "manufacturer": manufacturer_id,
            "transmission": transmission_id,
            "speeds": 8,
            "transmission_type": "Fully Automatic",
            "bodywork": bodywork_id,
            "body_code": "027",
            "body_name": "sedan",
        }
        assert model_family_links == 1
    finally:
        with graph_driver.session() as session:
            session.run(
                "MATCH (n) WHERE n.id IN $ids DETACH DELETE n",
                ids=[manufacturer_id, family_id, variant_id, engine_id, transmission_id,
                     bodywork_id, alias_id],
            ).consume()


def test_promotes_ktype_facts_without_fabricating_engine(graph_driver: Driver) -> None:
    promotion = CanonicalPromotion(
        manufacturer_id=mint_node_id("MFR"),
        manufacturer_name="TESLA",
        model_family_id=mint_node_id("FAM"),
        model_family_name="MODEL 3",
        variant_id=mint_node_id("VEH"),
        year_from=2020,
        year_to=None,
        engine_id=None,
        engine_code=None,
        displacement_cc=None,
        displacement_source=None,
        fuel_type="electric",
        tecdoc_fuel_code="011",
        tecdoc_engine_type_code="040",
        engine_link_status="allocation_missing",
        power_kw=208,
        alias_id=mint_node_id("ALI"),
        alias_text="900001",
        source_record_key="ktype:900001",
        source_assertion_key="ktype:900001",
        assertion_identity=build_assertion_identity("tecdoc", "ktype:900001"),
    )
    try:
        assert promote_canonical_vehicles(graph_driver, (promotion,)) == 1
        with graph_driver.session() as session:
            record = session.run(
                "MATCH (:Alias {id:$alias_id})-[:REFERS_TO]->(v:VehicleVariant) "
                "MATCH (v)-[:VARIANT_OF]->(f:ModelFamily)-[:MADE_BY]->(m:Manufacturer) "
                "OPTIONAL MATCH (v)-[:USES_ENGINE]->(e:Engine) "
                "RETURN v.engine_link_status AS status, v.tecdoc_engine_type_code AS type, "
                "count(e) AS engines, f.id AS family, m.id AS manufacturer",
                alias_id=promotion.alias_id,
            ).single(strict=True)
        assert dict(record) == {
            "status": "allocation_missing",
            "type": "040",
            "engines": 0,
            "family": promotion.model_family_id,
            "manufacturer": promotion.manufacturer_id,
        }
    finally:
        with graph_driver.session() as session:
            session.run(
                "MATCH (n) WHERE n.id IN $ids DETACH DELETE n",
                ids=[
                    promotion.manufacturer_id,
                    promotion.model_family_id,
                    promotion.variant_id,
                    promotion.alias_id,
                ],
            ).consume()
