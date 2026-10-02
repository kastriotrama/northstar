"""Build the pilot database: a seeded slice of the full build, for live.

Live cannot hold the full build (63 GB). The pilot is a new database on the same
server as the full build that works like it but holds only a random slice of its
cars -- by default 500,000 registered passenger cars -- with every row those cars
need, every rule, and one pinned TecDoc catalog batch. It is shipped to live as
a `pg_dump`.

Every table of the `core` and `staging` schemas is in one of three classes:

- **A, copied whole**: rules, reviewer decisions, the catalog batch, job runs.
- **B, the slice**: vehicles and every row keyed by a slice vehicle or by one of
  its Transportstyrelsen (TS) records.
- **C, left out**: run telemetry and staging areas the app rebuilds or never reads.

The source database is only ever read: its connection is opened read-only, in
one repeatable-read transaction, so the plan, the copy and the verification all
see the same rows. The target's schema comes from the repo's migrations, rows
keep their ids, and the build ends by comparing the target with the source.

The default seed is the match impact report's sample seed, on purpose. The slice
and the report's sample are the same seeded ordering of the same population cut
at different lengths, so the report's 30,000 cars are the first 30,000 of the
pick: a slice of at least that size holds every one of them, and a baseline
measured on live can be compared car by car with the local one.

A verified build writes a manifest: seed, size, catalog batch, the time of the
source snapshot and, per table, the row count and content checksum the
verification computed. It is stored in the pilot (`public.northstar_pilot_manifest`,
so a dump carries it) and written as a JSON file. `verify_pilot_database`
recomputes counts and checksums on any database -- the restored one on live --
in a read-only session and compares them with the manifest.

Nothing here prints a plate, a VIN or a vehicle id.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

import psycopg
from psycopg import Connection, IsolationLevel, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from api.app.features.vehicle_matching.repository import SAMPLE_SEED
from ingestion.confidence_routing_migrations import run_confidence_routing_migrations
from ingestion.job_bookkeeping_migrations import run_job_bookkeeping_migrations
from ingestion.ledger_migrations import run_ledger_migrations
from ingestion.match_chunk_migrations import run_match_chunk_migrations
from ingestion.match_run_migrations import run_match_run_migrations
from ingestion.normalization_migrations import run_normalization_migrations
from ingestion.review_queue_migrations import run_review_queue_migrations
from ingestion.rule_definition_migrations import run_rule_definition_migrations
from ingestion.staging_migrations import run_staging_migrations
from ingestion.tecdoc.canonical_rule_migrations import run_tecdoc_rule_migrations
from ingestion.tecdoc.migrations import run_tecdoc_migrations
from ingestion.tecdoc.resolution_migrations import run_tecdoc_resolution_migrations
from ingestion.vehicle_core_migrations import run_vehicle_core_migrations
from ingestion.vehicle_facts_migrations import run_vehicle_facts_migrations
from ingestion.vehicle_ktype_choice_migrations import (
    VEHICLE_KTYPE_CHOICES_TABLE,
    run_vehicle_ktype_choice_migrations,
)

PILOT_SCHEMAS: tuple[str, ...] = ("core", "staging")
# The match impact report's seed, imported rather than repeated: with it the
# report's sample is a prefix of the slice's pick (see the module docstring).
DEFAULT_SEED = SAMPLE_SEED
# The match impact report's default `--size`.
MATCH_IMPACT_SAMPLE_SIZE = 30_000
DEFAULT_SIZE = 500_000
DEFAULT_SCOPE = "passenger"
DEFAULT_CHUNK_SIZE = 20_000
DEFAULT_MAINTENANCE_DATABASE = "postgres"
TS_SOURCE_SYSTEM = "transportstyrelsen"
TS_RAW_TABLE = "staging.transportstyrelsen_raw"
MARKER_KEY = "northstar_pilot_build"
MANIFEST_KEY = "northstar_pilot_manifest"
MANIFEST_VERSION = 1
# Outside the pilot schemas, so no table check sees it; inside the dump.
MANIFEST_TABLE = "public.northstar_pilot_manifest"
_MANIFEST_TABLE_NAME = re.compile(r"^(core|staging)\.[a-z][a-z0-9_]*$")

# Both sessions render timestamps, dates and floats the same way, so a row's text
# -- and with it the content checksum -- is the same in source and target.
_SESSION_OPTIONS = (
    "-c TimeZone=UTC -c DateStyle=ISO,YMD -c IntervalStyle=postgres "
    "-c extra_float_digits=1 -c bytea_output=hex"
)
_DATABASE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_RESERVED_DATABASES = frozenset({"postgres", "template0", "template1"})

TableClass = Literal["A", "B", "C"]
KeySet = Literal["vehicles", "records", "chunks", "evidence"]
_KEY_TYPES: dict[KeySet, str] = {
    "vehicles": "text",
    "records": "bigint",
    "chunks": "uuid",
    "evidence": "bigint",
}
_T = TypeVar("_T")
Report = Callable[[str], None]


class PilotBuildError(RuntimeError):
    """The build refused to start or could not finish; the message is safe to print."""


# The repo has no single "migrate everything" command. These are the fifteen
# migration sets, in the order verified on an empty database. Only one has a
# foreign key into another: people's KType choices reference core.vehicles, so
# that set follows "vehicle core". Without it the API's vehicle lookups fail on
# the pilot.
PILOT_MIGRATIONS: tuple[tuple[str, Callable[[Connection[Any]], tuple[str, ...]]], ...] = (
    ("staging", run_staging_migrations),
    ("ledger", run_ledger_migrations),
    ("job bookkeeping", run_job_bookkeeping_migrations),
    ("review queue", run_review_queue_migrations),
    ("confidence routing", run_confidence_routing_migrations),
    ("match runs", run_match_run_migrations),
    ("vehicle facts", run_vehicle_facts_migrations),
    ("vehicle core", run_vehicle_core_migrations),
    ("vehicle ktype choices", run_vehicle_ktype_choice_migrations),
    ("normalization", run_normalization_migrations),
    ("rule definitions", run_rule_definition_migrations),
    ("match chunks", run_match_chunk_migrations),
    ("tecdoc catalog", run_tecdoc_migrations),
    ("tecdoc resolution rules", run_tecdoc_resolution_migrations),
    ("tecdoc rule catalog", run_tecdoc_rule_migrations),
)


def run_pilot_migrations(connection: Connection[Any]) -> tuple[str, ...]:
    """Create the whole PostgreSQL schema the app needs; returns the sets applied."""

    for _, migrate in PILOT_MIGRATIONS:
        migrate(connection)
    connection.commit()
    return tuple(name for name, _ in PILOT_MIGRATIONS)


@dataclass(frozen=True)
class PilotOptions:
    target_database: str
    catalog_batch: str
    size: int = DEFAULT_SIZE
    seed: str = DEFAULT_SEED
    scope: str = DEFAULT_SCOPE
    registered_only: bool = True


@dataclass(frozen=True)
class TableSpec:
    """How one table reaches the pilot. The list order is the load order."""

    table: str
    table_class: TableClass
    note: str
    # A fixed predicate with `%s` placeholders; `where_options` names the options
    # that fill them, in order.
    where: str | None = None
    where_options: tuple[str, ...] = ()
    # Slice tables: rows whose `key_column` is one of the slice's keys.
    key_column: str | None = None
    key_set: KeySet | None = None
    # No index starts with the key column: read the table once with all keys
    # instead of once per chunk of keys.
    one_pass: bool = False
    # Counters the pilot recomputes for the slice; left out of content checksums.
    recomputed: tuple[str, ...] = ()
    # A version table whose rows refuse children once sealed: loaded unsealed,
    # sealed again after its children are in.
    seal_column: str | None = None
    seal_key: str | None = None
    # Class C only: rows in the source are expected and deliberately left behind.
    may_hold_rows: bool = False


def _whole(table: str, note: str, **extra: Any) -> TableSpec:
    return TableSpec(table, "A", note, **extra)


def _slice(table: str, column: str, key_set: KeySet, note: str, **extra: Any) -> TableSpec:
    return TableSpec(table, "B", note, key_column=column, key_set=key_set, **extra)


def _left_out(table: str, note: str, *, may_hold_rows: bool = False) -> TableSpec:
    return TableSpec(table, "C", note, may_hold_rows=may_hold_rows)


_RAW_ONLY = f"source_table = '{TS_RAW_TABLE}'"
_BATCH = {"where": "batch_id = %s", "where_options": ("catalog_batch",)}

PILOT_TABLES: tuple[TableSpec, ...] = (
    # -- the pinned catalog batch
    _whole("core.tecdoc_source_batches", "the pinned catalog batch", **_BATCH),
    _whole("core.tecdoc_canonical_candidates", "the pinned batch's catalog", **_BATCH),
    _whole("core.tecdoc_candidate_relationships", "the pinned batch's engine links", **_BATCH),
    _whole("core.tecdoc_identity_registry", "keeps a later promotion from minting second ids"),
    # -- rules and reviewer decisions
    _whole("core.tecdoc_rule_versions", "TecDoc rule catalog versions",
           seal_column="sealed", seal_key="rule_version"),
    _whole("core.tecdoc_rules", "TecDoc rule catalog"),
    _whole("core.tecdoc_resolution_rules", "reviewer rulings on TecDoc and TS vocabulary"),
    _whole("core.translation_rule_versions", "policy overrides"),
    _whole("core.translation_rule_drafts", "reviewer drafts"),
    _whole("core.manufacturer_entity_drafts", "reviewer drafts"),
    _whole("core.translation_rule_definition_versions", "stored translation rule versions",
           seal_column="sealed", seal_key="rule_version"),
    _whole("core.translation_rule_definitions", "stored translation rules"),
    _whole("core.vehicle_enrichment_rules", "learned rules"),
    # -- chunk builds, reviewer rules and what they resolved
    _whole("core.match_chunk_builds", "parent of every reviewer rule; counters recomputed",
           recomputed=("row_count", "chunk_count")),
    _slice("core.match_chunks", "chunk_id", "chunks",
           "chunks with a slice member or a proposal; counters recomputed",
           recomputed=("member_count", "reason_profile")),
    _whole("core.match_chunk_proposals", "chunk proposals and their reviews"),
    _slice("core.match_chunk_members", "source_record_id", "records", "by TS record",
           one_pass=True),
    _whole("core.match_resolution_rules", "reviewer rules"),
    _slice("core.match_field_resolutions", "source_record_id", "records", "by TS record",
           one_pass=True),
    _slice("staging.oem_vin_evidence", "id", "evidence", "evidence behind copied samples"),
    _slice("core.match_chunk_samples", "source_record_id", "records", "by TS record",
           one_pass=True),
    # -- TS records of the slice
    _slice(TS_RAW_TABLE, "id", "records", "every TS record of a slice vehicle"),
    _slice("core.normalization_results", "source_record_id", "records", "by TS record",
           where=_RAW_ONLY),
    _slice("core.vehicle_facts", "source_record_id", "records", "by TS record"),
    _slice("core.review_queue", "source_record_id", "records", "by TS record", where=_RAW_ONLY),
    # -- the slice's vehicles
    _slice("core.vehicles", "vehicle_id", "vehicles", "the slice"),
    _slice("core.vehicle_identifiers", "vehicle_id", "vehicles", "by vehicle"),
    _slice("core.vehicle_source_links", "vehicle_id", "vehicles", "by vehicle"),
    _slice("core.enrichment_ledger", "target_node_id", "vehicles", "by vehicle"),
    # A car's whole chain in one COPY statement, so its links check at the end.
    # The plan refuses a cut that would leave a decided car behind.
    _slice(VEHICLE_KTYPE_CHOICES_TABLE, "vehicle_id", "vehicles",
           "people's KType choices, by vehicle"),
    # -- bookkeeping the app reads
    _whole("core.ingest_job_runs", "batch pickers, rule application runs, the AIS claim"),
    _whole("core.match_runs", "only runs a reviewer decision belongs to",
           where="operation_id IN (SELECT operation_id FROM core.match_review_rule_decisions)"),
    _whole("core.match_review_rule_decisions", "reviewer decisions on match patterns"),
    # -- left out
    _left_out("core.match_run_checkpoints", "run telemetry"),
    _left_out("core.match_run_reason_counts", "run telemetry"),
    _left_out("core.match_run_blocker_counts", "run telemetry"),
    _left_out("core.match_run_pattern_inventory", "run telemetry"),
    _left_out("core.match_run_pattern_batches", "run telemetry"),
    _left_out("core.match_run_pattern_members", "run telemetry"),
    _left_out("core.match_routing_decisions", "no writer in the vehicle path"),
    _left_out("core.match_decision_heads", "no writer in the vehicle path"),
    _left_out("core.match_decision_supersessions", "no writer in the vehicle path"),
    _left_out("staging.tecdoc_manufacturer", "import staging area"),
    _left_out("staging.tecdoc_model_family", "import staging area"),
    _left_out("staging.tecdoc_platform", "import staging area"),
    _left_out("staging.tecdoc_transmission", "import staging area"),
    _left_out("staging.tecdoc_vehicle_variant", "import staging area"),
    _left_out("staging.tecdoc_bodywork", "import staging area"),
    _left_out("staging.tecdoc_engine", "import staging area"),
    _left_out("staging.tecdoc_ktype_alias", "import staging area"),
    _left_out("core.tecdoc_gap_suggestions", "retired layer, no migration creates it",
              may_hold_rows=True),
    _left_out("core.remote_passenger_import_parts", "import resume cursor, holds plates",
              may_hold_rows=True),
)


# References no foreign key declares. Each query counts rows in the target that
# name a TS record, vehicle or rule the pilot does not hold; all must be zero.
REFERENCE_CHECKS: tuple[tuple[str, str], ...] = (
    (
        "vehicle origin records have a raw row",
        (
            "SELECT count(*) FROM core.vehicles AS v WHERE v.ts_record_id IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {TS_RAW_TABLE} AS r WHERE r.id = v.ts_record_id)"
        ),
    ),
    (
        "vehicle origin records are linked to their vehicle",
        (
            "SELECT count(*) FROM core.vehicles AS v WHERE v.ts_record_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM core.vehicle_source_links AS l "
            f"WHERE l.source_system = '{TS_SOURCE_SYSTEM}' "
            "AND l.source_record_key = v.ts_record_id::text AND l.vehicle_id = v.vehicle_id)"
        ),
    ),
    (
        "TS links have a raw row",
        (
            "SELECT count(*) FROM core.vehicle_source_links AS l "
            f"WHERE l.source_system = '{TS_SOURCE_SYSTEM}' AND NOT EXISTS "
            f"(SELECT 1 FROM {TS_RAW_TABLE} AS r WHERE r.id::text = l.source_record_key)"
        ),
    ),
    (
        "normalization results have a raw row",
        (
            f"SELECT count(*) FROM core.normalization_results AS n WHERE n.{_RAW_ONLY} AND "
            f"NOT EXISTS (SELECT 1 FROM {TS_RAW_TABLE} AS r WHERE r.id = n.source_record_id)"
        ),
    ),
    (
        "vehicle facts have a raw row",
        (
            "SELECT count(*) FROM core.vehicle_facts AS f WHERE "
            f"NOT EXISTS (SELECT 1 FROM {TS_RAW_TABLE} AS r WHERE r.id = f.source_record_id)"
        ),
    ),
    (
        "field resolutions have a raw row",
        (
            "SELECT count(*) FROM core.match_field_resolutions AS f WHERE "
            f"NOT EXISTS (SELECT 1 FROM {TS_RAW_TABLE} AS r WHERE r.id = f.source_record_id)"
        ),
    ),
    (
        "chunk members have a raw row",
        (
            "SELECT count(*) FROM core.match_chunk_members AS m WHERE "
            f"NOT EXISTS (SELECT 1 FROM {TS_RAW_TABLE} AS r WHERE r.id = m.source_record_id)"
        ),
    ),
    (
        "review items have a raw row",
        (
            f"SELECT count(*) FROM core.review_queue AS q WHERE q.{_RAW_ONLY} AND "
            f"NOT EXISTS (SELECT 1 FROM {TS_RAW_TABLE} AS r WHERE r.id = q.source_record_id)"
        ),
    ),
    (
        "ledger rows target a pilot vehicle",
        (
            "SELECT count(*) FROM core.enrichment_ledger AS e WHERE NOT EXISTS "
            "(SELECT 1 FROM core.vehicles AS v WHERE v.vehicle_id = e.target_node_id)"
        ),
    ),
    (
        "rule sources on vehicles name a learned rule",
        (
            "SELECT count(*) FROM core.vehicles AS v "
            "CROSS JOIN LATERAL jsonb_each_text(v.field_sources) AS s(field, source) "
            "WHERE left(s.source, 5) = 'rule:' AND NOT EXISTS (SELECT 1 "
            "FROM core.vehicle_enrichment_rules AS r WHERE r.rule_id = substr(s.source, 6))"
        ),
    ),
    (
        "chunk member counts match the copied members",
        (
            "SELECT count(*) FROM core.match_chunks AS c WHERE c.member_count <> "
            "(SELECT count(*) FROM core.match_chunk_members AS m WHERE m.chunk_id = c.chunk_id)"
        ),
    ),
    (
        "build counters match the copied chunks",
        (
            "SELECT count(*) FROM core.match_chunk_builds AS b CROSS JOIN LATERAL ("
            "SELECT coalesce(sum(c.member_count), 0) AS row_count, count(*) AS chunk_count "
            "FROM core.match_chunks AS c WHERE c.build_id = b.build_id) AS counted "
            "WHERE b.row_count <> counted.row_count OR b.chunk_count <> counted.chunk_count"
        ),
    ),
)


# --------------------------------------------------------------------------- pure parts


def spec_by_table() -> dict[str, TableSpec]:
    return {spec.table: spec for spec in PILOT_TABLES}


def check_target_name(target: str, *, source: str, maintenance: str) -> None:
    """Refuse a target that is, or could be mistaken for, a database that holds data."""

    if not _DATABASE_NAME.fullmatch(target):
        raise PilotBuildError(
            f"target database name {target!r} must be lower-case letters, digits and "
            "underscores, starting with a letter"
        )
    if target == source:
        raise PilotBuildError(
            f"target database {target!r} is the source database; the source is only read"
        )
    if target == maintenance or target in _RESERVED_DATABASES:
        raise PilotBuildError(f"target database {target!r} is a system database")


def slice_query(*, registered_only: bool) -> str:
    """The seeded pick: parameters are scope, seed, size.

    The same ordering as `VehicleMatchingRepository.sample_vehicle_ids`, so one
    seed names the same cars in the pilot and in a match impact report.
    """

    return (
        f"SELECT vehicle_id FROM core.vehicles WHERE {eligible_predicate(registered_only)} "
        "ORDER BY md5(%s || vehicle_id), vehicle_id LIMIT %s"
    )


def eligible_predicate(registered_only: bool) -> str:
    status = " AND registry_status = 'registered'" if registered_only else ""
    return f"vehicle_scope = %s{status}"


def chunked(values: Sequence[_T], size: int) -> Iterator[Sequence[_T]]:
    if size < 1:
        raise ValueError("chunk size must be at least 1")
    for start in range(0, len(values), size):
        yield values[start : start + size]


def selection_predicate(
    spec: TableSpec, options: PilotOptions, keys: Sequence[Any] | None
) -> tuple[str, list[Any]]:
    """The WHERE clause that picks a table's pilot rows in the source, with parameters."""

    clauses: list[str] = []
    params: list[Any] = []
    if spec.where is not None:
        clauses.append(f"({spec.where})")
        params.extend(getattr(options, name) for name in spec.where_options)
    if spec.key_column is not None:
        if spec.key_set is None or keys is None:
            raise ValueError(f"{spec.table} is sliced by key and needs its keys")
        clauses.append(f"{spec.key_column} = ANY(%s::{_KEY_TYPES[spec.key_set]}[])")
        params.append(list(keys))
    return " AND ".join(clauses) or "TRUE", params


