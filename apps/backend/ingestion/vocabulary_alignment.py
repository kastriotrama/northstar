"""Materialise approved vocabulary alignments into the graph (draft).

Postgres decides what is aligned; this module writes the approved set into
Neo4j using the alias pattern the graph already uses for KTypes:

    (:Alias {source_system, alias_type:'fuel', alias_text})-[:REFERS_TO]->(:FuelType)
    (:VehicleVariant)-[:USES_FUEL]->(:FuelType)

Fuel is currently a bare string property on VehicleVariant while bodywork,
engine and transmission are nodes. That inconsistency is why the
`electric`/`electricity` divergence was invisible: a string cannot be aliased,
so nothing could reconcile the two spellings. Promoting fuel to a node closes
that structurally rather than by another lookup table.

Compatibility is written as a separate, weighted edge so it can never be
mistaken for equality:

    (:Alias)-[:COMPATIBLE_WITH {support}]->(:BodyType)

Matching must keep treating a compatible pair as neutral, never as agreement.
The graph says what *can* align; the candidate set still decides whether the
field discriminates for a given query.

Every write is idempotent: concepts MERGE on canonical_name, aliases MERGE on
the shared assertion_identity helper, so re-running promotes nothing twice.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from neo4j import Driver, ManagedTransaction
from psycopg import Connection

from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.tecdoc.resolution_migrations import TECDOC_RESOLUTION_RULES_TABLE
from northstar.alias_identity import build_assertion_identity
from northstar.node_ids import mint_node_id

# Concept label per vocabulary. An unregistered vocabulary fails loudly
# instead of writing a stray label.
CONCEPT_LABELS: dict[str, str] = {
    "fuel": "FuelType",
    "bodywork": "BodyType",
    "drive": "DriveType",
}
CONCEPT_ID_PREFIX: dict[str, str] = {
    "fuel": "FUL",
    "bodywork": "BDY",
    "drive": "DRV",
}


@dataclass(frozen=True)
class VocabularyAlignment:
    """One live `equivalent`/`compatible` ruling from `core.tecdoc_resolution_rules`."""

    vocabulary: str
    source_system: str
    source_term: str
    canonical_term: str
    relation: str
    support: int | None

    def __post_init__(self) -> None:
        if self.vocabulary not in CONCEPT_LABELS:
            raise ValueError(f"unsupported vocabulary: {self.vocabulary!r}")
        if self.relation not in {"equivalent", "compatible"}:
            raise ValueError(f"unsupported relation: {self.relation!r}")
        for name in ("source_system", "source_term", "canonical_term"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be empty")

    @property
    def assertion_key(self) -> str:
        return f"vocabulary:{self.vocabulary}:{self.relation}:{self.source_term}:{self.canonical_term}"

    def graph_row(self, *, promoted_at: str) -> dict[str, object]:
        return {
            "alias_id": mint_node_id("ALI"),
            "concept_id": mint_node_id(CONCEPT_ID_PREFIX[self.vocabulary]),
            "source_system": self.source_system.lower(),
            "alias_type": self.vocabulary,
            "alias_text": self.source_term,
            "canonical_term": self.canonical_term,
            "relation": self.relation,
            "support": self.support,
            "promoted_at": promoted_at,
            "assertion_identity": build_assertion_identity(
                self.source_system.lower(), self.assertion_key
            ),
        }


def fetch_approved_alignments(
    connection: Connection, *, vocabulary: str
) -> tuple[VocabularyAlignment, ...]:
    """Read the current live rulings for one vocabulary (fuel/bodywork/drive).

    Live means mutable: unlike the retired `core.vocabulary_alignments`, there
    is no sealed version to pin against. A reviewer's correction takes effect
    on the very next read, the same guarantee `tecdoc_resolution_rules`
    already gives TecDoc's own value resolutions.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT source_system, source_term, canonical_value, relation, support "
            f"FROM {TECDOC_RESOLUTION_RULES_TABLE} "
            "WHERE canonical_field = %s AND decision = 'accepted' "
            "ORDER BY source_system, source_term, canonical_value",
            (vocabulary,),
        )
        rows = cursor.fetchall()
    return tuple(
        VocabularyAlignment(
            vocabulary=vocabulary,
            source_system=str(r[0]),
            source_term=str(r[1]),
            canonical_term=str(r[2]),
            relation=str(r[3]),
            support=None if r[4] is None else int(r[4]),
        )
        for r in rows
    )


