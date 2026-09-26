"""Transportstyrelsen as a source of `core.vehicles`.

The TS snapshot was, until now, the vehicle database itself: a vehicle *was* a
row of `vehicle_facts`, keyed by a staging row number. Here it becomes what it
is -- one provider. Each TS record is turned into observations and merged into
the vehicle it describes; the vehicle is found (or created) by identity, not
by row number.

Identity, in order (lookup, reconcile, then mint):

1. The TS record is already linked to a vehicle: that vehicle.
2. A full 17-character VIN another vehicle already holds: that vehicle. Only 180
   of 5.66M such VINs repeat among the TS survivors, and those are one import
   seen under a temporary and a permanent plate.
3. Otherwise a new vehicle. Short chassis numbers are *not* identity: 869k old
   cars carry them and "000003" belongs to three different Volvos.

`vehicle_facts` already holds one survivor per plate, so the backfill walks it.
The other TS copies of the same cars (722k rows from repeated batches) are linked
afterwards by plate, in one set-based statement.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from psycopg import Connection

from ingestion.match_chunk_migrations import MATCH_FIELD_RESOLUTIONS_TABLE
from ingestion.normalization_migrations import NORMALIZATION_RESULTS_TABLE
from ingestion.normalization_rules import classify_vehicle_scope
from ingestion.vehicle_core_fields import (
    FIELDS_BY_NAME,
    INTEGER_TYPES,
    REVIEWABLE_FIELDS,
    SOURCE_REVIEW,
    SOURCE_TS,
    SourceRef,
    clean_code,
    clean_engine_code,
    clean_int,
    clean_registry_date,
    clean_text,
    clean_year_month,
    is_strong_vin,
    normalize_plate,
)
from ingestion.vehicle_core_merge import (
    Observation,
    VehicleState,
    derive,
    merge,
    ts_ref,
)
from ingestion.vehicle_core_migrations import (
    VEHICLE_IDENTIFIERS_TABLE,
    VEHICLE_SOURCE_LINKS_TABLE,
)
from ingestion.vehicle_core_store import (
    IdentifierPlan,
    IdentifierRow,
    LedgerRow,
    LinkRow,
    add_links,
    current_owners,
    ledger_event_id,
    linked_vehicles,
    load_vehicles,
    mint_vehicle_id,
    record_ledger_rows,
    save_vehicles,
)
from ingestion.vehicle_facts import STAGING_TABLE
from ingestion.vehicle_facts_migrations import VEHICLE_FACTS_TABLE
from northstar.node_ids import NodeIdGenerator

DEFAULT_PAGE_SIZE = 20_000
DEFAULT_MIN_FREE_BYTES = 1024 * 1024 * 1024

# The normalized keys the vehicle reads, extracted in SQL so a page does not
# ship every record's full payload, decision trace included, to Python.
_PAYLOAD_KEYS: tuple[str, ...] = (
    "energy_sources",
    "fuel_match_tokens",
    "electrification_type",
    "transmission_type",
    "emission_standard",
    "production_date",
    "production_date_precision",
    "wheelbase_mm",
    "ev_power_kw",
    "tyre_front",
    "tyre_rear",
    "record_route",
    "parts_matching_exclusion_reason",
    "vehicle_scope",
)
_RAW_KEYS: tuple[str, ...] = (
    "vehicle_type",
    "eeg_type_approval",
    "registration_date",
    "build_month",
    "build_date",
    "tyre_front",
    "tyre_rear",
)


@dataclass(frozen=True)
class TsRecord:
    """One TS record as the vehicle reads it."""

    record_id: int
    observed_on: date | None
    facts: Mapping[str, Any]
    raw: Mapping[str, Any]
    normalized: Mapping[str, Any]
    status: str | None
    confidence: float | None
    resolutions: Mapping[str, tuple[str, str]] = field(default_factory=dict)


def _first(values: object, index: int) -> str | None:
    if isinstance(values, list | tuple) and len(values) > index:
        return clean_text(values[index])
    return None


def _production_month(normalized: Mapping[str, Any], raw: Mapping[str, Any]) -> int | None:
    precision = str(normalized.get("production_date_precision") or "")
    if precision in {"month", "day"}:
        _, month = clean_year_month(normalized.get("production_date"))
        if month:
            return month
    _, month = clean_year_month(raw.get("build_month") or raw.get("build_date"))
    return month


def _typed(name: str, value: str) -> Any:
    sql_type = FIELDS_BY_NAME[name].sql_type
    if sql_type in INTEGER_TYPES:
        return clean_int(value, zero_is_absent=False)
    return clean_text(value)


def ts_observations(
    record: TsRecord, *, ref: SourceRef | None = None
) -> dict[str, Observation | None]:
    """Everything one TS record says about its vehicle.

    `ref` defaults to the TS record itself. The AIS import passes its own: a car
    the register gained after our TS snapshot is described in the same TS terms,
    normalized by the same pipeline, but it is AIS that said it.
    """

    ref = ref or ts_ref(record.record_id, record.observed_on)
    facts, raw, normalized = record.facts, record.raw, record.normalized
    energy = normalized.get("energy_sources")
    tokens = normalized.get("fuel_match_tokens") or energy
    scope = clean_text(normalized.get("vehicle_scope")) or classify_vehicle_scope(
        {"eu_category": facts.get("eu_category"), "vehicle_type": raw.get("vehicle_type")},
        normalized,
    )
    four_wheel = clean_text(facts.get("is_4wd"))
    group = clean_code(facts.get("group_no"))
    values: dict[str, Any] = {
        "vin": clean_code(facts.get("vin")),
        "plate": normalize_plate(facts.get("plate")),
        "registry_status": "registered",
        "registry_vehicle_type": clean_code(raw.get("vehicle_type")),
        "eu_category": clean_code(facts.get("eu_category")),
        "vehicle_scope": scope,
        "manufacturer": clean_text(facts.get("n_manufacturer")),
        "model_family": clean_text(facts.get("n_model_family")),
        "registry_make_code": clean_code(facts.get("fab_code")),
        "registry_brand_text": clean_text(facts.get("brand")),
        "registry_model_text": clean_text(facts.get("model")),
        "registry_type_code": clean_code(facts.get("type_text")),
        "variant_code": clean_code(facts.get("variant")),
        "version_code": clean_code(facts.get("version")),
        "type_approval": clean_text(raw.get("eeg_type_approval")),
        "group_code": None if group in {None, "000000"} else group,
        "engine_code": clean_engine_code(facts.get("n_engine_code")),
        "power_kw": clean_int(facts.get("n_power_kw")),
        "ev_power_kw": clean_int(normalized.get("ev_power_kw")),
        "displacement_cc": clean_int(facts.get("n_displacement_cc")),
        "fuel": _first(energy, 0),
        "fuel_secondary": _first(energy, 1),
        "fuel_match_tokens": (
            [str(token) for token in tokens] if isinstance(tokens, list | tuple) and tokens else None
        ),
        "electrification_type": clean_text(normalized.get("electrification_type")),
        "transmission": clean_text(normalized.get("transmission_type")),
        "drive_type": clean_text(facts.get("n_drive_type")),
        "registry_all_wheel_drive": {"1": True, "0": False}.get(four_wheel or ""),
        "bodywork_form": clean_text(facts.get("n_bodywork_form")),
        "registry_body_code": clean_code(facts.get("body_code")),
        "emission_standard": clean_text(normalized.get("emission_standard")),
        "production_year": clean_int(facts.get("n_production_year")),
        "production_month": _production_month(normalized, raw),
        "model_year": clean_int(facts.get("model_year")),
        "registry_vehicle_year": clean_int(facts.get("vehicle_year")),
        "first_registration_date": clean_registry_date(raw.get("registration_date")),
        "colour": clean_text(facts.get("color")),
        "wheelbase_mm": clean_int(normalized.get("wheelbase_mm")),
        "tyre_front": clean_text(normalized.get("tyre_front") or raw.get("tyre_front")),
        "tyre_rear": clean_text(normalized.get("tyre_rear") or raw.get("tyre_rear")),
        "seats": clean_int(facts.get("passengers")),
        "normalization_status": clean_text(record.status),
        "normalization_confidence": record.confidence,
    }
    observations: dict[str, Observation | None] = {
        name: Observation(value, ref) for name, value in values.items()
    }
    return observations


def review_observations(record: TsRecord) -> dict[str, Observation | None]:
    """The live reviewer resolutions on the record, as the highest-ranked source."""

    observations: dict[str, Observation | None] = {}
    for name, (value, rule_id) in record.resolutions.items():
        if name not in REVIEWABLE_FIELDS:
            continue
        observations[name] = Observation(_typed(name, value), SourceRef(SOURCE_REVIEW, rule_id))
    return observations


def _page_statement() -> str:
    payload = ", ".join(f"'{key}', nr.normalized_payload -> 'normalized' -> '{key}'" for key in _PAYLOAD_KEYS)
    raw = ", ".join(f"'{key}', raw.raw_record -> '{key}'" for key in _RAW_KEYS)
    return f"""
        SELECT to_jsonb(facts) AS facts,
               jsonb_build_object({raw}) AS raw,
               jsonb_build_object({payload}) AS normalized,
               nr.status, nr.confidence, raw.ingested_at,
               res.fields, raw.source_batch_id
        FROM {VEHICLE_FACTS_TABLE} AS facts
        JOIN {STAGING_TABLE} AS raw ON raw.id = facts.source_record_id
        LEFT JOIN LATERAL (
            SELECT normalized_payload, status, confidence
            FROM {NORMALIZATION_RESULTS_TABLE}
            WHERE source_table = %s AND source_record_id = facts.source_record_id
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
        ) AS nr ON true
        LEFT JOIN LATERAL (
            SELECT jsonb_object_agg(target_field, jsonb_build_array(target_value, rule_id::text))
                   AS fields
            FROM {MATCH_FIELD_RESOLUTIONS_TABLE}
            WHERE source_record_id = facts.source_record_id AND superseded_at IS NULL
        ) AS res ON true
        WHERE facts.source_record_id > %s
        ORDER BY facts.source_record_id
        LIMIT %s
    """


def ts_batch_snapshots(connection: Connection) -> dict[str, date]:
    """When each TS batch's snapshot was taken, as far as the data can tell.

    A batch's load date says when it reached us, not when the register looked
    like that: our main TS batches were loaded in August 2026 but stop at
    registrations from early December 2023. The newest registration a batch
    contains is the earliest the snapshot can be, and it is what "newest wins"
    must compare against a later source -- the load date would let a years-old
    TS snapshot outrank the AIS export. Registrations dated after the load are
    typing errors and are ignored.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT source_batch_id,
                   max(CASE WHEN raw_record ->> 'registration_date' ~ '^[0-9]{{8}}$'
                             AND raw_record ->> 'registration_date'
                                 <= to_char(ingested_at, 'YYYYMMDD')
                            THEN raw_record ->> 'registration_date' END),
                   max(ingested_at)::date
            FROM {STAGING_TABLE}
            GROUP BY source_batch_id
            """
        )
        snapshots: dict[str, date] = {}
        for batch, newest, loaded in cursor.fetchall():
            snapshot = clean_registry_date(newest) or loaded
            if snapshot is not None:
                snapshots[str(batch)] = min(snapshot, loaded) if loaded else snapshot
        return snapshots


def fetch_ts_page(
    connection: Connection,
    after_id: int,
    limit: int,
    snapshots: Mapping[str, date] | None = None,
) -> list[TsRecord]:
    with connection.cursor() as cursor:
        cursor.execute(_page_statement(), (STAGING_TABLE, after_id, limit))
        rows = cursor.fetchall()
    records = []
    for facts, raw, normalized, status, confidence, ingested_at, resolutions, batch in rows:
        loaded = ingested_at.date() if isinstance(ingested_at, datetime) else ingested_at
        observed = (snapshots or {}).get(str(batch)) or loaded
        records.append(
            TsRecord(
                record_id=int(facts["source_record_id"]),
                observed_on=observed,
                facts=facts,
                raw={key: value for key, value in (raw or {}).items() if value is not None},
                normalized={key: value for key, value in (normalized or {}).items() if value is not None},
                status=status,
                confidence=None if confidence is None else float(confidence),
                resolutions={
                    str(name): (str(pair[0]), str(pair[1]))
                    for name, pair in dict(resolutions or {}).items()
                },
            )
        )
    return records


@dataclass
class BackfillSummary:
    records_read: int = 0
    vehicles_created: int = 0
    vehicles_updated: int = 0
    records_joined_existing: int = 0
    pages: int = 0
    highest_record_id: int = 0
    duplicates_linked: int = 0
    stopped_for_disk: bool = False


def _free_bytes(path: str) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return DEFAULT_MIN_FREE_BYTES + 1


def _today() -> date:
    return datetime.now(UTC).date()


def _newer(left: tuple[date | None, int], right: tuple[date | None, int]) -> bool:
    return ((left[0] or date.min), left[1]) > ((right[0] or date.min), right[1])


def process_ts_page(
    connection: Connection,
    records: Sequence[TsRecord],
    *,
    generator: NodeIdGenerator | None = None,
) -> tuple[int, int, int]:
    """Merge one page of TS records into their vehicles. Returns (created, updated, joined)."""

    generator = generator or NodeIdGenerator()
    keys = [str(record.record_id) for record in records]
    linked = linked_vehicles(connection, SOURCE_TS, keys)
    strong = {
        record.record_id: clean_code(record.facts.get("vin"))
        for record in records
        if is_strong_vin(record.facts.get("vin"))
    }
    vin_owner = current_owners(connection, "vin", [vin for vin in strong.values() if vin])

    vehicle_of: dict[int, str] = {}
    new_ids: set[str] = set()
    link_method: dict[int, str] = {}
    for record in records:
        existing = linked.get(str(record.record_id))
        if existing:
            vehicle_of[record.record_id] = existing
            continue
        vin = strong.get(record.record_id)
        if vin and vin in vin_owner:
            vehicle_of[record.record_id] = vin_owner[vin]
            link_method[record.record_id] = "vin"
            continue
        vehicle_id = mint_vehicle_id(generator)
        vehicle_of[record.record_id] = vehicle_id
        new_ids.add(vehicle_id)
        link_method[record.record_id] = "minted"
        if vin:
            # A later record in this page with the same VIN joins this vehicle.
            vin_owner[vin] = vehicle_id

    states = load_vehicles(connection, set(vehicle_of.values()) - new_ids)
    plan = IdentifierPlan()
    ledger: list[LedgerRow] = []
    created = updated = joined = 0

    for record in records:
        vehicle_id = vehicle_of[record.record_id]
        state = states.get(vehicle_id)
        is_new = state is None
        if state is None:
            state = VehicleState(
                vehicle_id=vehicle_id,
                origin_source=SOURCE_TS,
                origin_observed_on=record.observed_on,
                ts_record_id=record.record_id,
            )
            states[vehicle_id] = state

        observations = ts_observations(record)
        plate = observations["plate"].value if observations.get("plate") else None
        primary = state.ts_record_id is None or state.ts_record_id == record.record_id
        if not primary:
            # A second TS survivor for the same VIN: only the newer record speaks
            # for the vehicle; the older one's plate becomes history.
            current = (state.origin_observed_on, state.ts_record_id or 0)
            if _newer((record.observed_on, record.record_id), current):
                state.ts_record_id = record.record_id
                state.origin_observed_on = record.observed_on
            else:
                joined += 1
                if plate and plate != state.values.get("plate"):
                    plan.open(
                        IdentifierRow(vehicle_id, "plate", plate, SOURCE_TS, str(record.record_id),
                                      valid_to=state.origin_observed_on or _today())
                    )
                continue

        before = dict(state.values)
        result = merge(state, {**observations, **review_observations(record)})
        derive(state, record.observed_on)
        if is_new:
            created += 1
        elif result.touched:
            updated += 1
            evidence = result.evidence()
            attributes = tuple(result.attributes())
            ledger.append(
                LedgerRow(
                    event_id=ledger_event_id(
                        "ts", str(record.record_id), vehicle_id,
                        json.dumps({"a": attributes, "e": evidence}, sort_keys=True, default=str),
                    ),
                    source=SOURCE_TS,
                    target_node_id=vehicle_id,
                    attributes_added=attributes,
                    confidence=float(record.confidence or 0.5),
                    evidence=evidence,
                    source_batch_id=f"ts:{record.record_id}",
                )
            )
        identifier_changes(
            plan, vehicle_id, before, state.values, SOURCE_TS, str(record.record_id),
            record.observed_on,
        )

    save_vehicles(connection, states.values())
    plan.write(connection)
    add_links(
        connection,
        [
            LinkRow(SOURCE_TS, str(record.record_id), vehicle_of[record.record_id],
                    record.observed_on, link_method.get(record.record_id, "minted"))
            for record in records
            if str(record.record_id) not in linked
        ],
    )
    record_ledger_rows(connection, ledger)
    return created, updated, joined


def backfill_vehicle_core(
    connection: Connection,
    *,
    since_record_id: int = 0,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int | None = None,
    progress: Callable[[BackfillSummary], None] | None = None,
    min_free_bytes: int | None = DEFAULT_MIN_FREE_BYTES,
    disk_path: str = "/",
    link_duplicates: bool = True,
) -> BackfillSummary:
    """Create or refresh vehicles from every TS survivor, one committed page at a time.

    Resumable (`since_record_id`) and idempotent: a record already linked merges
    into its vehicle again rather than minting another, so re-running after a
    re-normalization is how TS changes reach the vehicles.
    """

    if page_size < 1:
        raise ValueError("page_size must be positive")
    summary = BackfillSummary(highest_record_id=since_record_id)
    generator = NodeIdGenerator()
    snapshots = ts_batch_snapshots(connection)
    while max_pages is None or summary.pages < max_pages:
        if min_free_bytes is not None and _free_bytes(disk_path) < min_free_bytes:
            summary.stopped_for_disk = True
            break
        records = fetch_ts_page(connection, summary.highest_record_id, page_size, snapshots)
        if not records:
            break
        created, updated, joined = process_ts_page(connection, records, generator=generator)
        connection.commit()
        summary.records_read += len(records)
        summary.vehicles_created += created
        summary.vehicles_updated += updated
        summary.records_joined_existing += joined
        summary.highest_record_id = records[-1].record_id
        summary.pages += 1
        if progress is not None:
            progress(summary)
    if link_duplicates and not summary.stopped_for_disk and max_pages is None:
        summary.duplicates_linked = link_ts_duplicates(connection)
        connection.commit()
    return summary


def link_ts_duplicates(connection: Connection) -> int:
    """Link every other TS copy of a vehicle -- repeated batches -- by its plate.

    `vehicle_facts` kept one survivor per plate; the copies it dropped describe the
    same cars. A copy with a full VIN must agree with the vehicle's VIN, so a plate
    that moved between cars cannot pull a copy onto the wrong one.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {VEHICLE_SOURCE_LINKS_TABLE}
                (source_system, source_record_key, vehicle_id, observed_on, link_method)
            SELECT %s, raw.id::text, plate.vehicle_id, raw.ingested_at::date, 'plate'
            FROM {STAGING_TABLE} AS raw
            JOIN {VEHICLE_IDENTIFIERS_TABLE} AS plate
              ON plate.kind = 'plate'
             AND plate.valid_to IS NULL
             AND plate.value = upper(replace(btrim(raw.raw_record ->> 'plate'), ' ', ''))
            LEFT JOIN {VEHICLE_IDENTIFIERS_TABLE} AS vin
              ON vin.vehicle_id = plate.vehicle_id AND vin.kind = 'vin' AND vin.valid_to IS NULL
            WHERE NOT EXISTS (
                SELECT 1 FROM {VEHICLE_SOURCE_LINKS_TABLE} AS link
                WHERE link.source_system = %s AND link.source_record_key = raw.id::text
            )
              AND (
                upper(btrim(raw.raw_record ->> 'vin')) !~ '^[A-HJ-NPR-Z0-9]{{17}}$'
                OR vin.value IS NULL
                OR vin.value = upper(btrim(raw.raw_record ->> 'vin'))
              )
            ON CONFLICT (source_system, source_record_key) DO NOTHING
            """,
            (SOURCE_TS, SOURCE_TS),
        )
        return cursor.rowcount


def identifier_changes(
    plan: IdentifierPlan,
    vehicle_id: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    source: str,
    source_ref: str,
    observed_on: date | None,
) -> None:
    """Identifiers follow the merged winner, never the observation that lost.

    A plate that changes is closed as of the observation date and the new one is
    opened; a VIN that changes (a corrected chassis number) is handled the same
    way. A losing observation changes no identifier at all.
    """

    on = observed_on or _today()
    for name in ("plate", "vin"):
        old, new = before.get(name), after.get(name)
        if new == old:
            continue
        if old:
            plan.close(vehicle_id, _kind(name, str(old)), str(old), on)
        if new:
            plan.open(IdentifierRow(vehicle_id, _kind(name, str(new)), str(new), source, source_ref))


def _kind(name: str, value: str) -> str:
    if name == "plate":
        return "plate"
    return "vin" if is_strong_vin(value) else "chassis"


def ts_record_ids_for_vehicles(connection: Connection, vehicle_ids: Sequence[str]) -> dict[str, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT vehicle_id, ts_record_id FROM core.vehicles "
            "WHERE vehicle_id = ANY(%s) AND ts_record_id IS NOT NULL",
            (list(vehicle_ids),),
        )
        return {str(row[0]): int(row[1]) for row in cursor.fetchall()}

