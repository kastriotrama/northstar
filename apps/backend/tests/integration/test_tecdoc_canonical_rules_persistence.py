"""Database-level guarantees for generated TecDoc normalization rules.

The unit suite covers what `generate_rules` decides. Nothing there touches
PostgreSQL, so the guarantees that only the schema can make -- immutability, the
seal, the accepted-needs-a-target check, and a sealed version refusing to mean
two different things -- were unexercised. Those are exactly the properties that
make a pinned run reproducible, so they are asserted against a real database
here, the way the TS rule-definition schema already is.
"""

from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import Connection

from ingestion.config import get_ingestion_settings
from ingestion.tecdoc.canonical_rule_migrations import (
    TECDOC_RULE_MIGRATIONS,
    TECDOC_RULE_VERSIONS_TABLE,
    TECDOC_RULES_TABLE,
    run_tecdoc_rule_migrations,
)
from ingestion.tecdoc.canonical_rule_proposals import (
    FIELD_SOURCES,
    TecDocRuleImportError,
    generate_rules,
    scan_observations,
    store_rules,
)
from ingestion.tecdoc.migrations import run_tecdoc_migrations

RELEASE = "tecdoc-test-release"

INSERT_RULE = (
    f"INSERT INTO {TECDOC_RULES_TABLE} (rule_version, rule_id, area, entity_type, "
    "source_field, source_term, key_table, canonical_field, canonical_value, decision, "
    "derivation, support, evidence) "
    "VALUES (%s, %s, 'fuel', 'engine', 'fuel_type', %s, '088', 'energy_sources', %s, %s, %s, "
    "%s, '{}')"
)

#: One small but realistic promoted batch, written with the attribute names both
#: candidate writers use: `tecdoc.mapping.candidates_for_row` emits the raw
#: extract value (`transmission.type`), `tecdoc.canonical_promotion` emits the
#: official key-table label (`transmission.transmission_type_name`) and
#: already-canonical values for the fields it resolves.
SEEDED_CANDIDATES: tuple[tuple[str, dict[str, object], int], ...] = (
    # A reviewed scalar KT088 label -- the only shape generation may accept.
    ("engine", {"fuel_type": "Petrol"}, 3),
    # A mixed descriptor. It appears in the scalar table too, as hybrid_petrol,
    # and must still not resolve: the pipeline reads it as mixed with no fuel.
    ("engine", {"fuel_type": "Petrol/Electric"}, 2),
    ("engine", {"fuel_type": "Diesel"}, 4),
    ("engine", {"fuel_type": "Kerosene Turbine"}, 1),
    # Promotion writes canonical values back into the candidate table, so the
    # generator also meets its own vocabulary spelled exactly.
    ("engine", {"fuel_type": "hybrid_petrol"}, 2),
    ("vehicle_variant", {"fuel_type": "Petrol", "drive_type": "001"}, 5),
    ("vehicle_variant", {"fuel_type": "Petrol/Liquified Petroleum Gas (LPG)",
                         "drive_type": "fwd"}, 2),
    ("bodywork", {"canonical_name": "hatchback"}, 6),
    ("bodywork", {"canonical_name": "025"}, 1),
    ("bodywork", {"canonical_name": "Estate Car"}, 2),
    ("transmission", {"type": "manual"}, 3),
    ("transmission", {"transmission_type_name": "Manual gearbox"}, 7),
    ("manufacturer", {"canonical_name": "VOLVO"}, 1),
    ("model_family", {"canonical_name": "XC90"}, 4),
    # Noise the scan must ignore: an entity nothing registers, and a blank value.
    ("alias", {"alias_text": "12345", "alias_type": "k_type"}, 3),
    ("engine", {"fuel_type": "   "}, 2),
)


@pytest.fixture(scope="module")
def pg_connection() -> Iterator[Connection]:
    try:
        connection = psycopg.connect(get_ingestion_settings().database_url)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is unavailable; start it with docker compose up -d postgres")
    run_tecdoc_migrations(connection)
    run_tecdoc_rule_migrations(connection)
    yield connection
    _remove_test_rows(connection)
    connection.close()