def selections(
    spec: TableSpec, options: PilotOptions, keys: Sequence[Any] | None, chunk_size: int
) -> Iterator[tuple[str, list[Any]]]:
    """One predicate for a whole table; one per chunk of keys for a slice table.

    The chunks partition the keys, so each pilot row is selected exactly once and
    counts and checksums add up across chunks.
    """

    if spec.table_class == "C":
        return
    if spec.key_column is None:
        yield selection_predicate(spec, options, None)
        return
    if not keys:
        return
    size = len(keys) if spec.one_pass else chunk_size
    for chunk in chunked(keys, size):
        yield selection_predicate(spec, options, chunk)


def estimate_bytes(pilot_rows: int, *, table_rows: int, table_bytes: int) -> int:
    """Pilot rows times the source's bytes per row, indexes included."""

    if pilot_rows <= 0 or table_rows <= 0:
        return 0
    return round(table_bytes * min(1.0, pilot_rows / table_rows))


def build_marker(
    options: PilotOptions, *, source_database: str, state: str, started_at: str,
    finished_at: str | None = None,
) -> str:
    """The database comment that says "this database is a pilot build"."""

    return json.dumps(
        {
            MARKER_KEY: {
                "state": state,
                "source_database": source_database,
                "size": options.size,
                "seed": options.seed,
                "scope": options.scope,
                "registered_only": options.registered_only,
                "catalog_batch": options.catalog_batch,
                "started_at": started_at,
                "finished_at": finished_at,
            }
        },
        sort_keys=True,
    )


