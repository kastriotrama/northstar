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
- *Model* rules fill the model family normalization left empty -- 2.37M passenger
  cars whose registry text names only the make ("VOLVO") or a manufacturer code.
  Learned from the TS vehicles that have one, keyed by variant + version, by the
  VIN's manufacturer and descriptor section, by type code, by variant, by the
  registry brand text or by its word after the make. On a 10% holdout of vehicles
  with a known model these keys predict it 99.77-99.99% right.

A rule is kept only when it is common and unanimous enough (by default at least 5
vehicles and 95% agreement). A rule never overrides a value a source stated; it
fills gaps, marks what it filled (`rule:<rule_id>`), and retiring it takes back
exactly what it filled.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from psycopg import Connection

from ingestion.vehicle_core_fields import (
    FIELDS_BY_NAME,
    REGISTRY_EVIDENCE_COLUMNS,
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
from ingestion.vehicle_model_patterns import pattern_model

Purpose = Literal["enrichment", "completion"]
#: What a pattern-proposed rule was learned from: the reviewed patterns, not a source.
PATTERN_SOURCE = "reviewed-pattern"
#: The statistical family keyed like the patterns; a key it learns needs no pattern.
STATISTICS_FAMILY_OF_PATTERNS = "MOD-BT"

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
    #: The family's own evidence bar; a caller's explicit threshold overrides it.
    min_support: int = DEFAULT_MIN_SUPPORT
    min_agreement: float = DEFAULT_MIN_AGREEMENT
    #: "statistics": learned from agreeing vehicles. "patterns": proposed by the
    #: reviewed patterns in `vehicle_model_patterns`, then checked against them.
    learner: Literal["statistics", "patterns"] = "statistics"


#: Keys computed from a vehicle's columns rather than read from one. A VIN's first
#: eight characters are the manufacturer (WMI) and the descriptor section (VDS),
#: which names the model; only a full 17-character VIN has them in those places.
#
#: Registry brand text ("TOYOTA RAV4", "BMW 320I TOURING") names the model after
#: the make. Text that is only the make ("POLESTAR", "VOLVO") names no model: a
#: rule learned from it would give every later model of a one-model make that
#: make's first one, so such text yields no key. The token is the first word after
#: the make, unless it repeats the make ("TOYOTA TOYOTA RAV4"); a two-word make's
#: second word ("MERCEDES BENZ 200 D", "ALFA ROMEO 156") is part of the make. Volvo registered its
#: 1990s cars as "<model-year letter> + <model>" ("VOLVO S + V70", "VOLVO 9 + 940"):
#: there the token is the word after the plus.
_BRAND = "upper(btrim({alias}registry_brand_text))"
_AFTER_MAKE = (
    f"CASE WHEN strpos({_BRAND}, ' + ') > 0 "
    f"THEN split_part(btrim(split_part({_BRAND}, ' + ', 2)), ' ', 1) "
    f"ELSE split_part(regexp_replace({_BRAND}, '^[^ ]+ +((BENZ|ROMEO|ROVER|MARTIN|ROYCE) +)?', ''), "
    "' ', 1) END"
)
KEY_EXPRESSIONS: dict[str, str] = {
    "vin_descriptor": "CASE WHEN length({alias}vin) = 17 THEN left({alias}vin, 8) END",
    "brand_text": f"CASE WHEN strpos({_BRAND}, ' ') > 0 THEN {_BRAND} END",
    "brand_token": (
        f"CASE WHEN strpos({_BRAND}, ' ') > 0 AND {_AFTER_MAKE} <> '' "
        f"AND {_AFTER_MAKE} <> split_part({_BRAND}, ' ', 1) THEN {_AFTER_MAKE} END"
    ),
}


def key_sql(field: str, alias: str = "") -> str:
    """The SQL for one key field, qualified by `alias` when given."""

    prefix = f"{alias}." if alias else ""
    expression = KEY_EXPRESSIONS.get(field)
    return f"({expression.format(alias=prefix)})" if expression else f"{prefix}{field}"


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
    # A model is identity, not a detail: the bar is higher than for the fields above.
    RuleFamily("MOD-VV", "model_family", ("registry_make_code", "variant_code", "version_code"),
               SOURCE_TS, "enrichment", "Model family by make, variant and version",
               min_support=10, min_agreement=0.98),
    RuleFamily("MOD-VIN", "model_family", ("manufacturer", "vin_descriptor"),
               SOURCE_TS, "enrichment", "Model family by manufacturer and VIN descriptor",
               min_support=10, min_agreement=0.98),
    RuleFamily("MOD-TP", "model_family", ("registry_make_code", "registry_type_code"),
               SOURCE_TS, "enrichment", "Model family by make and type code",
               min_support=10, min_agreement=0.98),
    RuleFamily("MOD-VAR", "model_family", ("registry_make_code", "variant_code"),
               SOURCE_TS, "enrichment", "Model family by make and variant",
               min_support=10, min_agreement=0.98),
    RuleFamily("MOD-BR", "model_family", ("registry_make_code", "brand_text"),
               SOURCE_TS, "enrichment", "Model family by make and registry brand text",
               min_support=10, min_agreement=0.98),
    RuleFamily("MOD-BT", "model_family", ("registry_make_code", "brand_token"),
               SOURCE_TS, "enrichment", "Model family by make and the brand text's word after the make",
               min_support=10, min_agreement=0.98),
    # Last: only for words no sibling taught, such as an old Volvo's type code.
    RuleFamily("MOD-PAT", "model_family", ("registry_make_code", "brand_token"),
               PATTERN_SOURCE, "enrichment", "Model family read from the brand text by reviewed patterns",
               min_support=1, min_agreement=0.98, learner="patterns"),
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
    keys = ", ".join(
        f"{key_sql(field)}::text AS k{index}" for index, field in enumerate(family.key_fields)
    )
    key_names = ", ".join(f"k{index}" for index in range(len(family.key_fields)))
    not_null = " AND ".join(
        f"{key_sql(field)} IS NOT NULL" for field in (*family.key_fields, family.target_field)
    )
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
    min_support: int | None = None,
    min_agreement: float | None = None,
) -> list[LearnedRule]:
    """Every key the family can state a value for, with the evidence behind it.

    Thresholds default to the family's own bar.
    """

    support = family.min_support if min_support is None else min_support
    agreement = family.min_agreement if min_agreement is None else min_agreement
    if family.learner == "patterns":
        return pattern_rules(connection, family, min_support=support, min_agreement=agreement)
    with connection.cursor() as cursor:
        cursor.execute(learn_statement(family), (family.learned_from, support, agreement))
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


