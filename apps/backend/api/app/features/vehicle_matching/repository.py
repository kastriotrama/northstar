"""Reads that feed the matching diagnostics: the catalog, and cars as match records.

Two ways in. A NorthStar vehicle (`core.vehicles`, the Vehicles tab) is handed
to the matcher with its *merged* values -- an engine code AIS supplied, a
reviewer's correction, a learned rule's fill -- laid over the normalization of
the TS record that created it. A single TS record (`source_record_id`) is
handed over as the pipeline would see it after the dashboard's rules ran: its
latest normalization result with live resolutions laid over it. Both carry the
raw registry fields the evaluator reads as source evidence.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Protocol

from psycopg import Connection

from ingestion.active_rules import load_active_rules
from ingestion.match_chunk_migrations import MATCH_FIELD_RESOLUTIONS_TABLE
from ingestion.match_run_service import MatchSourceRecord
from ingestion.normalization_migrations import NORMALIZATION_RESULTS_TABLE
from ingestion.tecdoc.match_run_adapters import load_postgres_ktype_catalog
from ingestion.tecdoc.remote_match_run import SOURCE_EVIDENCE_FIELDS
from ingestion.vehicle_core_fields import parse_source_ref
from ingestion.vehicle_core_migrations import VEHICLE_IDENTIFIERS_TABLE, VEHICLES_TABLE
from ingestion.vehicle_core_query import (
    ALIAS,
    VehicleTerm,
    compile_vehicle_filter,
    is_vehicle_id,
    resolve_search,
)
from ingestion.vehicle_facts import STAGING_TABLE
from ingestion.vehicle_facts_migrations import VEHICLE_FACTS_TABLE
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


#: The normalized keys the matcher reads that a vehicle carries under the same
#: name. `record_route` is not among them: it is the origin record's own routing.
MATCHER_FIELDS: tuple[str, ...] = (
    "manufacturer",
    "model_family",
    "production_year",
    "power_kw",
    "displacement_cc",
    "engine_code",
    "drive_type",
    "bodywork_form",
    "fuel_match_tokens",
)


@dataclass(frozen=True)
class CarRecord:
    """One car, ready to evaluate, with the identity the screen shows beside it."""

    source_record_id: int | None
    plate: str | None
    vin: str | None
    manufacturer: str | None
    model_family: str | None
    record: MatchSourceRecord
    rule_filled: tuple[str, ...]
    vehicle_id: str | None = None
    #: Vehicle values that replaced or filled the origin record's derivation,
    #: by field, with the source that supplied each (`ais`, `review`, `rule`, ...).
    overlaid: dict[str, str] = dataclass_field(default_factory=dict)


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


def overlay_vehicle(
    normalized: dict[str, Any],
    vehicle: dict[str, Any],
    field_sources: dict[str, str],
    origin_source: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Lay a vehicle's merged values over its origin record's normalization.

    The vehicle wins wherever it has a value: that value already beat every
    other source in the merge. Where it has none, the derivation stands -- a
    field the vehicle record does not carry must not blank what the matcher
    would otherwise have seen. Returns what changed and where it came from.
    """

    merged = dict(normalized)
    overlaid: dict[str, str] = {}
    for name in MATCHER_FIELDS:
        value = vehicle.get(name)
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, tuple):
            value = list(value)
        if merged.get(name) == value:
            continue
        merged[name] = value
        encoded = field_sources.get(name)
        overlaid[name] = parse_source_ref(encoded).source if encoded else origin_source
    return merged, overlaid