def parse_marker(comment: str | None) -> dict[str, Any] | None:
    if not comment:
        return None
    try:
        parsed = json.loads(comment)
    except ValueError:
        return None
    marker = parsed.get(MARKER_KEY) if isinstance(parsed, dict) else None
    return marker if isinstance(marker, dict) else None


def target_refusal(
    target: str, *, exists: bool, marker: dict[str, Any] | None, replace: bool
) -> str | None:
    """Why the target may not be (re)built, or None when it may."""

    if not exists:
        return None
    if marker is None:
        return (
            f"database {target!r} exists and is not a pilot build; it is never dropped. "
            "Choose another --target-database."
        )
    if not replace:
        return (
            f"database {target!r} exists (pilot build, state {marker.get('state')!r}); "
            "pass --replace to drop and rebuild it"
        )
    return None


@dataclass(frozen=True)
class Fingerprint:
    """Row count plus an order-independent sum of row hashes."""

    rows: int = 0
    high: int = 0
    low: int = 0

    def __add__(self, other: Fingerprint) -> Fingerprint:
        return Fingerprint(self.rows + other.rows, self.high + other.high, self.low + other.low)

    @property
    def checksum(self) -> str:
        """The content sums as the manifest writes them."""

        return f"{self.high}:{self.low}"


# Per table: the columns the content check covers, and what the target held.
Measured = dict[str, tuple[tuple[str, ...], Fingerprint]]


@dataclass(frozen=True)
class PlannedTable:
    spec: TableSpec
    in_source: bool
    pilot_rows: int
    source_rows: int
    estimated_bytes: int


@dataclass(frozen=True)
class PilotPlan:
    options: PilotOptions
    source_database: str
    target_exists: bool
    target_marker: dict[str, Any] | None
    eligible_vehicles: int
    slice_vehicles: int
    slice_records: int
    catalog_status: str | None
    tables: tuple[PlannedTable, ...]
    problems: tuple[str, ...]
    warnings: tuple[str, ...]
    # The match impact report's sample: how many cars it has, how many the slice holds.
    sample_vehicles: int = 0
    sample_in_slice: int = 0

    def of_class(self, table_class: TableClass) -> tuple[PlannedTable, ...]:
        return tuple(item for item in self.tables if item.spec.table_class == table_class)

    def rows(self, table_class: TableClass) -> int:
        return sum(item.pilot_rows for item in self.of_class(table_class))

    def estimated_bytes(self, table_class: TableClass | None = None) -> int:
        chosen = self.tables if table_class is None else self.of_class(table_class)
        return sum(item.estimated_bytes for item in chosen)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class PilotOutcome:
    plan: PilotPlan
    built: bool
    checks: tuple[Check, ...] = ()
    database_bytes: int | None = None
    # Only a build whose every check passed has a manifest.
    manifest: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        if not self.built:
            return not self.plan.problems
        return all(check.ok for check in self.checks)


def format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "kB", "MB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.2f} GB"


_CLASS_TITLES: dict[TableClass, str] = {
    "A": "Class A -- copied whole (rules, catalog batch, bookkeeping)",
    "B": "Class B -- the slice (rows of the slice's vehicles and TS records)",
    "C": "Class C -- left out (empty in the pilot)",
}


def format_plan(plan: PilotPlan) -> str:
    options = plan.options
    population = "registered only" if options.registered_only else "any registry status"
    if not plan.target_exists:
        target_state = "does not exist"
    elif plan.target_marker is None:
        target_state = "EXISTS and is not a pilot build"
    else:
        target_state = f"exists: pilot build, state {plan.target_marker.get('state')!r}"
    lines = [
        "Pilot database plan",
        f"  source database   {plan.source_database} (read-only)",
        f"  target database   {options.target_database} ({target_state})",
        (
            f"  slice             {plan.slice_vehicles:,} of {plan.eligible_vehicles:,} "
            f"vehicles (scope {options.scope}, {population})"
        ),
        f"  seed              {options.seed}",
        f"  TS records        {plan.slice_records:,}",
        (
            f"  30k sample        {plan.sample_in_slice:,} of {plan.sample_vehicles:,} "
            "match impact sample cars are in the slice "
            f"({'contained' if _sample_contained(plan) else 'NOT contained'})"
        ),
        (
            f"  catalog batch     {options.catalog_batch} "
            f"({plan.catalog_status or 'NOT IN THE SOURCE'})"
        ),
    ]
    copied_classes: tuple[TableClass, ...] = ("A", "B")
    for table_class in copied_classes:
        lines += ["", _CLASS_TITLES[table_class],
                  f"  {'table':<44}{'pilot rows':>12}{'source rows':>14}{'est. size':>12}"]
        for item in plan.of_class(table_class):
            name = item.spec.table if item.in_source else f"{item.spec.table} *"
            lines.append(
                f"  {name:<44}{item.pilot_rows:>12,}{item.source_rows:>14,}"
                f"{format_bytes(item.estimated_bytes):>12}"
            )
        lines.append(
            f"  {'total':<44}{plan.rows(table_class):>12,}{'':>14}"
            f"{format_bytes(plan.estimated_bytes(table_class)):>12}"
        )
    lines += ["", _CLASS_TITLES["C"], f"  {'table':<44}{'rows left behind':>18}"]
    for item in plan.of_class("C"):
        name = item.spec.table if item.in_source else f"{item.spec.table} *"
        lines.append(f"  {name:<44}{item.source_rows:>18,}")
    if any(not item.in_source for item in plan.tables):
        lines.append("  * not in the source")
    lines += [
        "",
        (
            f"Estimated pilot size: {format_bytes(plan.estimated_bytes())} "
            "(source bytes per row, indexes included)"
        ),
    ]
    if plan.warnings:
        lines += ["", "Warnings:", *(f"  - {warning}" for warning in plan.warnings)]
    if plan.problems:
        lines += ["", "Problems (the build refuses to start):",
                  *(f"  - {problem}" for problem in plan.problems)]
    return "\n".join(lines)


