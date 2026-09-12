"""Database-level guarantees for reviewer-authored TecDoc value rulings.

`core.tecdoc_resolution_rules` is the mutable sibling of the sealed
`core.tecdoc_rules`: a reviewer writes one row per value straight from the gap
browser, and it takes effect immediately. That makes its CHECK constraints the
whole safety story -- there is no sealing, no fingerprint and no review batch
behind it -- so they are asserted against a real database here.

Skips when PostgreSQL is unreachable, the same way the other integration tests
in this directory do.
"""

from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import Connection

from ingestion.config import get_ingestion_settings
from ingestion.tecdoc.resolution_migrations import (
    TECDOC_RESOLUTION_MIGRATIONS,
    TECDOC_RESOLUTION_RULES_TABLE,
    run_tecdoc_resolution_migrations,
)

INSERT = (
    f"INSERT INTO {TECDOC_RESOLUTION_RULES_TABLE} "
    "(canonical_field, comparison_key, source_term, key_table, decision, "
    "canonical_value, note, reviewed_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)


@pytest.fixture(scope="module")
def pg_connection() -> Iterator[Connection]:
    try:
        connection = psycopg.connect(get_ingestion_settings().database_url)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is unavailable; start it with docker compose up -d postgres")
    run_tecdoc_resolution_migrations(connection)
    yield connection
    # This table is mutable by design and may be pointed at a real database, so
    # the rows these tests write are removed again. Every one is keyed `TEST_`,
    # which no reviewer-authored comparison key can be: `comparison_key` upper-cases
    # and strips the source term, and no TecDoc value begins with that literal.
    with connection.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM {TECDOC_RESOLUTION_RULES_TABLE} WHERE comparison_key LIKE 'TEST\\_%'"
        )
    connection.commit()
    connection.close()


@pytest.fixture
def key() -> str:
    """A comparison key scoped to this test, so reruns never collide."""

    return f"TEST_{uuid4().hex.upper()}"


def _insert(connection: Connection, key: str, **overrides: object) -> None:
    row = {
        "canonical_field": "bodywork_form",
        "comparison_key": key,
        "source_term": "Targa",
        "key_table": "086",
        "decision": "accepted",
        "canonical_value": "coupe",
        "note": "",
        "reviewed_by": "pytest",
    }
    row.update(overrides)
    with connection.cursor() as cursor:
        cursor.execute(INSERT, tuple(row.values()))
    connection.commit()


class TestMigrations:
    def test_migrations_are_idempotent(self, pg_connection: Connection) -> None:
        first = run_tecdoc_resolution_migrations(pg_connection)
        second = run_tecdoc_resolution_migrations(pg_connection)

        assert first == second == tuple(name for name, _ in TECDOC_RESOLUTION_MIGRATIONS)

    def test_the_table_exists_after_migrating(self, pg_connection: Connection) -> None:
        with pg_connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass(%s)", (TECDOC_RESOLUTION_RULES_TABLE,))
            assert cursor.fetchone()[0] is not None


class TestAnAcceptanceMustNameATarget:
    def test_accepted_without_a_canonical_value_is_rejected(
        self, pg_connection: Connection, key: str
    ) -> None:
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(pg_connection, key, decision="accepted", canonical_value=None)
        pg_connection.rollback()

    def test_accepted_with_a_target_is_stored(
        self, pg_connection: Connection, key: str
    ) -> None:
        _insert(pg_connection, key, decision="accepted", canonical_value="coupe")

        with pg_connection.cursor() as cursor:
            cursor.execute(
                f"SELECT canonical_value FROM {TECDOC_RESOLUTION_RULES_TABLE} "
                "WHERE comparison_key = %s",
                (key,),
            )
            assert cursor.fetchone()[0] == "coupe"


