"""Give the vehicles AIS created the registry's make code, and what follows from it.

The AIS export writes the registry's make code and its six-digit group number
as one string. The import used to cut it after two characters, which is right
for "VW890007" and wrong for the newer makes the registry gives three
characters: "POL021900" (Polestar) became make "PO" -- Pontiac's code -- and
group "L021900". Two things went wrong for those cars: the normalizer's reviewed
rules could not name the manufacturer, which stopped the car before matching
(`review_required`), and none of the rules keyed by make and group code found
the car -- no model text, variant, type code or displacement from the registry's
cars of the same make and group.

The import now splits the string from its end. This step does the same for the
vehicles already created:

- it selects the AIS-origin vehicles whose group code is longer than a group
  number, which is exactly the cars cut wrongly,
- puts the export's string back together and describes each car again the way
  the import does, from what the vehicle still holds of its AIS record,
- and merges only the fields a make and group code decide (`REPAIRED_FIELDS`).

The export file is not needed. What the vehicle no longer holds -- the raw fuel
and gearbox codes -- is not read again, and no field outside `REPAIRED_FIELDS`
is touched. A vehicle whose make code, group code or brand text is no longer
AIS's own is skipped: the three together are what is read again.

Idempotent: a repaired vehicle has a six-digit group code and is not selected
again. Afterwards `apply-vehicle-rules` fills what the enrichment rules keyed by
make code now reach, and the stored match results of the changed cars are
refreshed by their normal run. Sync, PostgreSQL only; commits per page.
"""

from __future__ import annotations

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
)
from ingestion.vehicle_core_fields import SOURCE_AIS, SOURCE_RULE, SourceRef
from ingestion.vehicle_core_merge import (
    Observation,
    VehicleState,
    current_source,
    derive,
    merge,
)
from ingestion.vehicle_core_migrations import VEHICLES_TABLE
from ingestion.vehicle_core_rules import CompletionRules, load_completion_rules
from ingestion.vehicle_core_store import (
    LedgerRow,
    ledger_event_id,
    load_vehicles,
    record_ledger_rows,
    save_vehicles,
)

#: What a make and group code decide on a vehicle the AIS import created: the two
#: codes, the values completion rules supply by them with what the normalizer
#: reads from those, and how the record normalized.
REPAIRED_FIELDS: tuple[str, ...] = (
    "registry_make_code",
    "group_code",
    *COMPLETED_FIELDS,
    "normalization_status",
    "normalization_confidence",
)
#: The three values read again; each must still be what AIS said.
_READ_AGAIN: tuple[str, ...] = ("registry_make_code", "group_code", "registry_brand_text")
#: The registry's flag only says "not four-wheel drive". Where a drive rule has
#: already named the driven axle, that generic value does not replace it.
_GENERIC_DRIVE = "2wd"
#: Marks this step's ledger entries; one per vehicle, whatever the run.
_STEP = "ais-make-code"

_SELECT = f"""
    SELECT vehicle_id FROM {VEHICLES_TABLE}
    WHERE origin_source = %s AND length(group_code) > %s
    ORDER BY vehicle_id
"""


@dataclass
class MakeCodeRepairSummary:
    #: AIS-origin vehicles whose group code is longer than a group number.
    selected: int = 0
    #: Of those, vehicles whose content changed (written unless a dry run).
    repaired: int = 0
    #: Vehicles left alone: a value to read again is no longer AIS's own.
    skipped: int = 0
    written: int = 0
    #: Make code before -> after.
    make_codes: Counter[str] = field(default_factory=Counter)
    #: Normalization status before -> after.
    statuses: Counter[str] = field(default_factory=Counter)
    fields_filled: Counter[str] = field(default_factory=Counter)
    fields_changed: Counter[str] = field(default_factory=Counter)
    #: Replaced values as "field: before -> after", codes and status left out.
    changes: Counter[str] = field(default_factory=Counter)

    def to_json(self) -> dict[str, Any]:
        return {
            "selected": self.selected,
            "repaired": self.repaired,
            "skipped": self.skipped,
            "written": self.written,
            "make_codes": dict(self.make_codes.most_common()),
            "statuses": dict(self.statuses.most_common()),
            "fields_filled": dict(self.fields_filled.most_common()),
            "fields_changed": dict(self.fields_changed.most_common()),
            "commonest_changes": dict(self.changes.most_common(40)),
        }