def _remove_test_rows(connection: Connection) -> None:
    """Delete everything this module wrote, so a shared database stays usable.

    These tests may run against a real database, and the rows they seal are
    versions like any other. `fetch_tecdoc_rules` reads the *newest* sealed
    version, so leaving them behind makes a sixteen-rule fixture outrank the
    real catalog scan and the Rules screen shows the fixture -- which is exactly
    what happened before this teardown existed.

    The immutability trigger has to come off to do it. That is the point of the
    trigger and it stays on for everyone else; a test that seals throwaway
    versions is the one caller entitled to take its own rows back out.
    """

    with connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {TECDOC_RULES_TABLE} DISABLE TRIGGER tecdoc_rules_immutable")
        cursor.execute(
            f"ALTER TABLE {TECDOC_RULE_VERSIONS_TABLE} DISABLE TRIGGER tecdoc_rule_versions_guard"
        )
        cursor.execute(f"DELETE FROM {TECDOC_RULES_TABLE} WHERE rule_version LIKE 'test-%'")
        cursor.execute(
            f"DELETE FROM {TECDOC_RULE_VERSIONS_TABLE} WHERE rule_version LIKE 'test-%'"
        )
        cursor.execute(
            "DELETE FROM core.tecdoc_canonical_candidates WHERE batch_id IN "
            "(SELECT batch_id FROM core.tecdoc_source_batches WHERE source_version = %s)",
            (RELEASE,),
        )
        cursor.execute(
            "DELETE FROM core.tecdoc_source_batches WHERE source_version = %s", (RELEASE,)
        )
        cursor.execute(f"ALTER TABLE {TECDOC_RULES_TABLE} ENABLE TRIGGER tecdoc_rules_immutable")
        cursor.execute(
            f"ALTER TABLE {TECDOC_RULE_VERSIONS_TABLE} ENABLE TRIGGER tecdoc_rule_versions_guard"
        )
    connection.commit()


@pytest.fixture
def open_version(pg_connection: Connection) -> str:
    """A fresh, unsealed rule version. Sealing is one-way, so never reuse one."""

    rule_version = f"test-{uuid4()}"
    with pg_connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {TECDOC_RULE_VERSIONS_TABLE} (rule_version, tecdoc_release, "
            "source_note, generated_by, rule_count, content_fingerprint, sealed) "
            "VALUES (%s, %s, 'integration test', 'pytest', 1, %s, FALSE)",
            (rule_version, RELEASE, f"fingerprint-{rule_version}"),
        )
    pg_connection.commit()
    return rule_version


@pytest.fixture(scope="module")
def seeded_batch(pg_connection: Connection) -> str:
    """One promoted TecDoc batch, scoped to this run so reruns stay clean."""

    batch_id = f"tecdoc-batch-{uuid4()}"
    row_number = 0
    with pg_connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO core.tecdoc_source_batches (batch_id, source_version, "
            "format_version, license_reference, source_path, source_checksum, "
            "source_row_count, status) "
            "VALUES (%s, %s, 'fixed-width-v1', 'test-license', '/dev/null', 'sha', 0, "
            "'completed')",
            (batch_id, RELEASE),
        )
        for entity_type, attributes, count in SEEDED_CANDIDATES:
            for _ in range(count):
                row_number += 1
                cursor.execute(
                    "INSERT INTO core.tecdoc_canonical_candidates (batch_id, entity_type, "
                    "source_key, node_id, attributes) VALUES (%s, %s, %s, %s, %s)",
                    (
                        batch_id,
                        entity_type,
                        f"{entity_type}:{row_number}",
                        f"node-{row_number}",
                        psycopg.types.json.Json(attributes),
                    ),
                )
    pg_connection.commit()
    return batch_id


