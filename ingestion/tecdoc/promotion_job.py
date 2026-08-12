"""Repeatable full-source TecDoc canonical promotion orchestration."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from neo4j import Driver
from psycopg import Connection

from ingestion.tecdoc.canonical_promotion import (
    CanonicalPromotion,
    prepare_canonical_promotions,
)
from ingestion.tecdoc.dat_extraction import extract_dat_hierarchy
from ingestion.tecdoc.graph_writer import promote_canonical_vehicles
from ingestion.tecdoc.migrations import run_tecdoc_migrations
from ingestion.tecdoc.reference_data import canonical_engine_fuels
from ingestion.tecdoc.repository import complete_batch, register_batch


@dataclass(frozen=True)
class FullPromotionSummary:
    source_ktypes: int
    eligible_ktypes: int
    candidates_written: int
    graph_rows_written: int
    graph_chunks: int
    skipped_by_reason: dict[str, int]


def promote_graph_in_chunks(
    driver: Driver,
    promotions: Sequence[CanonicalPromotion],
    *,
    chunk_size: int,
    writer: Callable[[Driver, tuple[CanonicalPromotion, ...]], int] = promote_canonical_vehicles,
) -> tuple[int, int]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    written = 0
    chunks = 0
    for start in range(0, len(promotions), chunk_size):
        written += writer(driver, tuple(promotions[start : start + chunk_size]))
        chunks += 1
    return written, chunks


def run_full_canonical_promotion(
    connection: Connection,
    driver: Driver,
    *,
    source_directory: Path,
    reference_directory: Path,
    batch_id: str,
    source_version: str,
    format_version: str,
    source_checksum: str,
    license_reference: str | None = None,
    chunk_size: int = 500,
) -> FullPromotionSummary:
    records = tuple(extract_dat_hierarchy(source_directory))
    run_tecdoc_migrations(connection)
    register_batch(
        connection,
        batch_id=batch_id,
        source_version=source_version,
        format_version=format_version,
        license_reference=license_reference,
        source_path=str(source_directory),
        source_checksum=source_checksum,
        source_row_count=len(records),
    )
    prepared = prepare_canonical_promotions(
        connection,
        batch_id=batch_id,
        records=records,
        engine_fuels=canonical_engine_fuels(reference_directory),
        complete_source=True,
    )
    graph_rows, graph_chunks = promote_graph_in_chunks(
        driver, prepared.promotions, chunk_size=chunk_size
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM core.tecdoc_canonical_candidates WHERE batch_id=%s",
            (batch_id,),
        )
        count_row = cursor.fetchone()
        if count_row is None:
            raise RuntimeError("TecDoc candidate reconciliation returned no row")
        candidate_count = int(count_row[0])
    complete_batch(connection, batch_id=batch_id, expected_candidates=candidate_count)
    connection.commit()
    return FullPromotionSummary(
        source_ktypes=len(records),
        eligible_ktypes=len(prepared.promotions),
        candidates_written=prepared.candidates_written,
        graph_rows_written=graph_rows,
        graph_chunks=graph_chunks,
        skipped_by_reason=prepared.skipped_by_reason,
    )
