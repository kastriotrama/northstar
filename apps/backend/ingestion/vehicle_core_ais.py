"""The AIS VIN export as a source of `core.vehicles`.

The export is a Stibo STEP XML file (16 GB, 10.8M VINs): Transportstyrelsen's
register as AIS holds it, plus AIS's own engine code. It is read straight from
the file and merged into the vehicles -- it is not stored anywhere else. The file
itself stays archived on the server; re-reading it is how an AIS mapping fix is
replayed.

What the import does, per record:

- **Find the vehicle** (lookup, reconcile, then mint): a full VIN the vehicle holds;
  for an old car's short chassis number, the chassis *and* its plate together.
- **Merge** what AIS says by the field policy: registry facts are the newest word
  (the export is years newer than our TS snapshot), engine code and weights fill
  what TS never had, TS keeps the technical detail it describes better.
- **Registry changes**: a deregistered vehicle is marked, never deleted; a plate that
  moved to another car is closed on the car it left; a vehicle type that changed
  (A-traktor conversions) makes the old EU category stale.
- **Codes that changed** (fuel, gearbox, body, vehicle type) are normalized by the
  same pipeline TS records go through, on the vehicle's TS record with the AIS
  codes laid over it.
- **New cars** -- active passenger cars our TS snapshot never had -- are completed
  with the TS completion rules (EU category, displacement, variant, ... by make and
  group code), normalized like a TS record, and minted.

One import per extract: the run is claimed in `ingest_job_runs` under the extract's
id (export time + file checksum), so running the same file again does nothing, and
a failed run is retried from the start -- every step is idempotent.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from itertools import islice
from pathlib import Path
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.active_rules import load_active_rules
from ingestion.job_bookkeeping import (
    claim_job_run,
    complete_job_run,
    fail_job_run,
)
from ingestion.normalization_rules import (
    ManufacturerEntityRules,
    classify_vehicle_scope,
    normalize_ts_record,
)
from ingestion.translation_dictionaries import TranslationRuleSet
from ingestion.vehicle_core_fields import (
    SOURCE_AIS,
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
)
from ingestion.vehicle_core_rules import (
    CompletionRules,
    load_completion_rules,
    rule_ref,
)
from ingestion.vehicle_core_store import (
    IdentifierPlan,
    LedgerRow,
    LinkRow,
    add_links,
    current_owners,
    holders,
    ledger_event_id,
    load_vehicles,
    mint_vehicle_id,
    record_ledger_rows,
    save_vehicles,
)
from ingestion.vehicle_core_ts import TsRecord, identifier_changes, ts_observations
from ingestion.vehicle_facts import STAGING_TABLE
from ingestion.vehicle_facts_migrations import VEHICLE_FACTS_TABLE
from northstar.node_ids import NodeIdGenerator

JOB_NAME = "import-ais-vin-export"
DEFAULT_CHUNK_SIZE = 20_000
DEFAULT_MIN_FREE_BYTES = 1024 * 1024 * 1024

# STEP attribute ids, and the field each one fills.
_ATTRIBUTES: dict[str, str] = {
    "ATTR_License plate number": "plate_attribute",
    "ATTR_Name of the car": "car_name",
    "ATTR_Group code": "group_code",
    "ATTR_AIS Engine code": "engine_code",
    "ATTR_Model year": "model_year",
    "ATTR_Output kW": "kw",
    "ATTR_Power unit": "power_unit",
    "ATTR_Gearbox": "gearbox",
    "ATTR_Fuel": "fuel",
    "ATTR_Fuel_2": "fuel2",
    "ATTR_Type of vehicle": "vehicle_type",
    "ATTR_Chassie/Body": "body_code",
    "ATTR_Registration date": "registration_date",
    "ATTR_Month of manufacturing": "build_month",
    "ATTR_Weight": "kerb_weight",
    "ATTR_Max weight": "max_weight",
    "ATTR_Car length": "length",
    "ATTR_Car colour": "colour",
    "ATTR_Tyre dimension": "tyre",
    "Import_Deleted": "import_deleted",
    "ATTR_AIS_RegNumCountry": "regnum_country",
}


@dataclass(frozen=True)
class AisRecord:
    """One VIN entity of the export."""

    vin: str
    plate: str | None
    fields: Mapping[str, str]

    def get(self, name: str) -> str | None:
        return self.fields.get(name)

    @property
    def deleted(self) -> bool:
        return (self.fields.get("import_deleted") or "").lower() == "true"

    @property
    def vehicle_type(self) -> str | None:
        return clean_code(self.fields.get("vehicle_type"))

    @property
    def make_code(self) -> str | None:
        group = clean_code(self.fields.get("group_code"))
        return group[:2] if group and len(group) >= 8 else None

    @property
    def group_number(self) -> str | None:
        group = clean_code(self.fields.get("group_code"))
        number = group[2:] if group and len(group) >= 8 else None
        return None if number in {None, "000000"} else number


@dataclass(frozen=True)
class AisExtract:
    path: Path
    exported_at: datetime
    checksum: str

    @property
    def exported_on(self) -> date:
        return self.exported_at.date()

    @property
    def extract_id(self) -> str:
        return f"ais-{self.exported_at:%Y%m%dT%H%M%S}-{self.checksum[:12]}"

    @property
    def ref(self) -> SourceRef:
        # Short on purpose: it is written on every field AIS fills. The export
        # date identifies the extract; the full id is in the job run and ledger.
        return SourceRef(SOURCE_AIS, None, self.exported_on)


def read_export_time(path: Path) -> datetime:
    """The `ExportTime` of the export's root element."""

    for event, element in ET.iterparse(path, events=("start",)):
        if event == "start" and element.tag == "STEP-ProductInformation":
            stamp = element.get("ExportTime")
            if not stamp:
                raise ValueError("the export has no ExportTime")
            return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    raise ValueError("not a STEP export: no STEP-ProductInformation element")


