"""Reads that feed the matching diagnostics: the catalog, and cars as match records.

Two ways in. A NorthStar vehicle (`core.vehicles`, the Vehicles tab) is handed
to the matcher with its *merged* values -- an engine code AIS supplied, a
reviewer's correction, a learned rule's fill -- laid over the normalization of
the TS record that created it, and a person's corrections of that one car laid
over both. A single TS record (`source_record_id`) is handed over as the
pipeline would see it after the dashboard's rules ran: its latest normalization
result with live resolutions laid over it. Both carry the raw registry fields
the evaluator reads as source evidence.

A check of what a correction would do lays that correction over a car the same
way, as one more layer on top (`lay_hypothetical`). It reads and writes nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from typing import Any, Literal, NamedTuple, Protocol, TypeVar

from psycopg import Connection

from api.app.features.vehicle_corrections import fields as correction_fields
from ingestion.active_rules import load_active_rules
from ingestion.match_chunk_migrations import MATCH_FIELD_RESOLUTIONS_TABLE
from ingestion.match_run_service import MatchSourceRecord
from ingestion.normalization_migrations import NORMALIZATION_RESULTS_TABLE
from ingestion.tecdoc.match_run_adapters import load_postgres_ktype_catalog
from ingestion.tecdoc.remote_match_run import SOURCE_EVIDENCE_FIELDS
from ingestion.vehicle_core_fields import (
    REGISTRY_EVIDENCE_COLUMNS,
    SOURCE_CORRECTION,
    SOURCE_RULE,
    parse_source_ref,
)
from ingestion.vehicle_core_migrations import VEHICLE_IDENTIFIERS_TABLE, VEHICLES_TABLE
from ingestion.vehicle_core_query import (
    ALIAS,
    VehicleTerm,
    compile_vehicle_filter,
    is_vehicle_id,
    resolve_search,
)
from ingestion.vehicle_fact_corrections import CorrectionHead, correction_heads
from ingestion.vehicle_facts import STAGING_TABLE
from ingestion.vehicle_facts_migrations import VEHICLE_FACTS_TABLE
from ingestion.vehicle_ktype_choice_migrations import VEHICLE_KTYPE_CHOICES_TABLE
from ingestion.vocabulary_alignment import (
    load_bodywork_alignment,
    load_drive_alignment,
    load_fuel_alignment,
)

CATALOG_TABLE = "core.tecdoc_canonical_candidates"
#: Seed of the match impact sample; the Matching tab samples its filter with it too.
SAMPLE_SEED = "northstar-match-impact-v1"


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
    bodywork_alignment: Any = None


#: The normalized keys the matcher reads that a vehicle carries under the same
#: name. `record_route` is not among them: it is the origin record's own routing.
MATCHER_FIELDS: tuple[str, ...] = (
    "manufacturer",
    "model_family",
    "production_year",
    "production_month",
    "power_kw",
    "displacement_cc",
    "engine_code",
    "drive_type",
    "bodywork_form",
    "fuel_match_tokens",
)


#: Registry text the vehicle carries under its own name. A car no TS record
#: created (a new AIS car) has only these, and the matcher recovers a model from
#: them the way it does from a TS record.
EVIDENCE_FALLBACK = REGISTRY_EVIDENCE_COLUMNS


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
    #: A person's corrections in force on this vehicle, by field: what the last
    #: layer over the values was, and the release of a stopped car.
    corrections: dict[str, CorrectionHead] = dataclass_field(default_factory=dict)
    #: Why the car's record is stopped before matching (its normalization asks
    #: for review); kept when a person released the car, empty when it was
    #: never stopped.
    stop_reasons: tuple[str, ...] = ()
    #: A person has corrected this vehicle at some time (a withdrawn correction
    #: counts). Read with the car, so a lookup asks for the corrections' details
    #: only when there are any.
    has_corrections: bool = False
    #: A person has recorded a KType choice for this vehicle (a withdrawn one
    #: counts). Read with the car, so a lookup opens a second connection for the
    #: choice only when there is one.
    has_choices: bool = False
    #: Correctable fields whose copy on the vehicle no standing correction is
    #: behind (see `without_stale_copies`). The copy was not handed to the matcher.
    copy_drift: tuple[str, ...] = ()


class Hypothetical(NamedTuple):
    """A correction nobody stored: the "what if" a check asks about one field."""

    field: str
    action: Literal["set", "ignore"]
    value: str | None = None


class _Layer(Protocol):
    """What a correction layer needs of a head: a stored one, or a hypothetical."""

    @property
    def action(self) -> str: ...

    @property
    def value(self) -> str | None: ...


_L = TypeVar("_L", bound=_Layer)


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


def without_stale_copies(
    vehicle: Mapping[str, Any],
    field_sources: Mapping[str, str],
    heads: Mapping[str, CorrectionHead],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Take a correction's copy off the vehicle's values unless that correction stands.

    `core.vehicles` carries a copy of every `set` under the source
    `correction:<id>`. A writer that saved an older state can leave such a copy
    behind after the correction was withdrawn or replaced. The table is the
    truth, so a value whose source is a correction is used only when the
    field's head is a `set` with exactly that id; any other is dropped here and
    the derivation underneath (or nothing) stands. Returns the values to lay
    over and the correctable fields a stale copy was found on -- for a field
    with several columns (the fuel), on any of them.
    """

    kept = dict(vehicle)
    drifted: list[str] = []
    for spec in correction_fields.SPECS.values():
        head = heads.get(spec.field)
        standing = str(head.correction_id) if head is not None and head.action == "set" else None
        stale = False
        for column in spec.vehicle_columns:
            encoded = field_sources.get(column)
            if not encoded:
                continue
            source = parse_source_ref(encoded)
            if source.source == SOURCE_CORRECTION and source.ref != standing:
                kept.pop(column, None)
                stale = True
        if stale:
            drifted.append(spec.field)
    return kept, tuple(drifted)


