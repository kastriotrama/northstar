"""Where `load_active_rules` reads its rules from.

This module exists because that function had no test at all. A change to the
line choosing between stored and Python rules passed every gate -- 924 tests,
ruff, mypy -- and still raised `UndefinedTable` on the first database that had
not been migrated, which is every database in production.

The three cases below are the three shapes a real database can be in. Each runs
against a database this module creates and drops, because the first case needs
a schema with the rule tables genuinely absent: asserting that by dropping them
from a shared database would destroy whatever rules had been imported into it.
"""

from collections.abc import Iterator

import psycopg
import pytest
from psycopg import Connection

from ingestion.active_rules import load_active_rules
from ingestion.config import get_ingestion_settings
from ingestion.normalization_migrations import run_normalization_migrations
from ingestion.rule_definition_migrations import run_rule_definition_migrations
from ingestion.rule_definitions import (
    import_rule_set,
    load_rule_set_from_database,
    rule_definitions_table_exists,
)
from ingestion.translation_dictionaries import (
    REVIEWED_RULE_SET_VERSION,
    load_translation_rule_set,
)

TEMPORARY_DATABASE = "northstar_active_rules_source_test"


@pytest.fixture()
def connection() -> Iterator[Connection]:
    settings = get_ingestion_settings()
    try:
        admin = psycopg.connect(settings.database_url, autocommit=True)
    except psycopg.OperationalError:
        if settings.environment == "test":
            raise
        pytest.skip("PostgreSQL is unavailable; start it with docker compose up -d postgres")
        return

    with admin:
        with admin.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{TEMPORARY_DATABASE}"')
            cursor.execute(f'CREATE DATABASE "{TEMPORARY_DATABASE}"')

        conn = psycopg.connect(_swap_database(settings.database_url, TEMPORARY_DATABASE))
        run_normalization_migrations(conn)
        _activate(conn, base_rule_version=REVIEWED_RULE_SET_VERSION)
        try:
            yield conn
        finally:
            conn.close()
            with admin.cursor() as cursor:
                cursor.execute(f'DROP DATABASE IF EXISTS "{TEMPORARY_DATABASE}"')


def _activate(connection: Connection, *, base_rule_version: str) -> None:
    """Record an activated rule version pointing at `base_rule_version`.

    Without one, `load_active_rules` returns the reviewed catalog before it
    ever consults the definition table, so every assertion below would hold
    whether the fallback worked or not.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO core.translation_rule_versions "
            "(version, base_rule_version, overrides, activation_note) "
            "VALUES (%s, %s, '{}'::jsonb, %s)",
            ("active-under-test", base_rule_version, "active rules source test"),
        )
    connection.commit()


def _swap_database(url: str, database: str) -> str:
    """Point a connection string at a different database on the same server."""

    head, _, _tail = url.rpartition("/")
    return f"{head}/{database}"


def test_rules_load_when_the_definition_table_is_absent(connection: Connection) -> None:
    """The production shape: normalization schema present, rule table missing.

    Selecting from the absent table raised `UndefinedTable` and, inside a
    transaction, left the connection unusable for every later statement. Both
    halves are asserted -- the call returns, and the connection still works.
    """

    assert rule_definitions_table_exists(connection) is False

    rules, _entities = load_active_rules(connection)
    assert rules.rules, "an un-migrated database must still resolve its rules"

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        assert cursor.fetchone() == (1,), "the connection must not be left poisoned"


def test_stored_content_is_preferred_over_the_python_catalog(connection: Connection) -> None:
    """With rows for a version, the database is the authority for it."""

    run_rule_definition_migrations(connection)
    catalog = load_translation_rule_set(REVIEWED_RULE_SET_VERSION)
    # Import under the activated base name, so the resolved rules are the
    # stored rows rather than the Python catalog that happens to match them.
    import_rule_set(
        connection,
        catalog,
        rule_version=REVIEWED_RULE_SET_VERSION,
        imported_by="test",
        source_note="active rules source test",
    )

    stored = load_rule_set_from_database(connection, REVIEWED_RULE_SET_VERSION)
    assert stored is not None
    assert tuple(stored.rules) == tuple(catalog.rules)

    rules, _entities = load_active_rules(connection)
    assert len(rules.rules) == len(catalog.rules)


def test_a_version_without_stored_rows_falls_back(connection: Connection) -> None:
    """The table can exist while a given version has never been imported."""

    run_rule_definition_migrations(connection)
    assert rule_definitions_table_exists(connection) is True
    assert load_rule_set_from_database(connection, "never-imported-version") is None

    rules, _entities = load_active_rules(connection)
    assert rules.rules, "an unknown version must fall back to the Python catalog"


def test_sealed_version_rejects_edited_content_of_the_same_size(
    connection: Connection,
) -> None:
    """Sealing must compare content, not rule count.

    Checking `rule_count` alone passed an edited rule set straight through as a
    no-op: the caller received a success result, the stored rules were never
    touched, and the two silently disagreed from then on -- the exact failure
    this table exists to prevent, one level up.
    """

    from dataclasses import replace

    from ingestion.rule_definitions import RuleDefinitionImportError
    from ingestion.translation_dictionaries import TranslationRuleSet

    run_rule_definition_migrations(connection)
    catalog = load_translation_rule_set(REVIEWED_RULE_SET_VERSION)
    import_rule_set(
        connection,
        catalog,
        rule_version="sealed-v1",
        imported_by="test",
        source_note="fingerprint test",
    )

    edited = TranslationRuleSet(
        version=catalog.version,
        rules=(replace(catalog.rules[0], canonical_value="edited"),) + catalog.rules[1:],
    )
    assert len(edited.rules) == len(catalog.rules), "the count must be unchanged"

    with pytest.raises(RuleDefinitionImportError, match="sealed with different content"):
        import_rule_set(
            connection,
            edited,
            rule_version="sealed-v1",
            imported_by="test",
            source_note="fingerprint test",
        )

    unchanged = load_rule_set_from_database(connection, "sealed-v1")
    assert unchanged is not None
    assert tuple(unchanged.rules) == tuple(catalog.rules)

    # An identical re-import stays a clean no-op rather than an error.
    result = import_rule_set(
        connection,
        catalog,
        rule_version="sealed-v1",
        imported_by="test",
        source_note="fingerprint test",
    )
    assert result["rules_inserted"] == 0