#: A make's model families as TS spells them: what a pattern may answer with. A
#: name on fewer vehicles than this is too rare to be trusted as vocabulary.
VOCABULARY_MIN_VEHICLES = 3
#: Known vehicles under a pattern key that may disagree with it: one stray car
#: ("307 Cc" among 307s), or the family's tolerance once there are many.
PATTERN_TOLERATED_DISAGREEMENTS = 1


def pattern_rules(
    connection: Connection,
    family: RuleFamily,
    *,
    min_support: int,
    min_agreement: float,
) -> list[LearnedRule]:
    """Rules the reviewed patterns propose for every make + model word in the data.

    Each proposal is checked against the vehicles whose TS-stated model is known
    under the same key. More than one of them disagreeing (more than
    `1 - min_agreement` of them, when there are many) means the word is shared by
    models -- "C4" is also the C4 Grand Picasso -- and no rule is made. A key the
    statistics already learn (`statistics_family`) needs no pattern, and a key
    shared by two makes is skipped.
    """

    statistics_family = FAMILIES_BY_ID[STATISTICS_FAMILY_OF_PATTERNS]

    make_field, token_field = family.key_fields
    token = key_sql(token_field)
    source = _SOURCE_OF.format(field=family.target_field)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT manufacturer, {family.target_field}
            FROM {VEHICLES_TABLE}
            WHERE {family.target_field} IS NOT NULL AND manufacturer IS NOT NULL AND {source} = %s
            GROUP BY 1, 2 HAVING count(*) >= %s
            """,
            (SOURCE_TS, VOCABULARY_MIN_VEHICLES),
        )
        vocabulary: dict[str, set[str]] = {}
        for manufacturer, model in cursor.fetchall():
            vocabulary.setdefault(str(manufacturer), set()).add(str(model))
        cursor.execute(
            f"""
            SELECT {make_field}::text, {token}, array_agg(DISTINCT manufacturer), count(*),
                   count(*) FILTER (WHERE {family.target_field} IS NULL)
            FROM {VEHICLES_TABLE}
            WHERE {make_field} IS NOT NULL AND {token} IS NOT NULL AND manufacturer IS NOT NULL
            GROUP BY 1, 2
            """
        )
        keys = cursor.fetchall()
        cursor.execute(
            f"""
            SELECT {make_field}::text, {token}, {family.target_field}, count(*)
            FROM {VEHICLES_TABLE}
            WHERE {make_field} IS NOT NULL AND {token} IS NOT NULL
              AND {family.target_field} IS NOT NULL AND {source} = %s
            GROUP BY 1, 2, 3
            """,
            (SOURCE_TS,),
        )
        known: dict[tuple[str, str], dict[str, int]] = {}
        for make, word, model, count in cursor.fetchall():
            known.setdefault((str(make), str(word)), {})[str(model)] = int(count)
    rules: list[LearnedRule] = []
    for make, word, manufacturers, total, missing in keys:
        if len(manufacturers) != 1 or not missing or int(total) < min_support:
            continue
        stated = known.get((str(make), str(word)), {})
        stated_total = sum(stated.values())
        if (
            stated_total >= statistics_family.min_support
            and max(stated.values()) >= statistics_family.min_agreement * stated_total
        ):
            continue
        manufacturer = str(manufacturers[0])
        value = pattern_model(manufacturer, str(word), vocabulary.get(manufacturer, ()))
        if value is None:
            continue
        disagreeing = stated_total - stated.get(value, 0)
        if disagreeing > max(PATTERN_TOLERATED_DISAGREEMENTS, int((1 - min_agreement) * stated_total)):
            continue
        key_values = (str(make), str(word))
        rules.append(
            LearnedRule(
                rule_id=rule_id_for(family.family, key_values, value),
                family=family.family,
                target_field=family.target_field,
                key_fields=family.key_fields,
                key_values=key_values,
                value=value,
                support=int(total),
                agreement=round(stated.get(value, 0) / stated_total, 4) if stated_total else 1.0,
            )
        )
    return rules


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
    #: Fills the model guard refused, by reason.
    refused: dict[str, int] = field(default_factory=dict)


class ModelChecker(Protocol):
    """What judges a learned model against the car (`vehicle_model_guard.ModelGuard`)."""

    def verdict(
        self,
        *,
        manufacturer: str | None,
        model_family: str,
        evidence: Mapping[str, str | None],
    ) -> object | None: ...


_EVIDENCE_SELECT = ", ".join(f"v.{column}" for column in REGISTRY_EVIDENCE_COLUMNS.values())


def apply_rules(
    connection: Connection,
    family: RuleFamily,
    *,
    source_batch_id: str | None = None,
    guard: ModelChecker | None = None,
) -> ApplySummary:
    """Fill the family's target on every vehicle that lacks it and matches a key.

    Set-based: one UPDATE joins the family's active rules to the vehicles. Each
    fill is marked `rule:<rule_id>` and recorded in the ledger with the rule's
    agreement as its confidence -- a learned value is evidence, not a fact.

    A model family needs `guard`: every fill is first checked against the model
    word in the car's own registry text, and a contradicted fill is not made.
    """

    if family.purpose != "enrichment":
        raise ValueError(f"{family.family} completes AIS records before normalization")
    if family.target_field == "model_family":
        if guard is None:
            raise ValueError(f"{family.family} fills models: pass a guard (the catalog batch)")
        return _apply_guarded(connection, family, guard, source_batch_id)
    target = family.target_field
    sql_type = FIELDS_BY_NAME[target].sql_type
    cast = {"integer": "::integer", "smallint": "::smallint", "boolean": "::boolean"}.get(sql_type, "")
    key_match = " AND ".join(
        f"{key_sql(field, 'v')}::text = r.key_values[{index + 1}]"
        for index, field in enumerate(family.key_fields)
    )
    with connection.cursor() as cursor:
        # Fresh statistics first. Learning adds tens of thousands of rules at once;
        # planned against the old statistics the join looked like a handful of rules
        # and became a nested loop that rescanned 738k vehicles once per rule -- ten
        # minutes of CPU for 31k fills that take seconds as the merge join it is.
        cursor.execute(f"ANALYZE {VEHICLE_ENRICHMENT_RULES_TABLE}")
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


#: Years either side of a number rule's learned era it still fills.
ERA_SLACK = 2


def number_rule_eras(connection: Connection) -> dict[str, tuple[int, int]]:
    """The build years each model rule keyed on a number was learned from.

    A number in brand text is a chassis code as often as an engine size: "220"
    names the W220 S-Class the rule was learned from (1998-2005), but also the
    1970s "220 D" saloons that share no model with it. Such a rule only fills a
    car built in the era of the known vehicles it was learned from. A word key
    ("GOLF") has no era, and neither has a number that is itself one of the
    make's model names: a Mazda 6 or a Fiat 500 is that model in any decade.
    """

    family = FAMILIES_BY_ID[STATISTICS_FAMILY_OF_PATTERNS]
    make_field, token_field = family.key_fields
    source = _SOURCE_OF.format(field=family.target_field)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH names AS (
                SELECT DISTINCT {make_field}::text AS make, upper({family.target_field}) AS name
                FROM {VEHICLES_TABLE}
                WHERE {family.target_field} ~ '^[0-9]+$' AND {make_field} IS NOT NULL AND {source} = %s
            )
            SELECT r.rule_id, min(v.production_year), max(v.production_year)
            FROM {VEHICLE_ENRICHMENT_RULES_TABLE} AS r
            JOIN {VEHICLES_TABLE} AS v
              ON v.{make_field}::text = r.key_values[1] AND {key_sql(token_field, 'v')} = r.key_values[2]
             AND v.{family.target_field} = r.value
            WHERE r.rule_family = %s AND r.status = 'active' AND r.key_values[2] ~ '^[0-9]+$'
              AND NOT EXISTS (SELECT 1 FROM names WHERE names.make = r.key_values[1]
                              AND names.name = r.key_values[2])
              AND v.production_year IS NOT NULL AND {source} = %s
            GROUP BY r.rule_id
            """,
            (SOURCE_TS, family.family, SOURCE_TS),
        )
        return {str(rule_id): (int(first), int(last)) for rule_id, first, last in cursor.fetchall()}