def overlay_corrections(
    normalized: dict[str, Any],
    overlaid: dict[str, str],
    heads: Mapping[str, _L],
) -> tuple[dict[str, Any], dict[str, str], dict[str, _L]]:
    """Lay a person's corrections of this car over everything else: the last layer.

    `heads` are the heads of the car's correction chains. A `set` replaces
    whatever the matcher would have read for the field; an `ignore` takes it
    away, so the matcher has no value for it, whatever the vehicle or the
    derivation said. Either way the field's source becomes `correction`, which
    makes it neither an inferred nor a rule-filled value. A withdrawn head
    changes nothing. Which keys a field is read from, and which the matcher
    would fall back to, is the field's own definition
    (`vehicle_corrections.fields`). Returns the values, their sources and the
    corrections applied; one this version does not know is left alone.
    """

    merged = dict(normalized)
    sources = dict(overlaid)
    applied: dict[str, _L] = {}
    for spec in correction_fields.SPECS.values():
        head = heads.get(spec.field)
        if head is None or head.action == "withdraw":
            continue
        handed = (
            correction_fields.matcher_values(spec.field, head.value or "")
            if head.action == "set"
            else {}
        )
        if head.action == "set" and not handed:
            continue
        for key in (*spec.matcher_keys, *spec.matcher_fallbacks):
            merged.pop(key, None)
            sources.pop(key, None)
        merged.update(handed)
        sources[spec.field] = SOURCE_CORRECTION
        applied[spec.field] = head
    return merged, sources, applied


def _inferred(overlaid: Mapping[str, str]) -> list[str]:
    """Values a learned rule filled: the matcher reads the car's own text first."""

    return sorted(name for name, source in overlaid.items() if source == SOURCE_RULE)