def file_checksum(path: Path, *, chunk: int = 16 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def open_extract(path: Path) -> AisExtract:
    return AisExtract(path=path, exported_at=read_export_time(path), checksum=file_checksum(path))


def iter_ais_records(path: Path) -> Iterator[AisRecord]:
    """Stream the VIN entities; memory stays flat however large the file is.

    ElementTree keeps every parsed element attached to its parent, so the parent
    `Entities` element is cleared after each entity -- clearing only the entity
    lets the parent grow by one child per record until it holds the whole file.
    """

    depth = 0
    container: ET.Element | None = None
    for event, element in ET.iterparse(path, events=("start", "end")):
        if element.tag == "Entities" and event == "start" and container is None:
            container = element
            continue
        if element.tag != "Entity":
            continue
        if event == "start":
            depth += 1
            continue
        depth -= 1
        if depth:
            continue
        if element.get("UserTypeID") == "Vehicle Identification Number":
            values: dict[str, str] = {}
            for value in element.iter("Value"):
                name = _ATTRIBUTES.get(value.get("AttributeID") or "")
                if name and value.text and value.text.strip():
                    values[name] = value.text.strip()
            vin = clean_code(element.get("ID"))
            if vin:
                plate = normalize_plate(element.findtext("Name")) or normalize_plate(
                    values.get("plate_attribute")
                )
                yield AisRecord(vin=vin, plate=plate, fields=values)
        if container is not None:
            container.clear()


# --- observations ----------------------------------------------------------------------


def ais_observations(
    record: AisRecord, state: VehicleState, extract: AisExtract
) -> dict[str, Observation | None]:
    """What an AIS record says about a vehicle we already have."""

    ref = extract.ref
    observations: dict[str, Observation | None] = {}
    status = "deregistered" if record.deleted else "registered"
    if status != state.values.get("registry_status"):
        observations["registry_status"] = Observation(status, ref)
        observations["registry_status_observed_on"] = Observation(extract.exported_on, ref)
    if not record.deleted and record.plate:
        observations["plate"] = Observation(record.plate, ref)
    vehicle_type = record.vehicle_type
    if vehicle_type:
        observations["registry_vehicle_type"] = Observation(vehicle_type, ref)
        previous = state.values.get("registry_vehicle_type")
        if previous and previous != vehicle_type:
            # The register now calls it something else: the EU category it had as a
            # passenger car is stale, and its vehicle type follows the new code.
            observations["eu_category"] = Observation(None, ref, clears=True)
            observations["vehicle_scope"] = Observation(
                classify_vehicle_scope({"vehicle_type": vehicle_type}, {}), ref
            )
    observations["colour"] = Observation(clean_text(record.get("colour")), ref)
    observations["first_registration_date"] = Observation(
        clean_registry_date(record.get("registration_date")), ref
    )
    year, month = clean_year_month(record.get("build_month"))
    observations["production_month"] = Observation(month, ref)
    if state.values.get("production_year") is None:
        observations["production_year"] = Observation(year, ref)
    observations["model_year"] = Observation(clean_int(record.get("model_year")), ref)
    observations["engine_code"] = Observation(clean_engine_code(record.get("engine_code")), ref)
    observations["power_kw"] = Observation(clean_int(record.get("kw")), ref)
    observations["kerb_weight_kg"] = Observation(clean_int(record.get("kerb_weight")), ref)
    observations["max_weight_kg"] = Observation(clean_int(record.get("max_weight")), ref)
    observations["length_mm"] = Observation(clean_int(record.get("length")), ref)
    observations["registry_body_code"] = Observation(clean_code(record.get("body_code")), ref)
    return observations


# Registry codes whose change needs the normalizer, with the TS key each overlays.
_RENORMALIZED_CODES: tuple[tuple[str, str], ...] = (
    ("fuel", "fuel1"),
    ("fuel2", "fuel2"),
    ("gearbox", "gearbox"),
    ("body_code", "body_code"),
    ("vehicle_type", "vehicle_type"),
)
_CANONICAL_FROM_CODES: tuple[str, ...] = (
    "fuel",
    "fuel_secondary",
    "fuel_match_tokens",
    "electrification_type",
    "transmission",
    "bodywork_form",
    "vehicle_scope",
)


def _code(value: object) -> str | None:
    code = clean_code(value)
    return None if code in {None, "0"} else code


def changed_codes(record: AisRecord, ts_codes: Mapping[str, Any]) -> dict[str, str]:
    """The registry codes AIS states differently from the vehicle's TS record."""

    changes: dict[str, str] = {}
    for ais_name, ts_key in _RENORMALIZED_CODES:
        new = _code(record.get(ais_name))
        if new and new != _code(ts_codes.get(ts_key)):
            changes[ts_key] = new
    return changes


class Normalizer:
    """The TS pipeline with the active reviewed rules, loaded once per import."""

    def __init__(self, rule_set: TranslationRuleSet, manufacturer_rules: ManufacturerEntityRules):
        self._rule_set = rule_set
        self._manufacturer_rules = manufacturer_rules

    def normalize(self, raw: Mapping[str, Any]) -> tuple[dict[str, Any], str, float]:
        outcome = normalize_ts_record(
            dict(raw), rule_set=self._rule_set, manufacturer_entity_rules=self._manufacturer_rules
        )
        return dict(outcome.normalized), str(outcome.status), float(outcome.confidence)


def recanonicalized(
    normalizer: Normalizer,
    ts_raw: Mapping[str, Any],
    codes: Mapping[str, str],
    ref: SourceRef,
) -> dict[str, Observation | None]:
    """Canonical values after laying the AIS codes over the vehicle's TS record."""

    overlay = {**ts_raw, **codes}
    if "vehicle_type" in codes and codes["vehicle_type"] != clean_code(ts_raw.get("vehicle_type")):
        overlay.pop("eu_category", None)
    normalized, _, _ = normalizer.normalize(overlay)
    energy = normalized.get("energy_sources")
    tokens = normalized.get("fuel_match_tokens") or energy
    values = {
        "fuel": clean_text(energy[0]) if isinstance(energy, list) and energy else None,
        "fuel_secondary": clean_text(energy[1]) if isinstance(energy, list) and len(energy) > 1 else None,
        "fuel_match_tokens": [str(t) for t in tokens] if isinstance(tokens, list) and tokens else None,
        "electrification_type": clean_text(normalized.get("electrification_type")),
        "transmission": clean_text(normalized.get("transmission_type")),
        "bodywork_form": clean_text(normalized.get("bodywork_form")),
        "vehicle_scope": clean_text(normalized.get("vehicle_scope")),
    }
    return {name: Observation(values[name], ref) for name in _CANONICAL_FROM_CODES}


# --- new vehicles ------------------------------------------------------------------------

# Completion rule families, the TS key each fills, and the vehicle fields that then
# owe their value to that rule rather than to AIS.
_COMPLETIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("TSC-EU", "eu_category", ("eu_category", "vehicle_scope")),
    ("TSC-BRAND", "brand", ("registry_brand_text", "manufacturer")),
    ("TSC-MODEL", "model", ("registry_model_text", "model_family")),
    ("TSC-TYPE", "type_text", ("registry_type_code",)),
    ("TSC-VAR", "variant", ("variant_code",)),
    ("TSC-CCM", "ccm", ("displacement_cc",)),
    ("TSC-4WD", "is_4wd", ("registry_all_wheel_drive", "drive_type")),
)


