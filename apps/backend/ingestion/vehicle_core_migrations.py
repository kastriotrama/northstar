"""Idempotent schema for the NorthStar vehicle record (`core.vehicles`).

Four tables, each with one job:

- `core.vehicles`: one row per physical vehicle, keyed by an opaque `NOR-<ULID>`.
  The winning value of every field, plus where it came from (`field_sources`) and
  what lost (`field_alternatives`).
- `core.vehicle_identifiers`: VINs, chassis numbers and plates with validity
  periods. A plate or a full VIN can be *current* on one vehicle only -- enforced
  by a partial unique index, not by convention -- and a moved plate is closed, not
  overwritten, so the car it used to belong to stays findable by it.
- `core.vehicle_source_links`: which provider record describes which vehicle.
  Its primary key is the provider's own record key, so a re-import lands on the
  same vehicle instead of minting another.
- `core.vehicle_enrichment_rules`: learned "every car with this key has this
  value" rules. They fill gaps only.

Vehicles, identifiers and links are never deleted: a vehicle leaves the register
(`registry_status`), an identifier is closed (`valid_to`), and NOR IDs are never
reused. Triggers reject DELETE so no code path can quietly break that.
"""

from __future__ import annotations

from psycopg import Connection

from ingestion.vehicle_core_fields import (
    CORE_FIELDS,
    ORIGIN_SOURCES,
    REGISTRY_STATUSES,
)

CORE_SCHEMA = "core"
VEHICLES_TABLE = "core.vehicles"
VEHICLE_IDENTIFIERS_TABLE = "core.vehicle_identifiers"
VEHICLE_SOURCE_LINKS_TABLE = "core.vehicle_source_links"
VEHICLE_ENRICHMENT_RULES_TABLE = "core.vehicle_enrichment_rules"

IDENTIFIER_KINDS: tuple[str, ...] = ("vin", "chassis", "plate")
LINK_METHODS: tuple[str, ...] = ("minted", "vin", "chassis", "plate", "manual")
RULE_STATUSES: tuple[str, ...] = ("active", "retired")

_SQL_TYPES = {
    "text": "TEXT",
    "integer": "INTEGER",
    "smallint": "SMALLINT",
    "date": "DATE",
    "boolean": "BOOLEAN",
    "text[]": "TEXT[]",
    "real": "REAL",
}

