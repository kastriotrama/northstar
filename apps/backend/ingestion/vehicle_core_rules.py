"""Learned enrichment rules: "every vehicle with this key has this value".

Two kinds, both learned from `core.vehicles` itself and stored as rows of
`core.vehicle_enrichment_rules`:

- *Enrichment* rules learn a value TS never had from the vehicles a provider
  stated it for, and fill it in where no provider did. The engine code is the
  main one: within one make + variant + version the AIS engine code agrees on
  98.1% of cars, so a TS car the AIS export does not cover -- a future TS import,
  a car AIS left blank -- can still get one.
- *Completion* rules learn TS-only fields from TS vehicles, keyed by make + group
  code, for the vehicles the AIS export adds that TS never had: their records
  lack an EU category, a displacement, a variant, and the normalizer needs them.

A rule is kept only when it is common and unanimous enough (by default at least 5
vehicles and 95% agreement). A rule never overrides a value a source stated; it
fills gaps, marks what it filled (`rule:<rule_id>`), and retiring it takes back
exactly what it filled.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from psycopg import Connection

from ingestion.vehicle_core_fields import (
    FIELDS_BY_NAME,
    SOURCE_AIS,
    SOURCE_RULE,
    SOURCE_TS,
    SourceRef,
)
from ingestion.vehicle_core_merge import retract
from ingestion.vehicle_core_migrations import VEHICLE_ENRICHMENT_RULES_TABLE, VEHICLES_TABLE
from ingestion.vehicle_core_store import (
    LedgerRow,
    ledger_event_id,
    load_vehicles,
    record_ledger_rows,
    save_vehicles,
)

Purpose = Literal["enrichment", "completion"]

DEFAULT_MIN_SUPPORT = 5
DEFAULT_MIN_AGREEMENT = 0.95


@dataclass(frozen=True)
class RuleFamily:
    family: str
    target_field: str
    key_fields: tuple[str, ...]
    learned_from: str
    purpose: Purpose
    description: str


# Order matters within a target: a more specific key is tried first, so the engine
# code comes from variant + version before falling back to the group code.
RULE_FAMILIES: tuple[RuleFamily, ...] = (
    RuleFamily("ENG-VV", "engine_code", ("registry_make_code", "variant_code", "version_code"),
               SOURCE_AIS, "enrichment", "Engine code by make, variant and version"),
    RuleFamily("ENG-GC", "engine_code", ("registry_make_code", "group_code"),
               SOURCE_AIS, "enrichment", "Engine code by make and group code"),
    RuleFamily("ENG-TP", "engine_code",
               ("registry_make_code", "registry_type_code", "displacement_cc", "power_kw", "fuel"),
               SOURCE_AIS, "enrichment", "Engine code by make, type, displacement, power and fuel"),
    RuleFamily("MY-VB", "model_year",
               ("registry_make_code", "registry_vehicle_year", "production_year", "production_month"),
               SOURCE_AIS, "enrichment", "Model year by make, vehicle year and build month"),
    RuleFamily("MW-VV", "max_weight_kg", ("registry_make_code", "variant_code", "version_code"),
               SOURCE_AIS, "enrichment", "Max weight by make, variant and version"),
    RuleFamily("LEN-VV", "length_mm", ("registry_make_code", "variant_code", "version_code"),
               SOURCE_AIS, "enrichment", "Length by make, variant and version"),
    RuleFamily("TSC-EU", "eu_category", ("registry_make_code", "group_code"),
               SOURCE_TS, "completion", "EU category by make and group code"),
    RuleFamily("TSC-BRAND", "registry_brand_text", ("registry_make_code", "group_code"),
               SOURCE_TS, "completion", "Registry brand text by make and group code"),
    RuleFamily("TSC-MODEL", "registry_model_text", ("registry_make_code", "group_code"),
               SOURCE_TS, "completion", "Registry model text by make and group code"),
    RuleFamily("TSC-TYPE", "registry_type_code", ("registry_make_code", "group_code"),
               SOURCE_TS, "completion", "Registry type code by make and group code"),
    RuleFamily("TSC-VAR", "variant_code", ("registry_make_code", "group_code"),
               SOURCE_TS, "completion", "Variant by make and group code"),
    RuleFamily("TSC-CCM", "displacement_cc", ("registry_make_code", "group_code"),
               SOURCE_TS, "completion", "Displacement by make and group code"),
    RuleFamily("TSC-4WD", "registry_all_wheel_drive", ("registry_make_code", "group_code"),
               SOURCE_TS, "completion", "Registry 4WD flag by make and group code"),
)
FAMILIES_BY_ID: dict[str, RuleFamily] = {family.family: family for family in RULE_FAMILIES}
COMPLETION_FAMILIES: tuple[RuleFamily, ...] = tuple(
    family for family in RULE_FAMILIES if family.purpose == "completion"
)

_SOURCE_OF = (
    "substring(coalesce(field_sources ->> '{field}', origin_source) from '^[a-z_]+')"
)


def rule_id_for(family: str, key_values: Sequence[str], value: str) -> str:
    """Derived from the rule's whole content, so an id always means one assertion."""

    digest = hashlib.sha256("\x1f".join((family, *key_values, "=", value)).encode()).hexdigest()
    return f"{family}-{digest[:16]}"