def ts_shaped_record(
    record: AisRecord, completion: CompletionRules
) -> tuple[dict[str, Any], dict[str, str]]:
    """The AIS record in TS terms, completed by rules. Returns (record, field -> rule id)."""

    raw: dict[str, Any] = {
        "vin": record.vin,
        "plate": record.plate,
        "brand": record.get("car_name"),
        "fab_code": record.make_code,
        "group_no": record.group_number,
        "fuel1": record.get("fuel"),
        "fuel2": record.get("fuel2"),
        "gearbox": record.get("gearbox"),
        "body_code": record.get("body_code"),
        "kw": record.get("kw"),
        "registration_date": record.get("registration_date"),
        "build_month": record.get("build_month"),
        "vehicle_type": record.vehicle_type,
        "color": record.get("colour"),
        "model_year": record.get("model_year"),
        "tyre_front": record.get("tyre"),
        "tyre_rear": record.get("tyre"),
    }
    by_rule: dict[str, str] = {}
    key = (record.make_code or "", record.group_number or "")
    if record.make_code and record.group_number:
        found = {family: completion.lookup(family, key) for family, _, _ in _COMPLETIONS}
        for family, ts_key, fields in _COMPLETIONS:
            hit = found[family]
            if hit is None:
                continue
            if family == "TSC-MODEL" and found["TSC-BRAND"] is None:
                # A model text only means something beside the brand text it was
                # learned with; without that, the AIS name already carries both.
                continue
            value, rule_id = hit
            if ts_key == "is_4wd":
                value = "1" if value.lower() in {"true", "t", "1"} else "0"
            raw[ts_key] = value
            for name in fields:
                by_rule[name] = rule_id
    return {name: value for name, value in raw.items() if value not in (None, "")}, by_rule


