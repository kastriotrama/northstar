"""Idempotent PostgreSQL migrations for a person's decisions that correct many cars.

`core.vehicle_correction_decisions` is the append-only record of one decision:
"this correction applies to these cars". A decision is a short chain of events.
It starts as a proposal (`propose`, nothing written to any car) or is applied at
once (`apply`), and may later be taken back as a whole (`withdraw`). The cars a
decision wrote carry ordinary rows in `core.vehicle_fact_corrections` whose
`group_id` names the event that wrote them, which is why this schema is applied
before that one (see docs/vehicle-fact-corrections.md).

Every chain rule is declarative -- keys, a foreign key and CHECKs -- so a
single-statement bulk load passes in any row order. Each event carries its place
in its decision's chain (`chain_position`, 0 for the first):

- a decision has one event per position, so one root and one successor per event;
- an event at position n > 0 supersedes exactly the event of the same decision
  at position n - 1 (the foreign key carries the position), and the root, whose
  id is the decision's id, supersedes nothing.

Positions only go down along the links, so no statement -- not even one
inserting several rows at once -- can store a cycle, a detached chain, a second
root or a link into another decision. The event with the highest position says
where the decision stands.

The root, and only the root, says what was decided: the field, the action, the
value, the scope and the sentence the person saw. Every event carries the check
it rests on (`measurement`), and nothing is applied on a check that did not
cover every car. A CHECK that reads a JSON key is written NULL-safe: a missing
key makes the comparison NULL, which a CHECK lets through, so each is wrapped in
`coalesce(..., false)`.

Append-only is enforced by triggers, not convention. Statement names are a
stable contract; every statement is idempotent, and the verifier compares
definitions rather than names so a same-named but weaker object is caught.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from psycopg import Connection

CORE_SCHEMA_NAME = "core"
_TABLE_NAME = "vehicle_correction_decisions"
VEHICLE_CORRECTION_DECISIONS_TABLE = f"{CORE_SCHEMA_NAME}.{_TABLE_NAME}"
_BLOCK_FUNCTION = "vehicle_correction_decisions_block_mutation"

#: One event per (decision, position): two writers appending to one head collide here.
POSITION_KEY = "vehicle_correction_decisions_position_key"
#: An event links only to the same decision's event one position below it.
SUPERSEDES_FOREIGN_KEY = "vehicle_correction_decisions_supersedes_previous_fkey"
MAKE_MODEL_INDEX = "vehicle_correction_decisions_make_model_idx"

#: The numbers every measurement must carry: how many cars the scope held, how
#: many were checked, and what the check found for the cars that count most.
MEASURED_NUMBERS: tuple[str, ...] = ("affected", "checked", "gained", "lost", "moved", "worse")

_SUPERSEDES_POSITION = "supersedes_position"
_SUPERSEDES_POSITION_EXPRESSION = (
    "CASE WHEN (chain_position > 0) THEN (chain_position - 1) ELSE NULL::integer END"
)

# (name, information_schema data_type, nullable, default, generation expression)
_COLUMN_CONTRACT: tuple[tuple[str, str, bool, str | None, str | None], ...] = (
    ("event_id", "uuid", False, None, None),
    ("decision_id", "uuid", False, None, None),
    ("chain_position", "integer", False, None, None),
    ("event", "text", False, None, None),
    ("supersedes_event_id", "uuid", True, None, None),
    (_SUPERSEDES_POSITION, "integer", True, None, _SUPERSEDES_POSITION_EXPRESSION),
    ("field", "text", True, None, None),
    ("action", "text", True, None, None),
    ("value", "text", True, None, None),
    ("scope", "jsonb", True, None, None),
    ("scope_label", "text", True, None, None),
    ("manufacturer", "text", True, None, None),
    ("model_family", "text", True, None, None),
    ("reviewer", "text", False, None, None),
    ("reason", "text", True, None, None),
    ("catalog_batch", "text", False, None, None),
    ("code_version", "text", False, None, None),
    ("measurement", "jsonb", False, None, None),
    ("created_at", "timestamp with time zone", False, "now()", None),
)
#: The columns a writer or a copy handles; the generated one is the database's own.
COLUMNS: tuple[str, ...] = tuple(
    name for name, _, _, _, generated in _COLUMN_CONTRACT if generated is None
)

# name -> (pg_constraint.contype, fragments pg_get_constraintdef must contain)
_REQUIRED_CONSTRAINTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "vehicle_correction_decisions_pkey": ("p", ("PRIMARY KEY (event_id)",)),
    POSITION_KEY: ("u", ("UNIQUE (decision_id, chain_position)",)),
    "vehicle_correction_decisions_id_decision_position_key": (
        "u",
        ("UNIQUE (event_id, decision_id, chain_position)",),
    ),
    SUPERSEDES_FOREIGN_KEY: (
        "f",
        (
            "FOREIGN KEY (supersedes_event_id, decision_id, supersedes_position)",
            (
                "REFERENCES core.vehicle_correction_decisions"
                "(event_id, decision_id, chain_position)"
            ),
        ),
    ),
    "vehicle_correction_decisions_position_nonnegative": ("c", ("chain_position >= 0",)),
    "vehicle_correction_decisions_root_supersedes_nothing": (
        "c",
        ("(supersedes_event_id IS NULL) = (chain_position = 0)",),
    ),
    "vehicle_correction_decisions_root_is_the_decision": (
        "c",
        ("(chain_position = 0) = (event_id = decision_id)",),
    ),
    "vehicle_correction_decisions_event_values": (
        "c",
        ("event", "'propose'", "'apply'", "'withdraw'"),
    ),
    "vehicle_correction_decisions_event_fits_position": (
        "c",
        (
            "(event <> 'withdraw'::text) OR (chain_position > 0)",
            "(event <> 'propose'::text) OR (chain_position = 0)",
        ),
    ),
    "vehicle_correction_decisions_root_carries_decision": (
        "c",
        (
            "(chain_position = 0) = (field IS NOT NULL)",
            "(chain_position = 0) = (action IS NOT NULL)",
            "(chain_position = 0) = (scope IS NOT NULL)",
            "(chain_position = 0) = (scope_label IS NOT NULL)",
            "(chain_position = 0) = (manufacturer IS NOT NULL)",
            "(chain_position = 0) OR ((value IS NULL) AND (model_family IS NULL))",
        ),
    ),
    "vehicle_correction_decisions_field_format": (
        "c",
        ("field IS NULL", "field ~ '^[a-z][a-z_]*$'"),
    ),
    "vehicle_correction_decisions_action_values": ("c", ("action IS NULL", "'set'", "'ignore'")),
    "vehicle_correction_decisions_value_matches_action": (
        "c",
        (
            "COALESCE((action = 'set'::text), false) = (value IS NOT NULL)",
            "btrim(value) <> ''",
            "char_length(value) <= 200",
        ),
    ),
    # The JSON checks: one COALESCE around the whole test, so a row without a
    # key fails the check instead of passing it as NULL.
    "vehicle_correction_decisions_scope_shape": (
        "c",
        (
            "CHECK (((scope IS NULL) OR COALESCE((",
            "jsonb_typeof(scope) = 'object'",
            "jsonb_typeof((scope -> 'kind'::text)) = 'string'",
            "jsonb_typeof((scope -> 'conditions'::text)) = 'array'",
            "scope ? 'anchor_value'",
            "), false)))",
        ),
    ),
    "vehicle_correction_decisions_label_nonempty": (
        "c",
        ("scope_label IS NULL", "btrim(scope_label) <> ''", "char_length(scope_label) <= 500"),
    ),
    "vehicle_correction_decisions_make_nonempty": (
        "c",
        (
            "manufacturer IS NULL",
            "btrim(manufacturer) <> ''",
            "model_family IS NULL",
            "btrim(model_family) <> ''",
        ),
    ),
    "vehicle_correction_decisions_reviewer_nonempty": (
        "c",
        ("btrim(reviewer) <> ''", "char_length(reviewer) <= 120"),
    ),
    "vehicle_correction_decisions_reason_required": (
        "c",
        (
            "(event = 'propose'::text) OR (reason IS NOT NULL)",
            "reason IS NULL",
            "btrim(reason) <> ''",
            "char_length(reason) <= 1000",
        ),
    ),
    "vehicle_correction_decisions_provenance_nonempty": (
        "c",
        ("btrim(catalog_batch) <> ''", "btrim(code_version) <> ''"),
    ),
    "vehicle_correction_decisions_measurement_shape": (
        "c",
        (
            "CHECK (COALESCE((",
            "jsonb_typeof(measurement) = 'object'",
            *(
                f"jsonb_typeof((measurement -> '{number}'::text)) = 'number'"
                for number in MEASURED_NUMBERS
            ),
            "), false))",
        ),
    ),
    "vehicle_correction_decisions_measured_before_applied": (
        "c",
        (
            (
                "(event = 'propose'::text) OR "
                "COALESCE(((measurement ->> 'complete'::text) = 'true'::text), false)"
            ),
        ),
    ),
}
# The root's copies of the scope's make and model: what a lookup of the
# decisions that touch one model reads.
_REQUIRED_INDEX_FRAGMENTS: dict[str, tuple[str, ...]] = {
    MAKE_MODEL_INDEX: ("(manufacturer, model_family)", "WHERE (manufacturer IS NOT NULL)"),
}
_BLOCK_BODY = (
    f"BEGIN RAISE EXCEPTION '{VEHICLE_CORRECTION_DECISIONS_TABLE} is append-only: "
    "% is not allowed', TG_OP; END"
)
_APPEND_ONLY_TRIGGER = "vehicle_correction_decisions_append_only"
_APPEND_ONLY_TRUNCATE_TRIGGER = "vehicle_correction_decisions_append_only_truncate"
# tgtype bits: 27 = BEFORE UPDATE OR DELETE FOR EACH ROW, 34 = BEFORE TRUNCATE.
_REQUIRED_TRIGGERS: dict[str, tuple[int, str, str]] = {
    _APPEND_ONLY_TRIGGER: (27, CORE_SCHEMA_NAME, _BLOCK_FUNCTION),
    _APPEND_ONLY_TRUNCATE_TRIGGER: (34, CORE_SCHEMA_NAME, _BLOCK_FUNCTION),
}


class VehicleCorrectionDecisionSchemaContractError(RuntimeError):
    """The decisions table exists but does not carry the invariants it must."""


@dataclass(frozen=True)
class VehicleCorrectionDecisionMigrationStatement:
    """One named, idempotent schema statement."""

    name: str
    kind: Literal["table", "index", "function", "trigger"]
    sql: str


_MEASURED = "\n           AND ".join(
    f"jsonb_typeof(measurement -> '{number}') = 'number'" for number in MEASURED_NUMBERS
)

_CREATE_TABLE = f"""
CREATE TABLE IF NOT EXISTS {VEHICLE_CORRECTION_DECISIONS_TABLE} (
  event_id UUID NOT NULL,
  decision_id UUID NOT NULL,
  chain_position INTEGER NOT NULL,
  event TEXT NOT NULL,
  supersedes_event_id UUID,
  {_SUPERSEDES_POSITION} INTEGER GENERATED ALWAYS AS
    (CASE WHEN chain_position > 0 THEN chain_position - 1 END) STORED,
  field TEXT,
  action TEXT,
  value TEXT,
  scope JSONB,
  scope_label TEXT,
  manufacturer TEXT,
  model_family TEXT,
  reviewer TEXT NOT NULL,
  reason TEXT,
  catalog_batch TEXT NOT NULL,
  code_version TEXT NOT NULL,
  measurement JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT vehicle_correction_decisions_pkey PRIMARY KEY (event_id),
  CONSTRAINT {POSITION_KEY} UNIQUE (decision_id, chain_position),
  CONSTRAINT vehicle_correction_decisions_id_decision_position_key
    UNIQUE (event_id, decision_id, chain_position),
  CONSTRAINT {SUPERSEDES_FOREIGN_KEY}
    FOREIGN KEY (supersedes_event_id, decision_id, {_SUPERSEDES_POSITION})
    REFERENCES {VEHICLE_CORRECTION_DECISIONS_TABLE} (event_id, decision_id, chain_position),
  CONSTRAINT vehicle_correction_decisions_position_nonnegative CHECK (chain_position >= 0),
  CONSTRAINT vehicle_correction_decisions_root_supersedes_nothing
    CHECK ((supersedes_event_id IS NULL) = (chain_position = 0)),
  CONSTRAINT vehicle_correction_decisions_root_is_the_decision
    CHECK ((chain_position = 0) = (event_id = decision_id)),
  CONSTRAINT vehicle_correction_decisions_event_values
    CHECK (event IN ('propose','apply','withdraw')),
  CONSTRAINT vehicle_correction_decisions_event_fits_position
    CHECK ((event <> 'withdraw' OR chain_position > 0)
           AND (event <> 'propose' OR chain_position = 0)),
  CONSTRAINT vehicle_correction_decisions_root_carries_decision
    CHECK ((chain_position = 0) = (field IS NOT NULL)
           AND (chain_position = 0) = (action IS NOT NULL)
           AND (chain_position = 0) = (scope IS NOT NULL)
           AND (chain_position = 0) = (scope_label IS NOT NULL)
           AND (chain_position = 0) = (manufacturer IS NOT NULL)
           AND (chain_position = 0 OR (value IS NULL AND model_family IS NULL))),
  CONSTRAINT vehicle_correction_decisions_field_format
    CHECK (field IS NULL OR field ~ '^[a-z][a-z_]*$'),
  CONSTRAINT vehicle_correction_decisions_action_values
    CHECK (action IS NULL OR action IN ('set','ignore')),
  CONSTRAINT vehicle_correction_decisions_value_matches_action
    CHECK (coalesce(action = 'set', false) = (value IS NOT NULL)
           AND (value IS NULL OR (btrim(value) <> '' AND char_length(value) <= 200))),
  CONSTRAINT vehicle_correction_decisions_scope_shape
    CHECK (scope IS NULL OR coalesce(
           jsonb_typeof(scope) = 'object'
           AND jsonb_typeof(scope -> 'kind') = 'string'
           AND jsonb_typeof(scope -> 'conditions') = 'array'
           AND scope ? 'anchor_value', false)),
  CONSTRAINT vehicle_correction_decisions_label_nonempty
    CHECK (scope_label IS NULL
           OR (btrim(scope_label) <> '' AND char_length(scope_label) <= 500)),
  CONSTRAINT vehicle_correction_decisions_make_nonempty
    CHECK ((manufacturer IS NULL OR btrim(manufacturer) <> '')
           AND (model_family IS NULL OR btrim(model_family) <> '')),
  CONSTRAINT vehicle_correction_decisions_reviewer_nonempty
    CHECK (btrim(reviewer) <> '' AND char_length(reviewer) <= 120),
  CONSTRAINT vehicle_correction_decisions_reason_required
    CHECK ((event = 'propose' OR reason IS NOT NULL)
           AND (reason IS NULL OR (btrim(reason) <> '' AND char_length(reason) <= 1000))),
  CONSTRAINT vehicle_correction_decisions_provenance_nonempty
    CHECK (btrim(catalog_batch) <> '' AND btrim(code_version) <> ''),
  CONSTRAINT vehicle_correction_decisions_measurement_shape
    CHECK (coalesce(
           jsonb_typeof(measurement) = 'object'
           AND {_MEASURED}, false)),
  CONSTRAINT vehicle_correction_decisions_measured_before_applied
    CHECK (event = 'propose' OR coalesce(measurement ->> 'complete' = 'true', false))
)
"""

VEHICLE_CORRECTION_DECISION_MIGRATION_STATEMENTS: tuple[
    VehicleCorrectionDecisionMigrationStatement, ...
] = (
    VehicleCorrectionDecisionMigrationStatement(
        name="create_vehicle_correction_decisions_table", kind="table", sql=_CREATE_TABLE.strip()
    ),
    VehicleCorrectionDecisionMigrationStatement(
        name="vehicle_correction_decisions_make_model_index",
        kind="index",
        sql=(
            f"CREATE INDEX IF NOT EXISTS {MAKE_MODEL_INDEX} "
            f"ON {VEHICLE_CORRECTION_DECISIONS_TABLE} (manufacturer, model_family) "
            "WHERE manufacturer IS NOT NULL"
        ),
    ),
    VehicleCorrectionDecisionMigrationStatement(
        name="vehicle_correction_decisions_append_only_function",
        kind="function",
        sql=(
            f"CREATE OR REPLACE FUNCTION {CORE_SCHEMA_NAME}.{_BLOCK_FUNCTION}() "
            f"RETURNS trigger LANGUAGE plpgsql AS $$ {_BLOCK_BODY} $$"
        ),
    ),
    VehicleCorrectionDecisionMigrationStatement(
        name="vehicle_correction_decisions_append_only_trigger",
        kind="trigger",
        sql=(
            f"CREATE OR REPLACE TRIGGER {_APPEND_ONLY_TRIGGER} "
            f"BEFORE UPDATE OR DELETE ON {VEHICLE_CORRECTION_DECISIONS_TABLE} "
            f"FOR EACH ROW EXECUTE FUNCTION {CORE_SCHEMA_NAME}.{_BLOCK_FUNCTION}()"
        ),
    ),
    # Row-level triggers do not fire on TRUNCATE.
    VehicleCorrectionDecisionMigrationStatement(
        name="vehicle_correction_decisions_append_only_truncate_trigger",
        kind="trigger",
        sql=(
            f"CREATE OR REPLACE TRIGGER {_APPEND_ONLY_TRUNCATE_TRIGGER} "
            f"BEFORE TRUNCATE ON {VEHICLE_CORRECTION_DECISIONS_TABLE} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {CORE_SCHEMA_NAME}.{_BLOCK_FUNCTION}()"
        ),
    ),
)


def run_vehicle_correction_decision_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply the decisions schema atomically and idempotently, then verify it.

    Needs the `core` schema to exist. Run it before the corrections migrations:
    their table references this one.
    """

    try:
        with connection.cursor() as cursor:
            for statement in VEHICLE_CORRECTION_DECISION_MIGRATION_STATEMENTS:
                cursor.execute(statement.sql)
        verify_vehicle_correction_decision_schema_contract(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return tuple(statement.name for statement in VEHICLE_CORRECTION_DECISION_MIGRATION_STATEMENTS)


def verify_vehicle_correction_decision_schema_contract(connection: Connection) -> None:
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
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
            identity,
        )
        indexes = {str(row[0]): str(row[1]) for row in cursor.fetchall()}
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
            str(name): (str(enabled), int(events), str(schema), str(function), bool(plain), str(body))
            for name, enabled, events, schema, function, plain, body in cursor.fetchall()
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

    table = VEHICLE_CORRECTION_DECISIONS_TABLE
    if columns != _COLUMN_CONTRACT:
        raise VehicleCorrectionDecisionSchemaContractError(
            f"{table} column contract mismatch: expected {_COLUMN_CONTRACT!r}, got {columns!r}"
        )
    if table_row is None or str(table_row[0]) != "p" or int(table_row[1]) != 0:
        raise VehicleCorrectionDecisionSchemaContractError(
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
            raise VehicleCorrectionDecisionSchemaContractError(
                f"{table} constraint {name!r} mismatch: got {actual!r}"
            )
    for name, index_fragments in _REQUIRED_INDEX_FRAGMENTS.items():
        definition = indexes.get(name)
        if definition is None or any(fragment not in definition for fragment in index_fragments):
            raise VehicleCorrectionDecisionSchemaContractError(
                f"{table} index {name!r} mismatch: got {definition!r}"
            )
    if set(triggers) != set(_REQUIRED_TRIGGERS):
        raise VehicleCorrectionDecisionSchemaContractError(
            f"{table} triggers mismatch: expected {sorted(_REQUIRED_TRIGGERS)!r}, "
            f"got {sorted(triggers)!r}"
        )
    for name, (event_bits, function_schema, function_name) in _REQUIRED_TRIGGERS.items():
        expected = ("O", event_bits, function_schema, function_name, True, _BLOCK_BODY)
        if triggers[name] != expected:
            raise VehicleCorrectionDecisionSchemaContractError(
                f"{table} trigger {name!r} mismatch: expected {expected!r}, got {triggers[name]!r}"
            )
