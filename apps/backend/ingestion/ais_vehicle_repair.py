"""Describe the vehicles AIS created again, where the import read their record wrongly.

Two things the import got wrong for cars the registry snapshot does not have:

**The make code was cut after two characters.** The AIS export writes the
registry's make code and its six-digit group number as one string. For the
makes the registry gives three characters, "POL021900" (Polestar) became make
"PO" -- Pontiac's code -- and group "L021900". The normalizer's reviewed rules
could then not name the manufacturer, which stopped the car before matching
(`review_required`), and no rule keyed by make and group code found the car.

**The name was not divided.** The AIS name is the registry's brand text and
model text in a row ("VOLVO" + "EX30"). Where the car's group was new to us the
whole name was kept as the brand text and the car had no model text, so the
normalizer could not read a model from it.

The import now does both right. This step does the same for the vehicles
already created:

- it selects the AIS-origin vehicles whose group code is longer than a group
  number (cut wrongly), and those whose brand text is still the AIS name with
  no model text beside it (not divided),
- puts the export's values back together from what the vehicle still holds and
  describes the car again the way the import does,
- and merges only the fields that description decides (`REPAIRED_FIELDS`).

The export file is not needed. What the vehicle no longer holds -- the raw fuel
and gearbox codes -- is not read again, and no field outside `REPAIRED_FIELDS`
is touched; of those, a value a rule, a reviewer or a person has supplied since
is left as it is. For the same reason the car's normalization status is only replaced
when reading the same stored values the way they were last read gives the
status the vehicle carries: then nothing the status rests on is missing. A
vehicle whose make code, group code or brand text is no longer AIS's own is
skipped: the three together are what is read again.

Idempotent: a repaired vehicle has a six-digit group code and a brand text a
rule supplied, and is not selected again; a vehicle the new description changes
nothing on is read and left as it is. Afterwards `apply-vehicle-rules` fills
what the enrichment rules now reach, and the stored match results of the
changed cars are refreshed by their normal run. Sync, PostgreSQL only; commits
per page.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from psycopg import Connection

from ingestion.active_rules import load_active_rules
from ingestion.vehicle_core_ais import (
    COMPLETED_FIELDS,
    GROUP_NUMBER_LENGTH,
    AisRecord,
    Normalizer,
    described_in_ts_terms,
    ts_shaped_record,
)
from ingestion.vehicle_core_fields import SOURCE_AIS, SOURCE_RULE, SourceRef, clean_code
from ingestion.vehicle_core_merge import (
    Observation,
    VehicleState,
    current_source,
    derive,
    merge,
)
from ingestion.vehicle_core_migrations import VEHICLES_TABLE
from ingestion.vehicle_core_rules import (
    BRAND_TEXT_FAMILY,
    CompletionRules,
    load_completion_rules,
)
from ingestion.vehicle_core_store import (
    LedgerRow,
    ledger_event_id,
    load_vehicles,
    record_ledger_rows,
    save_vehicles,
)

#: What the description of an AIS record decides on the vehicle it created: the
#: make and group code, the values completion rules supply with what the
#: normalizer reads from them, and how the record normalized.
REPAIRED_FIELDS: tuple[str, ...] = (
    "registry_make_code",
    "group_code",
    *COMPLETED_FIELDS,
    "normalization_status",
    "normalization_confidence",
)
_STATUS_FIELDS: tuple[str, ...] = ("normalization_status", "normalization_confidence")
#: The three values read again; each must still be what AIS said.
_READ_AGAIN: tuple[str, ...] = ("registry_make_code", "group_code", "registry_brand_text")
#: Marks this step's ledger entries.
_STEP = "ais-vehicle-repair"

_SELECT = f"""
    SELECT vehicle_id FROM {VEHICLES_TABLE}
    WHERE origin_source = %s
      AND (
        length(group_code) > %s
        OR (registry_brand_text IS NOT NULL AND registry_model_text IS NULL
            AND NOT (field_sources ? 'registry_brand_text'))
      )
    ORDER BY vehicle_id
