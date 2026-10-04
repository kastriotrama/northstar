"""Idempotent PostgreSQL migrations for the stored outcome of matching, per car.

`core.vehicle_match_results` holds one row per NorthStar vehicle: where the
matcher ended for it, the KType it accepted or the KTypes it could not choose
between, and the short lists that say why. Statistics and car lists read this
table; the matcher runs only to fill or refresh it.

It is a cache, not a record of decisions: every row can be recomputed from the
car, the catalog and the matcher, so rows are overwritten in place and nothing
here is append-only. What a person decided lives in `core.vehicle_ktype_choices`
and is never written here.

`core.vehicle_match_runs` names each fill or refresh and the pins it ran with,
so a row says which catalog batch and matcher version produced it.

Statement names are a stable contract; every statement is idempotent, and the
verifier compares definitions rather than names.
"""

from __future__ import annotations

from psycopg import Connection

CORE_SCHEMA_NAME = "core"
_RESULTS_NAME = "vehicle_match_results"
_RUNS_NAME = "vehicle_match_runs"
VEHICLE_MATCH_RESULTS_TABLE = f"{CORE_SCHEMA_NAME}.{_RESULTS_NAME}"
VEHICLE_MATCH_RUNS_TABLE = f"{CORE_SCHEMA_NAME}.{_RUNS_NAME}"

#: Where the matcher ended for a car, as the overview counts it.
#: `resolved`: it accepted one KType. `several`: two or more KTypes conflict
#: with the car on nothing and none was accepted. `one_unconfirmed`: exactly one
#: such KType, not accepted. `none`: every candidate conflicts, or there is no
#: candidate. `not_matchable`: the car was stopped before matching.
MATCH_STATES: tuple[str, ...] = (
    "resolved",
    "several",
    "one_unconfirmed",
    "none",
    "not_matchable",
)
RUN_MODES: tuple[str, ...] = ("stale", "all", "sample", "vehicles")
RUN_STATUSES: tuple[str, ...] = ("running", "completed", "failed")

# (name, information_schema data_type, nullable)
_RESULT_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("vehicle_id", "text", False),
    ("state", "text", False),
    ("terminal", "text", False),
    ("ktype", "text", True),
    ("best_candidate_ktype", "text", True),
    ("confidence", "real", True),
    ("candidate_count", "smallint", False),
    ("candidate_ktypes", "ARRAY", False),
    ("candidate_confidences", "ARRAY", False),
    ("separating_fields", "ARRAY", False),
    ("missing_fields", "ARRAY", False),
    ("conflicting_fields", "ARRAY", False),
    ("reason_codes", "ARRAY", False),
    ("catalog_batch", "text", False),
    ("matcher_version", "text", False),
    ("input_hash", "text", False),
    ("run_id", "uuid", False),
    ("evaluated_at", "timestamp with time zone", False),
)
#: The columns a writer handles, in order.
RESULT_COLUMNS: tuple[str, ...] = tuple(name for name, _, _ in _RESULT_COLUMNS)

_RUN_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("run_id", "uuid", False),
    ("mode", "text", False),
    ("catalog_batch", "text", False),
    ("matcher_version", "text", False),
    ("rule_set_version", "text", True),
    ("status", "text", False),
    ("target", "integer", False),
    ("evaluated", "integer", False),
    ("unchanged", "integer", False),
    ("error", "text", True),
    ("started_at", "timestamp with time zone", False),
    ("finished_at", "timestamp with time zone", True),
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


# name -> (pg_constraint.contype, fragments pg_get_constraintdef must contain)
_RESULT_CONSTRAINTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "vehicle_match_results_pkey": ("p", ("PRIMARY KEY (vehicle_id)",)),
    "vehicle_match_results_vehicle_fkey": (
        "f",
        ("FOREIGN KEY (vehicle_id)", "REFERENCES core.vehicles(vehicle_id)", "ON DELETE RESTRICT"),
    ),
    "vehicle_match_results_run_fkey": (
        "f",
        ("FOREIGN KEY (run_id)", "REFERENCES core.vehicle_match_runs(run_id)"),
    ),
    "vehicle_match_results_state_values": ("c", ("state", *(f"'{s}'" for s in MATCH_STATES))),
    "vehicle_match_results_ktype_only_when_resolved": (
        "c",
        ("state = 'resolved'", "ktype IS NOT NULL", "btrim(ktype) <> ''"),
    ),
    "vehicle_match_results_candidates_counted": (
        "c",
        ("candidate_count", "cardinality(candidate_ktypes)", "cardinality(candidate_confidences)"),
    ),
    "vehicle_match_results_state_fits_candidates": (
        "c",
        ("'several'", "candidate_count >= 2", "'one_unconfirmed'", "candidate_count = 1",
         "'none'", "'not_matchable'", "candidate_count = 0"),
    ),
    "vehicle_match_results_confidence_range": (
        "c",
        ("confidence IS NULL", "confidence >= ", "confidence <= "),
    ),
    "vehicle_match_results_provenance_nonempty": (
        "c",
        ("btrim(terminal) <> ''", "btrim(catalog_batch) <> ''", "btrim(matcher_version) <> ''"),
    ),
    "vehicle_match_results_input_hash_format": ("c", ("input_hash ~ '^[0-9a-f]{64}$'",)),
}
_RUN_CONSTRAINTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "vehicle_match_runs_pkey": ("p", ("PRIMARY KEY (run_id)",)),
    "vehicle_match_runs_mode_values": ("c", ("mode", *(f"'{m}'" for m in RUN_MODES))),
    "vehicle_match_runs_status_values": ("c", ("status", *(f"'{s}'" for s in RUN_STATUSES))),
    "vehicle_match_runs_counts_nonnegative": (
        "c",
        ("target >= 0", "evaluated >= 0", "unchanged >= 0"),
    ),
    "vehicle_match_runs_finished_when_ended": (
        "c",
        ("status = 'running'", "finished_at IS NULL"),
    ),
    "vehicle_match_runs_provenance_nonempty": (
        "c",
        ("btrim(catalog_batch) <> ''", "btrim(matcher_version) <> ''"),
    ),
}
# name -> fragments pg_get_indexdef must contain
_RESULT_INDEXES: dict[str, tuple[str, ...]] = {
    "vehicle_match_results_state_idx": ("(state, vehicle_id)",),
    "vehicle_match_results_ktype_idx": ("(ktype)", "WHERE (ktype IS NOT NULL)"),
    "vehicle_match_results_candidates_idx": ("USING gin", "(candidate_ktypes)"),
}