# Columns the Vehicles tab filters and searches on. Lookups (plate, vin) and the
# default narrowing (status, scope) first, then the filters people actually use.
_VEHICLE_INDEX_COLUMNS: tuple[str, ...] = (
    "plate",
    "vin",
    "registry_status",
    "vehicle_scope",
    "manufacturer",
    "model_family",
    "fuel",
    "engine_code",
    "production_year",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _value_columns() -> str:
    parts = []
    for field in CORE_FIELDS:
        if field.name == "registry_status":
            parts.append("registry_status TEXT NOT NULL DEFAULT 'registered'")
        else:
            parts.append(f"{field.name} {_SQL_TYPES[field.sql_type]}")
    return ",\n            ".join(parts)


def _no_delete_function(name: str, noun: str) -> str:
    return f"""
        CREATE OR REPLACE FUNCTION core.{name}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '{noun} are never deleted'
                USING ERRCODE = 'restrict_violation';
        END;
        $$
    """


def _no_delete_trigger(table: str, trigger: str, function: str) -> tuple[str, ...]:
    return (
        f"DROP TRIGGER IF EXISTS {trigger} ON {table}",
        (f"CREATE TRIGGER {trigger} BEFORE DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION core.{function}()"),
    )


def _migrations() -> tuple[tuple[str, str], ...]:
    statements: list[tuple[str, str]] = [
        ("create_core_schema", f"CREATE SCHEMA IF NOT EXISTS {CORE_SCHEMA}"),
        (
            "create_vehicles_table",
            f"""
            CREATE TABLE IF NOT EXISTS {VEHICLES_TABLE} (
            vehicle_id TEXT NOT NULL,
            origin_source TEXT NOT NULL,
            origin_observed_on DATE,
            ts_record_id BIGINT,
            {_value_columns()},
            field_sources JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            field_alternatives JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT vehicles_pkey PRIMARY KEY (vehicle_id),
            CONSTRAINT vehicles_vehicle_id_format
                CHECK (vehicle_id ~ '^NOR-[0-7][0-9A-HJKMNP-TV-Z]{{25}}$'),
            CONSTRAINT vehicles_origin_source_values
                CHECK (origin_source IN ({_quoted(ORIGIN_SOURCES)})),
            CONSTRAINT vehicles_registry_status_values
                CHECK (registry_status IN ({_quoted(REGISTRY_STATUSES)})),
            CONSTRAINT vehicles_ts_record_id_key UNIQUE (ts_record_id),
            CONSTRAINT vehicles_field_sources_object
                CHECK (jsonb_typeof(field_sources) = 'object'),
            CONSTRAINT vehicles_field_alternatives_object
                CHECK (jsonb_typeof(field_alternatives) = 'object')
            )
            """,
        ),
        (
            "create_vehicle_identifiers_table",
            f"""
            CREATE TABLE IF NOT EXISTS {VEHICLE_IDENTIFIERS_TABLE} (
            id BIGINT GENERATED ALWAYS AS IDENTITY,
            vehicle_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            valid_from DATE,
            valid_to DATE,
            source TEXT NOT NULL,
            source_ref TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT vehicle_identifiers_pkey PRIMARY KEY (id),
            CONSTRAINT vehicle_identifiers_vehicle_fkey FOREIGN KEY (vehicle_id)
                REFERENCES {VEHICLES_TABLE} (vehicle_id) ON DELETE RESTRICT,
            CONSTRAINT vehicle_identifiers_kind_values
                CHECK (kind IN ({_quoted(IDENTIFIER_KINDS)})),
            CONSTRAINT vehicle_identifiers_value_nonempty CHECK (btrim(value) <> ''),
            CONSTRAINT vehicle_identifiers_source_nonempty CHECK (btrim(source) <> ''),
            CONSTRAINT vehicle_identifiers_valid_period
                CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
            )
            """,
        ),
        (
            "create_vehicle_source_links_table",
            f"""
            CREATE TABLE IF NOT EXISTS {VEHICLE_SOURCE_LINKS_TABLE} (
            source_system TEXT NOT NULL,
            source_record_key TEXT NOT NULL,
            vehicle_id TEXT NOT NULL,
            observed_on DATE,
            link_method TEXT NOT NULL,
            linked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT vehicle_source_links_pkey PRIMARY KEY (source_system, source_record_key),
            CONSTRAINT vehicle_source_links_vehicle_fkey FOREIGN KEY (vehicle_id)
                REFERENCES {VEHICLES_TABLE} (vehicle_id) ON DELETE RESTRICT,
            CONSTRAINT vehicle_source_links_source_nonempty CHECK (btrim(source_system) <> ''),
            CONSTRAINT vehicle_source_links_key_nonempty CHECK (btrim(source_record_key) <> ''),
            CONSTRAINT vehicle_source_links_method_values
                CHECK (link_method IN ({_quoted(LINK_METHODS)}))
            )
            """,
        ),
        (
            "create_vehicle_enrichment_rules_table",
            f"""
            CREATE TABLE IF NOT EXISTS {VEHICLE_ENRICHMENT_RULES_TABLE} (
            rule_id TEXT NOT NULL,
            rule_family TEXT NOT NULL,
            target_field TEXT NOT NULL,
            key_fields TEXT[] NOT NULL,
            key_values TEXT[] NOT NULL,
            value TEXT NOT NULL,
            support INTEGER NOT NULL,
            agreement REAL NOT NULL,
            learned_from TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            retired_at TIMESTAMPTZ,
            CONSTRAINT vehicle_enrichment_rules_pkey PRIMARY KEY (rule_id),
            CONSTRAINT vehicle_enrichment_rules_status_values
                CHECK (status IN ({_quoted(RULE_STATUSES)})),
            CONSTRAINT vehicle_enrichment_rules_key_shape
                CHECK (cardinality(key_fields) >= 1
                       AND cardinality(key_fields) = cardinality(key_values)),
            CONSTRAINT vehicle_enrichment_rules_support_positive CHECK (support >= 1),
            CONSTRAINT vehicle_enrichment_rules_agreement_range
                CHECK (agreement >= 0 AND agreement <= 1),
            CONSTRAINT vehicle_enrichment_rules_retired_consistent
                CHECK ((status = 'retired') = (retired_at IS NOT NULL))
            )
            """,
        ),
        # A plate or a full VIN is current on one vehicle only. Chassis numbers are
        # excluded on purpose: old cars share short ones ("000003" on three Volvos).
        (
            "create_vehicle_identifiers_one_current_owner_index",
            ("CREATE UNIQUE INDEX IF NOT EXISTS vehicle_identifiers_one_current_owner "
            f"ON {VEHICLE_IDENTIFIERS_TABLE} (kind, value) "
            "WHERE valid_to IS NULL AND kind IN ('vin', 'plate')"),
        ),
        # And a vehicle never holds the same current identifier twice, which is what
        # keeps a re-run from stacking duplicates of a chassis number.
        (
            "create_vehicle_identifiers_current_per_vehicle_index",
            ("CREATE UNIQUE INDEX IF NOT EXISTS vehicle_identifiers_current_per_vehicle "
            f"ON {VEHICLE_IDENTIFIERS_TABLE} (vehicle_id, kind, value) "
            "WHERE valid_to IS NULL"),
        ),
        (
            "create_vehicle_identifiers_lookup_index",
            ("CREATE INDEX IF NOT EXISTS vehicle_identifiers_lookup "
            f"ON {VEHICLE_IDENTIFIERS_TABLE} (kind, value)"),
        ),
        (
            "create_vehicle_identifiers_vehicle_index",
            ("CREATE INDEX IF NOT EXISTS vehicle_identifiers_vehicle "
            f"ON {VEHICLE_IDENTIFIERS_TABLE} (vehicle_id)"),
        ),
        (
            "create_vehicle_source_links_vehicle_index",
            ("CREATE INDEX IF NOT EXISTS vehicle_source_links_vehicle "
            f"ON {VEHICLE_SOURCE_LINKS_TABLE} (vehicle_id)"),
        ),
        (
            "create_vehicle_enrichment_rules_one_active_index",
            ("CREATE UNIQUE INDEX IF NOT EXISTS vehicle_enrichment_rules_one_active "
            f"ON {VEHICLE_ENRICHMENT_RULES_TABLE} (rule_family, key_values) "
            "WHERE status = 'active'"),
        ),
        ("create_vehicles_no_delete_function", _no_delete_function("vehicles_no_delete", "vehicles")),
        (
            "create_vehicle_identifiers_no_delete_function",
            _no_delete_function("vehicle_identifiers_no_delete", "vehicle identifiers"),
        ),
        (
            "create_vehicle_source_links_no_delete_function",
            _no_delete_function("vehicle_source_links_no_delete", "vehicle source links"),
        ),
    ]
    # A table created before a field existed gets it here; on a fresh table these
    # are no-ops. Same pattern as vehicle_facts, so a new field never needs a
    # hand-written migration to reach live.
    for field in CORE_FIELDS:
        if field.name == "registry_status":
            continue
        statements.append(
            (
                f"add_vehicles_{field.name}_column",
                (f"ALTER TABLE {VEHICLES_TABLE} ADD COLUMN IF NOT EXISTS "
                f"{field.name} {_SQL_TYPES[field.sql_type]}"),
            )
        )
    for table, trigger, function in (
        (VEHICLES_TABLE, "vehicles_no_delete", "vehicles_no_delete"),
        (VEHICLE_IDENTIFIERS_TABLE, "vehicle_identifiers_no_delete", "vehicle_identifiers_no_delete"),
        (VEHICLE_SOURCE_LINKS_TABLE, "vehicle_source_links_no_delete", "vehicle_source_links_no_delete"),
    ):
        drop, create = _no_delete_trigger(table, trigger, function)
        statements.append((f"drop_{trigger}_trigger", drop))
        statements.append((f"create_{trigger}_trigger", create))
    for column in _VEHICLE_INDEX_COLUMNS:
        statements.append(
            (
                f"create_vehicles_{column}_index",
                f"CREATE INDEX IF NOT EXISTS vehicles_{column}_idx ON {VEHICLES_TABLE} ({column})",
            )
        )
    return tuple(statements)


VEHICLE_CORE_MIGRATIONS: tuple[tuple[str, str], ...] = _migrations()

# What `verify_vehicle_core_schema_contract` insists on after every migration: the
# invariants live in these named objects, so their absence is a broken contract,
# not a missing optimization.
REQUIRED_CONSTRAINTS: dict[str, tuple[str, ...]] = {
    "vehicles": (
        "vehicles_pkey",
        "vehicles_vehicle_id_format",
        "vehicles_origin_source_values",
        "vehicles_registry_status_values",
        "vehicles_ts_record_id_key",
        "vehicles_field_sources_object",
        "vehicles_field_alternatives_object",
    ),
    "vehicle_identifiers": (
        "vehicle_identifiers_pkey",
        "vehicle_identifiers_vehicle_fkey",
        "vehicle_identifiers_kind_values",
        "vehicle_identifiers_value_nonempty",
        "vehicle_identifiers_source_nonempty",
        "vehicle_identifiers_valid_period",
    ),
    "vehicle_source_links": (
        "vehicle_source_links_pkey",
        "vehicle_source_links_vehicle_fkey",
        "vehicle_source_links_source_nonempty",
        "vehicle_source_links_key_nonempty",
        "vehicle_source_links_method_values",
    ),
    "vehicle_enrichment_rules": (
        "vehicle_enrichment_rules_pkey",
        "vehicle_enrichment_rules_status_values",
        "vehicle_enrichment_rules_key_shape",
        "vehicle_enrichment_rules_support_positive",
        "vehicle_enrichment_rules_agreement_range",
        "vehicle_enrichment_rules_retired_consistent",
    ),
}
REQUIRED_INDEXES: tuple[str, ...] = (
    "vehicle_identifiers_one_current_owner",
    "vehicle_identifiers_current_per_vehicle",
    "vehicle_enrichment_rules_one_active",
)
REQUIRED_TRIGGERS: dict[str, str] = {
    "vehicles_no_delete": "vehicles",
    "vehicle_identifiers_no_delete": "vehicle_identifiers",
    "vehicle_source_links_no_delete": "vehicle_source_links",
}


class VehicleCoreSchemaContractError(RuntimeError):
    """The vehicle tables exist but do not carry the invariants they must."""


def verify_vehicle_core_schema_contract(connection: Connection) -> None:
    """Fail loudly when a constraint, unique index or trigger is missing or disabled."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_class.relname, constraint_record.conname "
            "FROM pg_constraint AS constraint_record "
            "JOIN pg_class AS table_class ON constraint_record.conrelid = table_class.oid "
            "JOIN pg_namespace AS schema_ns ON table_class.relnamespace = schema_ns.oid "
            "WHERE schema_ns.nspname = %s",
            (CORE_SCHEMA,),
        )
        present = {(str(row[0]), str(row[1])) for row in cursor.fetchall()}
        cursor.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s",
            (CORE_SCHEMA,),
        )
        indexes = {str(row[0]): str(row[1]) for row in cursor.fetchall()}
        cursor.execute(
            "SELECT trigger.tgname, table_class.relname, trigger.tgenabled "
            "FROM pg_trigger AS trigger "
            "JOIN pg_class AS table_class ON trigger.tgrelid = table_class.oid "
            "JOIN pg_namespace AS schema_ns ON table_class.relnamespace = schema_ns.oid "
            "WHERE schema_ns.nspname = %s AND NOT trigger.tgisinternal",
            (CORE_SCHEMA,),
        )
        triggers = {str(row[0]): (str(row[1]), str(row[2])) for row in cursor.fetchall()}

    problems: list[str] = []
    for table, names in REQUIRED_CONSTRAINTS.items():
        problems.extend(
            f"missing constraint {table}.{name}" for name in names if (table, name) not in present
        )
    for name in REQUIRED_INDEXES:
        definition = indexes.get(name)
        if definition is None:
            problems.append(f"missing index {name}")
        elif "UNIQUE" not in definition.upper():
            problems.append(f"index {name} is not unique")
    for name, table in REQUIRED_TRIGGERS.items():
        found = triggers.get(name)
        if found is None:
            problems.append(f"missing trigger {name}")
        elif found[0] != table:
            problems.append(f"trigger {name} is on {found[0]}, expected {table}")
        elif found[1] == "D":
            problems.append(f"trigger {name} is disabled")
    if problems:
        raise VehicleCoreSchemaContractError("; ".join(problems))


def run_vehicle_core_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply the vehicle schema atomically and idempotently, then verify it."""

    try:
        with connection.cursor() as cursor:
            for _, statement in VEHICLE_CORE_MIGRATIONS:
                cursor.execute(statement)
        verify_vehicle_core_schema_contract(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return tuple(name for name, _ in VEHICLE_CORE_MIGRATIONS)