def stored_record(state: VehicleState) -> AisRecord | None:
    """The vehicle's AIS record as far as the vehicle still holds it.

    The export's group code is the make code and the group code as they were cut,
    put back together. None when one of them, or the brand text, is no longer
    what AIS said (a rule, a reviewer or a person supplied it).
    """

    values = state.values
    if any(
        not values.get(name) or current_source(state, name).source != SOURCE_AIS
        for name in _READ_AGAIN
    ):
        return None
    year, month = values.get("production_year"), values.get("production_month")
    registered = values.get("first_registration_date")
    stored: dict[str, Any] = {
        "car_name": values["registry_brand_text"],
        "group_code": f"{values['registry_make_code']}{values['group_code']}",
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


def repair(
    state: VehicleState, completion: CompletionRules, normalizer: Normalizer
) -> dict[str, tuple[Any, Any]] | None:
    """Merge the repaired fields into `state`. Returns field -> (before, after).

    None when the vehicle cannot be described again; an empty mapping when
    nothing changed. Pure apart from mutating `state`.
    """

    record = stored_record(state)
    if record is None:
        return None
    origin = SourceRef(state.origin_source, None, state.origin_observed_on)
    described = described_in_ts_terms(
        record, completion, normalizer, ref=origin, observed_on=state.origin_observed_on or date.min
    )
    before = {name: state.values.get(name) for name in REPAIRED_FIELDS}
    observations: dict[str, Observation | None] = {}
    withdrawn: dict[str, Observation | None] = {}
    for name in REPAIRED_FIELDS:
        observation = described.get(name)
        if observation is None:
            continue
        if observation.value is None and name != "group_code":
            # Silence is not a withdrawal here: the record was rebuilt from the
            # vehicle, and what it lacks the vehicle may hold from elsewhere. The
            # group code is the exception -- it is read whole from the string put
            # back together, and "000000" there means the car has no group.
            continue
        if name == "drive_type" and observation.value == _GENERIC_DRIVE and before[name] is not None:
            continue
        if (
            observation.ref.source == SOURCE_RULE
            and before[name] is not None
            and current_source(state, name).source == SOURCE_AIS
        ):
            # A completion rule now supplies what the import read off the AIS
            # record alone. As in the import, the rule's value takes that place
            # rather than standing behind it.
            withdrawn[name] = Observation(None, origin)
        observations[name] = observation
    merge(state, withdrawn)
    merge(state, observations)
    derive(state, state.origin_observed_on)
    return {
        name: (before[name], state.values.get(name))
        for name in REPAIRED_FIELDS
        if before[name] != state.values.get(name)
    }


def _ledger_row(vehicle_id: str, changes: dict[str, tuple[Any, Any]]) -> LedgerRow:
    evidence = {
        name: {"from": _plain(old), "to": _plain(new)} for name, (old, new) in sorted(changes.items())
    }
    return LedgerRow(
        event_id=ledger_event_id(_STEP, vehicle_id),
        source=SOURCE_AIS,
        target_node_id=vehicle_id,
        attributes_added=tuple(sorted(changes)),
        confidence=0.95,
        evidence=evidence,
        source_batch_id=_STEP,
    )


def _plain(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value


def repair_ais_make_codes(
    connection: Connection[Any], *, dry_run: bool = True, page_size: int = 2000
) -> MakeCodeRepairSummary:
    """Repair every AIS-origin vehicle whose make and group code were cut wrongly.

    A dry run reads, merges in memory and counts; it writes nothing.
    """

    summary = MakeCodeRepairSummary()
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
    summary: MakeCodeRepairSummary,
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
        changes = repair(state, completion, normalizer)
        if changes is None:
            summary.skipped += 1
            continue
        if not changes:
            continue
        summary.repaired += 1
        summary.statuses[f"{status_before} -> {state.values.get('normalization_status')}"] += 1
        for name, (old, new) in changes.items():
            if name == "registry_make_code":
                summary.make_codes[f"{old} -> {new}"] += 1
            if old is None:
                summary.fields_filled[name] += 1
                continue
            summary.fields_changed[name] += 1
            if name not in {"registry_make_code", "group_code", "normalization_status",
                            "normalization_confidence"}:
                summary.changes[f"{name}: {old} -> {new}"] += 1
        touched.append(state)
        ledger.append(_ledger_row(vehicle_id, changes))
    if dry_run:
        return
    summary.written += save_vehicles(connection, touched)
    record_ledger_rows(connection, ledger)
