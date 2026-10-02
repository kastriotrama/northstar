"""Idempotent PostgreSQL migrations for a person's KType choice per car.

`core.vehicle_ktype_choices` is the append-only record of what a person decided
for one car: a KType among the candidates shown, "none of these", or the
withdrawal of an earlier choice. The row id is the client's operation UUID, so
a retried request cannot record twice, and rows copy verbatim between databases
(the enrichment ledger's ids are database-local, which is why the ledger is not
the store -- see docs/vehicle-ktype-choices.md).

Every chain rule is declarative -- foreign keys, CHECKs and partial unique
indexes -- so a single-statement bulk load passes in any row order:

- a row supersedes at most one earlier row *of the same vehicle*;
- a row is superseded at most once (the chain is linear);
- a vehicle has one root, forever (a choice after a withdrawal supersedes it).

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

SUPERSEDES_ONCE_INDEX = "vehicle_ktype_choices_supersedes_once_idx"
ONE_ROOT_INDEX = "vehicle_ktype_choices_one_root_idx"
VEHICLE_INDEX = "vehicle_ktype_choices_vehicle_idx"
VEHICLE_FOREIGN_KEY = "vehicle_ktype_choices_vehicle_fkey"

# (name, information_schema data_type, nullable, default)
_COLUMN_CONTRACT: tuple[tuple[str, str, bool, str | None], ...] = (
    ("choice_id", "uuid", False, None),
    ("vehicle_id", "text", False, None),
    ("action", "text", False, None),
    ("ktype", "text", True, None),
    ("supersedes_choice_id", "uuid", True, None),
    ("reviewer", "text", False, None),
    ("reason", "text", True, None),
    ("catalog_batch", "text", False, None),
    ("automatic_terminal", "text", False, None),
    ("automatic_ktype", "text", True, None),
    ("code_version", "text", False, None),
    ("evidence_fingerprint", "text", False, None),
    ("evidence", "jsonb", False, None),
    ("created_at", "timestamp with time zone", False, "now()"),
)
COLUMNS: tuple[str, ...] = tuple(name for name, _, _, _ in _COLUMN_CONTRACT)

# name -> (pg_constraint.contype, fragments pg_get_constraintdef must contain)
_REQUIRED_CONSTRAINTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "vehicle_ktype_choices_pkey": ("p", ("PRIMARY KEY (choice_id)",)),
    "vehicle_ktype_choices_id_vehicle_key": ("u", ("UNIQUE (choice_id, vehicle_id)",)),
    VEHICLE_FOREIGN_KEY: (
        "f",
        ("FOREIGN KEY (vehicle_id)", "REFERENCES core.vehicles(vehicle_id)", "ON DELETE RESTRICT"),
    ),
    "vehicle_ktype_choices_supersedes_same_vehicle_fkey": (
        "f",
        (
            "FOREIGN KEY (supersedes_choice_id, vehicle_id)",
            "REFERENCES core.vehicle_ktype_choices(choice_id, vehicle_id)",
        ),
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
    "vehicle_ktype_choices_not_self": ("c", ("supersedes_choice_id <> choice_id",)),
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
    "vehicle_ktype_choices_evidence_shape": (
        "c",
        (
            "jsonb_typeof(evidence) = 'object'",
            "evidence ? 'schema'",
            "jsonb_typeof((evidence -> 'automatic'",
            "jsonb_typeof((evidence -> 'candidates'",
        ),
    ),
    "vehicle_ktype_choices_ktype_was_shown": (
        "c",
        ("action <> 'choose'", "evidence -> 'candidates'", "@>", "jsonb_build_object('ktype', ktype)"),
    ),
}
_REQUIRED_INDEX_FRAGMENTS: dict[str, tuple[str, ...]] = {
    SUPERSEDES_ONCE_INDEX: (
        "CREATE UNIQUE INDEX",
        "(supersedes_choice_id)",
        "WHERE (supersedes_choice_id IS NOT NULL)",
    ),
    ONE_ROOT_INDEX: (
        "CREATE UNIQUE INDEX",
        "(vehicle_id)",
        "WHERE (supersedes_choice_id IS NULL)",
    ),
    VEHICLE_INDEX: ("(vehicle_id)",),
}
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
  action TEXT NOT NULL,
  ktype TEXT,
  supersedes_choice_id UUID,
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
  CONSTRAINT vehicle_ktype_choices_id_vehicle_key UNIQUE (choice_id, vehicle_id),
  CONSTRAINT {VEHICLE_FOREIGN_KEY} FOREIGN KEY (vehicle_id)
    REFERENCES core.vehicles (vehicle_id) ON DELETE RESTRICT,
  CONSTRAINT vehicle_ktype_choices_supersedes_same_vehicle_fkey
    FOREIGN KEY (supersedes_choice_id, vehicle_id)
    REFERENCES {VEHICLE_KTYPE_CHOICES_TABLE} (choice_id, vehicle_id),
  CONSTRAINT vehicle_ktype_choices_action_values
    CHECK (action IN ('choose','none','withdraw')),
  CONSTRAINT vehicle_ktype_choices_ktype_matches_action
    CHECK ((action = 'choose') = (ktype IS NOT NULL) AND (ktype IS NULL OR btrim(ktype) <> '')),
  CONSTRAINT vehicle_ktype_choices_withdraw_supersedes
    CHECK (action <> 'withdraw' OR supersedes_choice_id IS NOT NULL),
  CONSTRAINT vehicle_ktype_choices_not_self CHECK (supersedes_choice_id <> choice_id),
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
    CHECK (jsonb_typeof(evidence) = 'object' AND evidence ? 'schema'
           AND jsonb_typeof(evidence -> 'automatic') = 'object'
           AND jsonb_typeof(evidence -> 'candidates') = 'array'),
  CONSTRAINT vehicle_ktype_choices_ktype_was_shown
    CHECK (action <> 'choose'
           OR evidence -> 'candidates' @> jsonb_build_array(jsonb_build_object('ktype', ktype)))
)
"""

