"""Flat, indexed projection of every vehicle, for filter-first rule authoring.

`staging.transportstyrelsen_raw` and `core.normalization_results` both hold
their interesting values inside JSONB, and neither carries a JSONB index. Any
question phrased over those keys therefore costs a sequential scan of 7.26M
rows: counting one brand takes ~30s, faceting takes ~200s, and a filter joined
against the unresolved predicate does not finish inside three minutes. That is
survivable for a report and fatal for a filter box.

This table projects the columns worth filtering on -- and only those -- into
typed, indexed columns. Detail views still read the source tables by primary
key, so nothing here has to carry a copy of the payloads.

A field is *unresolved* when normalization did not derive it (`n_...`) and no
applied resolution rule has filled it (`r_...`). Both halves are needed: without
the second, a rule would resolve rows and the screen would keep reporting them.
The partial indexes below are built over exactly that predicate, so the
unresolved population is the index rather than something filtered out of a scan.
"""

from __future__ import annotations

from psycopg import Connection

VEHICLE_FACTS_TABLE = "core.vehicle_facts"

# Raw registry keys worth filtering on. Chosen from the observed inventory:
# `plate` and `vin` are unique per row (identity, not a dimension), while
# `odometer`, `inspection`, `ev_15min_power` and `payload_avg` are empty on
# every sampled row. What remains is what a reviewer actually narrows by.
SOURCE_TEXT_COLUMNS: tuple[str, ...] = (
    "brand",
    "model",
    "variant",
    "version",
    "type_text",
    "model_no",
    "group_no",
    "fab_code",
    "base_manufacturer",
    "body_code",
    "body_code2",
    "fuel1",
    "fuel_combo",
    "gearbox",
    "is_4wd",
    "ev_config",
    "eu_category",
    "vehicle_class",
    "euro_class",
    "emission_class",
    "color",
)
SOURCE_INTEGER_COLUMNS: tuple[str, ...] = (
    "vehicle_year",
    "model_year",
    "kw",
    "ccm",
    "passengers",
)

# The fields a resolution rule can target. Deliberately not all 72 normalized
# keys: these are the ones the matcher's signature is built from, so they are
# the ones whose absence actually blocks a match.
NORMALIZED_TEXT_FIELDS: tuple[str, ...] = (
    "manufacturer",
    "model_family",
    "drive_type",
    "bodywork_form",
    "engine_code",
)
NORMALIZED_INTEGER_FIELDS: tuple[str, ...] = (
    "power_kw",
    "displacement_cc",
    "production_year",
)
RESOLVABLE_FIELDS: tuple[str, ...] = (
    NORMALIZED_TEXT_FIELDS + NORMALIZED_INTEGER_FIELDS
)

# Fields whose unresolved population is large enough to deserve its own partial
# index. drive_type is missing on 76.8% of rows and model_family on 41.0%; the
# rest are under 6% and are served well enough by the plain dimension indexes.
_PARTIAL_INDEX_FIELDS: tuple[str, ...] = ("drive_type", "model_family")

_DIMENSION_INDEX_COLUMNS: tuple[str, ...] = (
    "brand",
    "model",
    "variant",
    "type_text",
    "fab_code",
    "vehicle_year",
)


def _column_definitions() -> str:
    parts = [
        "source_record_id BIGINT PRIMARY KEY",
        "plate TEXT",
        "vin TEXT",
    ]
    parts.extend(f"{name} TEXT" for name in SOURCE_TEXT_COLUMNS)
    parts.extend(f"{name} INTEGER" for name in SOURCE_INTEGER_COLUMNS)
    parts.extend(f"n_{name} TEXT" for name in NORMALIZED_TEXT_FIELDS)
    parts.extend(f"n_{name} INTEGER" for name in NORMALIZED_INTEGER_FIELDS)
    # The rule overlay is stored beside the normalized value rather than joined
    # from the resolution ledger: the ledger stays the append-only record, but a
    # join against it on every filter would put the scan back.
    parts.extend(f"r_{name} TEXT" for name in NORMALIZED_TEXT_FIELDS)
    parts.extend(f"r_{name} INTEGER" for name in NORMALIZED_INTEGER_FIELDS)
    parts.extend(
        [
            "norm_status TEXT",
            "confidence REAL",
            "source_batch_id TEXT",
            "refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        ]
    )
    return ",\n            ".join(parts)


def unresolved_predicate(field: str, *, alias: str = "") -> str:
    """SQL for "this field is still missing", for one of RESOLVABLE_FIELDS."""

    if field not in RESOLVABLE_FIELDS:
        raise ValueError(f"{field!r} is not a resolvable field")
    prefix = f"{alias}." if alias else ""
    return f"{prefix}n_{field} IS NULL AND {prefix}r_{field} IS NULL"


def _migrations() -> tuple[tuple[str, str], ...]:
    statements: list[tuple[str, str]] = [
        ("create_core_schema", "CREATE SCHEMA IF NOT EXISTS core"),
        (
            "create_vehicle_facts_table",
            f"""
            CREATE TABLE IF NOT EXISTS {VEHICLE_FACTS_TABLE} (
            {_column_definitions()}
            )
            """,
        ),
    ]
    for column in _DIMENSION_INDEX_COLUMNS:
        statements.append(
            (
                f"create_vehicle_facts_{column}_index",
                f"CREATE INDEX IF NOT EXISTS vehicle_facts_{column}_idx "
                f"ON {VEHICLE_FACTS_TABLE} ({column})",
            )
        )
    for field in _PARTIAL_INDEX_FIELDS:
        statements.append(
            (
                f"create_vehicle_facts_unresolved_{field}_index",
                f"CREATE INDEX IF NOT EXISTS vehicle_facts_unresolved_{field}_idx "
                f"ON {VEHICLE_FACTS_TABLE} (brand, model, variant) "
                f"WHERE {unresolved_predicate(field)}",
            )
        )
    # Refreshes page by primary key, and the resolution job needs to find the
    # rows one rule covers without scanning; both are keyset reads.
    statements.append(
        (
            "create_vehicle_facts_batch_index",
            f"CREATE INDEX IF NOT EXISTS vehicle_facts_batch_idx "
            f"ON {VEHICLE_FACTS_TABLE} (source_batch_id, source_record_id)",
        )
    )
    return tuple(statements)


VEHICLE_FACTS_MIGRATIONS: tuple[tuple[str, str], ...] = _migrations()


def run_vehicle_facts_migrations(connection: Connection) -> tuple[str, ...]:
    """Apply the projection schema atomically and idempotently."""

    try:
        with connection.cursor() as cursor:
            for _, statement in VEHICLE_FACTS_MIGRATIONS:
                cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return tuple(name for name, _ in VEHICLE_FACTS_MIGRATIONS)