def _sample_contained(plan: PilotPlan) -> bool:
    return plan.sample_vehicles > 0 and plan.sample_in_slice == plan.sample_vehicles


def format_summary(outcome: PilotOutcome) -> str:
    failed = [check for check in outcome.checks if not check.ok]
    lines = ["Verification"]
    lines += [
        f"  {'ok  ' if check.ok else 'FAIL'}  {check.name}: {check.detail}"
        for check in outcome.checks
    ]
    plan = outcome.plan
    lines += [
        "",
        f"Pilot database {plan.options.target_database}: "
        f"{plan.slice_vehicles:,} vehicles, {plan.slice_records:,} TS records, "
        f"class A {plan.rows('A'):,} rows, class B {plan.rows('B'):,} rows"
        + (f", {format_bytes(outcome.database_bytes)} on disk"
           if outcome.database_bytes is not None else ""),
        f"{len(outcome.checks) - len(failed)} of {len(outcome.checks)} checks passed"
        + ("" if not failed else f"; {len(failed)} FAILED -- do not ship this database"),
    ]
    return "\n".join(lines)


def describe_database_error(error: psycopg.Error) -> str:
    """Name a database error without its message: messages can quote row values."""

    diag = error.diag
    parts = [type(error).__name__]
    if diag.sqlstate:
        parts.append(f"sqlstate {diag.sqlstate}")
    table = ".".join(part for part in (diag.schema_name, diag.table_name) if part)
    if table:
        parts.append(f"table {table}")
    if diag.column_name:
        parts.append(f"column {diag.column_name}")
    if diag.constraint_name:
        parts.append(f"constraint {diag.constraint_name}")
    return ", ".join(parts)


# --------------------------------------------------------------------------- connections


def _conninfo(url: str, database: str) -> str:
    params = conninfo_to_dict(url)
    params["dbname"] = database
    params.pop("options", None)
    return make_conninfo("", **params)


@contextmanager
def open_source(source_url: str) -> Iterator[Connection[Any]]:
    """The source, read-only twice over: session default and every transaction."""

    connection = psycopg.connect(
        source_url, options=f"-c default_transaction_read_only=on {_SESSION_OPTIONS}"
    )
    try:
        connection.read_only = True
        connection.isolation_level = IsolationLevel.REPEATABLE_READ
        # Unprepared statements are planned with their key arrays as constants,
        # which is what makes a large `= ANY(...)` a hash lookup.
        connection.prepare_threshold = None
        yield connection
    finally:
        connection.rollback()
        connection.close()


@contextmanager
def _open(url: str, database: str, *, autocommit: bool = False) -> Iterator[Connection[Any]]:
    connection = psycopg.connect(
        _conninfo(url, database), options=_SESSION_OPTIONS, autocommit=autocommit
    )
    try:
        yield connection
    finally:
        connection.close()


def qualified(table: str) -> sql.Identifier:
    schema, name = table.split(".", 1)
    return sql.Identifier(schema, name)


def _scalar(connection: Connection[Any], query: sql.Composed | str,
            params: Sequence[Any] | None = None) -> Any:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()
    return row[0] if row else None


def _column(connection: Connection[Any], query: str, params: Sequence[Any]) -> list[Any]:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return [row[0] for row in cursor.fetchall()]