def surrogate_record_id(vehicle_id: str) -> int:
    """A stable positive id for a vehicle no TS record created (the matcher needs one)."""

    digest = hashlib.blake2b(vehicle_id.encode(), digest_size=7).digest()
    return int.from_bytes(digest, "big") + 1


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

    def vehicles_for_identifier(self, identifier: str, *, limit: int = 10) -> list[str]:
        """Vehicles that hold or held this plate or VIN, the current holder first.

        A NOR ID is accepted too and names its own vehicle.
        """

        if is_vehicle_id(identifier):
            with self._connection_factory() as connection, connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT vehicle_id FROM {VEHICLES_TABLE} WHERE vehicle_id = %s",
                    (identifier,),
                )
                return [str(row[0]) for row in cursor.fetchall()]
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT vehicle_id FROM {VEHICLE_IDENTIFIERS_TABLE} "
                "WHERE kind IN ('plate', 'vin') AND value = %s "
                "GROUP BY vehicle_id "
                "ORDER BY bool_or(valid_to IS NULL) DESC, max(coalesce(valid_to, 'infinity'::date)) "
                "DESC, vehicle_id DESC LIMIT %s",
                (identifier, limit),
            )
            return [str(row[0]) for row in cursor.fetchall()]

    def vehicle_population(
        self, terms: Sequence[VehicleTerm], text: str, *, limit: int
    ) -> tuple[int, list[str]]:
        """How many vehicles the filter matches, and the first `limit` by NOR ID."""

        with self._connection_factory() as connection, connection.cursor() as cursor:
            predicate = compile_vehicle_filter(terms, resolve_search(connection, text))
            cursor.execute(
                f"SELECT count(*) FROM {VEHICLES_TABLE} AS {ALIAS} WHERE {predicate.sql}",
                predicate.parameters,
            )
            row = cursor.fetchone()
            total = int(row[0]) if row else 0
            cursor.execute(
                f"SELECT {ALIAS}.vehicle_id FROM {VEHICLES_TABLE} AS {ALIAS} "
                f"WHERE {predicate.sql} ORDER BY {ALIAS}.vehicle_id LIMIT %s",
                [*predicate.parameters, limit],
            )
            ids = [str(item[0]) for item in cursor.fetchall()]
        return total, ids

    def sample_vehicle_ids(
        self, *, seed: str, size: int, scope: str = "passenger", registered_only: bool = True
    ) -> list[str]:
        """A seeded random sample: the same seed over the same cars picks the same cars.

        Ordering by a hash of seed and NOR ID reads the whole scope once (seconds
        at 7M rows) but, unlike `TABLESAMPLE`, is stable across runs and
        unaffected by physical row order.
        """

        status = "AND registry_status = 'registered'" if registered_only else ""
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT vehicle_id FROM {VEHICLES_TABLE} "
                f"WHERE vehicle_scope = %s {status} "
                "ORDER BY md5(%s || vehicle_id), vehicle_id LIMIT %s",
                (scope, seed, size),
            )
            return [str(row[0]) for row in cursor.fetchall()]

    def vehicle_car_records(self, vehicle_ids: Sequence[str]) -> list[CarRecord]:
        """Match records for these vehicles, in the order asked for.

        Primary-key reads plus the origin TS record's latest normalization, so a
        page of two hundred vehicles is index lookups, never a scan. A vehicle no
        TS record created (a new AIS car) has no derivation: the matcher sees its
        merged values alone.
        """

        if not vehicle_ids:
            return []
        columns = ", ".join(f"vehicle.{name}" for name in MATCHER_FIELDS)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT vehicle.vehicle_id, vehicle.plate, vehicle.vin, vehicle.ts_record_id,
                       vehicle.origin_source, vehicle.normalization_status,
                       vehicle.field_sources, {columns},
                       latest.status, latest.normalized_payload, latest.review_reasons,
                       raw.raw_record
                FROM {VEHICLES_TABLE} AS vehicle
                LEFT JOIN LATERAL (
                    SELECT status, normalized_payload, review_reasons
                    FROM {NORMALIZATION_RESULTS_TABLE}
                    WHERE source_table = %s AND source_record_id = vehicle.ts_record_id
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                ) AS latest ON true
                LEFT JOIN {STAGING_TABLE} AS raw ON raw.id = vehicle.ts_record_id
                WHERE vehicle.vehicle_id = ANY(%s)
                """,
                (STAGING_TABLE, list(vehicle_ids)),
            )
            rows = cursor.fetchall()
        by_id = {str(row[0]): _vehicle_car_record(row) for row in rows}
        return [by_id[vehicle_id] for vehicle_id in vehicle_ids if vehicle_id in by_id]

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


def _vehicle_car_record(row: tuple[Any, ...]) -> CarRecord:
    count = len(MATCHER_FIELDS)
    vehicle_id, plate, vin, ts_record_id, origin_source, core_status, sources = row[:7]
    vehicle = dict(zip(MATCHER_FIELDS, row[7 : 7 + count], strict=True))
    status, payload, review_reasons, raw = row[7 + count :]
    payload = dict(payload or {})
    raw = dict(raw or {})
    normalized, overlaid = overlay_vehicle(
        dict(payload.get("normalized") or {}),
        vehicle,
        dict(sources or {}),
        str(origin_source),
    )
    record_id = int(ts_record_id) if ts_record_id else surrogate_record_id(str(vehicle_id))
    record = MatchSourceRecord(
        record_id,
        {
            "normalization_status": str(status or core_status or "resolved"),
            "normalized": normalized,
            "candidates": dict(payload.get("candidates") or {}),
            "review_reasons": [str(reason) for reason in (review_reasons or [])],
            "source_evidence": {field: raw.get(field) for field in SOURCE_EVIDENCE_FIELDS},
        },
    )
    return CarRecord(
        source_record_id=int(ts_record_id) if ts_record_id else None,
        plate=plate,
        vin=vin,
        manufacturer=normalized.get("manufacturer"),
        model_family=normalized.get("model_family"),
        record=record,
        rule_filled=tuple(
            sorted(name for name, source in overlaid.items() if source in {"review", "rule"})
        ),
        vehicle_id=str(vehicle_id),
        overlaid=overlaid,
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