def _rejects(connection: Connection, statement: str, params: tuple = ()) -> str:
    """Run a statement expected to fail, and hand back the message it failed with."""

    with pytest.raises(psycopg.errors.Error) as failure:
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
        connection.commit()
    connection.rollback()
    return str(failure.value)


class TestSchema:
    def test_migrations_are_idempotent(self, pg_connection: Connection) -> None:
        first = run_tecdoc_rule_migrations(pg_connection)
        second = run_tecdoc_rule_migrations(pg_connection)

        assert first == second
        assert len(first) == len(TECDOC_RULE_MIGRATIONS)

    def test_a_stored_rule_cannot_be_updated_or_deleted(
        self, pg_connection: Connection, open_version: str
    ) -> None:
        """A rule that can be edited in place cannot pin anything."""

        with pg_connection.cursor() as cursor:
            cursor.execute(
                INSERT_RULE, (open_version, "r1", "Petrol", "petrol", "accepted",
                              "reviewed_mapping", 5),
            )
        pg_connection.commit()

        updated = _rejects(
            pg_connection,
            f"UPDATE {TECDOC_RULES_TABLE} SET support = 9 WHERE rule_version = %s",
            (open_version,),
        )
        deleted = _rejects(
            pg_connection,
            f"DELETE FROM {TECDOC_RULES_TABLE} WHERE rule_version = %s",
            (open_version,),
        )

        assert "immutable" in updated
        assert "immutable" in deleted

    def test_a_sealed_version_takes_no_further_rules(
        self, pg_connection: Connection, open_version: str
    ) -> None:
        with pg_connection.cursor() as cursor:
            cursor.execute(
                INSERT_RULE, (open_version, "r1", "Petrol", "petrol", "accepted",
                              "reviewed_mapping", 5),
            )
            cursor.execute(
                f"UPDATE {TECDOC_RULE_VERSIONS_TABLE} SET sealed = TRUE "
                "WHERE rule_version = %s",
                (open_version,),
            )
        pg_connection.commit()

        message = _rejects(
            pg_connection,
            INSERT_RULE,
            (open_version, "r2", "Diesel", "diesel", "accepted", "reviewed_mapping", 4),
        )

        assert "sealed" in message

    def test_a_sealed_version_cannot_be_unsealed_edited_or_deleted(
        self, pg_connection: Connection, open_version: str
    ) -> None:
        with pg_connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {TECDOC_RULE_VERSIONS_TABLE} SET sealed = TRUE "
                "WHERE rule_version = %s",
                (open_version,),
            )
        pg_connection.commit()

        for statement in (
            f"UPDATE {TECDOC_RULE_VERSIONS_TABLE} SET sealed = FALSE WHERE rule_version = %s",
            (
                f"UPDATE {TECDOC_RULE_VERSIONS_TABLE} SET tecdoc_release = 'other' "
                "WHERE rule_version = %s"
            ),
            f"DELETE FROM {TECDOC_RULE_VERSIONS_TABLE} WHERE rule_version = %s",
        ):
            assert "immutable" in _rejects(pg_connection, statement, (open_version,))

    def test_an_accepted_rule_must_name_a_canonical_value(
        self, pg_connection: Connection, open_version: str
    ) -> None:
        """The whole point of the table: acceptance without a target is a guess."""

        message = _rejects(
            pg_connection,
            INSERT_RULE,
            (open_version, "r1", "Mystery Label", None, "accepted", "generated", 5),
        )

        assert "tecdoc_rules_accepted_needs_target" in message

    def test_a_proposal_may_stand_with_no_canonical_value(
        self, pg_connection: Connection, open_version: str
    ) -> None:
        """The unmapped value is the finding; it must be storable, not rejected."""

        with pg_connection.cursor() as cursor:
            cursor.execute(
                INSERT_RULE,
                (open_version, "r1", "Mystery Label", None, "proposed", "generated", 5),
            )
            cursor.execute(
                f"SELECT canonical_value FROM {TECDOC_RULES_TABLE} "
                "WHERE rule_version = %s AND rule_id = 'r1'",
                (open_version,),
            )
            assert cursor.fetchone() == (None,)
        pg_connection.commit()

    def test_one_term_cannot_map_twice_in_a_version(
        self, pg_connection: Connection, open_version: str
    ) -> None:
        with pg_connection.cursor() as cursor:
            cursor.execute(
                INSERT_RULE, (open_version, "r1", "Petrol", "petrol", "accepted",
                              "reviewed_mapping", 5),
            )
        pg_connection.commit()

        # A different rule id, so the primary key does not mask the constraint.
        message = _rejects(
            pg_connection,
            INSERT_RULE,
            (open_version, "r2", "Petrol", "diesel", "accepted", "reviewed_mapping", 5),
        )

        assert "tecdoc_rules_unique_term" in message

    def test_the_same_term_on_another_entity_is_a_separate_ruling(
        self, pg_connection: Connection, open_version: str
    ) -> None:
        """KT088 and KT182 share labels and are still different authorities."""

        with pg_connection.cursor() as cursor:
            cursor.execute(
                INSERT_RULE, (open_version, "r1", "Petrol", "petrol", "accepted",
                              "reviewed_mapping", 5),
            )
            cursor.execute(
                f"INSERT INTO {TECDOC_RULES_TABLE} (rule_version, rule_id, area, "
                "entity_type, source_field, source_term, key_table, canonical_field, "
                "canonical_value, decision, derivation, support, evidence) "
                "VALUES (%s, 'r2', 'fuel', 'vehicle_variant', 'fuel_type', 'Petrol', "
                "'182', 'energy_sources', 'petrol', 'accepted', 'reviewed_mapping', 5, '{}')",
                (open_version,),
            )
        pg_connection.commit()

    def test_a_generated_rule_must_carry_its_evidence(
        self, pg_connection: Connection, open_version: str
    ) -> None:
        message = _rejects(
            pg_connection,
            INSERT_RULE,
            (open_version, "r1", "Nothing Observed", None, "proposed", "generated", 0),
        )

        assert "tecdoc_rules_generated_needs_support" in message