def new_vehicle_observations(
    record: AisRecord,
    completion: CompletionRules,
    normalizer: Normalizer,
    extract: AisExtract,
) -> dict[str, Observation | None]:
    raw, by_rule = ts_shaped_record(record, completion)
    normalized, status, confidence = normalizer.normalize(raw)
    facts = {
        **raw,
        "type_text": raw.get("type_text"),
        "passengers": None,
        "n_manufacturer": normalized.get("manufacturer"),
        "n_model_family": normalized.get("model_family"),
        "n_drive_type": normalized.get("drive_type"),
        "n_bodywork_form": normalized.get("bodywork_form"),
        "n_engine_code": None,
        "n_power_kw": normalized.get("power_kw"),
        "n_displacement_cc": normalized.get("displacement_cc"),
        "n_production_year": normalized.get("production_year"),
        "vehicle_year": None,
        "model_year": clean_int(record.get("model_year")),
    }
    ts_like = TsRecord(
        record_id=0,
        observed_on=extract.exported_on,
        facts=facts,
        raw=raw,
        normalized=normalized,
        status=status,
        confidence=confidence,
    )
    observations = ts_observations(ts_like, ref=extract.ref)
    observations.update(
        {
            "engine_code": Observation(clean_engine_code(record.get("engine_code")), extract.ref),
            "kerb_weight_kg": Observation(clean_int(record.get("kerb_weight")), extract.ref),
            "max_weight_kg": Observation(clean_int(record.get("max_weight")), extract.ref),
            "length_mm": Observation(clean_int(record.get("length")), extract.ref),
            "registry_status": Observation("registered", extract.ref),
        }
    )
    for name, rule_id in by_rule.items():
        existing = observations.get(name)
        if existing is not None and existing.value is not None:
            observations[name] = replace(existing, ref=rule_ref(rule_id))
    return observations