@dataclass(frozen=True)
class VocabularyComparisonAlignment:
    """Source-scoped comparison rules for one vocabulary, never a normalization rewrite."""

    vocabulary: str
    ts_equivalences: Mapping[str, str]
    tecdoc_equivalences: Mapping[str, str]
    compatible_pairs: frozenset[tuple[str, str]]


# Kept as the name every existing caller and test already imports; fuel was
# the first vocabulary this shape served, so nothing about the dataclass
# itself is fuel-specific.
FuelAlignment = VocabularyComparisonAlignment


def load_vocabulary_alignment(
    connection: Connection, *, vocabulary: str
) -> VocabularyComparisonAlignment:
    """Build TS- and TecDoc-scoped equivalence maps from the live rules table.

    No rows for a vocabulary is a valid, harmless state (empty maps are a
    no-op for `canonical_fuels`/`align_catalog_fuels`), not an error -- a
    reviewer simply hasn't needed to reconcile that vocabulary yet.
    """

    rows = fetch_approved_alignments(connection, vocabulary=vocabulary)
    maps: dict[str, dict[str, str]] = {"transportstyrelsen": {}, "tecdoc": {}}
    for row in rows:
        if row.source_system not in maps:
            raise ValueError("unsupported alignment source")
        if row.relation != "equivalent":
            continue
        mapping = maps[row.source_system]
        if row.source_term in mapping and mapping[row.source_term] != row.canonical_term:
            raise ValueError("ambiguous vocabulary equivalence")
        mapping[row.source_term] = row.canonical_term
    for mapping in maps.values():
        if set(mapping) & set(mapping.values()):
            raise ValueError("vocabulary equivalences must be flat")
    pairs = set()
    for row in rows:
        if row.relation == "compatible":
            if row.source_system != "transportstyrelsen":
                raise ValueError("only directional TS compatibility is supported")
            pairs.add((
                maps["transportstyrelsen"].get(row.source_term, row.source_term),
                maps["tecdoc"].get(row.canonical_term, row.canonical_term),
            ))
    return VocabularyComparisonAlignment(
        vocabulary, maps["transportstyrelsen"], maps["tecdoc"], frozenset(pairs)
    )


def load_fuel_alignment(connection: Connection) -> VocabularyComparisonAlignment:
    return load_vocabulary_alignment(connection, vocabulary="fuel")


def load_drive_alignment(connection: Connection) -> VocabularyComparisonAlignment:
    return load_vocabulary_alignment(connection, vocabulary="drive")


# Concepts are looked up before minting so a re-run never creates a second
# node for the same canonical term.
_CONCEPT_QUERY = """
UNWIND $rows AS row
MERGE (concept:%(label)s {canonical_name: row.canonical_term})
ON CREATE SET concept.id = row.concept_id
RETURN count(concept) AS written
"""

_EQUIVALENT_QUERY = """
UNWIND $rows AS row
MATCH (concept:%(label)s {canonical_name: row.canonical_term})
MERGE (alias:Alias {assertion_identity: row.assertion_identity})
ON CREATE SET alias.id = row.alias_id,
              alias.source_system = row.source_system,
              alias.alias_type = row.alias_type,
              alias.source_assertion_key = row.assertion_identity
SET alias.alias_text = row.alias_text,
    alias.promoted_at = row.promoted_at
MERGE (alias)-[:REFERS_TO]->(concept)
RETURN count(alias) AS written
"""