class VehicleMatchResultSchemaContractError(RuntimeError):
    """A match result table exists but does not carry the definition it must."""


_CREATE_RUNS = f"""
CREATE TABLE IF NOT EXISTS {VEHICLE_MATCH_RUNS_TABLE} (
  run_id UUID NOT NULL,
  mode TEXT NOT NULL,
  catalog_batch TEXT NOT NULL,
  matcher_version TEXT NOT NULL,
  rule_set_version TEXT,
  status TEXT NOT NULL DEFAULT 'running',
  target INTEGER NOT NULL DEFAULT 0,
  evaluated INTEGER NOT NULL DEFAULT 0,
  unchanged INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  CONSTRAINT vehicle_match_runs_pkey PRIMARY KEY (run_id),
  CONSTRAINT vehicle_match_runs_mode_values CHECK (mode IN ({_quoted(RUN_MODES)})),
  CONSTRAINT vehicle_match_runs_status_values CHECK (status IN ({_quoted(RUN_STATUSES)})),
  CONSTRAINT vehicle_match_runs_counts_nonnegative
    CHECK (target >= 0 AND evaluated >= 0 AND unchanged >= 0),
  CONSTRAINT vehicle_match_runs_finished_when_ended
    CHECK ((status = 'running') = (finished_at IS NULL)),
  CONSTRAINT vehicle_match_runs_provenance_nonempty
    CHECK (btrim(catalog_batch) <> '' AND btrim(matcher_version) <> '')
)
"""

_CREATE_RESULTS = f"""
CREATE TABLE IF NOT EXISTS {VEHICLE_MATCH_RESULTS_TABLE} (
  vehicle_id TEXT NOT NULL,
  state TEXT NOT NULL,
  terminal TEXT NOT NULL,
  ktype TEXT,
  best_candidate_ktype TEXT,
  confidence REAL,
  candidate_count SMALLINT NOT NULL,
  candidate_ktypes TEXT[] NOT NULL,
  candidate_confidences REAL[] NOT NULL,
  separating_fields TEXT[] NOT NULL,
  missing_fields TEXT[] NOT NULL,
  conflicting_fields TEXT[] NOT NULL,
  reason_codes TEXT[] NOT NULL,
  catalog_batch TEXT NOT NULL,
  matcher_version TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  run_id UUID NOT NULL,
  evaluated_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT vehicle_match_results_pkey PRIMARY KEY (vehicle_id),
  CONSTRAINT vehicle_match_results_vehicle_fkey FOREIGN KEY (vehicle_id)
    REFERENCES core.vehicles (vehicle_id) ON DELETE RESTRICT,
  CONSTRAINT vehicle_match_results_run_fkey FOREIGN KEY (run_id)
    REFERENCES {VEHICLE_MATCH_RUNS_TABLE} (run_id),
  CONSTRAINT vehicle_match_results_state_values CHECK (state IN ({_quoted(MATCH_STATES)})),
  CONSTRAINT vehicle_match_results_ktype_only_when_resolved
    CHECK ((state = 'resolved') = (ktype IS NOT NULL)
           AND (ktype IS NULL OR btrim(ktype) <> '')),
  CONSTRAINT vehicle_match_results_candidates_counted
    CHECK (candidate_count = cardinality(candidate_ktypes)
           AND candidate_count = cardinality(candidate_confidences)),
  CONSTRAINT vehicle_match_results_state_fits_candidates
    CHECK (CASE state
             WHEN 'several' THEN candidate_count >= 2
             WHEN 'one_unconfirmed' THEN candidate_count = 1
             WHEN 'none' THEN candidate_count = 0
             WHEN 'not_matchable' THEN candidate_count = 0
             ELSE true
           END),
  CONSTRAINT vehicle_match_results_confidence_range
    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  CONSTRAINT vehicle_match_results_provenance_nonempty
    CHECK (btrim(terminal) <> '' AND btrim(catalog_batch) <> ''
           AND btrim(matcher_version) <> ''),
  CONSTRAINT vehicle_match_results_input_hash_format
    CHECK (input_hash ~ '^[0-9a-f]{{64}}$')
)
"""