# --- the import -------------------------------------------------------------------------


@dataclass
class AisImportSummary:
    extract_id: str = ""
    skipped_already_imported: bool = False
    records_read: int = 0
    matched: int = 0
    matched_by_chassis_and_plate: int = 0
    vehicles_updated: int = 0
    vehicles_created: int = 0
    vin_corrections: int = 0
    deregistered: int = 0
    plates_moved: int = 0
    vehicle_type_changes: int = 0
    renormalized: int = 0
    not_in_scope: int = 0
    plate_conflicts: int = 0
    stopped_for_disk: bool = False
    fields_filled: dict[str, int] = field(default_factory=dict)
    fields_changed: dict[str, int] = field(default_factory=dict)

    def count(self, bucket: dict[str, int], names: Iterable[str]) -> None:
        for name in names:
            bucket[name] = bucket.get(name, 0) + 1


def _chunks(records: Iterable[AisRecord], size: int) -> Iterator[list[AisRecord]]:
    iterator = iter(records)
    while chunk := list(islice(iterator, size)):
        yield chunk


def _ts_codes(connection: Connection, record_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    """The TS registry codes AIS is compared with, from the TS projection."""

    if not record_ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT source_record_id, fuel1, gearbox, body_code FROM {VEHICLE_FACTS_TABLE} "
            "WHERE source_record_id = ANY(%s)",
            (list(record_ids),),
        )
        return {
            int(row[0]): {"fuel1": row[1], "gearbox": row[2], "body_code": row[3]}
            for row in cursor.fetchall()
        }


def _ts_raw(connection: Connection, record_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    if not record_ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT id, raw_record FROM {STAGING_TABLE} WHERE id = ANY(%s)",
            (list(record_ids),),
        )
        return {int(row[0]): dict(row[1]) for row in cursor.fetchall()}