@dataclass(frozen=True)
class LearnedRule:
    rule_id: str
    family: str
    target_field: str
    key_fields: tuple[str, ...]
    key_values: tuple[str, ...]
    value: str
    support: int
    agreement: float


def learn_statement(family: RuleFamily) -> str:
    keys = ", ".join(f"{field}::text AS k{index}" for index, field in enumerate(family.key_fields))
    key_names = ", ".join(f"k{index}" for index in range(len(family.key_fields)))
    not_null = " AND ".join(f"{field} IS NOT NULL" for field in (*family.key_fields, family.target_field))
    source = _SOURCE_OF.format(field=family.target_field)
    return f"""
        WITH training AS (
            SELECT {keys}, {family.target_field}::text AS value
            FROM {VEHICLES_TABLE}
            WHERE {not_null} AND {source} = %s
        ),
        grouped AS (
            SELECT {key_names}, value, count(*) AS n FROM training GROUP BY {key_names}, value
        ),
        ranked AS (
            SELECT {key_names}, value, n,
                   sum(n) OVER (PARTITION BY {key_names}) AS total,
                   row_number() OVER (PARTITION BY {key_names} ORDER BY n DESC, value) AS rank
            FROM grouped
        )
        SELECT ARRAY[{key_names}], value, n::int, total::int
        FROM ranked
        WHERE rank = 1 AND total >= %s AND n >= %s * total
    """


def learn_rules(
    connection: Connection,
    family: RuleFamily,
    *,
    min_support: int = DEFAULT_MIN_SUPPORT,
    min_agreement: float = DEFAULT_MIN_AGREEMENT,
) -> list[LearnedRule]:
    """Every key the family can state a value for, with the evidence behind it."""

    with connection.cursor() as cursor:
        cursor.execute(learn_statement(family), (family.learned_from, min_support, min_agreement))
        rows = cursor.fetchall()
    return [
        LearnedRule(
            rule_id=rule_id_for(family.family, tuple(str(k) for k in keys), str(value)),
            family=family.family,
            target_field=family.target_field,
            key_fields=family.key_fields,
            key_values=tuple(str(value) for value in keys),
            value=str(value),
            support=int(total),
            agreement=round(int(n) / int(total), 4),
        )
        for keys, value, n, total in rows
    ]


@dataclass
class StoreSummary:
    family: str
    added: int = 0
    kept: int = 0
    retired: int = 0


def store_rules(
    connection: Connection, family: RuleFamily, rules: Sequence[LearnedRule], *, learned_from: str
) -> StoreSummary:
    """Make the family's active rules exactly `rules`.

    A rule whose key and value are unchanged stays as it is -- its id and what it
    filled remain valid. A key whose value changed, or that no longer qualifies,
    is retired, and the new rule gets a new id: a rule's content never changes
    under its id. Callers commit.
    """

    summary = StoreSummary(family.family)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT rule_id, key_values, value FROM {VEHICLE_ENRICHMENT_RULES_TABLE} "
            "WHERE rule_family = %s AND status = 'active'",
            (family.family,),
        )
        active = {tuple(keys): (str(rule_id), str(value)) for rule_id, keys, value in cursor.fetchall()}
        wanted = {rule.key_values: rule for rule in rules}
        retire = [
            rule_id
            for keys, (rule_id, value) in active.items()
            if keys not in wanted or wanted[keys].value != value
        ]
        if retire:
            cursor.execute(
                f"UPDATE {VEHICLE_ENRICHMENT_RULES_TABLE} SET status = 'retired', retired_at = now() "
                "WHERE rule_id = ANY(%s)",
                (retire,),
            )
            summary.retired = len(retire)
        fresh = [
            rule
            for rule in rules
            if rule.key_values not in active or active[rule.key_values][1] != rule.value
        ]
        summary.kept = len(rules) - len(fresh)
        if fresh:
            cursor.execute(
                "CREATE TEMP TABLE IF NOT EXISTS vehicle_rule_page "
                f"(LIKE {VEHICLE_ENRICHMENT_RULES_TABLE} INCLUDING DEFAULTS) ON COMMIT DELETE ROWS"
            )
            cursor.execute("TRUNCATE vehicle_rule_page")
            with cursor.copy(
                "COPY vehicle_rule_page (rule_id, rule_family, target_field, key_fields, "
                "key_values, value, support, agreement, learned_from) FROM STDIN"
            ) as copy:
                for rule in fresh:
                    copy.write_row(
                        (rule.rule_id, rule.family, rule.target_field, list(rule.key_fields),
                         list(rule.key_values), rule.value, rule.support, rule.agreement,
                         learned_from)
                    )
            # A rule learned again after it was retired comes back under its own id:
            # same content, same id, so what it filled before stays attributable.
            cursor.execute(
                f"""
                INSERT INTO {VEHICLE_ENRICHMENT_RULES_TABLE} (rule_id, rule_family, target_field,
                    key_fields, key_values, value, support, agreement, learned_from)
                SELECT rule_id, rule_family, target_field, key_fields, key_values, value,
                       support, agreement, learned_from
                FROM vehicle_rule_page
                ON CONFLICT (rule_id) DO UPDATE
                    SET status = 'active', retired_at = NULL, support = EXCLUDED.support,
                        agreement = EXCLUDED.agreement, learned_from = EXCLUDED.learned_from
                """
            )
            summary.added = len(fresh)
    return summary


