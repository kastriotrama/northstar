import re
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from neo4j import Driver, GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from api.app.core.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DOC = REPO_ROOT / "docs" / "graph-schema-design.md"

SEED_QUERY = """
CREATE
  (mfr:Manufacturer:Scrum13Fixture
    {id: 'MFR-01A', canonical_name: 'Mercedes-Benz'}),
  (e_family:ModelFamily:Scrum13Fixture
    {id: 'FAM-02B', canonical_name: 'E-Class'}),
  (m_family:ModelFamily:Scrum13Fixture
    {id: 'FAM-02C', canonical_name: 'M-Class'}),
  (w212:Platform:Scrum13Fixture {id: 'PLT-03C', platform_code: 'W212'}),
  (w164:Platform:Scrum13Fixture {id: 'PLT-16Q', platform_code: 'W164'}),
  (diesel:Engine:Scrum13Fixture
    {id: 'ENG-04D', engine_code: 'OM642', fuel_type: 'diesel'}),
  (petrol:Engine:Scrum13Fixture
    {id: 'ENG-GAP', engine_code: 'M276', fuel_type: 'petrol'}),
  (transmission:Transmission:Scrum13Fixture {id: 'TRN-05E'}),
  (sedan:BodyType:Scrum13Fixture {id: 'BDY-06F', canonical_name: 'sedan'}),
  (suv:BodyType:Scrum13Fixture {id: 'BDY-17R', canonical_name: 'suv'}),
  (v1:VehicleVariant:Scrum13Fixture
    {id: 'VEH-07G', year_from: 2009, year_to: 2013}),
  (v2:VehicleVariant:Provisional:Scrum13Fixture
    {id: 'VEH-08H', year_from: 2009, year_to: 2011}),
  (v3:VehicleVariant:Scrum13Fixture
    {id: 'VEH-15P', year_from: 2009, year_to: 2013}),
  (gap:VehicleVariant:Scrum13Fixture
    {id: 'VEH-GAP', year_from: 2010, year_to: 2012}),
  (plate:Alias:Scrum13Fixture {
    id: 'ALI-10J', alias_text: 'ABC123', alias_type: 'plate',
    source_system: 'transportstyrelsen',
    source_assertion_key: 'vehicle-abc123:plate:0', confidence: 0.97
  }),
  (ktype:Alias:Scrum13Fixture {
    id: 'ALI-09I', alias_text: '13902', alias_type: 'k_type',
    source_system: 'tecdoc',
    source_assertion_key: 'vehicle-82931:k_type:0', confidence: 1.0
  }),
  (name1:Alias:Scrum13Fixture {
    id: 'ALI-12L', alias_text: 'E350', alias_type: 'model_name',
    source_system: 'tecdoc',
    source_assertion_key: 'vehicle-82931:model_name:0', confidence: 0.92
  }),
  (name2:Alias:Scrum13Fixture {
    id: 'ALI-13M', alias_text: 'E350', alias_type: 'model_name',
    source_system: 'tecdoc',
    source_assertion_key: 'vehicle-82940:model_name:0', confidence: 0.90
  }),
  (name3:Alias:Scrum13Fixture {
    id: 'ALI-14N', alias_text: 'E350', alias_type: 'model_name',
    source_system: 'transportstyrelsen',
    source_assertion_key: 'vehicle-abc123:model_name:0', confidence: 0.81
  }),
  (e_family)-[:MADE_BY]->(mfr),
  (m_family)-[:MADE_BY]->(mfr),
  (w212)-[:PLATFORM_OF]->(e_family),
  (w164)-[:PLATFORM_OF]->(m_family),
  (v1)-[:BUILT_ON]->(w212),
  (v2)-[:BUILT_ON]->(w164),
  (v3)-[:BUILT_ON]->(w212),
  (gap)-[:BUILT_ON]->(w212),
  (v1)-[:USES_ENGINE]->(diesel),
  (v2)-[:USES_ENGINE]->(diesel),
  (v3)-[:USES_ENGINE]->(diesel),
  (gap)-[:USES_ENGINE]->(petrol),
  (v1)-[:USES_TRANSMISSION]->(transmission),
  (v2)-[:USES_TRANSMISSION]->(transmission),
  (v3)-[:USES_TRANSMISSION]->(transmission),
  (v1)-[:HAS_BODY]->(sedan),
  (v2)-[:HAS_BODY]->(suv),
  (v3)-[:HAS_BODY]->(sedan),
  (gap)-[:HAS_BODY]->(sedan),
  (plate)-[:REFERS_TO]->(v1),
  (ktype)-[:REFERS_TO]->(v1),
  (name1)-[:REFERS_TO]->(v1),
  (name2)-[:REFERS_TO]->(v3),
  (name3)-[:REFERS_TO]->(v1)
"""


