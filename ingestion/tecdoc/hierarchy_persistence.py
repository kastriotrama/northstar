"""Multiplicity-safe persistence for extracted TecDoc hierarchy relationships."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass

from psycopg import Connection

from ingestion.tecdoc.dat_extraction import TecDocHierarchyRecord
from ingestion.tecdoc.models import CanonicalCandidate
from ingestion.tecdoc.repository import (
    count_relationship_candidates,
    get_or_mint_node_id,
    write_relationship_candidate,
)


@dataclass(frozen=True)
class RelationshipPersistenceSummary:
    ktypes_processed: int
    distinct_relationships: int
    relationships_written: int
    ambiguous_ktypes: int
    engine_missing_ktypes: int


def persist_engine_relationship_candidates(
    connection: Connection,
    *,
    batch_id: str,
    records: Iterable[TecDocHierarchyRecord],
) -> RelationshipPersistenceSummary:
    """Persist one candidate per distinct KType/engine without graph promotion."""

    materialized = tuple(records)
    expected = sum(len(record.engines) for record in materialized)
    written = 0
    try:
        for record in materialized:
            variant_key = f"variant:{record.ktype_id}"
            variant_id = get_or_mint_node_id(
                connection,
                CanonicalCandidate("vehicle_variant", variant_key, {}),
            )
            for engine in record.engines:
                engine_key = f"engine:{engine.engine_id}"
                engine_id = get_or_mint_node_id(
                    connection,
                    CanonicalCandidate("engine", engine_key, {}),
                )
                applicability = [asdict(item) for item in engine.applicability]
                if write_relationship_candidate(
                    connection,
                    batch_id=batch_id,
                    relationship_type="USES_ENGINE",
                    source_assertion_key=f"ktype:{record.ktype_id}:engine:{engine.engine_id}",
                    from_source_key=variant_key,
                    from_node_id=variant_id,
                    to_source_key=engine_key,
                    to_node_id=engine_id,
                    attributes={"power_kw": record.power_kw},
                    evidence={
                        "ktype_source_row_refs": list(record.source_row_refs),
                        "engine_source_row_ref": engine.engine_source_row_ref,
                        "engine_deleted": engine.deleted,
                        "applicability": applicability,
                    },
                ):
                    written += 1
        actual = count_relationship_candidates(connection, batch_id=batch_id)
        if actual != expected:
            raise RuntimeError(
                f"TecDoc relationship reconciliation failed: expected={expected}, actual={actual}"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return RelationshipPersistenceSummary(
        ktypes_processed=len(materialized),
        distinct_relationships=expected,
        relationships_written=written,
        ambiguous_ktypes=sum(len(record.engines) > 1 for record in materialized),
        engine_missing_ktypes=sum(not record.engines for record in materialized),
    )
