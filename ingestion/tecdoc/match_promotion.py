"""Evidence-gated KType promotion and Transportstyrelsen alias attachment."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from neo4j import Driver, ManagedTransaction

from northstar.alias_identity import build_assertion_identity
from northstar.node_ids import mint_node_id


class PromotionMode(StrEnum):
    DRY_RUN = "dry_run"
    CONTROLLED = "controlled"
    PRODUCTION = "production"


class MatchPromotionConflictError(RuntimeError):
    """Raised when graph state cannot safely represent a persisted match decision."""


@dataclass(frozen=True)
class MatchPromotion:
    decision_id: UUID
    source_system: str
    source_version: str
    source_entity_key: str
    alias_type: str
    alias_text: str
    ktype_reference: str
    confidence: float

    def __post_init__(self) -> None:
        for name in (
            "source_system",
            "source_version",
            "source_entity_key",
            "alias_type",
            "alias_text",
            "ktype_reference",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    def graph_row(self) -> dict[str, object]:
        assertion_key = f"match-decision:{self.decision_id}:{self.alias_type}"
        return {
            "decision_id": str(self.decision_id),
            "source_system": self.source_system.lower(),
            "source_version": self.source_version,
            "source_entity_key": self.source_entity_key,
            "alias_id": mint_node_id("ALI"),
            "alias_type": self.alias_type,
            "alias_text": self.alias_text,
            "ktype_reference": self.ktype_reference,
            "confidence": self.confidence,
            "source_assertion_key": assertion_key,
            "assertion_identity": build_assertion_identity(
                self.source_system.lower(), assertion_key
            ),
        }


@dataclass(frozen=True)
class MatchAliasRetirement:
    """Retire one immutable alias assertion after its decision is superseded."""

    predecessor_decision_id: UUID
    successor_decision_id: UUID
    source_system: str
    alias_type: str
    alias_text: str
    reason: str

    def __post_init__(self) -> None:
        for name in ("source_system", "alias_type", "alias_text", "reason"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be empty")
        if self.predecessor_decision_id == self.successor_decision_id:
            raise ValueError("alias retirement requires a different successor decision")

    def graph_row(self) -> dict[str, object]:
        assertion_key = (
            f"match-decision:{self.predecessor_decision_id}:{self.alias_type}"
        )
        return {
            "predecessor_decision_id": str(self.predecessor_decision_id),
            "successor_decision_id": str(self.successor_decision_id),
            "source_system": self.source_system.lower(),
            "alias_type": self.alias_type,
            "alias_text": self.alias_text,
            "reason": self.reason,
            "assertion_identity": build_assertion_identity(
                self.source_system.lower(), assertion_key
            ),
        }


_PREFLIGHT_QUERY = """
UNWIND $rows AS row
MATCH (ktype:Alias {source_system: 'tecdoc', alias_type: 'k_type', alias_text: row.ktype_reference})
      -[:REFERS_TO]->(variant:VehicleVariant)
OPTIONAL MATCH (same:Alias {source_system: row.source_system,
                            alias_type: row.alias_type,
                            alias_text: row.alias_text})-[:REFERS_TO]->(other:VehicleVariant)
WHERE coalesce(same.active, true) = true
WITH row, variant, collect(DISTINCT other.id) AS existing_targets,
     collect(DISTINCT same.match_decision_id) AS existing_decisions
WHERE (size(existing_targets) = 0 OR existing_targets = [variant.id])
  AND (size(existing_decisions) = 0 OR existing_decisions = [row.decision_id])
RETURN row.decision_id AS decision_id, variant.id AS variant_id,
       variant:Provisional AS provisional
"""

_WRITE_QUERY = """
UNWIND $rows AS row
MATCH (ktype:Alias {source_system: 'tecdoc', alias_type: 'k_type', alias_text: row.ktype_reference})
      -[:REFERS_TO]->(variant:VehicleVariant)
OPTIONAL MATCH (same:Alias {source_system: row.source_system,
                            alias_type: row.alias_type,
                            alias_text: row.alias_text})-[:REFERS_TO]->(other:VehicleVariant)
WHERE coalesce(same.active, true) = true
WITH row, variant, collect(DISTINCT other.id) AS existing_targets,
     collect(DISTINCT same.match_decision_id) AS existing_decisions