def _tables(connection: Connection[Any]) -> dict[str, tuple[int, int]]:
    """Every table of the pilot schemas: (planner row estimate, bytes with indexes)."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT n.nspname || '.' || c.relname, c.reltuples::bigint, "
            "pg_total_relation_size(c.oid) "
            "FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "WHERE c.relkind IN ('r', 'p') AND n.nspname = ANY(%s)",
            (list(PILOT_SCHEMAS),),
        )
        return {str(name): (int(rows), int(size)) for name, rows, size in cursor.fetchall()}


def _columns(connection: Connection[Any], table: str) -> tuple[str, ...]:
    return tuple(
        str(name) for name in _column(
            connection,
            "SELECT a.attname FROM pg_attribute AS a WHERE a.attrelid = %s::regclass "
            "AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = '' ORDER BY a.attnum",
            (table,),
        )
    )


def _target_state(connection: Connection[Any], target: str) -> tuple[bool, dict[str, Any] | None]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = %s",
            (target,),
        )
        row = cursor.fetchone()
    if row is None:
        return False, None
    return True, parse_marker(row[0])


# --------------------------------------------------------------------------- plan


@dataclass(frozen=True)
class SliceKeys:
    """The slice, as the key sets its tables are cut by. Team-only: never printed."""

    vehicles: tuple[str, ...]
    records: tuple[int, ...]
    chunks: tuple[Any, ...]
    evidence: tuple[int, ...]

    def of(self, key_set: KeySet | None) -> tuple[Any, ...] | None:
        if key_set is None:
            return None
        by_name: dict[KeySet, tuple[Any, ...]] = {
            "vehicles": self.vehicles,
            "records": self.records,
            "chunks": self.chunks,
            "evidence": self.evidence,
        }
        return by_name[key_set]


def _count(connection: Connection[Any], spec: TableSpec, options: PilotOptions,
           keys: SliceKeys, chunk_size: int) -> int:
    total = 0
    for predicate, params in selections(spec, options, keys.of(spec.key_set), chunk_size):
        total += int(_scalar(
            connection,
            sql.SQL("SELECT count(*) FROM {} WHERE {}").format(
                qualified(spec.table), sql.SQL(predicate)
            ),
            params,
        ))
    return total


def _slice_keys(source: Connection[Any], options: PilotOptions, present: set[str],
                chunk_size: int) -> SliceKeys:
    vehicles = sorted(
        str(value) for value in _column(
            source, slice_query(registered_only=options.registered_only),
            (options.scope, options.seed, options.size),
        )
    )
    records: set[int] = set()
    for chunk in chunked(vehicles, chunk_size):
        # Filter the source system before reading the key: an AIS link's key is a VIN.
        linked = _column(
            source,
            "SELECT source_record_key FROM core.vehicle_source_links "
            "WHERE source_system = %s AND vehicle_id = ANY(%s::text[])",
            (TS_SOURCE_SYSTEM, list(chunk)),
        )
        if not all(str(key).isdecimal() for key in linked):
            # Said without the key: it may be a VIN.
            raise PilotBuildError(
                "a Transportstyrelsen source link of a slice vehicle has a key that is not "
                "a record id"
            )
        records.update(int(key) for key in linked)
        records.update(int(key) for key in _column(
            source,
            "SELECT ts_record_id FROM core.vehicles "
            "WHERE vehicle_id = ANY(%s::text[]) AND ts_record_id IS NOT NULL",
            (list(chunk),),
        ))
    record_ids = sorted(records)
    chunks: set[Any] = set()
    evidence: set[int] = set()
    if "core.match_chunk_proposals" in present:
        chunks.update(_column(source, "SELECT chunk_id FROM core.match_chunk_proposals", ()))
    if record_ids:
        if "core.match_chunk_members" in present:
            chunks.update(_column(
                source,
                "SELECT DISTINCT chunk_id FROM core.match_chunk_members "
                "WHERE source_record_id = ANY(%s::bigint[])",
                (record_ids,),
            ))
        if "core.match_chunk_samples" in present:
            with source.cursor() as cursor:
                cursor.execute(
                    "SELECT chunk_id, evidence_id FROM core.match_chunk_samples "
                    "WHERE source_record_id = ANY(%s::bigint[])",
                    (record_ids,),
                )
                for chunk_id, evidence_id in cursor.fetchall():
                    chunks.add(chunk_id)
                    evidence.add(int(evidence_id))
    return SliceKeys(
        vehicles=tuple(vehicles),
        records=tuple(record_ids),
        chunks=tuple(sorted(chunks, key=str)),
        evidence=tuple(sorted(evidence)),
    )


def _closed_reviews_outside_slice(
    source: Connection[Any], options: PilotOptions, keys: SliceKeys, chunk_size: int
) -> int:
    """Review items a reviewer resolved or rejected whose car is not in the slice."""

    closed = "status IN ('resolved', 'rejected')"
    total = int(_scalar(
        source, f"SELECT count(*) FROM core.review_queue WHERE {_RAW_ONLY} AND {closed}"
    ))
    if not total:
        return 0
    kept = 0
    spec = spec_by_table()["core.review_queue"]
    for predicate, params in selections(spec, options, keys.records, chunk_size):
        kept += int(_scalar(
            source, f"SELECT count(*) FROM core.review_queue WHERE {predicate} AND {closed}",
            params,
        ))
    return total - kept


def plan_pilot(
    source: Connection[Any], options: PilotOptions, *, chunk_size: int = DEFAULT_CHUNK_SIZE
) -> tuple[PilotPlan, SliceKeys]:
    """Read the source and say what the pilot will hold. Writes nothing."""

    if options.size < 1:
        raise PilotBuildError("--size must be at least 1")
    source_tables = _tables(source)
    present = set(source_tables)
    problems: list[str] = []
    warnings: list[str] = []
    for required in ("core.vehicles", "core.vehicle_source_links", "core.tecdoc_source_batches"):
        if required not in present:
            raise PilotBuildError(f"the source database has no {required}; it is not a full build")

    catalog_status = _scalar(
        source, "SELECT status FROM core.tecdoc_source_batches WHERE batch_id = %s",
        (options.catalog_batch,),
    )
    if catalog_status is None:
        problems.append(f"catalog batch {options.catalog_batch} is not in the source")
    elif catalog_status != "completed":
        problems.append(
            f"catalog batch {options.catalog_batch} has status {catalog_status!r}, not 'completed'"
        )

    eligible = int(_scalar(
        source,
        f"SELECT count(*) FROM core.vehicles WHERE {eligible_predicate(options.registered_only)}",
        (options.scope,),
    ))
    keys = _slice_keys(source, options, present, chunk_size)
    if len(keys.vehicles) < options.size:
        problems.append(
            f"only {eligible:,} vehicles are eligible; a slice of {options.size:,} cannot be cut"
        )

    planned: list[PlannedTable] = []
    for spec in PILOT_TABLES:
        if spec.table not in present:
            planned.append(PlannedTable(spec, False, 0, 0, 0))
            continue
        estimated_rows, table_bytes = source_tables[spec.table]
        if spec.table_class == "C":
            left_behind = int(_scalar(
                source, sql.SQL("SELECT count(*) FROM {}").format(qualified(spec.table))
            ))
            if left_behind and not spec.may_hold_rows:
                warnings.append(
                    f"{spec.table} holds {left_behind:,} rows in the source; class C leaves "
                    "them behind"
                )
            planned.append(PlannedTable(spec, True, 0, left_behind, 0))
            continue
        pilot_rows = _count(source, spec, options, keys, chunk_size)
        # A table never analyzed has no estimate; a whole copy knows its exact count.
        table_rows = estimated_rows if estimated_rows >= 0 else int(_scalar(
            source, sql.SQL("SELECT count(*) FROM {}").format(qualified(spec.table))
        ))
        table_rows = max(table_rows, pilot_rows)
        planned.append(PlannedTable(
            spec, True, pilot_rows, table_rows,
            estimate_bytes(pilot_rows, table_rows=table_rows, table_bytes=table_bytes),
        ))

    classified = spec_by_table()
    for table in sorted(present - set(classified)):
        rows = int(_scalar(source, sql.SQL("SELECT count(*) FROM {}").format(qualified(table))))
        if rows:
            problems.append(
                f"{table} holds {rows:,} rows and has no class; classify it in "
                "ingestion/pilot_database.py before cutting a pilot"
            )
    if VEHICLE_KTYPE_CHOICES_TABLE in present:
        decided = set(_column(
            source, f"SELECT DISTINCT vehicle_id FROM {VEHICLE_KTYPE_CHOICES_TABLE}", ()
        ))
        outside = len(decided - set(keys.vehicles))
        if outside:
            problems.append(
                f"{outside:,} cars with a person's KType choice are outside the slice; their "
                "choices would be left behind. Pinning decided cars into the slice is not "
                "built yet (docs/vehicle-ktype-choices.md)"
            )
    if "core.review_queue" in present:
        other_reviews = int(_scalar(
            source, f"SELECT count(*) FROM core.review_queue WHERE NOT ({_RAW_ONLY})"
        ))
        if other_reviews:
            warnings.append(
                f"core.review_queue holds {other_reviews:,} items that are not on TS records; "
                "they are left behind"
            )
        closed_outside = _closed_reviews_outside_slice(source, options, keys, chunk_size)
        if closed_outside:
            warnings.append(
                f"{closed_outside:,} review items a reviewer closed are on TS records outside "
                "the slice; they are left behind with their cars"
            )

    target_exists, target_marker = _target_state(source, options.target_database)
    if target_exists and target_marker is None:
        problems.append(
            f"database {options.target_database!r} exists and is not a pilot build; it is "
            "never dropped. Choose another --target-database."
        )
    sample = _column(
        source, slice_query(registered_only=True),
        (DEFAULT_SCOPE, SAMPLE_SEED, MATCH_IMPACT_SAMPLE_SIZE),
    )
    in_slice = set(keys.vehicles)
    plan = PilotPlan(
        options=options,
        source_database=str(source.info.dbname),
        target_exists=target_exists,
        target_marker=target_marker,
        eligible_vehicles=eligible,
        slice_vehicles=len(keys.vehicles),
        slice_records=len(keys.records),
        catalog_status=None if catalog_status is None else str(catalog_status),
        tables=tuple(planned),
        problems=tuple(problems),
        warnings=tuple(warnings),
        sample_vehicles=len(sample),
        sample_in_slice=sum(1 for vehicle in sample if str(vehicle) in in_slice),
    )
    return plan, keys


# --------------------------------------------------------------------------- build


def create_target_database(
    admin: Connection[Any], options: PilotOptions, *, source_database: str, replace: bool,
    started_at: str,
) -> None:
    """Create the empty target, marked as a pilot build so only such a database is replaced."""

    target = options.target_database
    exists, marker = _target_state(admin, target)
    refusal = target_refusal(target, exists=exists, marker=marker, replace=replace)
    if refusal is not None:
        raise PilotBuildError(refusal)
    if exists:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(target)))
    with admin.cursor() as cursor:
        cursor.execute(
            "SELECT pg_encoding_to_char(encoding), datcollate, datctype "
            "FROM pg_database WHERE datname = %s",
            (source_database,),
        )
        row = cursor.fetchone()
    if row is None:
        raise PilotBuildError(f"source database {source_database!r} is not on this server")
    encoding, collate, ctype = row
    admin.execute(
        sql.SQL(
            "CREATE DATABASE {} TEMPLATE template0 ENCODING {} LC_COLLATE {} LC_CTYPE {}"
        ).format(sql.Identifier(target), sql.Literal(encoding), sql.Literal(collate),
                 sql.Literal(ctype))
    )
    _write_marker(admin, target, build_marker(
        options, source_database=source_database, state="building", started_at=started_at
    ))


def _write_marker(admin: Connection[Any], target: str, marker: str) -> None:
    admin.execute(
        sql.SQL("COMMENT ON DATABASE {} IS {}").format(sql.Identifier(target), sql.Literal(marker))
    )


def shared_columns(
    source: Connection[Any], target: Connection[Any], plan: PilotPlan
) -> dict[str, tuple[str, ...]]:
    """Columns to copy per table, in the target's order; refuses on schema drift.

    The full build grew by migrations over time, so its column order can differ
    from a fresh schema. Copying by name makes the order irrelevant; a column that
    exists on one side only is a drift the build must not paper over.
    """

    drift: list[str] = []
    columns: dict[str, tuple[str, ...]] = {}
    target_tables = set(_tables(target))
    unclassified = sorted(target_tables - set(spec_by_table()))
    drift.extend(f"{table} is created by the migrations but has no class"
                 for table in unclassified)
    for item in plan.tables:
        table = item.spec.table
        if item.spec.table_class == "C" or not item.in_source:
            continue
        if table not in target_tables:
            drift.append(f"{table} is in the source but no migration creates it")
            continue
        in_source, in_target = _columns(source, table), _columns(target, table)
        if set(in_source) != set(in_target):
            only_source = sorted(set(in_source) - set(in_target))
            only_target = sorted(set(in_target) - set(in_source))
            drift.append(
                f"{table}: columns only in the source {only_source}, only from the "
                f"migrations {only_target}"
            )
        columns[table] = in_target
    if drift:
        raise PilotBuildError("the source schema and the migrations disagree: " + "; ".join(drift))
    return columns


def _copy_table(
    source: Connection[Any], target: Connection[Any], spec: TableSpec, options: PilotOptions,
    keys: SliceKeys, columns: tuple[str, ...], chunk_size: int,
) -> int:
    """Stream one table's pilot rows from the source into the target; returns the row count."""

    select_list = sql.SQL(", ").join(
        sql.SQL("false AS {}").format(sql.Identifier(name)) if name == spec.seal_column
        else sql.Identifier(name)
        for name in columns
    )
    into = sql.SQL("COPY {} ({}) FROM STDIN").format(
        qualified(spec.table), sql.SQL(", ").join(map(sql.Identifier, columns))
    )
    try:
        with target.cursor() as writer, source.cursor() as reader:
            with writer.copy(into) as sink:
                for predicate, params in selections(
                    spec, options, keys.of(spec.key_set), chunk_size
                ):
                    out = sql.SQL("COPY (SELECT {} FROM {} WHERE {}) TO STDOUT").format(
                        select_list, qualified(spec.table), sql.SQL(predicate)
                    )
                    with reader.copy(out, params) as feed:
                        for block in feed:
                            sink.write(block)
            copied = writer.rowcount
        target.commit()
    except psycopg.Error as error:
        target.rollback()
        raise PilotBuildError(
            f"copying {spec.table} failed: {describe_database_error(error)}"
        ) from None
    return max(copied, 0)