VEHICLE_MATCH_RESULT_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("create_vehicle_match_runs_table", _CREATE_RUNS.strip()),
    ("create_vehicle_match_results_table", _CREATE_RESULTS.strip()),
    # The car lists page one state in NOR ID order.
    (
        "create_vehicle_match_results_state_index",
        (
            "CREATE INDEX IF NOT EXISTS vehicle_match_results_state_idx "
            f"ON {VEHICLE_MATCH_RESULTS_TABLE} (state, vehicle_id)"
        ),
    ),
    (
        "create_vehicle_match_results_ktype_index",
        (
            "CREATE INDEX IF NOT EXISTS vehicle_match_results_ktype_idx "
            f"ON {VEHICLE_MATCH_RESULTS_TABLE} (ktype) WHERE ktype IS NOT NULL"
        ),
    ),
    # "Which cars could be this KType": the tied cars a KType is among.
    (
        "create_vehicle_match_results_candidates_index",
        (
            "CREATE INDEX IF NOT EXISTS vehicle_match_results_candidates_idx "
            f"ON {VEHICLE_MATCH_RESULTS_TABLE} USING gin (candidate_ktypes)"
        ),
    ),
)


def run_vehicle_match_result_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply the match result schema atomically and idempotently, then verify it.

    Needs `core.vehicles` (the vehicle-core migrations) to exist.
    """

    try:
        with connection.cursor() as cursor:
            for _, statement in VEHICLE_MATCH_RESULT_MIGRATIONS:
                cursor.execute(statement)
        verify_vehicle_match_result_schema_contract(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return tuple(name for name, _ in VEHICLE_MATCH_RESULT_MIGRATIONS)


def _columns(connection: Connection, table: str) -> tuple[tuple[str, str, bool], ...]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (CORE_SCHEMA_NAME, table),
        )
        return tuple((str(row[0]), str(row[1]), str(row[2]) == "YES") for row in cursor.fetchall())


def _constraints(connection: Connection, table: str) -> dict[str, tuple[str, str, bool]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT constraint_record.conname, constraint_record.contype, "
            "pg_get_constraintdef(constraint_record.oid), "
            "constraint_record.convalidated AND NOT constraint_record.condeferrable "
            "FROM pg_constraint AS constraint_record "
            "JOIN pg_class AS table_class ON constraint_record.conrelid = table_class.oid "
            "JOIN pg_namespace AS schema_ns ON table_class.relnamespace = schema_ns.oid "
            "WHERE schema_ns.nspname = %s AND table_class.relname = %s",
            (CORE_SCHEMA_NAME, table),
        )
        return {str(row[0]): (str(row[1]), str(row[2]), bool(row[3])) for row in cursor.fetchall()}


def _verify_table(
    connection: Connection,
    table: str,
    columns: tuple[tuple[str, str, bool], ...],
    constraints: dict[str, tuple[str, tuple[str, ...]]],
) -> None:
    qualified = f"{CORE_SCHEMA_NAME}.{table}"
    actual_columns = _columns(connection, table)
    if actual_columns != columns:
        raise VehicleMatchResultSchemaContractError(
            f"{qualified} column contract mismatch: expected {columns!r}, got {actual_columns!r}"
        )
    actual = _constraints(connection, table)
    for name, (expected_type, fragments) in constraints.items():
        found = actual.get(name)
        if (
            found is None
            or found[0] != expected_type
            or not found[2]
            or any(fragment not in found[1] for fragment in fragments)
        ):
            raise VehicleMatchResultSchemaContractError(
                f"{qualified} constraint {name!r} mismatch: got {found!r}"
            )


def verify_vehicle_match_result_schema_contract(connection: Connection) -> None:
    """Fail loudly unless both tables carry their columns, constraints and indexes."""

    _verify_table(connection, _RUNS_NAME, _RUN_COLUMNS, _RUN_CONSTRAINTS)
    _verify_table(connection, _RESULTS_NAME, _RESULT_COLUMNS, _RESULT_CONSTRAINTS)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
            (CORE_SCHEMA_NAME, _RESULTS_NAME),
        )
        indexes = {str(row[0]): str(row[1]) for row in cursor.fetchall()}
    for name, fragments in _RESULT_INDEXES.items():
        definition = indexes.get(name)
        if definition is None or any(fragment not in definition for fragment in fragments):
            raise VehicleMatchResultSchemaContractError(
                f"{VEHICLE_MATCH_RESULTS_TABLE} index {name!r} mismatch: got {definition!r}"
            )
