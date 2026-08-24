"""Safe promotion of resolved TecDoc engine relationships to Neo4j."""

from __future__ import annotations

from dataclasses import dataclass

from neo4j import Driver, ManagedTransaction

from ingestion.tecdoc.canonical_promotion import CanonicalPromotion
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


_PROMOTION_QUERY = """
UNWIND $rows AS row
MERGE (manufacturer:Manufacturer {id: row.manufacturer_id})
SET manufacturer.canonical_name = row.manufacturer_name
MERGE (family:ModelFamily {id: row.model_family_id})
SET family.canonical_name = row.model_family_name
MERGE (family)-[:MADE_BY]->(manufacturer)
MERGE (engine:Engine {id: row.engine_id})
SET engine.engine_code = row.engine_code,
    engine.displacement_cc = row.displacement_cc,
    engine.fuel_type = row.fuel_type
MERGE (variant:VehicleVariant:Provisional {id: row.variant_id})
SET variant.market = [], variant.year_from = row.year_from, variant.year_to = row.year_to
SET variant.engine_link_status = row.engine_link_status,
    variant.power_kw = row.power_kw,
    variant.displacement_cc = row.displacement_cc,
    variant.fuel_type = row.fuel_type,
    variant.tecdoc_fuel_code = row.tecdoc_fuel_code,
    variant.tecdoc_engine_type_code = row.tecdoc_engine_type_code,
    variant.drive_type = row.drive_type,
    variant.tecdoc_drive_type_code = row.drive_code,
    variant.tecdoc_drive_official_label = row.drive_official_label
MERGE (variant)-[:VARIANT_OF]->(family)
FOREACH (_ IN CASE WHEN row.bodywork_id IS NULL THEN [] ELSE [1] END |
  MERGE (bodywork:BodyType {id: row.bodywork_id})
  SET bodywork.canonical_name = row.bodywork_name,
      bodywork.official_label = row.bodywork_official_label,
      bodywork.tecdoc_body_type_code = row.bodywork_code,
      bodywork.terminology_status = 'canonical_mapped_from_official_english'
  MERGE (variant)-[:HAS_BODY]->(bodywork)
)
FOREACH (_ IN CASE WHEN row.transmission_id IS NULL THEN [] ELSE [1] END |
  MERGE (transmission:Transmission {id: row.transmission_id})
  SET transmission.transmission_code = row.transmission_code,
      transmission.tecdoc_transmission_type_code = row.transmission_type_code,
      transmission.transmission_type_name = row.transmission_type_name,
      transmission.speeds = row.transmission_speeds
  MERGE (variant)-[:USES_TRANSMISSION]->(transmission)
)
MERGE (alias:Alias {assertion_identity: row.assertion_identity})
ON CREATE SET alias.id = row.alias_id,
              alias.source_system = 'tecdoc',
              alias.source_record_key = row.source_record_key,
              alias.source_assertion_key = row.source_assertion_key,
              alias.alias_type = 'k_type'
SET alias.alias_text = row.alias_text, alias.confidence = 1.0
MERGE (alias)-[:REFERS_TO]->(variant)
WITH row, variant, engine
OPTIONAL MATCH (variant)-[:USES_ENGINE]->(other:Engine)
WITH row, variant, engine, collect(other.id) AS existing_engine_ids
WHERE size(existing_engine_ids) = 0 OR existing_engine_ids = [engine.id]
MERGE (variant)-[relationship:USES_ENGINE]->(engine)
SET relationship.power_kw = row.power_kw,
    relationship.source_system = 'tecdoc',
    relationship.source_assertion_key = row.source_assertion_key + ':engine'
RETURN count(variant) AS written
"""