# Compatibility is a distinct edge carrying its evidence, so nothing downstream
# can read it as equality.
_COMPATIBLE_QUERY = """
UNWIND $rows AS row
MATCH (concept:%(label)s {canonical_name: row.canonical_term})
MERGE (alias:Alias {assertion_identity: row.assertion_identity})
ON CREATE SET alias.id = row.alias_id,
              alias.source_system = row.source_system,
              alias.alias_type = row.alias_type,
              alias.source_assertion_key = row.assertion_identity
SET alias.alias_text = row.alias_text,
    alias.promoted_at = row.promoted_at
MERGE (alias)-[edge:COMPATIBLE_WITH]->(concept)
SET edge.support = row.support,
    edge.promoted_at = row.promoted_at
RETURN count(alias) AS written
"""

# Existing variants carry fuel as a string; this attaches them to the concept
# node without dropping the property, so a rollback needs no backfill.
_LINK_VARIANT_FUEL_QUERY = """
MATCH (v:VehicleVariant) WHERE v.fuel_type IS NOT NULL
MATCH (concept:FuelType {canonical_name: v.fuel_type})
MERGE (v)-[:USES_FUEL]->(concept)
RETURN count(*) AS linked
"""


def canonical_fuels(
    fuels: frozenset[str], mapping: Mapping[str, str]
) -> frozenset[str]:
    """Map a fuel set into the shared vocabulary; unmapped terms pass through."""

    return frozenset(mapping.get(fuel, fuel) for fuel in fuels)


def align_catalog_fuels(
    catalog: Sequence[VehicleCandidate], mapping: Mapping[str, str]
) -> tuple[VehicleCandidate, ...]:
    """Return the catalog with candidate fuels expressed in the shared vocabulary."""

    if not mapping:
        return tuple(catalog)
    return tuple(
        replace(candidate, fuels=canonical_fuels(candidate.fuels, mapping))
        if candidate.fuels
        else candidate
        for candidate in catalog
    )


def _run(transaction: ManagedTransaction, query: str, rows: list[dict[str, object]]) -> int:
    record = transaction.run(query, rows=rows).single()
    return 0 if record is None else int(record["written"])


def promote_vocabulary_alignments(
    driver: Driver,
    alignments: tuple[VocabularyAlignment, ...],
    *,
    dry_run: bool = True,
) -> dict[str, int]:
    """Write one approved vocabulary set into the graph, idempotently."""

    if not alignments:
        return {"concepts": 0, "equivalent": 0, "compatible": 0}
    vocabularies = {a.vocabulary for a in alignments}
    if len(vocabularies) != 1:
        raise ValueError("promote one vocabulary at a time")

    label = CONCEPT_LABELS[next(iter(vocabularies))]
    promoted_at = datetime.now(UTC).isoformat()
    rows = [a.graph_row(promoted_at=promoted_at) for a in alignments]
    equivalent = [r for r in rows if r["relation"] == "equivalent"]
    compatible = [r for r in rows if r["relation"] == "compatible"]

    if dry_run:
        return {
            "concepts": len({str(r["canonical_term"]) for r in rows}),
            "equivalent": len(equivalent),
            "compatible": len(compatible),
        }

    with driver.session() as session:
        concepts = session.execute_write(_run, _CONCEPT_QUERY % {"label": label}, rows)
        written_eq = (
            session.execute_write(_run, _EQUIVALENT_QUERY % {"label": label}, equivalent)
            if equivalent
            else 0
        )
        written_cmp = (
            session.execute_write(_run, _COMPATIBLE_QUERY % {"label": label}, compatible)
            if compatible
            else 0
        )
    return {"concepts": concepts, "equivalent": written_eq, "compatible": written_cmp}


def link_variants_to_fuel_concepts(driver: Driver, *, dry_run: bool = True) -> int:
    """Attach existing variants to their FuelType concept, keeping the property."""

    if dry_run:
        with driver.session() as session:
            record = session.run(
                "MATCH (v:VehicleVariant) WHERE v.fuel_type IS NOT NULL "
                "MATCH (c:FuelType {canonical_name: v.fuel_type}) RETURN count(*) AS linked"
            ).single()
        return 0 if record is None else int(record["linked"])
    with driver.session() as session:
        record = session.run(_LINK_VARIANT_FUEL_QUERY).single()
    return 0 if record is None else int(record["linked"])