"""


class _AsImported(AisRecord):
    """The record as the import used to read it: the group code cut after two characters."""

    @property
    def make_code(self) -> str | None:
        group = clean_code(self.fields.get("group_code"))
        return group[:2] if group and len(group) >= 8 else None

    @property
    def group_number(self) -> str | None:
        group = clean_code(self.fields.get("group_code"))
        number = group[2:] if group and len(group) >= 8 else None
        return None if number in {None, "000000"} else number


@dataclass
class AisRepairSummary:
    #: AIS-origin vehicles cut wrongly or with an undivided name.
    selected: int = 0
    #: Of those, vehicles whose content changed (written unless a dry run).
    repaired: int = 0
    #: Vehicles read and left as they are: the new description says the same.
    unchanged: int = 0
    #: Vehicles left alone: a value to read again is no longer AIS's own.
    skipped: int = 0
    #: Repaired vehicles that keep their status: the stored values do not
    #: reproduce it, so it rests on something the vehicle no longer holds.
    status_kept: int = 0
    written: int = 0
    #: Make code before -> after.
    make_codes: Counter[str] = field(default_factory=Counter)
    #: Normalization status before -> after.
    statuses: Counter[str] = field(default_factory=Counter)
    fields_filled: Counter[str] = field(default_factory=Counter)
    fields_changed: Counter[str] = field(default_factory=Counter)
    #: Replaced values as "field: before -> after", texts, codes and status left out.
    changes: Counter[str] = field(default_factory=Counter)

    def to_json(self) -> dict[str, Any]:
        return {
            "selected": self.selected,
            "repaired": self.repaired,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "status_kept": self.status_kept,
            "written": self.written,
            "make_codes": dict(self.make_codes.most_common()),
            "statuses": dict(self.statuses.most_common()),
            "fields_filled": dict(self.fields_filled.most_common()),
            "fields_changed": dict(self.fields_changed.most_common()),
            "commonest_changes": dict(self.changes.most_common(60)),
        }


def stored_record(state: VehicleState) -> AisRecord | None:
    """The vehicle's AIS record as far as the vehicle still holds it.

    The export's group code is the make code and the group code put back
    together, which also undoes a wrong cut. None when one of them, or the brand
    text, is no longer what AIS said (a rule, a reviewer or a person supplied it).
    """

    values = state.values
    if not values.get("registry_make_code") or not values.get("registry_brand_text"):
        return None
    if any(
        values.get(name) is not None and current_source(state, name).source != SOURCE_AIS
        for name in _READ_AGAIN
    ):
        return None
    year, month = values.get("production_year"), values.get("production_month")
    registered = values.get("first_registration_date")
    stored: dict[str, Any] = {
        "car_name": values["registry_brand_text"],
        "group_code": f"{values['registry_make_code']}{values.get('group_code') or '000000'}",
        "vehicle_type": values.get("registry_vehicle_type"),
        "body_code": values.get("registry_body_code"),
        "kw": values.get("power_kw"),
        "registration_date": registered.isoformat() if isinstance(registered, date) else None,
        "build_month": f"{year:04d}{month:02d}" if year and month else None,
        "model_year": values.get("model_year"),
        "colour": values.get("colour"),
        "tyre": values.get("tyre_front"),
        "engine_code": values.get("engine_code"),
    }
    return AisRecord(
        vin=str(values.get("vin") or ""),
        plate=values.get("plate"),
        fields={name: str(value) for name, value in stored.items() if value not in (None, "")},
    )


def status_as_read_before(
    state: VehicleState, record: AisRecord, completion: CompletionRules, normalizer: Normalizer
) -> str:
    """How the stored values normalize when read the way they were last read.

    A vehicle still carrying a group code longer than a group number was read by
    the old cut; any other by today's. Neither reading divided the name.
    """

    if len(state.values.get("group_code") or "") > GROUP_NUMBER_LENGTH:
        record = _AsImported(record.vin, record.plate, record.fields)
    raw, _ = ts_shaped_record(record, completion.without(BRAND_TEXT_FAMILY))
    return normalizer.normalize(raw)[1]


@dataclass(frozen=True)
class Repair:
    """What describing one vehicle again changed: field -> (before, after)."""

    changes: dict[str, tuple[Any, Any]]
    #: The status stayed because the stored values do not reproduce it.
    status_kept: bool = False


def repair(
    state: VehicleState, completion: CompletionRules, normalizer: Normalizer
) -> Repair | None:
    """Merge the repaired fields into `state`.

    None when the vehicle cannot be described again. Pure apart from mutating
    `state`.
    """

    record = stored_record(state)
    if record is None:
        return None
    origin = SourceRef(state.origin_source, None, state.origin_observed_on)
    described = described_in_ts_terms(
        record, completion, normalizer, ref=origin, observed_on=state.origin_observed_on or date.min
    )
    before = {name: state.values.get(name) for name in REPAIRED_FIELDS}
    # The status the vehicle carries may rest on a code it no longer holds. It is
    # replaced only when the stored values, read as before, give that status.
    status_kept = (
        status_as_read_before(state, record, completion, normalizer)
        != before["normalization_status"]
    )
    observations: dict[str, Observation | None] = {}
    withdrawn: dict[str, Observation | None] = {}
    for name in REPAIRED_FIELDS:
        observation = described.get(name)
        if observation is None or (status_kept and name in _STATUS_FIELDS):
            continue
        if observation.value is None and name != "group_code":
            # Silence is not a withdrawal here: the record was rebuilt from the
            # vehicle, and what it lacks the vehicle may hold from elsewhere. The
            # group code is the exception -- it is read whole from the string put
            # back together, and "000000" there means the car has no group.
            continue
        if before[name] is not None and current_source(state, name).source != SOURCE_AIS:
            # A rule, a reviewer or a person has supplied this value since the
            # import, from more than this partial record: a model or a driven
            # axle read again here fills a gap and overrules nothing.
            continue
        if observation.ref.source == SOURCE_RULE and before[name] is not None:
            # A completion rule now supplies what the import read off the AIS
            # record alone. As in the import, the rule's value takes that place
            # rather than standing behind it.
            withdrawn[name] = Observation(None, origin)
        observations[name] = observation
    merge(state, withdrawn)
    merge(state, observations)
    derive(state, state.origin_observed_on)
    changes = {
        name: (before[name], state.values.get(name))
        for name in REPAIRED_FIELDS
        if before[name] != state.values.get(name)
    }
    return Repair(changes, status_kept=status_kept and bool(changes))


def _ledger_row(vehicle_id: str, changes: dict[str, tuple[Any, Any]]) -> LedgerRow:
    evidence = {
        name: {"from": _plain(old), "to": _plain(new)} for name, (old, new) in sorted(changes.items())
    }
    return LedgerRow(
        # The content is part of the identity: a vehicle cut wrongly and, in a
        # later run, divided has two entries, and a replayed run adds none.
        event_id=ledger_event_id(_STEP, vehicle_id, json.dumps(evidence, sort_keys=True)),
        source=SOURCE_AIS,
        target_node_id=vehicle_id,
        attributes_added=tuple(sorted(changes)),
        confidence=0.95,
        evidence=evidence,
        source_batch_id=_STEP,
    )


def _plain(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value


def repair_ais_vehicles(
    connection: Connection[Any], *, dry_run: bool = True, page_size: int = 2000
) -> AisRepairSummary:
    """Describe again every AIS-origin vehicle cut wrongly or with an undivided name.

    A dry run reads, merges in memory and counts; it writes nothing.
    """

    summary = AisRepairSummary()
    with connection.cursor() as cursor:
        cursor.execute(_SELECT, (SOURCE_AIS, GROUP_NUMBER_LENGTH))
        vehicle_ids = [str(row[0]) for row in cursor.fetchall()]
    summary.selected = len(vehicle_ids)
    if not vehicle_ids:
        return summary
    rule_set, manufacturer_rules = load_active_rules(connection)
    normalizer = Normalizer(rule_set, manufacturer_rules)
    completion = load_completion_rules(connection)
    for start in range(0, len(vehicle_ids), page_size):
        page = vehicle_ids[start : start + page_size]
        _repair_page(connection, page, completion, normalizer, summary, dry_run=dry_run)
        if dry_run:
            connection.rollback()
        else:
            connection.commit()
    return summary


def _repair_page(
    connection: Connection[Any],
    vehicle_ids: Sequence[str],
    completion: CompletionRules,
    normalizer: Normalizer,
    summary: AisRepairSummary,
    *,
    dry_run: bool,
) -> None:
    states = load_vehicles(connection, vehicle_ids)
    touched: list[VehicleState] = []
    ledger: list[LedgerRow] = []
    for vehicle_id in vehicle_ids:
        state = states.get(vehicle_id)
        if state is None:
            continue
        status_before = state.values.get("normalization_status")
        repaired = repair(state, completion, normalizer)
        if repaired is None:
            summary.skipped += 1
            continue
        if not repaired.changes:
            summary.unchanged += 1
            continue
        summary.repaired += 1
        summary.status_kept += repaired.status_kept
        summary.statuses[f"{status_before} -> {state.values.get('normalization_status')}"] += 1
        for name, (old, new) in repaired.changes.items():
            if name == "registry_make_code":
                summary.make_codes[f"{old} -> {new}"] += 1
            if old is None:
                summary.fields_filled[name] += 1
                continue
            summary.fields_changed[name] += 1
            if name not in {"registry_make_code", "group_code", "registry_brand_text",
                            *_STATUS_FIELDS}:
                summary.changes[f"{name}: {old} -> {new}"] += 1
        touched.append(state)
        ledger.append(_ledger_row(vehicle_id, repaired.changes))
    if dry_run:
        return
    summary.written += save_vehicles(connection, touched)
    record_ledger_rows(connection, ledger)