def _seal_versions(source: Connection[Any], target: Connection[Any], spec: TableSpec) -> None:
    if spec.seal_column is None or spec.seal_key is None:
        return
    table, flag, key = qualified(spec.table), sql.Identifier(spec.seal_column), sql.Identifier(
        spec.seal_key
    )
    with source.cursor() as cursor:
        cursor.execute(sql.SQL("SELECT {} FROM {} WHERE {}").format(key, table, flag))
        sealed = [row[0] for row in cursor.fetchall()]
    if sealed:
        target.execute(
            sql.SQL("UPDATE {} SET {} = true WHERE {} = ANY(%s)").format(table, flag, key),
            (sealed,),
        )
    target.commit()


def recompute_chunk_counters(target: Connection[Any]) -> None:
    """Make chunk and build counters describe the copied members, not the full build.

    The same arithmetic as `match_chunks._finalize_build`, without touching status
    or timestamps, and a chunk kept for its proposal alone counts zero members.
    """

    target.execute(
        """
        UPDATE core.match_chunks AS chunks
        SET member_count = coalesce(counted.member_count, 0),
            reason_profile = coalesce(profiled.reason_profile, '{}'::jsonb)
        FROM core.match_chunks AS base
        LEFT JOIN (
            SELECT chunk_id, count(*) AS member_count
            FROM core.match_chunk_members GROUP BY chunk_id
        ) AS counted USING (chunk_id)
        LEFT JOIN (
            SELECT chunk_id, jsonb_object_agg(reason, occurrences) AS reason_profile
            FROM (
                SELECT members.chunk_id, reason, count(*) AS occurrences
                FROM core.match_chunk_members AS members,
                     unnest(members.review_reasons) AS reason
                GROUP BY members.chunk_id, reason
            ) AS reason_counts
            GROUP BY chunk_id
        ) AS profiled USING (chunk_id)
        WHERE chunks.chunk_id = base.chunk_id
        """
    )
    target.execute(
        """
        UPDATE core.match_chunk_builds AS builds
        SET row_count = totals.row_count, chunk_count = totals.chunk_count
        FROM (
            SELECT b.build_id, coalesce(sum(c.member_count), 0) AS row_count,
                   count(c.chunk_id) AS chunk_count
            FROM core.match_chunk_builds AS b
            LEFT JOIN core.match_chunks AS c USING (build_id)
            GROUP BY b.build_id
        ) AS totals
        WHERE builds.build_id = totals.build_id
        """
    )
    target.commit()


@dataclass(frozen=True)
class SequenceState:
    sequence: str
    table: str
    column: str
    source_last_value: int | None


_OWNED_SEQUENCES = """
    SELECT seq_ns.nspname, seq.relname, tab_ns.nspname || '.' || tab.relname, att.attname
    FROM pg_class AS seq
    JOIN pg_namespace AS seq_ns ON seq_ns.oid = seq.relnamespace
    JOIN pg_depend AS dep
      ON dep.classid = 'pg_class'::regclass AND dep.objid = seq.oid
     AND dep.refclassid = 'pg_class'::regclass AND dep.deptype IN ('a', 'i')
    JOIN pg_class AS tab ON tab.oid = dep.refobjid
    JOIN pg_namespace AS tab_ns ON tab_ns.oid = tab.relnamespace
    JOIN pg_attribute AS att ON att.attrelid = tab.oid AND att.attnum = dep.refobjsubid
    WHERE seq.relkind = 'S' AND seq_ns.nspname = ANY(%s)
    ORDER BY 1, 2
"""


def _sequence_values(connection: Connection[Any]) -> dict[str, int | None]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT schemaname || '.' || sequencename, last_value FROM pg_sequences "
            "WHERE schemaname = ANY(%s)",
            (list(PILOT_SCHEMAS),),
        )
        return {str(name): None if value is None else int(value)
                for name, value in cursor.fetchall()}


def reset_sequences(source: Connection[Any], target: Connection[Any]) -> tuple[SequenceState, ...]:
    """Move every sequence past the full build's ids, not just past the slice's.

    Rows keep their ids and the slice's ids are interleaved with the rest of the
    full build's. Starting after the full build's last value means an id minted
    on live never collides with a row loaded later from the same full build.
    """

    source_values = _sequence_values(source)
    states: list[SequenceState] = []
    with target.cursor() as cursor:
        cursor.execute(_OWNED_SEQUENCES, (list(PILOT_SCHEMAS),))
        owned = cursor.fetchall()
        for schema, name, table, column in owned:
            sequence = f"{schema}.{name}"
            source_last = source_values.get(sequence)
            cursor.execute(
                sql.SQL("SELECT max({}) FROM {}").format(sql.Identifier(column), qualified(table))
            )
            row = cursor.fetchone()
            highest = max(source_last or 0, int(row[0]) if row and row[0] is not None else 0)
            if highest > 0:
                cursor.execute("SELECT setval(%s::regclass, %s, true)", (sequence, highest))
            states.append(SequenceState(sequence, str(table), str(column), source_last))
    target.commit()
    return tuple(states)


def load_pilot(
    source: Connection[Any], target: Connection[Any], plan: PilotPlan, keys: SliceKeys,
    columns: dict[str, tuple[str, ...]], *, chunk_size: int, report: Report,
) -> tuple[SequenceState, ...]:
    """Copy class A and B into a migrated, empty target, then finish it."""

    for item in plan.tables:
        spec = item.spec
        if spec.table not in columns:
            continue
        started = time.monotonic()
        copied = _copy_table(source, target, spec, plan.options, keys, columns[spec.table],
                             chunk_size)
        report(f"  copied {spec.table}: {copied:,} rows in {time.monotonic() - started:.1f}s")
    for item in plan.tables:
        if item.spec.table in columns:
            _seal_versions(source, target, item.spec)
    recompute_chunk_counters(target)
    sequences = reset_sequences(source, target)
    target.execute("ANALYZE")
    target.commit()
    return sequences


# --------------------------------------------------------------------------- verification


def _fingerprint(
    connection: Connection[Any], table: str, columns: Sequence[str], predicate: str,
    params: Sequence[Any],
) -> Fingerprint:
    row_text = sql.SQL("md5(ROW({})::text)").format(
        sql.SQL(", ").join(map(sql.Identifier, columns))
    )
    query = sql.SQL(
        "SELECT count(*), "
        "coalesce(sum(('x' || substr(h, 1, 16))::bit(64)::bigint::numeric), 0), "
        "coalesce(sum(('x' || substr(h, 17, 16))::bit(64)::bigint::numeric), 0) "
        "FROM (SELECT {} AS h FROM {} WHERE {}) AS hashed"
    ).format(row_text, qualified(table), sql.SQL(predicate))
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()
    if row is None:
        return Fingerprint()
    return Fingerprint(int(row[0]), int(row[1]), int(row[2]))