def documented_query(name: str) -> str:
    text = SCHEMA_DOC.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"<!-- query:{re.escape(name)}:start -->\s*"
        r"```cypher\n(.*?)\n```\s*"
        rf"<!-- query:{re.escape(name)}:end -->",
        flags=re.DOTALL,
    )
    match = pattern.search(text)
    if match is None:
        raise AssertionError(f"Missing documented query block: {name}")
    return match.group(1)


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

    with driver.session() as session:
        session.run("MATCH (n:Scrum13Fixture) DETACH DELETE n").consume()
        session.run(SEED_QUERY).consume()

    yield driver

    with driver.session() as session:
        session.run("MATCH (n:Scrum13Fixture) DETACH DELETE n").consume()
    driver.close()


def run_query(driver: Driver, name: str, parameters: dict[str, object]) -> list[dict[str, object]]:
    with driver.session() as session:
        return session.run(documented_query(name), parameters=parameters).data()


def test_plate_resolve_returns_unambiguous_candidate(graph_driver: Driver) -> None:
    records = run_query(
        graph_driver,
        "plate_resolve",
        {"source_system": "transportstyrelsen", "alias_text": "ABC123"},
    )

    assert records == [
        {
            "alias_id": "ALI-10J",
            "assertion_key": "vehicle-abc123:plate:0",
            "confidence": 0.97,
            "variant_id": "VEH-07G",
        }
    ]


def test_k_type_resolve_returns_unambiguous_candidate(graph_driver: Driver) -> None:
    records = run_query(
        graph_driver,
        "k_type_resolve",
        {"source_system": "tecdoc", "alias_text": "13902"},
    )

    assert [record["variant_id"] for record in records] == ["VEH-07G"]


def test_sibling_amortization_excludes_input_and_provisional_nodes(
    graph_driver: Driver,
) -> None:
    records = run_query(
        graph_driver,
        "sibling_amortization",
        {"variant_id": "VEH-07G"},
    )

    assert records == [
        {"engine_code": "OM642", "sibling_variant_ids": ["VEH-15P"]}
    ]


def test_structured_form_search_uses_canonical_hierarchy(graph_driver: Driver) -> None:
    records = run_query(
        graph_driver,
        "structured_form_search",
        {"make": "Mercedes-Benz", "model": "E-Class", "year": 2011, "fuel": "diesel"},
    )

    assert [record["variant_id"] for record in records] == ["VEH-07G", "VEH-15P"]


def test_conflict_lookup_returns_distinct_alias_targets(graph_driver: Driver) -> None:
    records = run_query(graph_driver, "conflict_lookup", {"alias_text": "E350"})

    assert len(records) == 1
    target_ids = cast(list[str], records[0]["target_ids"])
    assert set(target_ids) == {"VEH-07G", "VEH-15P"}
    assertions = cast(list[dict[str, object]], records[0]["assertions"])
    assert {assertion["alias_id"] for assertion in assertions} == {
        "ALI-12L",
        "ALI-13M",
        "ALI-14N",
    }


def test_gap_detection_finds_only_incomplete_active_variants(graph_driver: Driver) -> None:
    records = run_query(
        graph_driver,
        "gap_detection",
        {"variant_ids": ["VEH-07G", "VEH-08H", "VEH-GAP"]},
    )

    assert records == [{"variant_id": "VEH-GAP"}]
