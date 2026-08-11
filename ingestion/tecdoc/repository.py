"""PostgreSQL persistence for TecDoc canonical candidates."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.tecdoc.models import CanonicalCandidate
from northstar.node_ids import NodeIdPrefix, mint_node_id

_PREFIXES = {
    "manufacturer": NodeIdPrefix.MANUFACTURER,
    "model_family": NodeIdPrefix.MODEL_FAMILY,
    "platform": NodeIdPrefix.PLATFORM,
    "vehicle_variant": NodeIdPrefix.VEHICLE_VARIANT,
    "engine": NodeIdPrefix.ENGINE,
    "transmission": NodeIdPrefix.TRANSMISSION,
    "bodywork": NodeIdPrefix.BODY_TYPE,
    "alias": NodeIdPrefix.ALIAS,
}


def register_batch(
    connection: Connection,
    *,
    batch_id: str,
    source_version: str,
    format_version: str,
    license_reference: str,
    source_path: str,
    source_checksum: str,
    source_row_count: int,
) -> None:
    """Register a versioned batch; reject a reused ID with different evidence."""

    if not all(value.strip() for value in (
        batch_id, source_version, format_version, license_reference, source_path, source_checksum
    )):
        raise ValueError("TecDoc batch metadata fields must not be empty")
    if source_row_count < 0:
        raise ValueError("source_row_count must be non-negative")
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO core.tecdoc_source_batches "
            "(batch_id, source_version, format_version, license_reference, source_path, "
            "source_checksum, source_row_count, status) VALUES (%s,%s,%s,%s,%s,%s,%s,'loading') "
            "ON CONFLICT (batch_id) DO NOTHING",
            (batch_id, source_version, format_version, license_reference, source_path,
             source_checksum, source_row_count),
        )
        cursor.execute(
            "SELECT source_version, format_version, license_reference, source_path, "
            "source_checksum, source_row_count FROM core.tecdoc_source_batches WHERE batch_id=%s",
            (batch_id,),
        )
        existing = cursor.fetchone()
    expected = (source_version, format_version, license_reference, source_path,
                source_checksum, source_row_count)
    if existing is None or tuple(existing) != expected:
        raise ValueError(f"batch_id {batch_id!r} is already registered with different metadata")


def get_or_mint_node_id(
    connection: Connection,
    candidate: CanonicalCandidate,
    *,
    minter: Callable[[NodeIdPrefix], str] = mint_node_id,
) -> str:
    prefix = _PREFIXES[candidate.entity_type]
    minted = minter(prefix)
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO core.tecdoc_identity_registry (entity_type, source_key, node_id) "
            "VALUES (%s,%s,%s) ON CONFLICT (entity_type, source_key) DO NOTHING",
            (candidate.entity_type, candidate.source_key, minted),
        )
        cursor.execute(
            "SELECT node_id FROM core.tecdoc_identity_registry WHERE entity_type=%s AND source_key=%s",
            (candidate.entity_type, candidate.source_key),
        )
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("TecDoc identity registration returned no row")
    return str(row[0])


def write_candidate(
    connection: Connection,
    *,
    batch_id: str,
    candidate: CanonicalCandidate,
    node_id: str,
    source_row_refs: Sequence[str],
) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO core.tecdoc_canonical_candidates "
            "(batch_id,entity_type,source_key,node_id,attributes,source_row_refs) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING source_key",
            (batch_id, candidate.entity_type, candidate.source_key, node_id,
             Jsonb(candidate.attributes), list(source_row_refs)),
        )
        return cursor.fetchone() is not None


def complete_batch(connection: Connection, *, batch_id: str, expected_candidates: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM core.tecdoc_canonical_candidates WHERE batch_id=%s",
            (batch_id,),
        )
        row = cursor.fetchone()
        actual = 0 if row is None else int(row[0])
        if actual != expected_candidates:
            raise RuntimeError(
                f"TecDoc candidate reconciliation failed: expected={expected_candidates}, actual={actual}"
            )
        cursor.execute(
            "UPDATE core.tecdoc_source_batches SET status='completed', completed_at=now() "
            "WHERE batch_id=%s",
            (batch_id,),
        )


def count_batch_ledger_entries(connection: Connection, *, batch_id: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM core.enrichment_ledger "
            "WHERE source_batch_id=%s AND source='TecDoc'",
            (batch_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("TecDoc ledger reconciliation returned no row")
    return int(row[0])
