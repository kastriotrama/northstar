"""Idempotent PostgreSQL migrations for a person's KType choice per car.

`core.vehicle_ktype_choices` is the append-only record of what a person decided
for one car: a KType among the candidates shown, "none of these", or the
withdrawal of an earlier choice. The row id is the client's operation UUID, so
a retried request cannot record twice, and rows copy verbatim between databases
(the enrichment ledger's ids are database-local, which is why the ledger is not
the store -- see docs/vehicle-ktype-choices.md).

Every chain rule is declarative -- keys, a foreign key and CHECKs -- so a
single-statement bulk load passes in any row order. Each row carries its place
in its vehicle's chain (`chain_position`, 0 for the first):

- a vehicle has one row per position, so one root and one successor per row;
- a row at position n > 0 supersedes exactly the row of the same vehicle at
  position n - 1 (the foreign key carries the position), and the root
  supersedes nothing.

Positions only go down along the links, so no statement -- not even one
inserting several rows at once -- can store a cycle, a detached chain, a second
root or a link into another vehicle. The row with the highest position is the
car's current choice.

Append-only is enforced by triggers, not convention. Statement names are a
stable contract; every statement is idempotent, and the verifier compares
definitions rather than names so a same-named but weaker object is caught.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from psycopg import Connection

CORE_SCHEMA_NAME = "core"
_TABLE_NAME = "vehicle_ktype_choices"
VEHICLE_KTYPE_CHOICES_TABLE = f"{CORE_SCHEMA_NAME}.{_TABLE_NAME}"
_BLOCK_FUNCTION = "vehicle_ktype_choices_block_mutation"

#: One row per (vehicle, position): two writers appending to one head collide here.
POSITION_KEY = "vehicle_ktype_choices_position_key"
#: A row links only to the same vehicle's row one position below it.
SUPERSEDES_FOREIGN_KEY = "vehicle_ktype_choices_supersedes_previous_fkey"
VEHICLE_FOREIGN_KEY = "vehicle_ktype_choices_vehicle_fkey"

_SUPERSEDES_POSITION = "supersedes_position"
_SUPERSEDES_POSITION_EXPRESSION = (
    "CASE WHEN (chain_position > 0) THEN (chain_position - 1) ELSE NULL::integer END"
)

# (name, information_schema data_type, nullable, default, generation expression)
_COLUMN_CONTRACT: tuple[tuple[str, str, bool, str | None, str | None], ...] = (
    ("choice_id", "uuid", False, None, None),
    ("vehicle_id", "text", False, None, None),
    ("chain_position", "integer", False, None, None),
    ("action", "text", False, None, None),
    ("ktype", "text", True, None, None),
    ("supersedes_choice_id", "uuid", True, None, None),
    (_SUPERSEDES_POSITION, "integer", True, None, _SUPERSEDES_POSITION_EXPRESSION),
    ("reviewer", "text", False, None, None),
    ("reason", "text", True, None, None),
    ("catalog_batch", "text", False, None, None),
    ("automatic_terminal", "text", False, None, None),
    ("automatic_ktype", "text", True, None, None),
    ("code_version", "text", False, None, None),
    ("evidence_fingerprint", "text", False, None, None),
    ("evidence", "jsonb", False, None, None),
    ("created_at", "timestamp with time zone", False, "now()", None),
)
#: The columns a writer or a copy handles; the generated one is the database's own.
COLUMNS: tuple[str, ...] = tuple(
    name for name, _, _, _, generated in _COLUMN_CONTRACT if generated is None
)

# name -> (pg_constraint.contype, fragments pg_get_constraintdef must contain)
_REQUIRED_CONSTRAINTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "vehicle_ktype_choices_pkey": ("p", ("PRIMARY KEY (choice_id)",)),
    POSITION_KEY: ("u", ("UNIQUE (vehicle_id, chain_position)",)),
    "vehicle_ktype_choices_id_vehicle_position_key": (
        "u",
        ("UNIQUE (choice_id, vehicle_id, chain_position)",),
    ),
    VEHICLE_FOREIGN_KEY: (
        "f",
        ("FOREIGN KEY (vehicle_id)", "REFERENCES core.vehicles(vehicle_id)", "ON DELETE RESTRICT"),
    ),
    SUPERSEDES_FOREIGN_KEY: (
        "f",
        (
            "FOREIGN KEY (supersedes_choice_id, vehicle_id, supersedes_position)",
            "REFERENCES core.vehicle_ktype_choices(choice_id, vehicle_id, chain_position)",
        ),
    ),
    "vehicle_ktype_choices_position_nonnegative": ("c", ("chain_position >= 0",)),
    "vehicle_ktype_choices_root_supersedes_nothing": (
        "c",
        ("(supersedes_choice_id IS NULL) = (chain_position = 0)",),
    ),
    "vehicle_ktype_choices_action_values": ("c", ("action", "'choose'", "'none'", "'withdraw'")),
    "vehicle_ktype_choices_ktype_matches_action": (
        "c",
        ("action = 'choose'", "ktype IS NOT NULL", "btrim(ktype) <> ''"),
    ),
    "vehicle_ktype_choices_withdraw_supersedes": (
        "c",
        ("action <> 'withdraw'", "supersedes_choice_id IS NOT NULL"),
    ),
    "vehicle_ktype_choices_reviewer_nonempty": (
        "c",
        ("btrim(reviewer) <> ''", "char_length(reviewer) <= 120"),
    ),
    "vehicle_ktype_choices_reason_nonempty": (
        "c",
        ("reason IS NULL", "btrim(reason) <> ''", "char_length(reason) <= 1000"),
    ),
    "vehicle_ktype_choices_provenance_nonempty": (
        "c",
        ("btrim(catalog_batch) <> ''", "btrim(automatic_terminal) <> ''", "btrim(code_version) <> ''"),
    ),
    "vehicle_ktype_choices_fingerprint_format": (
        "c",
        ("evidence_fingerprint ~ '^[0-9a-f]{64}$'",),
    ),
    # Both evidence checks are NULL-safe: a CHECK passes on NULL, and a missing key
    # makes these comparisons NULL, so without the COALESCE a row lacking
    # `automatic` or `candidates` would be let through.
    "vehicle_ktype_choices_evidence_shape": (
        "c",
        (
            "COALESCE(",
            "jsonb_typeof(evidence) = 'object'",
            "evidence ? 'schema'",
            "jsonb_typeof((evidence -> 'automatic'",
            "jsonb_typeof((evidence -> 'candidates'",
            ", false)",
        ),
    ),
    "vehicle_ktype_choices_ktype_was_shown": (
        "c",
        (
            "action <> 'choose'",
            "COALESCE(",
            "evidence -> 'candidates'",
            "@>",
            "jsonb_build_object('ktype', ktype)",
            ", false)",
        ),
    ),
}
_BLOCK_BODY = (
    f"BEGIN RAISE EXCEPTION '{VEHICLE_KTYPE_CHOICES_TABLE} is append-only: % is not allowed'"
    ", TG_OP; END"
)
_APPEND_ONLY_TRIGGER = "vehicle_ktype_choices_append_only"
_APPEND_ONLY_TRUNCATE_TRIGGER = "vehicle_ktype_choices_append_only_truncate"
# tgtype bits: 27 = BEFORE UPDATE OR DELETE FOR EACH ROW, 34 = BEFORE TRUNCATE.
_REQUIRED_TRIGGERS: dict[str, tuple[int, str, str]] = {
    _APPEND_ONLY_TRIGGER: (27, CORE_SCHEMA_NAME, _BLOCK_FUNCTION),
    _APPEND_ONLY_TRUNCATE_TRIGGER: (34, CORE_SCHEMA_NAME, _BLOCK_FUNCTION),
}


class VehicleKTypeChoiceSchemaContractError(RuntimeError):
    """The choices table exists but does not carry the invariants it must."""


@dataclass(frozen=True)
class VehicleKTypeChoiceMigrationStatement:
    """One named, idempotent schema statement."""

    name: str
    kind: Literal["table", "index", "function", "trigger"]
    sql: str


_CREATE_TABLE = f"""
CREATE TABLE IF NOT EXISTS {VEHICLE_KTYPE_CHOICES_TABLE} (
  choice_id UUID NOT NULL,
  vehicle_id TEXT NOT NULL,
  chain_position INTEGER NOT NULL,
  action TEXT NOT NULL,
  ktype TEXT,
  supersedes_choice_id UUID,
  {_SUPERSEDES_POSITION} INTEGER GENERATED ALWAYS AS
    (CASE WHEN chain_position > 0 THEN chain_position - 1 END) STORED,
  reviewer TEXT NOT NULL,
  reason TEXT,
  catalog_batch TEXT NOT NULL,
  automatic_terminal TEXT NOT NULL,
  automatic_ktype TEXT,
  code_version TEXT NOT NULL,
  evidence_fingerprint TEXT NOT NULL,
  evidence JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT vehicle_ktype_choices_pkey PRIMARY KEY (choice_id),
  CONSTRAINT {POSITION_KEY} UNIQUE (vehicle_id, chain_position),
  CONSTRAINT vehicle_ktype_choices_id_vehicle_position_key
    UNIQUE (choice_id, vehicle_id, chain_position),
  CONSTRAINT {VEHICLE_FOREIGN_KEY} FOREIGN KEY (vehicle_id)
    REFERENCES core.vehicles (vehicle_id) ON DELETE RESTRICT,
  CONSTRAINT {SUPERSEDES_FOREIGN_KEY}
    FOREIGN KEY (supersedes_choice_id, vehicle_id, {_SUPERSEDES_POSITION})
    REFERENCES {VEHICLE_KTYPE_CHOICES_TABLE} (choice_id, vehicle_id, chain_position),
  CONSTRAINT vehicle_ktype_choices_position_nonnegative CHECK (chain_position >= 0),
  CONSTRAINT vehicle_ktype_choices_root_supersedes_nothing
    CHECK ((supersedes_choice_id IS NULL) = (chain_position = 0)),
  CONSTRAINT vehicle_ktype_choices_action_values
    CHECK (action IN ('choose','none','withdraw')),
  CONSTRAINT vehicle_ktype_choices_ktype_matches_action
    CHECK ((action = 'choose') = (ktype IS NOT NULL) AND (ktype IS NULL OR btrim(ktype) <> '')),
  CONSTRAINT vehicle_ktype_choices_withdraw_supersedes
    CHECK (action <> 'withdraw' OR supersedes_choice_id IS NOT NULL),
  CONSTRAINT vehicle_ktype_choices_reviewer_nonempty
    CHECK (btrim(reviewer) <> '' AND char_length(reviewer) <= 120),
  CONSTRAINT vehicle_ktype_choices_reason_nonempty
    CHECK (reason IS NULL OR (btrim(reason) <> '' AND char_length(reason) <= 1000)),
  CONSTRAINT vehicle_ktype_choices_provenance_nonempty
    CHECK (btrim(catalog_batch) <> '' AND btrim(automatic_terminal) <> ''
           AND btrim(code_version) <> ''),
  CONSTRAINT vehicle_ktype_choices_fingerprint_format
    CHECK (evidence_fingerprint ~ '^[0-9a-f]{{64}}$'),
  CONSTRAINT vehicle_ktype_choices_evidence_shape
    CHECK (coalesce(jsonb_typeof(evidence) = 'object' AND evidence ? 'schema'
           AND jsonb_typeof(evidence -> 'automatic') = 'object'
           AND jsonb_typeof(evidence -> 'candidates') = 'array', false)),
  CONSTRAINT vehicle_ktype_choices_ktype_was_shown
    CHECK (action <> 'choose'
           OR coalesce(evidence -> 'candidates'
                       @> jsonb_build_array(jsonb_build_object('ktype', ktype)), false))
)
"""

VEHICLE_KTYPE_CHOICE_MIGRATION_STATEMENTS: tuple[VehicleKTypeChoiceMigrationStatement, ...] = (
    VehicleKTypeChoiceMigrationStatement(
        name="create_vehicle_ktype_choices_table", kind="table", sql=_CREATE_TABLE.strip()
    ),
    VehicleKTypeChoiceMigrationStatement(
        name="vehicle_ktype_choices_append_only_function",
        kind="function",
        sql=(
            f"CREATE OR REPLACE FUNCTION {CORE_SCHEMA_NAME}.{_BLOCK_FUNCTION}() "
            f"RETURNS trigger LANGUAGE plpgsql AS $$ {_BLOCK_BODY} $$"
        ),
    ),
    VehicleKTypeChoiceMigrationStatement(
        name="vehicle_ktype_choices_append_only_trigger",
        kind="trigger",
        sql=(
            f"CREATE OR REPLACE TRIGGER {_APPEND_ONLY_TRIGGER} "
            f"BEFORE UPDATE OR DELETE ON {VEHICLE_KTYPE_CHOICES_TABLE} "
            f"FOR EACH ROW EXECUTE FUNCTION {CORE_SCHEMA_NAME}.{_BLOCK_FUNCTION}()"
        ),
    ),
    # Row-level triggers do not fire on TRUNCATE.
    VehicleKTypeChoiceMigrationStatement(
        name="vehicle_ktype_choices_append_only_truncate_trigger",
        kind="trigger",
        sql=(
            f"CREATE OR REPLACE TRIGGER {_APPEND_ONLY_TRUNCATE_TRIGGER} "
            f"BEFORE TRUNCATE ON {VEHICLE_KTYPE_CHOICES_TABLE} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {CORE_SCHEMA_NAME}.{_BLOCK_FUNCTION}()"
        ),
    ),
)


def run_vehicle_ktype_choice_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply the choices schema atomically and idempotently, then verify it.

    Needs `core.vehicles` (the vehicle-core migrations) to exist.
    """

    try:
        with connection.cursor() as cursor:
            for statement in VEHICLE_KTYPE_CHOICE_MIGRATION_STATEMENTS:
                cursor.execute(statement.sql)
        verify_vehicle_ktype_choice_schema_contract(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return tuple(statement.name for statement in VEHICLE_KTYPE_CHOICE_MIGRATION_STATEMENTS)


def verify_vehicle_ktype_choice_schema_contract(connection: Connection) -> None:
    """Fail loudly unless the table carries every invariant, by definition not by name.

    Beyond columns and constraints it refuses what would quietly switch the
    guarantees off: a deferrable constraint, a trigger with a WHEN clause or a
    column list, a trigger function that no longer raises, any other trigger, a
    rewrite rule, and a table that is not durable (UNLOGGED).
    """

    identity = (CORE_SCHEMA_NAME, _TABLE_NAME)
    relation = (
        "JOIN pg_class AS table_class ON {} = table_class.oid "
        "JOIN pg_namespace AS schema_ns ON table_class.relnamespace = schema_ns.oid "
        "WHERE schema_ns.nspname = %s AND table_class.relname = %s"
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, data_type, is_nullable, column_default, generation_expression "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            identity,
        )
        columns = tuple(
            (
                str(row[0]),
                str(row[1]),
                str(row[2]) == "YES",
                None if row[3] is None else str(row[3]),
                # The server pretty-prints a generation expression over several lines.
                None if row[4] is None else " ".join(str(row[4]).split()),
            )
            for row in cursor.fetchall()
        )
        cursor.execute(
            "SELECT constraint_record.conname, constraint_record.contype, "
            "pg_get_constraintdef(constraint_record.oid), "
            "constraint_record.convalidated AND NOT constraint_record.condeferrable "
            "FROM pg_constraint AS constraint_record "
            + relation.format("constraint_record.conrelid"),
            identity,
        )
        constraints = {
            str(row[0]): (str(row[1]), str(row[2]), bool(row[3])) for row in cursor.fetchall()
        }
        cursor.execute(
            "SELECT trigger.tgname, trigger.tgenabled, trigger.tgtype, "
            "function_ns.nspname, function_record.proname, "
            "trigger.tgqual IS NULL AND trigger.tgattr::text = '', "
            "btrim(function_record.prosrc) "
            "FROM pg_trigger AS trigger "
            "JOIN pg_proc AS function_record ON trigger.tgfoid = function_record.oid "
            "JOIN pg_namespace AS function_ns ON function_record.pronamespace = function_ns.oid "
            + relation.format("trigger.tgrelid")
            + " AND NOT trigger.tgisinternal",
            identity,
        )
        triggers = {
            str(row[0]): (str(row[1]), int(row[2]), str(row[3]), str(row[4]), bool(row[5]), str(row[6]))
            for row in cursor.fetchall()
        }
        cursor.execute(
            "SELECT table_class.relpersistence, "
            "(SELECT count(*) FROM pg_rewrite WHERE ev_class = table_class.oid) "
            "FROM pg_class AS table_class "
            "JOIN pg_namespace AS schema_ns ON table_class.relnamespace = schema_ns.oid "
            "WHERE schema_ns.nspname = %s AND table_class.relname = %s",
            identity,
        )
        table_row = cursor.fetchone()

    table = VEHICLE_KTYPE_CHOICES_TABLE
    if columns != _COLUMN_CONTRACT:
        raise VehicleKTypeChoiceSchemaContractError(
            f"{table} column contract mismatch: expected {_COLUMN_CONTRACT!r}, got {columns!r}"
        )
    if table_row is None or str(table_row[0]) != "p" or int(table_row[1]) != 0:
        raise VehicleKTypeChoiceSchemaContractError(
            f"{table} must be a durable table without rewrite rules: got {table_row!r}"
        )
    for name, (expected_type, fragments) in _REQUIRED_CONSTRAINTS.items():
        actual = constraints.get(name)
        if (
            actual is None
            or actual[0] != expected_type
            or not actual[2]
            or any(fragment not in actual[1] for fragment in fragments)
        ):
            raise VehicleKTypeChoiceSchemaContractError(
                f"{table} constraint {name!r} mismatch: got {actual!r}"
            )
    if set(triggers) != set(_REQUIRED_TRIGGERS):
        raise VehicleKTypeChoiceSchemaContractError(
            f"{table} triggers mismatch: expected {sorted(_REQUIRED_TRIGGERS)!r}, "
            f"got {sorted(triggers)!r}"
        )
    for name, (event_bits, function_schema, function_name) in _REQUIRED_TRIGGERS.items():
        expected = ("O", event_bits, function_schema, function_name, True, _BLOCK_BODY)
        if triggers[name] != expected:
            raise VehicleKTypeChoiceSchemaContractError(
                f"{table} trigger {name!r} mismatch: expected {expected!r}, got {triggers[name]!r}"
            )