@dataclass
class ApplySummary:
    family: str
    filled: int = 0


def apply_rules(
    connection: Connection,
    family: RuleFamily,
    *,
    source_batch_id: str | None = None,
) -> ApplySummary:
    """Fill the family's target on every vehicle that lacks it and matches a key.

    Set-based: one UPDATE joins the family's active rules to the vehicles. Each
    fill is marked `rule:<rule_id>` and recorded in the ledger with the rule's
    agreement as its confidence -- a learned value is evidence, not a fact.
    """

    if family.purpose != "enrichment":
        raise ValueError(f"{family.family} completes AIS records before normalization")
    target = family.target_field
    sql_type = FIELDS_BY_NAME[target].sql_type
    cast = {"integer": "::integer", "smallint": "::smallint", "boolean": "::boolean"}.get(sql_type, "")
    key_match = " AND ".join(
        f"v.{field}::text = r.key_values[{index + 1}]" for index, field in enumerate(family.key_fields)
    )
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH filled AS (
                UPDATE {VEHICLES_TABLE} AS v
                SET {target} = r.value{cast},
                    field_sources = v.field_sources
                        || jsonb_build_object('{target}', 'rule:' || r.rule_id),
                    updated_at = now()
                FROM {VEHICLE_ENRICHMENT_RULES_TABLE} AS r
                WHERE r.rule_family = %s AND r.status = 'active'
                  AND v.{target} IS NULL
                  AND {key_match}
                RETURNING v.vehicle_id, r.rule_id, r.value, r.agreement
            )
            SELECT vehicle_id, rule_id, value, agreement FROM filled
            """,
            (family.family,),
        )
        rows = cursor.fetchall()
    record_ledger_rows(
        connection,
        [
            LedgerRow(
                event_id=ledger_event_id("rule", str(rule_id), str(vehicle_id)),
                source=SOURCE_RULE,
                target_node_id=str(vehicle_id),
                attributes_added=(target,),
                confidence=float(agreement),
                evidence={target: {"to": value, "rule_id": rule_id}},
                source_batch_id=source_batch_id or str(rule_id),
            )
            for vehicle_id, rule_id, value, agreement in rows
        ],
    )
    return ApplySummary(family.family, filled=len(rows))


def retire_rule(connection: Connection, rule_id: str) -> int:
    """Retire one rule and take back exactly what it filled."""

    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {VEHICLE_ENRICHMENT_RULES_TABLE} SET status = 'retired', retired_at = now() "
            "WHERE rule_id = %s AND status = 'active' RETURNING target_field",
            (rule_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return 0
        target = str(row[0])
        cursor.execute(
            f"SELECT vehicle_id FROM {VEHICLES_TABLE} WHERE field_sources ->> %s = %s",
            (target, f"rule:{rule_id}"),
        )
        vehicle_ids = [str(r[0]) for r in cursor.fetchall()]
    states = load_vehicles(connection, vehicle_ids)
    for state in states.values():
        retract(state, target, SOURCE_RULE, rule_id)
    save_vehicles(connection, states.values())
    return len(states)


# --- completion rules, read by the AIS import ---------------------------------------


@dataclass(frozen=True)
class CompletionRules:
    """Active completion rules by family, keyed by (make code, group code)."""

    rules: Mapping[str, Mapping[tuple[str, ...], tuple[str, str]]]

    def lookup(self, family: str, key: tuple[str, ...]) -> tuple[str, str] | None:
        return self.rules.get(family, {}).get(key)


def load_completion_rules(connection: Connection) -> CompletionRules:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT rule_family, key_values, value, rule_id FROM {VEHICLE_ENRICHMENT_RULES_TABLE} "
            "WHERE status = 'active' AND rule_family = ANY(%s)",
            ([family.family for family in COMPLETION_FAMILIES],),
        )
        rules: dict[str, dict[tuple[str, ...], tuple[str, str]]] = {}
        for family, keys, value, rule_id in cursor.fetchall():
            rules.setdefault(str(family), {})[tuple(keys)] = (str(value), str(rule_id))
    return CompletionRules(rules)


def rule_ref(rule_id: str) -> SourceRef:
    return SourceRef(SOURCE_RULE, rule_id)