VEHICLE_KTYPE_CHOICE_MIGRATION_STATEMENTS: tuple[VehicleKTypeChoiceMigrationStatement, ...] = (
    VehicleKTypeChoiceMigrationStatement(
        name="create_vehicle_ktype_choices_table", kind="table", sql=_CREATE_TABLE.strip()
    ),
    VehicleKTypeChoiceMigrationStatement(
        name="vehicle_ktype_choices_supersedes_once_index",
        kind="index",
        sql=(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {SUPERSEDES_ONCE_INDEX} "
            f"ON {VEHICLE_KTYPE_CHOICES_TABLE} (supersedes_choice_id) "
            "WHERE supersedes_choice_id IS NOT NULL"
        ),
    ),
    VehicleKTypeChoiceMigrationStatement(
        name="vehicle_ktype_choices_one_root_index",
        kind="index",
        sql=(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {ONE_ROOT_INDEX} "
            f"ON {VEHICLE_KTYPE_CHOICES_TABLE} (vehicle_id) "
            "WHERE supersedes_choice_id IS NULL"
        ),
    ),
    VehicleKTypeChoiceMigrationStatement(
        name="vehicle_ktype_choices_vehicle_index",
        kind="index",
        sql=(
            f"CREATE INDEX IF NOT EXISTS {VEHICLE_INDEX} "
            f"ON {VEHICLE_KTYPE_CHOICES_TABLE} (vehicle_id)"
        ),
    ),
    VehicleKTypeChoiceMigrationStatement(
        name="vehicle_ktype_choices_append_only_function",
        kind="function",
        sql=(
            f"CREATE OR REPLACE FUNCTION {CORE_SCHEMA_NAME}.{_BLOCK_FUNCTION}() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            f"RAISE EXCEPTION '{VEHICLE_KTYPE_CHOICES_TABLE} is append-only: % is not allowed'"
            ", TG_OP; "
            "END $$"
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
    """Fail loudly unless the table carries every invariant, by definition not by name."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (CORE_SCHEMA_NAME, _TABLE_NAME),
        )
        columns = tuple(
            (str(row[0]), str(row[1]), str(row[2]) == "YES", None if row[3] is None else str(row[3]))
            for row in cursor.fetchall()
        )
        cursor.execute(
            "SELECT constraint_record.conname, constraint_record.contype, "
            "pg_get_constraintdef(constraint_record.oid), constraint_record.convalidated "
            "FROM pg_constraint AS constraint_record "
            "JOIN pg_class AS table_class ON constraint_record.conrelid = table_class.oid "
            "JOIN pg_namespace AS schema_ns ON table_class.relnamespace = schema_ns.oid "
            "WHERE schema_ns.nspname = %s AND table_class.relname = %s",
            (CORE_SCHEMA_NAME, _TABLE_NAME),
        )
        constraints = {
            str(row[0]): (str(row[1]), str(row[2]), bool(row[3])) for row in cursor.fetchall()
        }
        cursor.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
            (CORE_SCHEMA_NAME, _TABLE_NAME),
        )
        indexes = {str(row[0]): str(row[1]) for row in cursor.fetchall()}
        cursor.execute(
            "SELECT trigger.tgname, trigger.tgenabled, trigger.tgtype, "
            "function_ns.nspname, function_record.proname "
            "FROM pg_trigger AS trigger "
            "JOIN pg_class AS table_class ON trigger.tgrelid = table_class.oid "
            "JOIN pg_namespace AS schema_ns ON table_class.relnamespace = schema_ns.oid "
            "JOIN pg_proc AS function_record ON trigger.tgfoid = function_record.oid "
            "JOIN pg_namespace AS function_ns ON function_record.pronamespace = function_ns.oid "
            "WHERE schema_ns.nspname = %s AND table_class.relname = %s "
            "AND NOT trigger.tgisinternal",
            (CORE_SCHEMA_NAME, _TABLE_NAME),
        )
        triggers = {
            str(row[0]): (str(row[1]), int(row[2]), str(row[3]), str(row[4]))
            for row in cursor.fetchall()
        }

    table = VEHICLE_KTYPE_CHOICES_TABLE
    if columns != _COLUMN_CONTRACT:
        raise VehicleKTypeChoiceSchemaContractError(
            f"{table} column contract mismatch: expected {_COLUMN_CONTRACT!r}, got {columns!r}"
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
    for name, index_fragments in _REQUIRED_INDEX_FRAGMENTS.items():
        definition = indexes.get(name)
        if definition is None or any(fragment not in definition for fragment in index_fragments):
            raise VehicleKTypeChoiceSchemaContractError(
                f"{table} index {name!r} mismatch: got {definition!r}"
            )
    for name, (event_bits, function_schema, function_name) in _REQUIRED_TRIGGERS.items():
        actual_trigger = triggers.get(name)
        expected = ("O", event_bits, function_schema, function_name)
        if actual_trigger != expected:
            raise VehicleKTypeChoiceSchemaContractError(
                f"{table} trigger {name!r} mismatch: expected {expected!r}, got {actual_trigger!r}"
            )
