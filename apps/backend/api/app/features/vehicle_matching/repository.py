"""Reads that feed the matching diagnostics: the catalog, and cars as match records.

A car is handed to the matcher as the pipeline would see it after the
dashboard's rules ran: its latest normalization result, with live resolutions
laid over it -- the rule's value winning, as everywhere else since override
rules -- plus the raw registry fields the evaluator reads as source evidence.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from psycopg import Connection

from ingestion.active_rules import load_active_rules
from ingestion.match_chunk_migrations import MATCH_FIELD_RESOLUTIONS_TABLE
from ingestion.match_run_service import MatchSourceRecord
from ingestion.normalization_migrations import NORMALIZATION_RESULTS_TABLE
from ingestion.tecdoc.match_run_adapters import load_postgres_ktype_catalog
from ingestion.tecdoc.remote_match_run import SOURCE_EVIDENCE_FIELDS
from ingestion.vehicle_facts import STAGING_TABLE
from ingestion.vehicle_facts_migrations import VEHICLE_FACTS_TABLE
from ingestion.vehicle_facts_query import (
    CompiledPredicate,
    compile_predicate,
    compile_search_text,
)
from ingestion.vocabulary_alignment import load_drive_alignment, load_fuel_alignment

CATALOG_TABLE = "core.tecdoc_canonical_candidates"


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


@dataclass(frozen=True)
class MatcherSources:
    """Everything `TecDocDryRunEvaluator` is built from, read in one pass."""

    batch_id: str
    catalog: tuple[Any, ...]
    rule_set: Any
    manufacturer_rules: Any
    fuel_alignment: Any
    drive_alignment: Any


@dataclass(frozen=True)
class CarRecord:
    """One car, ready to evaluate, with the identity the screen shows beside it."""

    source_record_id: int
    plate: str | None
    vin: str | None
    manufacturer: str | None
    model_family: str | None
    record: MatchSourceRecord
    rule_filled: tuple[str, ...]


def overlay_resolutions(
    normalized: dict[str, Any], resolutions: dict[str, Any]
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Lay live resolutions over a normalized payload, the rule's value winning.

    The same precedence as `effective_value`: a reviewer's assertion outranks
    the derivation it corrects, so a car corrected on the dashboard is matched
    as corrected. Returns the fields a rule supplied.
    """

    merged = dict(normalized)
    applied = []
    for field, value in sorted(resolutions.items()):
        if value in (None, ""):
            continue
        merged[field] = str(value)
        applied.append(field)
    return merged, tuple(applied)


class VehicleMatchingRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def latest_catalog_batch(self) -> str | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT batch_id FROM {CATALOG_TABLE} "
                "WHERE entity_type = 'vehicle_variant' "
                "GROUP BY batch_id ORDER BY max(created_at) DESC LIMIT 1"
            )
            row = cursor.fetchone()
        return str(row[0]) if row else None

    def matcher_sources(self, batch_id: str) -> MatcherSources:
        with self._connection_factory() as connection:
            rule_set, manufacturer_rules = load_active_rules(connection)
            return MatcherSources(
                batch_id=batch_id,
                catalog=load_postgres_ktype_catalog(connection, batch_id=batch_id),
                rule_set=rule_set,
                manufacturer_rules=manufacturer_rules,
                fuel_alignment=load_fuel_alignment(connection),
                drive_alignment=load_drive_alignment(connection),
            )

    def ids_for_identifier(self, identifier: str, *, limit: int = 10) -> list[int]:
        """Cars registered under this plate or VIN, newest record first."""

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT source_record_id FROM {VEHICLE_FACTS_TABLE} "
                "WHERE plate = %s OR vin = %s "
                "ORDER BY source_record_id DESC LIMIT %s",
                (identifier, identifier, limit),
            )
            return [int(row[0]) for row in cursor.fetchall()]

    def population(
        self, conditions: Sequence[Any], text: str, *, limit: int
    ) -> tuple[int, list[int]]:
        """How many cars the filter matches, and the first `limit` of them."""

        predicate = _population_predicate(conditions, text)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT count(*) FROM {VEHICLE_FACTS_TABLE} WHERE {predicate.sql}",
                predicate.parameters,
            )
            row = cursor.fetchone()
            total = int(row[0]) if row else 0
            cursor.execute(
                f"SELECT source_record_id FROM {VEHICLE_FACTS_TABLE} "
                f"WHERE {predicate.sql} ORDER BY source_record_id LIMIT %s",
                [*predicate.parameters, limit],
            )
            ids = [int(item[0]) for item in cursor.fetchall()]
        return total, ids

    def car_records(self, source_record_ids: Sequence[int]) -> list[CarRecord]:
        """Match records for these cars, in the order asked for.

        Reads by primary key and `(source_table, source_record_id)`, so a page of
        a thousand cars is a thousand index lookups, never a scan.
        """

        if not source_record_ids:
            return []
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT facts.source_record_id, facts.plate, facts.vin,
                       latest.status, latest.normalized_payload, latest.review_reasons,
                       raw.raw_record, resolved.fields
                FROM {VEHICLE_FACTS_TABLE} AS facts
                JOIN {STAGING_TABLE} AS raw ON raw.id = facts.source_record_id
                JOIN LATERAL (
                    SELECT status, normalized_payload, review_reasons
                    FROM {NORMALIZATION_RESULTS_TABLE}
                    WHERE source_table = %s AND source_record_id = facts.source_record_id
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                ) AS latest ON true
                LEFT JOIN LATERAL (
                    SELECT jsonb_object_agg(target_field, target_value) AS fields
                    FROM {MATCH_FIELD_RESOLUTIONS_TABLE}
                    WHERE source_record_id = facts.source_record_id
                      AND superseded_at IS NULL
                ) AS resolved ON true
                WHERE facts.source_record_id = ANY(%s)
                """,
                (STAGING_TABLE, list(source_record_ids)),
            )
            rows = cursor.fetchall()
        by_id = {int(row[0]): _car_record(row) for row in rows}
        return [by_id[rid] for rid in source_record_ids if rid in by_id]


def _population_predicate(conditions: Sequence[Any], text: str) -> CompiledPredicate:
    terms = [
        (condition.layer, condition.field, condition.operator, condition.terms)
        for condition in conditions
    ]
    base = compile_predicate(terms) if terms else CompiledPredicate("true", [])
    search = compile_search_text(text)
    if search is None:
        return base
    return CompiledPredicate(
        f"({base.sql}) AND ({search.sql})", [*base.parameters, *search.parameters]
    )


def _car_record(row: tuple[Any, ...]) -> CarRecord:
    source_record_id, plate, vin, status, payload, review_reasons, raw, resolutions = row
    payload = dict(payload or {})
    raw = dict(raw or {})
    normalized, rule_filled = overlay_resolutions(
        dict(payload.get("normalized") or {}), dict(resolutions or {})
    )
    record = MatchSourceRecord(
        int(source_record_id),
        {
            "normalization_status": str(status),
            "normalized": normalized,
            "candidates": dict(payload.get("candidates") or {}),
            "review_reasons": [str(reason) for reason in (review_reasons or [])],
            "source_evidence": {field: raw.get(field) for field in SOURCE_EVIDENCE_FIELDS},
        },
    )
    return CarRecord(
        source_record_id=int(source_record_id),
        plate=plate,
        vin=vin,
        manufacturer=normalized.get("manufacturer"),
        model_family=normalized.get("model_family"),
        record=record,
        rule_filled=rule_filled,
    )