_VEHICLE_FACTS_PROMOTION_QUERY = """
UNWIND $rows AS row
MERGE (manufacturer:Manufacturer {id: row.manufacturer_id})
SET manufacturer.canonical_name = row.manufacturer_name
MERGE (family:ModelFamily {id: row.model_family_id})
SET family.canonical_name = row.model_family_name
MERGE (family)-[:MADE_BY]->(manufacturer)
MERGE (variant:VehicleVariant:Provisional {id: row.variant_id})
SET variant.market = [], variant.year_from = row.year_from, variant.year_to = row.year_to,
    variant.engine_link_status = row.engine_link_status,
    variant.power_kw = row.power_kw,
    variant.displacement_cc = row.displacement_cc,
    variant.fuel_type = row.fuel_type,
    variant.tecdoc_fuel_code = row.tecdoc_fuel_code,
    variant.tecdoc_engine_type_code = row.tecdoc_engine_type_code
SET variant.drive_type = row.drive_type,
    variant.tecdoc_drive_type_code = row.drive_code,
    variant.tecdoc_drive_official_label = row.drive_official_label
MERGE (variant)-[:VARIANT_OF]->(family)
FOREACH (_ IN CASE WHEN row.bodywork_id IS NULL THEN [] ELSE [1] END |
  MERGE (bodywork:BodyType {id: row.bodywork_id})
  SET bodywork.canonical_name = row.bodywork_name,
      bodywork.official_label = row.bodywork_official_label,
      bodywork.tecdoc_body_type_code = row.bodywork_code,
      bodywork.terminology_status = 'canonical_mapped_from_official_english'
  MERGE (variant)-[:HAS_BODY]->(bodywork)
)
FOREACH (_ IN CASE WHEN row.transmission_id IS NULL THEN [] ELSE [1] END |
  MERGE (transmission:Transmission {id: row.transmission_id})
  SET transmission.transmission_code = row.transmission_code,
      transmission.tecdoc_transmission_type_code = row.transmission_type_code,
      transmission.transmission_type_name = row.transmission_type_name,
      transmission.speeds = row.transmission_speeds
  MERGE (variant)-[:USES_TRANSMISSION]->(transmission)
)
MERGE (alias:Alias {assertion_identity: row.assertion_identity})
ON CREATE SET alias.id = row.alias_id,
              alias.source_system = 'tecdoc',
              alias.source_record_key = row.source_record_key,
              alias.source_assertion_key = row.source_assertion_key,
              alias.alias_type = 'k_type'
SET alias.alias_text = row.alias_text, alias.confidence = 1.0
MERGE (alias)-[:REFERS_TO]->(variant)
RETURN count(variant) AS written
"""


def _promote_vehicle_facts_transaction(
    transaction: ManagedTransaction,
    rows: list[dict[str, object]],
) -> int:
    record = transaction.run(_VEHICLE_FACTS_PROMOTION_QUERY, rows=rows).single()
    written = 0 if record is None else int(record["written"])
    if written != len(rows):
        raise GraphRelationshipConflictError("KType facts-only promotion was incomplete")
    return written


def _promote_transaction(
    transaction: ManagedTransaction,
    rows: list[dict[str, object]],
) -> int:
    record = transaction.run(_PROMOTION_QUERY, rows=rows).single()
    written = 0 if record is None else int(record["written"])
    if written != len(rows):
        raise GraphRelationshipConflictError(
            "Canonical promotion stopped: a variant already uses another engine"
        )
    return written


def promote_canonical_vehicles(
    driver: Driver,
    promotions: tuple[CanonicalPromotion, ...],
) -> int:
    """Idempotently create graph nodes for graph-safe provisional KTypes."""

    linked_rows = [promotion.__dict__ for promotion in promotions if promotion.engine_id]
    facts_only_rows = [promotion.__dict__ for promotion in promotions if not promotion.engine_id]
    if not linked_rows and not facts_only_rows:
        return 0
    with driver.session() as session:
        written = session.execute_write(_promote_transaction, linked_rows) if linked_rows else 0
        if facts_only_rows:
            written += session.execute_write(_promote_vehicle_facts_transaction, facts_only_rows)
        return written