def _table_checks(
    source: Connection[Any], target: Connection[Any], plan: PilotPlan, keys: SliceKeys,
    columns: dict[str, tuple[str, ...]], chunk_size: int, measured: Measured | None = None,
) -> Iterator[Check]:
    target_tables = set(_tables(target))
    for item in plan.tables:
        spec = item.spec
        if spec.table not in target_tables:
            continue
        if spec.table not in columns:
            rows = int(_scalar(
                target, sql.SQL("SELECT count(*) FROM {}").format(qualified(spec.table))
            ))
            if measured is not None:
                measured[spec.table] = ((), Fingerprint(rows))
            yield Check(f"{spec.table} (class {spec.table_class})", rows == 0,
                        "empty" if rows == 0 else f"{rows:,} rows, expected none")
            continue
        compared = sorted(set(columns[spec.table]) - set(spec.recomputed))
        expected = Fingerprint()
        for predicate, params in selections(spec, plan.options, keys.of(spec.key_set),
                                            chunk_size):
            expected += _fingerprint(source, spec.table, compared, predicate, params)
        found = _fingerprint(target, spec.table, compared, "TRUE", ())
        if measured is not None:
            measured[spec.table] = (tuple(compared), found)
        what = "whole table" if spec.table_class == "A" else "slice rows"
        if found == expected:
            detail = f"{found.rows:,} rows, content equals the source ({what})"
        elif found.rows != expected.rows:
            detail = f"{found.rows:,} rows, the source has {expected.rows:,} ({what})"
        else:
            detail = f"{found.rows:,} rows, but their content differs from the source"
        yield Check(f"{spec.table} (class {spec.table_class})", found == expected, detail)


def _slice_checks(target: Connection[Any], options: PilotOptions) -> Iterator[Check]:
    vehicles = int(_scalar(target, "SELECT count(*) FROM core.vehicles"))
    yield Check("slice size", vehicles == options.size,
                f"{vehicles:,} vehicles, asked for {options.size:,}")
    outside = int(_scalar(
        target,
        "SELECT count(*) FROM core.vehicles "
        f"WHERE NOT coalesce({eligible_predicate(options.registered_only)}, false)",
        (options.scope,),
    ))
    population = f"scope {options.scope}" + (", registered" if options.registered_only else "")
    yield Check("slice population", outside == 0,
                f"all {population}" if outside == 0 else f"{outside:,} vehicles are not {population}")


def _catalog_checks(
    source: Connection[Any], target: Connection[Any], options: PilotOptions
) -> Iterator[Check]:
    def counts(connection: Connection[Any], table: str, column: str,
               batch: str | None) -> dict[str, int]:
        where = sql.SQL("WHERE batch_id = %s") if batch is not None else sql.SQL("")
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("SELECT {}, count(*) FROM {} {} GROUP BY 1").format(
                    sql.Identifier(column), qualified(table), where
                ),
                (batch,) if batch is not None else None,
            )
            return {str(name): int(total) for name, total in cursor.fetchall()}

    for table, column, noun in (
        ("core.tecdoc_canonical_candidates", "entity_type", "catalog entities"),
        ("core.tecdoc_candidate_relationships", "relationship_type", "catalog relationships"),
    ):
        expected = counts(source, table, column, options.catalog_batch)
        # The target is counted without a batch filter: any other batch is a mismatch.
        found = counts(target, table, column, None)
        shown = ", ".join(f"{name} {total:,}" for name, total in sorted(found.items())) or "none"
        yield Check(f"{noun} of the pinned batch", found == expected,
                    shown if found == expected else f"{shown}; the source has {expected}")
    with target.cursor() as cursor:
        cursor.execute("SELECT batch_id, status FROM core.tecdoc_source_batches")
        batches = cursor.fetchall()
    only_pin = batches == [(options.catalog_batch, "completed")]
    yield Check("catalog batch", only_pin,
                f"only {options.catalog_batch}, completed" if only_pin
                else f"expected one completed batch, found {len(batches)}")


_FOREIGN_KEYS = """
    SELECT con.conname,
           child_ns.nspname || '.' || child.relname,
           parent_ns.nspname || '.' || parent.relname,
           (SELECT array_agg(att.attname::text ORDER BY k.ord)
            FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
            JOIN pg_attribute AS att
              ON att.attrelid = con.conrelid AND att.attnum = k.attnum),
           (SELECT array_agg(att.attname::text ORDER BY k.ord)
            FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord)
            JOIN pg_attribute AS att
              ON att.attrelid = con.confrelid AND att.attnum = k.attnum)
    FROM pg_constraint AS con
    JOIN pg_class AS child ON child.oid = con.conrelid
    JOIN pg_namespace AS child_ns ON child_ns.oid = child.relnamespace
    JOIN pg_class AS parent ON parent.oid = con.confrelid
    JOIN pg_namespace AS parent_ns ON parent_ns.oid = parent.relnamespace
    WHERE con.contype = 'f' AND child_ns.nspname = ANY(%s)
    ORDER BY 2, 1
"""


def foreign_keys(connection: Connection[Any]) -> list[tuple[str, str, str, list[str], list[str]]]:
    """Declared foreign keys: (name, child table, parent table, child columns, parent columns)."""

    with connection.cursor() as cursor:
        cursor.execute(_FOREIGN_KEYS, (list(PILOT_SCHEMAS),))
        return [
            (str(name), str(child), str(parent), list(child_columns), list(parent_columns))
            for name, child, parent, child_columns, parent_columns in cursor.fetchall()
        ]


def _reference_checks(target: Connection[Any]) -> Iterator[Check]:
    dangling: list[str] = []
    keys = foreign_keys(target)
    for name, child, parent, child_columns, parent_columns in keys:
        present = sql.SQL(" AND ").join(
            sql.SQL("c.{} IS NOT NULL").format(sql.Identifier(column)) for column in child_columns
        )
        joined = sql.SQL(" AND ").join(
            sql.SQL("p.{} = c.{}").format(sql.Identifier(parent_column), sql.Identifier(column))
            for column, parent_column in zip(child_columns, parent_columns, strict=True)
        )
        orphans = int(_scalar(
            target,
            sql.SQL(
                "SELECT count(*) FROM {} AS c WHERE {} "
                "AND NOT EXISTS (SELECT 1 FROM {} AS p WHERE {})"
            ).format(qualified(child), present, qualified(parent), joined),
        ))
        if orphans:
            dangling.append(f"{name}: {orphans:,}")
    yield Check("foreign keys", not dangling,
                f"{len(keys)} constraints, no row points to a missing row" if not dangling
                else "rows without a parent -- " + "; ".join(dangling))
    for name, query in REFERENCE_CHECKS:
        offenders = int(_scalar(target, query))
        yield Check(name, offenders == 0,
                    "none dangling" if offenders == 0 else f"{offenders:,} rows dangle")


def _sequence_checks(
    target: Connection[Any], sequences: Sequence[SequenceState]
) -> Iterator[Check]:
    values = _sequence_values(target)
    behind: list[str] = []
    for state in sequences:
        current = values.get(state.sequence)
        highest = _scalar(
            target,
            sql.SQL("SELECT max({}) FROM {}").format(
                sql.Identifier(state.column), qualified(state.table)
            ),
        )
        needed = max(state.source_last_value or 0, int(highest) if highest is not None else 0)
        if needed and (current is None or current < needed):
            behind.append(state.sequence)
    yield Check("sequences", not behind,
                f"{len(sequences)} sequences at or past the full build's last ids"
                if not behind else "behind their ids -- " + ", ".join(behind))


def verify_pilot(
    source: Connection[Any], target: Connection[Any], plan: PilotPlan, keys: SliceKeys,
    columns: dict[str, tuple[str, ...]], sequences: Sequence[SequenceState], *, chunk_size: int,
    measured: Measured | None = None,
) -> tuple[Check, ...]:
    """Compare the finished target with the source; every check must hold.

    `measured`, when given, receives each table's compared columns and the count
    and checksum found in the target: what the manifest records.
    """

    checks = [
        *_slice_checks(target, plan.options),
        *_table_checks(source, target, plan, keys, columns, chunk_size, measured),
        *_catalog_checks(source, target, plan.options),
        *_reference_checks(target),
        *_sequence_checks(target, sequences),
    ]
    target.rollback()
    return tuple(checks)


# --------------------------------------------------------------------------- manifest


def build_manifest(
    plan: PilotPlan, measured: Measured, *, source_snapshot_at: str, built_at: str
) -> dict[str, Any]:
    """What a verified pilot holds, in a form any copy of it can be checked against."""

    classes = {item.spec.table: item.spec.table_class for item in plan.tables}
    options = plan.options
    return {
        MANIFEST_KEY: MANIFEST_VERSION,
        "seed": options.seed,
        "size": options.size,
        "scope": options.scope,
        "registered_only": options.registered_only,
        "catalog_batch": options.catalog_batch,
        "source_database": plan.source_database,
        "source_snapshot_at": source_snapshot_at,
        "built_at": built_at,
        "slice_vehicles": plan.slice_vehicles,
        "ts_records": plan.slice_records,
        "tables": {
            table: {
                "class": classes[table],
                "rows": found.rows,
                "checksum": found.checksum,
                "columns": list(compared),
            }
            for table, (compared, found) in sorted(measured.items())
        },
    }