class TestAnExclusionMustNameNoTargetAndMustSayWhy:
    def test_excluded_carrying_a_target_is_rejected(
        self, pg_connection: Connection, key: str
    ) -> None:
        """Ruling a value out of scope is not the same as mapping it."""

        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(
                pg_connection, key, decision="excluded",
                canonical_value="coupe", note="a motorcycle body",
            )
        pg_connection.rollback()

    def test_excluded_without_a_note_is_rejected(
        self, pg_connection: Connection, key: str
    ) -> None:
        """An exclusion is a claim, so it has to state one."""

        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(
                pg_connection, key, decision="excluded", canonical_value=None, note="   "
            )
        pg_connection.rollback()

    def test_excluded_with_a_note_and_no_target_is_stored(
        self, pg_connection: Connection, key: str
    ) -> None:
        _insert(
            pg_connection, key, decision="excluded",
            canonical_value=None, note="KT086 motorcycle body, out of scope",
        )

        with pg_connection.cursor() as cursor:
            cursor.execute(
                f"SELECT decision, canonical_value FROM {TECDOC_RESOLUTION_RULES_TABLE} "
                "WHERE comparison_key = %s",
                (key,),
            )
            assert cursor.fetchone() == ("excluded", None)


class TestFieldValidation:
    def test_an_unknown_decision_is_rejected(
        self, pg_connection: Connection, key: str
    ) -> None:
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(pg_connection, key, decision="maybe")
        pg_connection.rollback()

    def test_a_blank_reviewer_is_rejected(
        self, pg_connection: Connection, key: str
    ) -> None:
        """A live ruling with no accountable author is unreviewable."""

        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(pg_connection, key, reviewed_by="   ")
        pg_connection.rollback()

    def test_a_blank_source_term_is_rejected(
        self, pg_connection: Connection, key: str
    ) -> None:
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(pg_connection, key, source_term="  ")
        pg_connection.rollback()

    def test_a_blank_comparison_key_is_rejected(self, pg_connection: Connection) -> None:
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(pg_connection, "   ")
        pg_connection.rollback()


class TestOneRulingPerValue:
    def test_a_second_ruling_on_the_same_value_collides(
        self, pg_connection: Connection, key: str
    ) -> None:
        """The primary key is what makes a correction an update, not a duplicate."""

        _insert(pg_connection, key, canonical_value="coupe")

        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert(pg_connection, key, canonical_value="sedan")
        pg_connection.rollback()

    def test_the_same_key_under_a_different_field_is_a_different_ruling(
        self, pg_connection: Connection, key: str
    ) -> None:
        _insert(pg_connection, key, canonical_field="bodywork_form", canonical_value="coupe")
        _insert(pg_connection, key, canonical_field="drive_type", canonical_value="fwd")

        with pg_connection.cursor() as cursor:
            cursor.execute(
                f"SELECT count(*) FROM {TECDOC_RESOLUTION_RULES_TABLE} "
                "WHERE comparison_key = %s",
                (key,),
            )
            assert cursor.fetchone()[0] == 2


class TestCorrectionsAreAllowed:
    def test_a_reviewer_can_correct_their_own_ruling(
        self, pg_connection: Connection, key: str
    ) -> None:
        """Unlike `core.tecdoc_rules`, this table is deliberately mutable.

        A sealed generated rule is corrected by generating a new version. A live
        ruling has no version to supersede it, so it must be editable in place.
        """

        _insert(pg_connection, key, canonical_value="coupe")

        with pg_connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {TECDOC_RESOLUTION_RULES_TABLE} SET canonical_value = %s "
                "WHERE comparison_key = %s",
                ("convertible", key),
            )
        pg_connection.commit()

        with pg_connection.cursor() as cursor:
            cursor.execute(
                f"SELECT canonical_value FROM {TECDOC_RESOLUTION_RULES_TABLE} "
                "WHERE comparison_key = %s",
                (key,),
            )
            assert cursor.fetchone()[0] == "convertible"

    def test_a_correction_still_obeys_the_checks(
        self, pg_connection: Connection, key: str
    ) -> None:
        """Mutable is not unguarded: an update cannot strip an acceptance's target."""

        _insert(pg_connection, key, canonical_value="coupe")

        with pytest.raises(psycopg.errors.CheckViolation), pg_connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {TECDOC_RESOLUTION_RULES_TABLE} SET canonical_value = NULL "
                "WHERE comparison_key = %s",
                (key,),
            )
        pg_connection.rollback()