def _outside_era(eras: Mapping[str, tuple[int, int]], rule_id: str, year: int | None) -> bool:
    era = eras.get(rule_id)
    return era is not None and year is not None and not era[0] - ERA_SLACK <= year <= era[1] + ERA_SLACK


def _apply_guarded(
    connection: Connection, family: RuleFamily, guard: ModelChecker, source_batch_id: str | None
) -> ApplySummary:
    target = family.target_field
    key_match = " AND ".join(
        f"{key_sql(key, 'v')}::text = r.key_values[{index + 1}]"
        for index, key in enumerate(family.key_fields)
    )
    eras = number_rule_eras(connection) if family.family == STATISTICS_FAMILY_OF_PATTERNS else {}
    with connection.cursor() as cursor:
        cursor.execute(f"ANALYZE {VEHICLE_ENRICHMENT_RULES_TABLE}")
        cursor.execute(
            f"""
            SELECT v.vehicle_id, r.rule_id, r.value, r.agreement, v.manufacturer, v.production_year,
                   {_EVIDENCE_SELECT}
            FROM {VEHICLES_TABLE} AS v
            JOIN {VEHICLE_ENRICHMENT_RULES_TABLE} AS r
              ON r.rule_family = %s AND r.status = 'active' AND {key_match}
            WHERE v.{target} IS NULL
            """,
            (family.family,),
        )
        proposed = cursor.fetchall()
        refused: Counter[str] = Counter()
        accepted = []
        for vehicle_id, rule_id, value, agreement, manufacturer, year, *texts in proposed:
            if _outside_era(eras, str(rule_id), year):
                refused["outside_learned_era"] += 1
                continue
            verdict = guard.verdict(
                manufacturer=manufacturer, model_family=str(value),
                evidence=dict(zip(REGISTRY_EVIDENCE_COLUMNS, texts, strict=True)),
            )
            if verdict is None:
                accepted.append((vehicle_id, rule_id, value, agreement))
            else:
                refused[str(getattr(verdict, "reason", verdict))] += 1
        cursor.execute(
            "CREATE TEMP TABLE IF NOT EXISTS guarded_fill "
            "(vehicle_id TEXT, rule_id TEXT, value TEXT, agreement REAL) ON COMMIT DROP"
        )
        cursor.execute("TRUNCATE guarded_fill")
        with cursor.copy("COPY guarded_fill FROM STDIN") as copy:
            for row in accepted:
                copy.write_row(row)
        cursor.execute(
            f"""
            UPDATE {VEHICLES_TABLE} AS v
            SET {target} = f.value,
                field_sources = v.field_sources || jsonb_build_object('{target}', 'rule:' || f.rule_id),
                updated_at = now()
            FROM guarded_fill AS f
            WHERE v.vehicle_id = f.vehicle_id AND v.{target} IS NULL
            RETURNING v.vehicle_id, f.rule_id, f.value, f.agreement
            """
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
    return ApplySummary(family.family, filled=len(rows), refused=dict(refused))


@dataclass
class CheckSummary:
    checked: int = 0
    contradicted: dict[str, int] = field(default_factory=dict)
    retracted: int = 0
    examples: list[dict[str, object]] = field(default_factory=list)


_EXAMPLES = 25
_RETRACT_PAGE = 5000


def check_model_fills(
    connection: Connection, guard: ModelChecker, *, retract_contradicted: bool = False
) -> CheckSummary:
    """Check every model a rule filled against its car, and take back contradicted ones.

    Fills made before the guard existed, or before a catalog or text changed, are
    judged the same way new fills are. Without `retract_contradicted` it only counts.
    """

    summary = CheckSummary()
    contradicted: Counter[str] = Counter()
    retract_ids: list[tuple[str, str]] = []
    eras = number_rule_eras(connection)
    with connection.cursor(name="model_fill_check") as cursor:
        cursor.itersize = 20000
        cursor.execute(
            f"""
            SELECT v.vehicle_id, substring(v.field_sources ->> 'model_family' from 6), v.model_family,
                   v.manufacturer, v.production_year, {_EVIDENCE_SELECT}
            FROM {VEHICLES_TABLE} AS v
            WHERE v.field_sources ->> 'model_family' LIKE 'rule:MOD-%'
            """
        )
        for vehicle_id, rule_id, value, manufacturer, year, *texts in cursor:
            summary.checked += 1
            evidence = dict(zip(REGISTRY_EVIDENCE_COLUMNS, texts, strict=True))
            verdict: object | None
            if _outside_era(eras, str(rule_id), year):
                verdict, reason = "outside_learned_era", "outside_learned_era"
            else:
                verdict = guard.verdict(manufacturer=manufacturer, model_family=str(value), evidence=evidence)
                if verdict is None:
                    continue
                reason = str(getattr(verdict, "reason", verdict))
            contradicted[reason] += 1
            retract_ids.append((str(vehicle_id), str(rule_id)))
            if len(summary.examples) < _EXAMPLES:
                summary.examples.append({
                    "manufacturer": manufacturer, "brand_text": evidence.get("brand"),
                    "model_text": evidence.get("model"), "filled": value,
                    "reason": reason, "detail": getattr(verdict, "detail", None),
                })
    summary.contradicted = dict(contradicted)
    if retract_contradicted:
        for start in range(0, len(retract_ids), _RETRACT_PAGE):
            page = dict(retract_ids[start : start + _RETRACT_PAGE])
            states = load_vehicles(connection, list(page))
            for vehicle_id, state in states.items():
                retract(state, "model_family", SOURCE_RULE, page[vehicle_id])
            save_vehicles(connection, states.values())
            summary.retracted += len(states)
    return summary


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