def check_manifest(manifest: Any) -> dict[str, Any]:
    """Refuse anything that is not a pilot manifest; table and column names become SQL."""

    if not isinstance(manifest, dict) or manifest.get(MANIFEST_KEY) != MANIFEST_VERSION:
        raise PilotBuildError(f"not a pilot manifest (version {MANIFEST_VERSION})")
    tables = manifest.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise PilotBuildError("the manifest lists no tables")
    for table, entry in tables.items():
        if not isinstance(table, str) or not _MANIFEST_TABLE_NAME.fullmatch(table):
            raise PilotBuildError(f"the manifest names a table outside the pilot: {table!r}")
        valid = (
            isinstance(entry, dict)
            and isinstance(entry.get("rows"), int)
            and isinstance(entry.get("checksum"), str)
            and isinstance(entry.get("class"), str)
            and isinstance(entry.get("columns"), list)
            and all(isinstance(column, str) for column in entry["columns"])
        )
        if not valid:
            raise PilotBuildError(f"the manifest entry of {table} is incomplete")
    return manifest


def read_manifest(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise PilotBuildError(f"cannot read the manifest {path}: {error.strerror}") from None
    except ValueError:
        raise PilotBuildError(f"the manifest {path} is not JSON") from None
    return check_manifest(parsed)


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as error:
        raise PilotBuildError(f"cannot write the manifest {path}: {error.strerror}") from None


def store_manifest(target: Connection[Any], manifest: dict[str, Any]) -> None:
    """Keep a copy of the manifest in the pilot itself, where a dump carries it."""

    table = qualified(MANIFEST_TABLE)
    target.execute(sql.SQL(
        "CREATE TABLE {} (only_row boolean PRIMARY KEY DEFAULT true CHECK (only_row), "
        "manifest jsonb NOT NULL)"
    ).format(table))
    target.execute(
        sql.SQL("INSERT INTO {} (manifest) VALUES (%s::jsonb)").format(table),
        (json.dumps(manifest, sort_keys=True),),
    )
    target.commit()


def stored_manifest(connection: Connection[Any]) -> dict[str, Any] | None:
    if _scalar(connection, "SELECT to_regclass(%s)", (MANIFEST_TABLE,)) is None:
        return None
    stored = _scalar(
        connection, sql.SQL("SELECT manifest FROM {}").format(qualified(MANIFEST_TABLE))
    )
    return stored if isinstance(stored, dict) else None


def verify_against_manifest(
    connection: Connection[Any], manifest: dict[str, Any]
) -> tuple[Check, ...]:
    """Recount and re-checksum every table of the manifest in this database.

    The same query and the same columns as the build's own verification, so the
    numbers are equal exactly when the rows are.
    """

    present = set(_tables(connection))
    tables: dict[str, dict[str, Any]] = manifest["tables"]
    checks: list[Check] = []
    for table, entry in tables.items():
        name = f"{table} (class {entry['class']})"
        if table not in present:
            checks.append(Check(name, False, "the table is missing"))
            continue
        columns: list[str] = entry["columns"]
        missing = sorted(set(columns) - set(_columns(connection, table)))
        if missing:
            checks.append(Check(name, False, f"columns missing: {', '.join(missing)}"))
            continue
        if columns:
            found = _fingerprint(connection, table, columns, "TRUE", ())
        else:
            found = Fingerprint(int(_scalar(
                connection, sql.SQL("SELECT count(*) FROM {}").format(qualified(table))
            )))
        if found.rows != entry["rows"]:
            checks.append(Check(
                name, False, f"{found.rows:,} rows, the manifest has {entry['rows']:,}"
            ))
        elif found.checksum != entry["checksum"]:
            checks.append(Check(
                name, False, f"{found.rows:,} rows, but their content differs from the manifest"
            ))
        else:
            checks.append(Check(name, True, f"{found.rows:,} rows, content equals the manifest"))
    extra = [
        table for table in sorted(present - set(tables))
        if int(_scalar(connection, sql.SQL("SELECT count(*) FROM {}").format(qualified(table))))
    ]
    checks.append(Check(
        "tables outside the manifest", not extra,
        "none hold rows" if not extra else "hold rows -- " + ", ".join(extra),
    ))
    stored = stored_manifest(connection)
    if stored is None:
        detail = f"{MANIFEST_TABLE} is missing or empty"
    elif stored != manifest:
        detail = "differs from the manifest file: the file belongs to another build"
    else:
        detail = "equals the manifest file"
    checks.append(Check("manifest stored in the database", stored == manifest, detail))
    connection.rollback()
    return tuple(checks)


def verify_pilot_database(
    url: str, database: str, manifest: dict[str, Any]
) -> tuple[Check, ...]:
    """Check `database` on the server of `url` against a manifest; reads only."""

    with open_source(_conninfo(url, database)) as connection:
        return verify_against_manifest(connection, check_manifest(manifest))


def format_manifest_verification(
    database: str, manifest: dict[str, Any], checks: Sequence[Check]
) -> str:
    failed = [check for check in checks if not check.ok]
    lines = [
        f"Verification of database {database} against its manifest",
        (
            f"  manifest: seed {manifest.get('seed')}, size {manifest.get('size')}, catalog "
            f"batch {manifest.get('catalog_batch')}, source snapshot "
            f"{manifest.get('source_snapshot_at')}"
        ),
    ]
    lines += [
        f"  {'ok  ' if check.ok else 'FAIL'}  {check.name}: {check.detail}" for check in checks
    ]
    lines += [
        "",
        f"{len(checks) - len(failed)} of {len(checks)} checks passed"
        + ("" if not failed else
           f"; {len(failed)} FAILED -- this database is not the pilot that was built: "
           + ", ".join(check.name for check in failed)),
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- entry point


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def build_pilot_database(
    source_url: str,
    options: PilotOptions,
    *,
    commit: bool = False,
    replace: bool = False,
    maintenance_database: str = DEFAULT_MAINTENANCE_DATABASE,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    manifest_path: Path | None = None,
    report: Report = lambda _line: None,
) -> PilotOutcome:
    """Plan the pilot and, with `commit`, build and verify it.

    Without `commit` nothing is created: the plan is reported and returned.
    A build whose every check passed gets a manifest: stored in the target and,
    with `manifest_path`, written as a JSON file. A failed build gets neither,
    and its marker never says "verified".
    """

    with open_source(source_url) as source:
        source_database = str(source.info.dbname)
        check_target_name(options.target_database, source=source_database,
                          maintenance=maintenance_database)
        if commit:
            # Refuse before the minutes of planning, and again when creating.
            exists, marker = _target_state(source, options.target_database)
            refusal = target_refusal(options.target_database, exists=exists, marker=marker,
                                     replace=replace)
            if refusal is not None:
                raise PilotBuildError(refusal)
        plan, keys = plan_pilot(source, options, chunk_size=chunk_size)
        report(format_plan(plan))
        if not commit:
            return PilotOutcome(plan, built=False)
        if plan.problems:
            raise PilotBuildError("the plan has problems: " + "; ".join(plan.problems))

        started_at = _now()
        with _open(source_url, maintenance_database, autocommit=True) as admin:
            create_target_database(admin, options, source_database=source_database,
                                   replace=replace, started_at=started_at)
        report(f"\nCreated database {options.target_database}; running migrations")
        with _open(source_url, options.target_database) as target:
            run_pilot_migrations(target)
            columns = shared_columns(source, target, plan)
            target.rollback()
            report("Copying")
            sequences = load_pilot(source, target, plan, keys, columns,
                                   chunk_size=chunk_size, report=report)
            report("Verifying")
            measured: Measured = {}
            checks = verify_pilot(source, target, plan, keys, columns, sequences,
                                  chunk_size=chunk_size, measured=measured)
            database_bytes = int(_scalar(target, "SELECT pg_database_size(current_database())"))
            target.rollback()
            manifest: dict[str, Any] | None = None
            if all(check.ok for check in checks):
                snapshot = _scalar(source, "SELECT transaction_timestamp()")
                manifest = build_manifest(
                    plan, measured,
                    source_snapshot_at=snapshot.astimezone(UTC).isoformat(timespec="seconds"),
                    built_at=_now(),
                )
                store_manifest(target, manifest)
        outcome = PilotOutcome(plan, built=True, checks=checks, database_bytes=database_bytes,
                               manifest=manifest)
        with _open(source_url, maintenance_database, autocommit=True) as admin:
            _write_marker(admin, options.target_database, build_marker(
                options, source_database=source_database,
                state="verified" if outcome.ok else "verification-failed",
                started_at=started_at, finished_at=_now(),
            ))
        if manifest is not None and manifest_path is not None:
            write_manifest(manifest_path, manifest)
            report(f"Manifest written to {manifest_path}")
        return outcome
