"""Transactional orchestration for SCRUM-95 through SCRUM-98."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from uuid import NAMESPACE_URL, uuid5

from psycopg import Connection

from ingestion.ledger import record_ledger_entry
from ingestion.tecdoc.mapping import deduplicate_candidates
from ingestion.tecdoc.models import TecDocIngestionSummary, TecDocVehicleRow
from ingestion.tecdoc.repository import (
    complete_batch,
    count_batch_ledger_entries,
    get_or_mint_node_id,
    register_batch,
    write_candidate,
)


def ingest_tecdoc_vehicle_tree(
    connection: Connection,
    *,
    rows: Iterable[TecDocVehicleRow],
    batch_id: str,
    source_version: str,
    format_version: str,
    license_reference: str | None = None,
    source_path: str,
    source_checksum: str,
) -> TecDocIngestionSummary:
    """Persist one repeatable TecDoc batch and its append-only provenance."""

    materialized = tuple(rows)
    ktypes = {row.ktype_id for row in materialized}
    if len(ktypes) != len(materialized):
        raise ValueError("TecDoc vehicle-tree extract contains duplicate KType rows")
    candidates = deduplicate_candidates(materialized)
    refs_by_key: dict[str, set[str]] = defaultdict(set)
    for row in materialized:
        row_refs = row.source_row_refs or (f"ktype:{row.ktype_id}",)
        for candidate in deduplicate_candidates((row,)):
            refs_by_key[candidate.source_key].update(row_refs)

    try:
        register_batch(
            connection,
            batch_id=batch_id,
            source_version=source_version,
            format_version=format_version,
            license_reference=license_reference,
            source_path=source_path,
            source_checksum=source_checksum,
            source_row_count=len(materialized),
        )
        written = 0
        for candidate in candidates:
            node_id = get_or_mint_node_id(connection, candidate)
            refs: tuple[str, ...] = tuple(sorted(refs_by_key[candidate.source_key]))
            if write_candidate(
                connection,
                batch_id=batch_id,
                candidate=candidate,
                node_id=node_id,
                source_row_refs=refs,
            ):
                written += 1
            event_id = uuid5(
                NAMESPACE_URL,
                f"northstar:tecdoc:{source_version}:{batch_id}:{candidate.entity_type}:{candidate.source_key}",
            )
            record_ledger_entry(
                connection,
                event_id=event_id,
                source="TecDoc",
                target_node_id=node_id,
                confidence=1.0,
                attributes_added=tuple(sorted(candidate.attributes)),
                evidence={
                    "source_version": source_version,
                    "format_version": format_version,
                    "source_key": candidate.source_key,
                    "source_row_refs": list(refs),
                },
                source_batch_id=batch_id,
            )
        complete_batch(connection, batch_id=batch_id, expected_candidates=len(candidates))
        ledger_count = count_batch_ledger_entries(connection, batch_id=batch_id)
        if ledger_count != len(candidates):
            raise RuntimeError(
                f"TecDoc ledger reconciliation failed: expected={len(candidates)}, "
                f"actual={ledger_count}"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return TecDocIngestionSummary(
        batch_id=batch_id,
        source_version=source_version,
        source_rows=len(materialized),
        unique_ktypes=len(ktypes),
        candidates_written=written,
        ledger_entries_written=ledger_count,
    )