WHERE (size(existing_targets) = 0 OR existing_targets = [variant.id])
  AND (size(existing_decisions) = 0 OR existing_decisions = [row.decision_id])
REMOVE variant:Provisional
SET variant.ktype_promotion_decision_id = row.decision_id,
    variant.ktype_promotion_source_version = row.source_version
MERGE (alias:Alias {assertion_identity: row.assertion_identity})
ON CREATE SET alias.id = row.alias_id,
              alias.source_system = row.source_system,
              alias.source_record_key = row.source_entity_key,
              alias.source_assertion_key = row.source_assertion_key,
              alias.alias_type = row.alias_type
SET alias.alias_text = row.alias_text,
    alias.confidence = row.confidence,
    alias.match_decision_id = row.decision_id,
    alias.source_version = row.source_version,
    alias.active = true
SET alias:Active
MERGE (alias)-[:REFERS_TO]->(variant)
RETURN count(alias) AS written
"""

_RETIRE_PREFLIGHT_QUERY = """
UNWIND $rows AS row
OPTIONAL MATCH (alias:Alias {assertion_identity: row.assertion_identity})
OPTIONAL MATCH (alias)-[:REFERS_TO]->(variant:VehicleVariant)
RETURN row.predecessor_decision_id AS predecessor_decision_id,
       alias.match_decision_id AS graph_decision_id,
       alias.retired_by_decision_id AS retired_by_decision_id,
       alias.retirement_reason AS retirement_reason,
       alias:Superseded AS superseded,
       collect(DISTINCT variant.id) AS current_targets
"""

_RETIRE_QUERY = """
UNWIND $rows AS row
MATCH (alias:Alias {assertion_identity: row.assertion_identity})
      -[current:REFERS_TO]->(variant:VehicleVariant)
WHERE alias.match_decision_id = row.predecessor_decision_id
  AND coalesce(alias.active, true) = true
SET alias.active = false,
    alias.retired_by_decision_id = row.successor_decision_id,
    alias.retirement_reason = row.reason
REMOVE alias:Active
SET alias:Superseded
MERGE (alias)-[history:PREVIOUSLY_REFERRED_TO]->(variant)
ON CREATE SET history.retired_by_decision_id = row.successor_decision_id,
              history.retirement_reason = row.reason
DELETE current
WITH collect(DISTINCT variant) AS variants, count(alias) AS retired
UNWIND variants AS variant
OPTIONAL MATCH (active_alias:Alias)-[:REFERS_TO]->(variant)
WHERE active_alias.match_decision_id IS NOT NULL
  AND coalesce(active_alias.active, true) = true