class AisImporter:
    """One run over one extract. Build it per run; it holds per-run state."""

    def __init__(
        self,
        connection: Connection,
        extract: AisExtract,
        *,
        normalizer: Normalizer,
        completion: CompletionRules,
        generator: NodeIdGenerator | None = None,
    ) -> None:
        self._connection = connection
        self._extract = extract
        self._normalizer = normalizer
        self._completion = completion
        self._generator = generator or NodeIdGenerator()
        self.summary = AisImportSummary(extract_id=extract.extract_id)

    # -- pass 1: records that find their vehicle --------------------------------------

    def prepare(self) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "CREATE TEMP TABLE IF NOT EXISTS ais_unmatched (vin TEXT PRIMARY KEY, record JSONB)"
            )
            cursor.execute("CREATE TEMP TABLE IF NOT EXISTS ais_seen (vehicle_id TEXT PRIMARY KEY)")
            cursor.execute("TRUNCATE ais_unmatched, ais_seen")

    def process_chunk(self, records: Sequence[AisRecord]) -> None:
        connection = self._connection
        self.summary.records_read += len(records)
        strong = [record.vin for record in records if is_strong_vin(record.vin)]
        weak = [record.vin for record in records if not is_strong_vin(record.vin)]
        vin_owner = current_owners(connection, "vin", strong)
        chassis_holders = holders(connection, "chassis", weak)
        candidates = {vid for ids in chassis_holders.values() for vid in ids}
        states = load_vehicles(connection, set(vin_owner.values()) | candidates)

        matched: list[tuple[AisRecord, VehicleState]] = []
        unmatched: list[AisRecord] = []
        for record in records:
            vehicle_id = vin_owner.get(record.vin)
            if vehicle_id is None:
                # An old car's chassis number is shared; the plate says which car.
                owners = [
                    vid for vid in chassis_holders.get(record.vin, [])
                    if record.plate and states[vid].values.get("plate") == record.plate
                ]
                if len(owners) == 1:
                    vehicle_id = owners[0]
                    self.summary.matched_by_chassis_and_plate += 1
            if vehicle_id is None:
                unmatched.append(record)
            else:
                matched.append((record, states[vehicle_id]))
        self.summary.matched += len(matched)
        self._merge_matched(matched, states)
        self._park_unmatched(unmatched)

    def _merge_matched(
        self, matched: Sequence[tuple[AisRecord, VehicleState]], states: dict[str, VehicleState]
    ) -> None:
        connection, extract = self._connection, self._extract
        plan = IdentifierPlan()
        ledger: list[LedgerRow] = []
        touched: dict[str, VehicleState] = {}

        # Plates this chunk assigns, and who holds them now: a plate that moved is
        # taken from the car it left before it is given to the one it went to.
        claims: dict[str, set[str]] = {}
        for record, state in matched:
            if record.plate and not record.deleted:
                claims.setdefault(record.plate, set()).add(state.vehicle_id)
        contested = {plate for plate, claimants in claims.items() if len(claimants) > 1}
        self.summary.plate_conflicts += len(contested)
        assignments = {
            record.plate: state.vehicle_id
            for record, state in matched
            if record.plate and not record.deleted and record.plate not in contested
            and record.plate != state.values.get("plate")
        }
        holders_now = current_owners(connection, "plate", assignments)
        losers = {
            holder for plate, holder in holders_now.items() if holder != assignments[plate]
        } - set(states)
        states.update(load_vehicles(connection, losers))
        blocked: set[str] = set()
        for plate, holder in holders_now.items():
            if holder == assignments[plate]:
                continue
            loser = states[holder]
            before = dict(loser.values)
            result = merge(loser, {"plate": Observation(None, extract.ref, clears=True)})
            if loser.values.get("plate") == plate:
                # The holder's claim is the newer one: the plate stays where it is,
                # and the record that claimed it goes without.
                blocked.add(plate)
                self.summary.plate_conflicts += 1
                continue
            identifier_changes(plan, loser.vehicle_id, before, loser.values, SOURCE_AIS,
                               extract.extract_id, extract.exported_on)
            touched[loser.vehicle_id] = loser
            self.summary.plates_moved += 1
            ledger.append(self._ledger(loser.vehicle_id, result.attributes(), result.evidence(),
                                       kind="plate-moved-away"))

        ts_ids = [s.ts_record_id for _, s in matched if s.ts_record_id]
        codes = _ts_codes(connection, ts_ids)
        needs_pipeline = {}
        for record, state in matched:
            if state.ts_record_id is None:
                continue
            ts_codes = dict(codes.get(state.ts_record_id, {}))
            ts_codes["vehicle_type"] = state.values.get("registry_vehicle_type")
            changes = changed_codes(record, ts_codes)
            if changes:
                needs_pipeline[state.vehicle_id] = changes
        raw = _ts_raw(connection, [
            states[vid].ts_record_id for vid in needs_pipeline if states[vid].ts_record_id
        ])

        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO ais_seen (vehicle_id) VALUES (%s) ON CONFLICT DO NOTHING",
                [(state.vehicle_id,) for _, state in matched],
            )

        for record, state in matched:
            before = dict(state.values)
            observations = ais_observations(record, state, extract)
            if record.plate in contested or record.plate in blocked:
                # Two records of this extract claim the plate, or its holder's claim is
                # newer: this record does not get it.
                observations.pop("plate", None)
            changes = needs_pipeline.get(state.vehicle_id)
            if changes and state.ts_record_id in raw:
                observations.update(
                    recanonicalized(self._normalizer, raw[state.ts_record_id], changes, extract.ref)
                )
                self.summary.renormalized += 1
            result = merge(state, observations)
            derived = derive(state, extract.exported_on)
            if not (result.touched or derived.touched):
                continue
            touched[state.vehicle_id] = state
            identifier_changes(plan, state.vehicle_id, before, state.values, SOURCE_AIS,
                               extract.extract_id, extract.exported_on)
            self.summary.vehicles_updated += 1
            self.summary.count(self.summary.fields_filled, (c.field for c in result.filled))
            self.summary.count(self.summary.fields_changed,
                               (c.field for c in (*result.changed, *result.cleared)))
            if before.get("registry_status") != state.values.get("registry_status") and \
                    state.values.get("registry_status") == "deregistered":
                self.summary.deregistered += 1
            if before.get("registry_vehicle_type") and before.get("registry_vehicle_type") != \
                    state.values.get("registry_vehicle_type"):
                self.summary.vehicle_type_changes += 1
            if result.changed or result.cleared:
                # Values replaced or withdrawn are recorded in full. A pure fill is
                # traced by the field's own source reference instead: 6M engine codes
                # would otherwise be 6M ledger rows that replace nothing.
                ledger.append(self._ledger(state.vehicle_id, result.attributes(), result.evidence()))

        save_vehicles(connection, touched.values())
        plan.write(connection)
        record_ledger_rows(connection, ledger)

    def _ledger(self, vehicle_id: str, attributes: Sequence[str], evidence: dict[str, Any],
                *, kind: str = "update") -> LedgerRow:
        content = json.dumps({"a": list(attributes), "e": evidence}, sort_keys=True, default=str)
        return LedgerRow(
            event_id=ledger_event_id("ais", self._extract.extract_id, kind, vehicle_id, content),
            source=SOURCE_AIS,
            target_node_id=vehicle_id,
            attributes_added=tuple(attributes),
            confidence=0.95,
            evidence=evidence,
            source_batch_id=self._extract.extract_id,
        )

    def _park_unmatched(self, records: Sequence[AisRecord]) -> None:
        """Active passenger cars without a vehicle wait for pass 2; the rest are out of scope."""

        rows = []
        for record in records:
            if record.deleted or record.vehicle_type != "PB":
                self.summary.not_in_scope += 1
                continue
            rows.append((record.vin, Jsonb({"plate": record.plate, "fields": dict(record.fields)})))
        if rows:
            with self._connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO ais_unmatched (vin, record) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    rows,
                )

    # -- pass 2: reconcile, then mint ------------------------------------------------

    def reconcile_unmatched(self, chunk_size: int) -> None:
        connection = self._connection
        after = ""
        while True:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT vin, record FROM ais_unmatched WHERE vin > %s ORDER BY vin LIMIT %s",
                    (after, chunk_size),
                )
                rows = cursor.fetchall()
            if not rows:
                break
            after = str(rows[-1][0])
            records = [
                AisRecord(vin=str(vin), plate=data.get("plate"), fields=dict(data.get("fields") or {}))
                for vin, data in rows
            ]
            self._reconcile_chunk(records)
            connection.commit()

    def _reconcile_chunk(self, records: Sequence[AisRecord]) -> None:
        connection, extract = self._connection, self._extract
        plate_holder = current_owners(connection, "plate", [r.plate for r in records if r.plate])
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT vehicle_id FROM ais_seen WHERE vehicle_id = ANY(%s)",
                (list(set(plate_holder.values())),),
            )
            seen = {str(row[0]) for row in cursor.fetchall()}
        states = load_vehicles(connection, plate_holder.values())
        plan = IdentifierPlan()
        ledger: list[LedgerRow] = []
        links: list[LinkRow] = []
        touched: dict[str, VehicleState] = {}

        claimed: dict[str, int] = {}
        for record in records:
            if record.plate:
                claimed[record.plate] = claimed.get(record.plate, 0) + 1
        contested = {plate for plate, count in claimed.items() if count > 1}
        self.summary.plate_conflicts += len(contested)

        for record in records:
            if record.plate in contested:
                record = AisRecord(vin=record.vin, plate=None, fields=record.fields)
            holder_id = plate_holder.get(record.plate or "")
            holder = states.get(holder_id) if holder_id else None
            if holder is not None and holder_id not in seen and self._same_car(record, holder):
                # The register knows this car under a corrected VIN: it is the car we
                # have, not a new one.
                before = dict(holder.values)
                result = merge(holder, {"vin": Observation(record.vin, extract.ref)})
                result2 = merge(holder, ais_observations(record, holder, extract))
                derive(holder, extract.exported_on)
                identifier_changes(plan, holder.vehicle_id, before, holder.values, SOURCE_AIS,
                                   extract.extract_id, extract.exported_on)
                links.append(LinkRow(SOURCE_AIS, record.vin, holder.vehicle_id,
                                     extract.exported_on, "plate"))
                touched[holder.vehicle_id] = holder
                attributes = sorted(set(result.attributes()) | set(result2.attributes()))
                ledger.append(self._ledger(holder.vehicle_id, attributes,
                                           {**result.evidence(), **result2.evidence()},
                                           kind="vin-corrected"))
                self.summary.vin_corrections += 1
                continue
            if holder is not None:
                # The plate moved to this new car: close it on the car it left --
                # unless the holder's claim is the newer one, in which case the new
                # car is created without it.
                before = dict(holder.values)
                result = merge(holder, {"plate": Observation(None, extract.ref, clears=True)})
                if holder.values.get("plate") == record.plate:
                    self.summary.plate_conflicts += 1
                    record = AisRecord(vin=record.vin, plate=None, fields=record.fields)
                else:
                    identifier_changes(plan, holder.vehicle_id, before, holder.values, SOURCE_AIS,
                                       extract.extract_id, extract.exported_on)
                    touched[holder.vehicle_id] = holder
                    ledger.append(self._ledger(holder.vehicle_id, result.attributes(),
                                               result.evidence(), kind="plate-moved-away"))
                    self.summary.plates_moved += 1
            vehicle_id = mint_vehicle_id(self._generator)
            state = VehicleState(
                vehicle_id=vehicle_id,
                origin_source=SOURCE_AIS,
                origin_observed_on=extract.exported_on,
            )
            observations = new_vehicle_observations(record, self._completion, self._normalizer, extract)
            result = merge(state, observations)
            derive(state, extract.exported_on)
            identifier_changes(plan, vehicle_id, {}, state.values, SOURCE_AIS,
                               extract.extract_id, extract.exported_on)
            links.append(LinkRow(SOURCE_AIS, record.vin, vehicle_id, extract.exported_on, "minted"))
            touched[vehicle_id] = state
            ledger.append(self._ledger(vehicle_id, result.attributes(), {}, kind="created"))
            self.summary.vehicles_created += 1

        save_vehicles(connection, touched.values())
        plan.write(connection)
        add_links(connection, links)
        record_ledger_rows(connection, ledger)

    @staticmethod
    def _same_car(record: AisRecord, holder: VehicleState) -> bool:
        """Same car under a corrected VIN: first registration and make agree."""

        registered = clean_registry_date(record.get("registration_date"))
        make = record.make_code
        return (
            registered is not None
            and registered == holder.values.get("first_registration_date")
            and make is not None
            and make == holder.values.get("registry_make_code")
        )