class TestScan:
    def test_the_scan_reads_every_registered_attribute_and_nothing_else(
        self, pg_connection: Connection, seeded_batch: str
    ) -> None:
        observations = scan_observations(pg_connection, batch_id=seeded_batch)

        seen = {(o.entity_type, o.source_field) for o in observations}
        assert seen <= {(s.entity_type, s.source_field) for s in FIELD_SOURCES}
        # `alias` is not registered, so nothing it carries may be observed.
        assert not any(o.entity_type == "alias" for o in observations)
        # A blank value is not a value; it must not become a rule to review.
        assert all(o.source_term.strip() for o in observations)

    def test_support_counts_the_rows_that_carry_the_value(
        self, pg_connection: Connection, seeded_batch: str
    ) -> None:
        observations = scan_observations(pg_connection, batch_id=seeded_batch)
        by_term = {(o.entity_type, o.source_field, o.source_term): o.support
                   for o in observations}

        assert by_term[("engine", "fuel_type", "Petrol")] == 3
        assert by_term[("engine", "fuel_type", "Diesel")] == 4
        assert by_term[("bodywork", "canonical_name", "hatchback")] == 6

    def test_both_candidate_writers_transmission_attributes_are_scanned(
        self, pg_connection: Connection, seeded_batch: str
    ) -> None:
        """`type` and `transmission_type_name` are the same fact, named twice.

        Registering only one reported zero transmission coverage for every batch
        written by the other writer, and reported it as a plausible number
        rather than as a missing scan.
        """

        observations = scan_observations(pg_connection, batch_id=seeded_batch)
        transmission = {
            (o.source_field, o.source_term)
            for o in observations
            if o.entity_type == "transmission"
        }

        assert ("type", "manual") in transmission
        assert ("transmission_type_name", "Manual gearbox") in transmission

    def test_a_scan_of_an_unknown_batch_observes_nothing(
        self, pg_connection: Connection
    ) -> None:
        assert scan_observations(pg_connection, batch_id=f"absent-{uuid4()}") == ()