def _rule_filled(overlaid: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(sorted(name for name, source in overlaid.items() if source in {"review", "rule"}))


def lay_hypothetical(car: CarRecord, hypothetical: Hypothetical) -> CarRecord:
    """The car as the matcher would be handed it with one more correction on top.

    A pure layer over a car already read, applied after the corrections that
    stand: a `set` replaces what the matcher would read for the field, an
    `ignore` takes it away, exactly as a stored correction does
    (`overlay_corrections`), so the field is no longer an inferred or a
    rule-filled value either. Nothing is read or written; the corrections the
    car record lists stay the ones that are stored.
    """

    payload = dict(car.record.payload)
    current = payload.get("normalized")
    normalized, overlaid, _ = overlay_corrections(
        dict(current) if isinstance(current, dict) else {},
        car.overlaid,
        {hypothetical.field: hypothetical},
    )
    payload["normalized"] = normalized
    payload["inferred_fields"] = _inferred(overlaid)
    return replace(
        car,
        manufacturer=normalized.get("manufacturer"),
        model_family=normalized.get("model_family"),
        record=MatchSourceRecord(car.record.source_record_id, payload),
        rule_filled=_rule_filled(overlaid),
        overlaid=overlaid,
    )


def matcher_input_hash(record: MatchSourceRecord) -> str:
    """sha256 of exactly what the matcher is handed for a car.

    Two cars with one hash get one evaluation, and a car whose hash is what it
    was when something was decided about it is, to the matcher, the same car.
    The record id is left out: the matcher does not read it.
    """

    canonical = json.dumps(
        record.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


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
                bodywork_alignment=load_bodywork_alignment(connection),
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
        self, terms: Sequence[VehicleTerm], text: str, *, limit: int, seed: str = SAMPLE_SEED
    ) -> tuple[int, list[str]]:
        """How many vehicles the filter matches, and a seeded random `limit` of them.

        The lowest NOR IDs are no fair sample (the first 200 passenger cars lack
        a model five times as often as the rest), so the cars are ordered the way
        `sample_vehicle_ids` orders them: the same filter picks the same cars.
        """

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
                f"WHERE {predicate.sql} "
                f"ORDER BY md5(%s || {ALIAS}.vehicle_id), {ALIAS}.vehicle_id LIMIT %s",
                [*predicate.parameters, seed, limit],
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

        Primary-key reads plus the origin TS record's latest normalization and one
        index probe for "has a person's KType choice", so a page of two hundred
        vehicles is index lookups, never a scan. A vehicle no
        TS record created (a new AIS car) has no derivation: the matcher sees its
        merged values alone.

        People's corrections of these cars are read from their own table on
        the same connection -- the heads of their chains, by the vehicle key --
        and laid over last. A failing read fails the call: a car is never
        matched as if nobody had corrected it.
        """

        if not vehicle_ids:
            return []
        with self._connection_factory() as connection:
            return read_vehicle_car_records(connection, vehicle_ids)

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


def read_vehicle_car_records(
    connection: Connection[Any], vehicle_ids: Sequence[str]
) -> list[CarRecord]:
    """`VehicleMatchingRepository.vehicle_car_records` on a connection the caller holds.

    A writer that has locked a vehicle's row reads the car again through here,
    in its own transaction, to see what the matcher is handed at that moment.
    """

    if not vehicle_ids:
        return []
    columns = ", ".join(f"vehicle.{name}" for name in MATCHER_FIELDS)
    fallback = ", ".join(f"vehicle.{name}" for name in EVIDENCE_FALLBACK.values())
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT vehicle.vehicle_id, vehicle.plate, vehicle.vin, vehicle.ts_record_id,
                   vehicle.origin_source, vehicle.normalization_status,
                   vehicle.field_sources, {columns},
                   latest.status, latest.normalized_payload, latest.review_reasons,
                   raw.raw_record, {fallback},
                   EXISTS (SELECT 1 FROM {VEHICLE_KTYPE_CHOICES_TABLE} AS choice
                           WHERE choice.vehicle_id = vehicle.vehicle_id)
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
    heads = correction_heads(connection, [str(row[0]) for row in rows])
    by_id = {str(row[0]): _vehicle_car_record(row, heads.get(str(row[0]))) for row in rows}
    return [by_id[vehicle_id] for vehicle_id in vehicle_ids if vehicle_id in by_id]


def _vehicle_car_record(
    row: tuple[Any, ...], heads: Mapping[str, CorrectionHead] | None = None
) -> CarRecord:
    count = len(MATCHER_FIELDS)
    vehicle_id, plate, vin, ts_record_id, origin_source, core_status, sources = row[:7]
    vehicle = dict(zip(MATCHER_FIELDS, row[7 : 7 + count], strict=True))
    status, payload, review_reasons, raw = row[7 + count : 11 + count]
    registry = dict(zip(EVIDENCE_FALLBACK, row[11 + count : -1], strict=True))
    has_choices = bool(row[-1])
    payload = dict(payload or {})
    raw = dict(raw or {})
    # The table is the truth: a correction's copy on the vehicle that no
    # standing correction is behind is never handed to the matcher.
    vehicle, copy_drift = without_stale_copies(vehicle, dict(sources or {}), heads or {})
    normalized, overlaid = overlay_vehicle(
        dict(payload.get("normalized") or {}),
        vehicle,
        dict(sources or {}),
        str(origin_source),
    )
    normalized, overlaid, applied = overlay_corrections(normalized, overlaid, heads or {})
    record_status = str(status or core_status or "resolved")
    reasons = [str(reason) for reason in (review_reasons or [])]
    stopped_for = correction_fields.stop_reasons(record_status, reasons)
    release = (heads or {}).get(correction_fields.NORMALIZATION_STOP)
    if stopped_for and release is not None and release.action == "ignore":
        # A person released this car: the matcher is handed it as a resolved
        # record, every guard on. The record itself keeps its status and reasons.
        record_status, reasons = correction_fields.RELEASED_STATUS, []
        applied[correction_fields.NORMALIZATION_STOP] = release
    record_id = int(ts_record_id) if ts_record_id else surrogate_record_id(str(vehicle_id))
    record = MatchSourceRecord(
        record_id,
        {
            "normalization_status": record_status,
            "normalized": normalized,
            "candidates": dict(payload.get("candidates") or {}),
            "review_reasons": reasons,
            "source_evidence": {
                field: raw.get(field) or registry.get(field) for field in SOURCE_EVIDENCE_FIELDS
            },
            "inferred_fields": _inferred(overlaid),
        },
    )
    return CarRecord(
        source_record_id=int(ts_record_id) if ts_record_id else None,
        plate=plate,
        vin=vin,
        manufacturer=normalized.get("manufacturer"),
        model_family=normalized.get("model_family"),
        record=record,
        rule_filled=_rule_filled(overlaid),
        vehicle_id=str(vehicle_id),
        overlaid=overlaid,
        corrections=applied,
        stop_reasons=stopped_for,
        has_corrections=bool(heads),
        has_choices=has_choices,
        copy_drift=copy_drift,
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
