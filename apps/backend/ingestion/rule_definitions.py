"""Import and read versioned rule content stored in PostgreSQL.

A rule version stored here is the set of rows carrying it. Importing seals the
version, so its content cannot change afterwards and a run pinned to it means
the same thing on every machine and at every later date -- the guarantee a bare
`base_rule_version` string cannot make while the catalog behind it is code.

Reading falls back to the Python catalog when a version has no rows, so
versions pinned before this table existed keep resolving exactly as they did.
"""

from __future__ import annotations

import hashlib
import json

from psycopg import Connection

from ingestion.rule_definition_migrations import (
    RULE_DEFINITION_VERSIONS_TABLE,
    RULE_DEFINITIONS_TABLE,
)
from ingestion.translation_dictionaries import TranslationRule, TranslationRuleSet

_COLUMNS = (
    "rule_id, area, source_fields, source_terms, canonical_field, canonical_value, "
    "decision, display_value, vehicle_scopes, manufacturers, requires_electrification"
)


def rule_set_fingerprint(rule_set: TranslationRuleSet) -> str:
    """Return a stable content hash of a rule set.

    Keyed on every field the table stores, in a fixed field order and sorted by
    `rule_id`, so the hash follows content rather than declaration order. The
    version name is deliberately excluded: the question this answers is whether
    two sets say the same thing, not what they are called.
    """

    payload = [
        [
            rule.rule_id,
            rule.area,
            list(rule.source_fields),
            list(rule.source_terms),
            rule.canonical_field,
            rule.canonical_value,
            rule.decision,
            rule.display_value,
            list(rule.vehicle_scopes),
            list(rule.manufacturers),
            rule.requires_electrification,
        ]
        for rule in sorted(rule_set.rules, key=lambda rule: rule.rule_id)
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class RuleDefinitionImportError(RuntimeError):
    """Raised when a rule version cannot be imported safely."""


def rule_version_exists(connection: Connection, rule_version: str) -> bool:
    """True when the version has sealed content in the database."""

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT sealed FROM {RULE_DEFINITION_VERSIONS_TABLE} WHERE rule_version = %s",
            (rule_version,),
        )
        row = cursor.fetchone()
    return bool(row and row[0])


def import_rule_set(
    connection: Connection,
    rule_set: TranslationRuleSet,
    *,
    rule_version: str,
    imported_by: str,
    source_note: str,
) -> dict[str, int]:
    """Store one rule set under a new version and seal it.

    Re-importing an identical set is a no-op. A sealed version whose content
    differs is an error rather than a silent divergence: runs pinned to it
    would otherwise mean two different things.
    """

    if not rule_set.rules:
        raise RuleDefinitionImportError("refusing to import an empty rule set")

    fingerprint = rule_set_fingerprint(rule_set)
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {RULE_DEFINITION_VERSIONS_TABLE} "
            "(rule_version, source_note, imported_by, rule_count, content_fingerprint, sealed) "
            "VALUES (%s, %s, %s, %s, %s, FALSE) ON CONFLICT (rule_version) DO NOTHING",
            (rule_version, source_note, imported_by, len(rule_set.rules), fingerprint),
        )
        created = cursor.rowcount == 1

        cursor.execute(
            f"SELECT sealed, content_fingerprint FROM {RULE_DEFINITION_VERSIONS_TABLE} "
            "WHERE rule_version = %s FOR UPDATE",
            (rule_version,),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuleDefinitionImportError("rule version disappeared during import")
        sealed, recorded_fingerprint = bool(row[0]), row[1]

        if sealed:
            # Compare content, never the rule count: an edited rule leaves the
            # count untouched, so a count check reports a silent no-op and the
            # caller believes an import happened that did not. Versions sealed
            # before the fingerprint column existed carry none, so recompute
            # theirs from the rows actually stored.
            stored = (
                str(recorded_fingerprint)
                if recorded_fingerprint is not None
                else _stored_fingerprint(connection, rule_version)
            )
            if stored != fingerprint:
                raise RuleDefinitionImportError(
                    f"rule version {rule_version!r} is sealed with different content "
                    f"(stored {stored[:12]}, incoming {fingerprint[:12]}); "
                    "import under a new version instead"
                )
            return {"version_created": 0, "rules_inserted": 0, "sealed": 1}

        inserted = 0
        for rule in rule_set.rules:
            cursor.execute(
                f"INSERT INTO {RULE_DEFINITIONS_TABLE} (rule_version, {_COLUMNS}) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (rule_version, rule_id) DO NOTHING",
                (
                    rule_version,
                    rule.rule_id,
                    rule.area,
                    list(rule.source_fields),
                    list(rule.source_terms),
                    rule.canonical_field,
                    rule.canonical_value,
                    rule.decision,
                    rule.display_value,
                    list(rule.vehicle_scopes),
                    list(rule.manufacturers),
                    rule.requires_electrification,
                ),
            )
            inserted += cursor.rowcount

        cursor.execute(
            f"UPDATE {RULE_DEFINITION_VERSIONS_TABLE} SET sealed = TRUE "
            "WHERE rule_version = %s",
            (rule_version,),
        )
    connection.commit()
    return {
        "version_created": int(created),
        "rules_inserted": inserted,
        "sealed": 1,
    }


def rule_definitions_table_exists(connection: Connection) -> bool:
    """True when this database has been migrated to carry rule content.

    A read path must ask before it selects. Selecting from a table that does
    not exist raises `UndefinedTable`, and inside a transaction that error also
    poisons every later statement on the connection, so the failure surfaces
    far from its cause. A database migrated before this table existed is the
    ordinary case rather than a fault, and must read as "no stored content".
    """

    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (RULE_DEFINITIONS_TABLE,))
        row = cursor.fetchone()
    return bool(row and row[0])


def _stored_fingerprint(connection: Connection, rule_version: str) -> str:
    """Fingerprint the rows a version actually holds.

    Only reached for versions sealed before `content_fingerprint` existed. The
    seal guard permits no update but the initial sealing, so such a row cannot
    be backfilled; deriving the hash from its rows costs one read and keeps the
    comparison exact rather than falling back to a weaker check.
    """

    stored = load_rule_set_from_database(connection, rule_version)
    if stored is None:
        raise RuleDefinitionImportError(
            f"rule version {rule_version!r} is sealed but holds no rules"
        )
    return rule_set_fingerprint(stored)


def load_rule_set_from_database(
    connection: Connection, rule_version: str
) -> TranslationRuleSet | None:
    """Return the stored rule set, or None when the version has no rows here.

    None also covers a database without the table at all, which is what every
    database looks like until the rule-definition migration has run on it.
    """

    if not rule_definitions_table_exists(connection):
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {_COLUMNS} FROM {RULE_DEFINITIONS_TABLE} "
            "WHERE rule_version = %s ORDER BY rule_id",
            (rule_version,),
        )
        rows = cursor.fetchall()
    if not rows:
        return None
    return TranslationRuleSet(
        version=rule_version,
        rules=tuple(
            TranslationRule(
                rule_id=str(r[0]),
                area=str(r[1]),  # type: ignore[arg-type]
                source_fields=tuple(r[2]),
                source_terms=tuple(r[3]),
                canonical_field=str(r[4]),
                canonical_value=None if r[5] is None else str(r[5]),
                decision=str(r[6]),  # type: ignore[arg-type]
                display_value=None if r[7] is None else str(r[7]),
                vehicle_scopes=tuple(r[8] or ()),
                manufacturers=tuple(r[9] or ()),
                requires_electrification=bool(r[10]),
            )
            for r in rows
        ),
    )