class TestGenerationAgainstTheDatabase:
    def test_a_mixed_descriptor_is_proposed_with_no_target(
        self, pg_connection: Connection, seeded_batch: str
    ) -> None:
        """"Petrol/Electric" must not become hybrid_petrol by generation.

        The scalar reviewed table does map that label to `hybrid_petrol`, but
        `engine_fuel_evidence` reads it as mixed and refuses a scalar fuel.
        Accepting it here would be the generator overruling the pipeline.
        """

        report = generate_rules(scan_observations(pg_connection, batch_id=seeded_batch))
        rule = next(r for r in report.rules if r.source_term == "Petrol/Electric")

        assert rule.decision == "proposed"
        assert rule.canonical_value is None
        assert rule.evidence["components"] == ["petrol", "electric"]
        assert rule.evidence["reason"] == "mixed_descriptor"

    def test_only_a_reviewed_mapping_produces_an_accepted_rule(
        self, pg_connection: Connection, seeded_batch: str
    ) -> None:
        report = generate_rules(scan_observations(pg_connection, batch_id=seeded_batch))

        assert report.accepted
        for rule in report.accepted:
            assert rule.derivation == "reviewed_mapping"
            assert rule.canonical_value is not None
        assert {r.source_term for r in report.accepted} == {"Petrol", "Diesel", "001", "025"}

    def test_an_unmapped_value_is_carried_as_a_countable_gap(
        self, pg_connection: Connection, seeded_batch: str
    ) -> None:
        report = generate_rules(scan_observations(pg_connection, batch_id=seeded_batch))

        assert {r.source_term for r in report.unmapped} >= {
            "Kerosene Turbine", "Estate Car", "Manual gearbox", "Petrol/Electric",
        }
        assert all(r.canonical_value is None for r in report.unmapped)

    def test_coverage_is_weighted_by_rows_and_excludes_open_vocabulary(
        self, pg_connection: Connection, seeded_batch: str
    ) -> None:
        report = generate_rules(scan_observations(pg_connection, batch_id=seeded_batch))

        closed = [r for r in report.rules if not r.is_open_vocabulary]
        total = sum(r.support for r in closed)
        resolved = sum(r.support for r in closed if not r.is_unmapped)
        assert report.coverage() == pytest.approx(resolved / total)
        assert 0.0 < report.coverage() < 1.0

        # Model names and manufacturer names have no canonical list, so they
        # carry no coverage number at all rather than a misleading zero.
        by_field = report.coverage_by_field()
        assert "model_family" not in by_field
        assert "manufacturer" not in by_field
        assert by_field["drive_type"] == pytest.approx(1.0)
        assert all(0.0 <= value <= 1.0 for value in by_field.values())