WITH retired, variant, count(active_alias) AS active_match_aliases
FOREACH (_ IN CASE WHEN active_match_aliases = 0 THEN [1] ELSE [] END |
  SET variant:Provisional
  REMOVE variant.ktype_promotion_decision_id,
         variant.ktype_promotion_source_version
)
RETURN max(retired) AS retired
"""


def _write(transaction: ManagedTransaction, rows: list[dict[str, object]]) -> int:
    record = transaction.run(_WRITE_QUERY, rows=rows).single()
    return 0 if record is None else int(record["written"])


def _retire(transaction: ManagedTransaction, rows: list[dict[str, object]]) -> int:
    record = transaction.run(_RETIRE_QUERY, rows=rows).single()
    return 0 if record is None else int(record["retired"])


def promote_and_attach_matches(
    driver: Driver,
    promotions: tuple[MatchPromotion, ...],
    *,
    mode: PromotionMode = PromotionMode.DRY_RUN,
    controlled_limit: int = 1_000,
) -> int:
    """Preflight every row, then atomically promote KTypes and attach safe aliases."""

    if controlled_limit < 1:
        raise ValueError("controlled_limit must be positive")
    if mode == PromotionMode.CONTROLLED and len(promotions) > controlled_limit:
        raise ValueError("controlled promotion cohort exceeds the configured limit")
    identities: dict[tuple[str, str, str], str] = {}
    for promotion in promotions:
        key = (
            promotion.source_system.lower(),
            promotion.alias_type,
            promotion.alias_text,
        )
        existing = identities.setdefault(key, promotion.ktype_reference)
        if existing != promotion.ktype_reference:
            raise MatchPromotionConflictError("one source alias selected multiple KTypes")
    rows = [promotion.graph_row() for promotion in promotions]
    if not rows:
        return 0
    with driver.session() as session:
        preflight = session.run(_PREFLIGHT_QUERY, rows=rows).data()
        if len(preflight) != len(rows):
            raise MatchPromotionConflictError(
                "promotion stopped: KType target is missing, ambiguous, or alias conflicts"
            )
        if mode == PromotionMode.DRY_RUN:
            return len(preflight)
        if mode not in {PromotionMode.CONTROLLED, PromotionMode.PRODUCTION}:
            raise ValueError(f"unsupported promotion mode: {mode}")
        written = session.execute_write(_write, rows)
    if written != len(rows):
        raise MatchPromotionConflictError("promotion transaction was incomplete")
    return written


def retire_match_aliases(
    driver: Driver,
    retirements: tuple[MatchAliasRetirement, ...],
    *,
    mode: PromotionMode = PromotionMode.DRY_RUN,
    controlled_limit: int = 1_000,
) -> int:
    """Retire superseded alias edges while preserving their immutable history."""

    if controlled_limit < 1:
        raise ValueError("controlled_limit must be positive")
    if mode == PromotionMode.CONTROLLED and len(retirements) > controlled_limit:
        raise ValueError("controlled retirement cohort exceeds the configured limit")
    rows = [retirement.graph_row() for retirement in retirements]
    if len({str(row["assertion_identity"]) for row in rows}) != len(rows):
        raise MatchPromotionConflictError("one alias assertion was retired more than once")
    if not rows:
        return 0
    with driver.session() as session:
        preflight = session.run(_RETIRE_PREFLIGHT_QUERY, rows=rows).data()
        if len(preflight) != len(rows):
            raise MatchPromotionConflictError("alias retirement preflight was incomplete")
        pending: list[dict[str, object]] = []
        by_predecessor = {
            str(row["predecessor_decision_id"]): row for row in rows
        }
        for result in preflight:
            row = by_predecessor[str(result["predecessor_decision_id"])]
            current_targets = result["current_targets"]
            graph_decision = result["graph_decision_id"]
            if graph_decision != row["predecessor_decision_id"]:
                raise MatchPromotionConflictError(
                    "alias retirement stopped: predecessor assertion is missing or divergent"
                )
            if len(current_targets) == 1 and not result["superseded"]:
                pending.append(row)
                continue
            if (
                len(current_targets) == 0
                and result["superseded"]
                and result["retired_by_decision_id"] == row["successor_decision_id"]
                and result["retirement_reason"] == row["reason"]
            ):
                continue
            raise MatchPromotionConflictError(
                "alias retirement stopped: current target or retirement evidence is divergent"
            )
        if mode == PromotionMode.DRY_RUN:
            return len(rows)
        if mode not in {PromotionMode.CONTROLLED, PromotionMode.PRODUCTION}:
            raise ValueError(f"unsupported promotion mode: {mode}")
        if pending:
            retired = session.execute_write(_retire, pending)
            if retired != len(pending):
                raise MatchPromotionConflictError("alias retirement transaction was incomplete")
    return len(rows)


def reconcile_match_promotions(
    driver: Driver,
    promotions: tuple[MatchPromotion, ...],
) -> tuple[dict[str, object], ...]:
    """Return only PostgreSQL decision expectations not represented exactly in Neo4j."""

    rows = [promotion.graph_row() for promotion in promotions]
    if not rows:
        return ()
    query = """
    UNWIND $rows AS row
    OPTIONAL MATCH (alias:Alias {assertion_identity: row.assertion_identity})-[:REFERS_TO]->(v)
    WITH row, collect(DISTINCT v.id) AS targets, collect(DISTINCT alias.match_decision_id) AS decisions
    WHERE size(targets) <> 1 OR size(decisions) <> 1 OR decisions[0] <> row.decision_id
    RETURN row.decision_id AS decision_id, row.alias_text AS alias_text,
           targets, decisions
    """
    with driver.session() as session:
        return tuple(session.run(query, rows=rows).data())