def _free_bytes(path: str) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return DEFAULT_MIN_FREE_BYTES + 1


def import_ais_extract(
    connection: Connection,
    path: Path,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    progress: Callable[[AisImportSummary], None] | None = None,
    min_free_bytes: int | None = DEFAULT_MIN_FREE_BYTES,
    disk_path: str = "/",
    extract: AisExtract | None = None,
    records: Iterable[AisRecord] | None = None,
) -> AisImportSummary:
    """Import one extract into `core.vehicles`, exactly once."""

    extract = extract or open_extract(path)
    claim = claim_job_run(connection, job_name=JOB_NAME, batch_id=extract.extract_id)
    connection.commit()
    if not claim.should_execute:
        return AisImportSummary(extract_id=extract.extract_id, skipped_already_imported=True)

    rule_set, manufacturer_rules = load_active_rules(connection)
    importer = AisImporter(
        connection,
        extract,
        normalizer=Normalizer(rule_set, manufacturer_rules),
        completion=load_completion_rules(connection),
    )
    try:
        importer.prepare()
        connection.commit()
        for chunk in _chunks(records if records is not None else iter_ais_records(path), chunk_size):
            if min_free_bytes is not None and _free_bytes(disk_path) < min_free_bytes:
                importer.summary.stopped_for_disk = True
                raise RuntimeError("stopped: free disk space fell below the guard")
            importer.process_chunk(chunk)
            connection.commit()
            if progress is not None:
                progress(importer.summary)
        importer.reconcile_unmatched(chunk_size)
        summary = importer.summary
        # Every record read was handled: merged, minted, reconciled or found out of
        # scope. A record that cannot be handled fails the run instead of being
        # counted, so a completed run never hides a skipped record.
        complete_job_run(
            connection,
            claim.job_run.id,
            records_processed=summary.records_read,
            records_succeeded=summary.records_read,
            records_failed=0,
        )
        connection.commit()
        return summary
    except Exception as error:
        connection.rollback()
        summary = importer.summary
        fail_job_run(
            connection,
            claim.job_run.id,
            records_processed=summary.records_read,
            records_succeeded=summary.records_read,
            records_failed=0,
            error_code=type(error).__name__,
            error_summary=str(error)[:400],
        )
        connection.commit()
        raise