class TestStore:
    def test_storing_the_same_scan_twice_is_a_no_op(
        self, pg_connection: Connection, seeded_batch: str
    ) -> None:
        report = generate_rules(scan_observations(pg_connection, batch_id=seeded_batch))
        rule_version = f"test-{uuid4()}"
        arguments = {
            "rule_version": rule_version,
            "tecdoc_release": RELEASE,
            "generated_by": "pytest",
            "source_note": "integration test",
        }

        first = store_rules(pg_connection, report.rules, **arguments)
        second = store_rules(pg_connection, report.rules, **arguments)

        assert first == {"version_created": 1, "rules_inserted": len(report.rules),
                         "sealed": 1}
        assert second == {"version_created": 0, "rules_inserted": 0, "sealed": 1}
        with pg_connection.cursor() as cursor:
            cursor.execute(
                f"SELECT count(*) FROM {TECDOC_RULES_TABLE} WHERE rule_version = %s",
                (rule_version,),
            )
            assert cursor.fetchone() == (len(report.rules),)

    def test_a_sealed_version_refuses_different_content(
        self, pg_connection: Connection, seeded_batch: str
    ) -> None:
        """A pinned version that could change content would pin nothing."""

        from dataclasses import replace

        report = generate_rules(scan_observations(pg_connection, batch_id=seeded_batch))
        rule_version = f"test-{uuid4()}"
        arguments = {
            "rule_version": rule_version,
            "tecdoc_release": RELEASE,
            "generated_by": "pytest",
            "source_note": "integration test",
        }
        store_rules(pg_connection, report.rules, **arguments)

        drifted = list(report.rules)
        drifted[0] = replace(drifted[0], support=drifted[0].support + 1)
        with pytest.raises(TecDocRuleImportError, match="sealed with different content"):
            store_rules(pg_connection, drifted, **arguments)
        pg_connection.rollback()

        # A subset is a difference too; rule_count alone would not notice.
        with pytest.raises(TecDocRuleImportError, match="sealed with different content"):
            store_rules(pg_connection, report.rules[:-1], **arguments)
        pg_connection.rollback()

        with pg_connection.cursor() as cursor:
            cursor.execute(
                f"SELECT rule_count FROM {TECDOC_RULE_VERSIONS_TABLE} "
                "WHERE rule_version = %s",
                (rule_version,),
            )
            assert cursor.fetchone() == (len(report.rules),)

    def test_a_version_opened_for_one_release_refuses_another(
        self, pg_connection: Connection, seeded_batch: str, open_version: str
    ) -> None:
        report = generate_rules(scan_observations(pg_connection, batch_id=seeded_batch))

        with pytest.raises(TecDocRuleImportError, match="was opened for release"):
            store_rules(
                pg_connection,
                report.rules,
                rule_version=open_version,
                tecdoc_release="a-different-release",
                generated_by="pytest",
                source_note="integration test",
            )
        pg_connection.rollback()

        with pg_connection.cursor() as cursor:
            cursor.execute(
                f"SELECT count(*) FROM {TECDOC_RULES_TABLE} WHERE rule_version = %s",
                (open_version,),
            )
            assert cursor.fetchone() == (0,)

    def test_an_empty_rule_set_is_refused(self, pg_connection: Connection) -> None:
        with pytest.raises(TecDocRuleImportError, match="empty rule set"):
            store_rules(
                pg_connection,
                [],
                rule_version=f"test-{uuid4()}",
                tecdoc_release=RELEASE,
                generated_by="pytest",
                source_note="integration test",
            )

    def test_stored_evidence_stays_queryable_as_jsonb(
        self, pg_connection: Connection, seeded_batch: str
    ) -> None:
        """Evidence must be readable by a reviewer's query, not an opaque blob."""

        report = generate_rules(scan_observations(pg_connection, batch_id=seeded_batch))
        rule_version = f"test-{uuid4()}"
        store_rules(
            pg_connection,
            report.rules,
            rule_version=rule_version,
            tecdoc_release=RELEASE,
            generated_by="pytest",
            source_note="integration test",
        )

        with pg_connection.cursor() as cursor:
            cursor.execute(
                f"SELECT evidence -> 'components', evidence ->> 'reason' "
                f"FROM {TECDOC_RULES_TABLE} "
                "WHERE rule_version = %s AND source_term = 'Petrol/Electric'",
                (rule_version,),
            )
            assert cursor.fetchone() == (["petrol", "electric"], "mixed_descriptor")

            # The unmapped index exists to answer exactly this question.
            cursor.execute(
                f"SELECT count(*) FROM {TECDOC_RULES_TABLE} "
                "WHERE rule_version = %s AND canonical_value IS NULL",
                (rule_version,),
            )
            assert cursor.fetchone()[0] > 0

            cursor.execute(
                f"SELECT count(*) FROM {TECDOC_RULES_TABLE} "
                "WHERE rule_version = %s AND decision = 'accepted' "
                "AND derivation <> 'reviewed_mapping'",
                (rule_version,),
            )
            assert cursor.fetchone() == (0,)
