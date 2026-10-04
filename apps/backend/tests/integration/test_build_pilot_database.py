"""The pilot database is a closed, verified slice of the full build.

A small "full build" is made in a throwaway database the way production makes it
(TS records, normalization, facts, backfill, an AIS import, learned rules) plus
rules, a pinned catalog batch and chunk data. The pilot is cut from it into a
second throwaway database on the same server.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import Connection, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.errors import ReadOnlySqlTransaction
from psycopg.types.json import Jsonb

from api.app.features.vehicle_matching.repository import SAMPLE_SEED, VehicleMatchingRepository
from ingestion import pilot_database
from ingestion.config import get_ingestion_settings
from ingestion.pilot_database import (
    MANIFEST_KEY,
    PILOT_TABLES,
    Check,
    PilotBuildError,
    PilotOptions,
    PilotOutcome,
    build_pilot_database,
    foreign_keys,
    open_source,
    plan_pilot,
    run_pilot_migrations,
    shared_columns,
    spec_by_table,
    stored_manifest,
    verify_pilot,
    verify_pilot_database,
)
from ingestion.vehicle_core_ais import AisExtract, AisRecord, import_ais_extract
from ingestion.vehicle_core_rules import FAMILIES_BY_ID, apply_rules, learn_rules, store_rules
from ingestion.vehicle_core_ts import backfill_vehicle_core
from ingestion.vehicle_correction_decision_migrations import (
    verify_vehicle_correction_decision_schema_contract,
)
from ingestion.vehicle_fact_correction_migrations import (
    verify_vehicle_fact_correction_schema_contract,
)
from ingestion.vehicle_ktype_choice_migrations import verify_vehicle_ktype_choice_schema_contract
from ingestion.vehicle_ktype_choices import project_choices
from scripts import build_pilot_database as script
from tests.integration.throwaway_database import throwaway_database
from tests.integration.vehicle_core_fixtures import insert_ts_record, project, volvo

PINNED_BATCH = "tecdoc-pilot-test-pinned"
OTHER_BATCH = "tecdoc-pilot-test-other"
SLICE_SIZE = 6
CHUNK_SIZE = 4  # smaller than the slice, so every sliced table is read in several chunks
CARS = 12
TWO_RECORD_PLATE = "PIL000"
PROPOSAL_PLATE = "PIL001"
AIS_ONLY_VIN = "YV1BW84S1F1299999"
FORMER_PLATE = "OLD000"  # a plate the two-record car carried before: closed, not current

# Class A tables with rows in the fixture, and the key that orders them.
RULE_TABLES = {
    "core.match_resolution_rules": "rule_id",
    "core.match_chunk_proposals": "proposal_id",
    "core.tecdoc_resolution_rules": "id",
    "core.translation_rule_versions": "version",
    "core.vehicle_enrichment_rules": "rule_id",
    "core.tecdoc_rule_versions": "rule_version",
    "core.tecdoc_rules": "rule_id",
    "core.tecdoc_identity_registry": "node_id",
    "core.ingest_job_runs": "id",
}


def _url(database: str) -> str:
    params = conninfo_to_dict(get_ingestion_settings().database_url)
    params["dbname"] = database
    return make_conninfo("", **params)


def _vin(index: int) -> str:
    return f"YV1BW84S1F12345{index:02d}"


def _rows(connection: Connection, query: str, params: Any = None) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def _one(connection: Connection, query: str, params: Any = None) -> Any:
    return _rows(connection, query, params)[0][0]


@dataclass
class Source:
    url: str
    connection: Connection
    seed: str
    build_id: Any
    proposal_chunk: Any

    def options(self, target: str, **overrides: Any) -> PilotOptions:
        values: dict[str, Any] = {
            "target_database": target,
            "catalog_batch": PINNED_BATCH,
            "size": SLICE_SIZE,
            "seed": self.seed,
        }
        values.update(overrides)
        return PilotOptions(**values)

    def sample(self, seed: str, size: int = SLICE_SIZE) -> list[str]:
        """The slice as the API's own seeded sample picks it."""

        @contextmanager
        def factory() -> Iterator[Connection]:
            yield self.connection

        ids = VehicleMatchingRepository(factory).sample_vehicle_ids(seed=seed, size=size)
        self.connection.rollback()
        return ids

    def vehicle(self, plate: str) -> str:
        return str(_one(self.connection,
                        "SELECT vehicle_id FROM core.vehicles WHERE plate = %s", (plate,)))


def _load_vehicles(connection: Connection) -> None:
    for index in range(CARS):
        insert_ts_record(connection, volvo(vin=_vin(index), plate=f"PIL{index:03d}"),
                         batch="pilot-batch-part-1", ingested_at="2026-08-07")
    # The same car in an older batch copy: a second TS record of one vehicle.
    insert_ts_record(connection, volvo(vin=_vin(0), plate=TWO_RECORD_PLATE),
                     batch="pilot-batch-part-0", ingested_at="2026-08-02")
    # Cars the slice must never pick: a light truck and a deregistered car.
    insert_ts_record(connection, volvo(vin=_vin(90), plate="TRK001", eu_category="N1",
                                       vehicle_type="LB"), batch="pilot-batch-part-1")
    insert_ts_record(connection, volvo(vin=_vin(91), plate="DER001"), batch="pilot-batch-part-1")
    connection.commit()
    project(connection)
    backfill_vehicle_core(connection, min_free_bytes=None)
    extract = AisExtract(Path("export.xml"), datetime(2026, 9, 19, tzinfo=UTC), "ab" * 32)
    records = [
        AisRecord(_vin(index), f"PIL{index:03d}", {
            "vehicle_type": "PB",
            # The last two cars AIS left without an engine code: a learned rule fills them.
            **({"engine_code": "D5244T21"} if index < CARS - 2 else {}),
        })
        for index in range(CARS)
    ]
    # A car only AIS knows: its source link is keyed by VIN, not by a TS record id.
    records.append(AisRecord(AIS_ONLY_VIN, "AIS001", {"vehicle_type": "PB"}))
    import_ais_extract(connection, Path("export.xml"), extract=extract, records=records,
                       min_free_bytes=None)
    family = FAMILIES_BY_ID["ENG-VV"]
    store_rules(connection, family, learn_rules(connection, family, min_support=4),
                learned_from="ais")
    apply_rules(connection, family)
    connection.execute(
        "UPDATE core.vehicles SET registry_status = 'deregistered' WHERE plate = 'DER001'"
    )
    connection.commit()


def _load_catalog(connection: Connection) -> None:
    for batch, variants in ((PINNED_BATCH, 3), (OTHER_BATCH, 2)):
        connection.execute(
            "INSERT INTO core.tecdoc_source_batches (batch_id, source_path, source_version, "
            "format_version, license_reference, source_checksum, source_row_count, status, "
            "completed_at) VALUES (%s, 'fixture', '0326', 'v1', 'test', 'checksum', %s, "
            "'completed', now())",
            (batch, variants),
        )
        for number in range(variants):
            for entity, key in (("vehicle_variant", f"ktype-{number}"), ("engine", f"e-{number}")):
                connection.execute(
                    "INSERT INTO core.tecdoc_canonical_candidates (batch_id, entity_type, "
                    "source_key, node_id, attributes) VALUES (%s, %s, %s, %s, %s)",
                    (batch, entity, key, f"node-{entity}-{number}", Jsonb({"number": number})),
                )
                connection.execute(
                    "INSERT INTO core.tecdoc_identity_registry (entity_type, source_key, node_id) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (entity, key, f"node-{entity}-{number}"),
                )
            connection.execute(
                "INSERT INTO core.tecdoc_candidate_relationships (batch_id, relationship_type, "
                "source_assertion_key, from_node_id, from_source_key, to_node_id, to_source_key) "
                "VALUES (%s, 'USES_ENGINE', %s, %s, %s, %s, %s)",
                (batch, f"uses-{number}", f"node-vehicle_variant-{number}", f"ktype-{number}",
                 f"node-engine-{number}", f"e-{number}"),
            )


def _load_rules(connection: Connection) -> None:
    connection.execute(
        "INSERT INTO core.tecdoc_rule_versions (rule_version, tecdoc_release, generated_by, "
        "source_note, rule_count, content_fingerprint) VALUES ('rules-v1', '0326', 'test', "
        "'fixture', 2, 'fingerprint')"
    )
    for term in ("Diesel", "Petrol"):
        connection.execute(
            "INSERT INTO core.tecdoc_rules (rule_version, rule_id, area, entity_type, "
            "source_field, source_term, canonical_field, canonical_value, decision, derivation, "
            "support) VALUES ('rules-v1', %s, 'fuel', 'engine', 'fuel', %s, 'fuel', %s, "
            "'accepted', 'reviewed_mapping', 1)",
            (f"FUEL-{term}", term, term.lower()),
        )
    # Sealed like every live version: from now on the table refuses new rules for it.
    connection.execute(
        "UPDATE core.tecdoc_rule_versions SET sealed = true WHERE rule_version = 'rules-v1'"
    )
    connection.execute(
        "INSERT INTO core.tecdoc_resolution_rules (canonical_field, comparison_key, source_term, "
        "canonical_value, decision, reviewed_by) VALUES ('fuel', 'diesel', 'Diesel', 'diesel', "
        "'accepted', 'reviewer')"
    )
    connection.execute(
        "INSERT INTO core.translation_rule_versions (version, base_rule_version, overrides, "
        "activation_note) VALUES ('policy-test-1', 'ts-translation-v7', '{}', 'fixture policy')"
    )
    connection.execute(
        "INSERT INTO core.ingest_job_runs (job_name, batch_id, status, finished_at, "
        "records_processed, records_succeeded) VALUES ('normalize', 'pilot-batch-part-1', "
        "'completed', now(), 14, 14)"
    )


def _load_chunks(connection: Connection, proposal_record: int) -> tuple[Any, Any]:
    """One chunk per TS record, a reviewer rule resolving every record, one proposal."""

    build_id, rule_id, proposal_chunk = uuid4(), uuid4(), None
    records = [row[0] for row in _rows(connection,
                                       "SELECT id FROM staging.transportstyrelsen_raw ORDER BY id")]
    connection.execute(
        "INSERT INTO core.match_chunk_builds (build_id, source_batch_id, signature_version, "
        "status_filter, status, finished_at, row_count, chunk_count) VALUES (%s, 'pilot-batch', "
        "'v1', '{review_required}', 'completed', now(), %s, %s)",
        (build_id, len(records), len(records)),
    )
    connection.execute(
        "INSERT INTO core.match_resolution_rules (rule_id, build_id, source_field, source_value, "
        "target_field, target_value, conditions, author, matched_rows, would_resolve, "
        "already_resolved) VALUES (%s, %s, 'body_code', 'AC', 'bodywork_form', 'estate', %s, "
        "'reviewer', %s, %s, 0)",
        (rule_id, build_id, Jsonb([{"field": "body_code", "value": "AC"}]), len(records),
         len(records)),
    )
    for position, record in enumerate(records):
        chunk_id = uuid4()
        connection.execute(
            "INSERT INTO core.match_chunks (chunk_id, build_id, signature, signature_key, "
            "member_count, reason_profile) VALUES (%s, %s, %s, %s, 1, %s)",
            (chunk_id, build_id, Jsonb({"record": position}), f"{position:064x}",
             Jsonb({"missing_engine_code": 1})),
        )
        connection.execute(
            "INSERT INTO core.match_chunk_members (chunk_id, source_record_id, source_batch_id, "
            "normalization_status, review_reasons) VALUES (%s, %s, 'pilot-batch', "
            "'review_required', '{missing_engine_code}')",
            (chunk_id, record),
        )
        connection.execute(
            "INSERT INTO core.match_field_resolutions (rule_id, build_id, source_record_id, "
            "target_field, target_value) VALUES (%s, %s, %s, 'bodywork_form', 'estate')",
            (rule_id, build_id, record),
        )
        if position % 2 == 0:
            connection.execute(
                "INSERT INTO core.review_queue (review_id, source_system, source_table, "
                "source_record_id, source_batch_id, reason_code, target_entity_type) VALUES "
                "(%s, 'transportstyrelsen', 'staging.transportstyrelsen_raw', %s, 'pilot-batch', "
                "'missing_engine_code', 'vehicle')",
                (uuid4(), record),
            )
        if record == proposal_record:
            proposal_chunk = chunk_id
            connection.execute(
                "INSERT INTO core.match_chunk_proposals (proposal_id, chunk_id, proposal_source, "
                "recommendation, confidence, reasoning, evidence, adjudicator_version) VALUES "
                "(%s, %s, 'heuristic', 'needs_more_evidence', 0.5, 'fixture', '{}', 'v1')",
                (uuid4(), chunk_id),
            )
    return build_id, proposal_chunk


def _load_history(connection: Connection) -> None:
    """Rows that are history, not current state, on a car the slice will hold.

    A predicate such as `valid_to IS NULL` or `superseded_at IS NULL` on a slice
    table would drop them without the verification noticing.
    """

    vehicle, record = _rows(connection, "SELECT vehicle_id, ts_record_id FROM core.vehicles "
                            "WHERE plate = %s", (TWO_RECORD_PLATE,))[0]
    connection.execute(
        "INSERT INTO core.vehicle_identifiers (vehicle_id, kind, value, valid_from, valid_to, "
        "source) VALUES (%s, 'plate', %s, '2015-03-01', '2019-06-30', 'transportstyrelsen')",
        (vehicle, FORMER_PLATE),
    )
    connection.execute(
        "INSERT INTO core.match_field_resolutions (rule_id, build_id, source_record_id, "
        "target_field, target_value, applied_at, superseded_at) "
        "SELECT rule_id, build_id, %s, 'bodywork_form', 'saloon', '2026-08-01', '2026-08-05' "
        "FROM core.match_resolution_rules",
        (record,),
    )


@pytest.fixture(scope="module")
def source() -> Iterator[Source]:
    with throwaway_database("pilot_source") as connection:
        run_pilot_migrations(connection)
        _load_vehicles(connection)
        _load_catalog(connection)
        _load_rules(connection)
        proposal_record = _one(connection,
                               "SELECT ts_record_id FROM core.vehicles WHERE plate = %s",
                               (PROPOSAL_PLATE,))
        build_id, proposal_chunk = _load_chunks(connection, proposal_record)
        _load_history(connection)
        connection.commit()
        built = Source(_url(connection.info.dbname), connection, "", build_id, proposal_chunk)
        # Vehicle ids are minted per run, so which cars a seed picks differs per run.
        # Take the first seed whose slice holds the cases the tests look at.
        wanted = {built.vehicle(TWO_RECORD_PLATE),
                  str(_one(connection, "SELECT vehicle_id FROM core.vehicles WHERE vin = %s",
                           (AIS_ONLY_VIN,)))}
        unwanted = built.vehicle(PROPOSAL_PLATE)
        for attempt in range(2000):
            picked = set(built.sample(f"pilot-test-{attempt}"))
            if wanted <= picked and unwanted not in picked:
                built.seed = f"pilot-test-{attempt}"
                break
        else:  # pragma: no cover - 2000 seeds each pass with probability about 1/8
            raise AssertionError("no seed puts the wanted cars in the slice")
        # A person decided one slice car twice: its whole chain must come along.
        decided = built.vehicle(TWO_RECORD_PLATE)
        first = _choose(connection, decided, 0, None)
        _choose(connection, decided, 1, first)
        project_choices(connection, [decided])
        # The same car was corrected twice: once by a decision about many cars,
        # once by a person on top. A second decision is only a proposal.
        applied = _decide(connection, "apply")
        _decide(connection, "propose")
        member = _correct(connection, decided, 0, None, group=applied)
        _correct(connection, decided, 1, member)
        connection.commit()
        yield built


def _choose(connection: Connection, vehicle_id: str, position: int, supersedes: Any) -> Any:
    """One stored KType choice, written the way the table itself accepts it."""

    choice_id = uuid4()
    connection.execute(
        "INSERT INTO core.vehicle_ktype_choices (choice_id, vehicle_id, chain_position, action, "
        "ktype, supersedes_choice_id, reviewer, catalog_batch, automatic_terminal, code_version, "
        "evidence_fingerprint, evidence) VALUES (%s, %s, %s, 'choose', %s, %s, 'Ada', %s, "
        "'review_required', 'test', %s, %s)",
        (choice_id, vehicle_id, position, f"K{position}", supersedes, PINNED_BATCH, "a" * 64,
         Jsonb({"schema": "manual-ktype-choice-evidence-v1", "automatic": {},
                "candidates": [{"ktype": "K0"}, {"ktype": "K1"}]})),
    )
    return choice_id


def _drop(database: str) -> None:
    with psycopg.connect(get_ingestion_settings().database_url, autocommit=True) as admin:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database))
        )


def _exists(database: str) -> bool:
    with psycopg.connect(get_ingestion_settings().database_url, autocommit=True) as admin:
        return bool(_rows(admin, "SELECT 1 FROM pg_database WHERE datname = %s", (database,)))


@pytest.fixture
def target_name() -> Iterator[str]:
    name = f"nstest_pilot_{uuid4().hex[:10]}"
    try:
        yield name
    finally:
        _drop(name)


@dataclass
class Pilot:
    outcome: PilotOutcome
    connection: Connection
    name: str
    output: str


@pytest.fixture(scope="module")
def pilot(source: Source) -> Iterator[Pilot]:
    name = f"nstest_pilot_{uuid4().hex[:10]}"
    lines: list[str] = []
    try:
        outcome = build_pilot_database(source.url, source.options(name), commit=True,
                                       chunk_size=CHUNK_SIZE, report=lines.append)
        with psycopg.connect(_url(name)) as connection:
            yield Pilot(outcome, connection, name, "\n".join(lines))
    finally:
        _drop(name)


def test_the_fixture_is_a_small_full_build(source: Source) -> None:
    scopes = dict(_rows(source.connection,
                        "SELECT plate, vehicle_scope FROM core.vehicles WHERE plate = 'TRK001'"))
    assert scopes and scopes["TRK001"] != "passenger"
    eligible = _one(source.connection, "SELECT count(*) FROM core.vehicles "
                    "WHERE vehicle_scope = 'passenger' AND registry_status = 'registered'")
    assert eligible == CARS + 1  # the twelve TS cars and the car only AIS knows
    assert eligible > SLICE_SIZE
    source.connection.rollback()


def test_a_dry_run_plans_the_slice_and_creates_nothing(source: Source, target_name: str) -> None:
    lines: list[str] = []
    outcome = build_pilot_database(source.url, source.options(target_name),
                                   chunk_size=CHUNK_SIZE, report=lines.append)

    assert not outcome.built
    assert outcome.ok
    assert not _exists(target_name)
    plan = outcome.plan
    assert plan.eligible_vehicles == CARS + 1
    assert plan.slice_vehicles == SLICE_SIZE
    rows = {item.spec.table: item.pilot_rows for item in plan.tables}
    assert rows["core.vehicles"] == SLICE_SIZE
    assert rows["core.tecdoc_canonical_candidates"] == 6  # the pinned batch only
    assert rows["core.vehicle_source_links"] > SLICE_SIZE  # TS and AIS links
    assert rows["staging.transportstyrelsen_raw"] == plan.slice_records
    assert "Class B" in lines[0] and target_name in lines[0]


def test_the_pilot_holds_exactly_the_seeded_slice(source: Source, pilot: Pilot) -> None:
    held = [row[0] for row in _rows(pilot.connection,
                                    "SELECT vehicle_id FROM core.vehicles ORDER BY vehicle_id")]

    assert held == sorted(source.sample(source.seed))
    assert len(held) == SLICE_SIZE
    assert _rows(pilot.connection, "SELECT DISTINCT vehicle_scope, registry_status "
                 "FROM core.vehicles") == [("passenger", "registered")]
    assert not _rows(pilot.connection,
                     "SELECT 1 FROM core.vehicles WHERE plate IN ('TRK001', 'DER001')")


def test_the_same_seed_picks_the_same_cars(source: Source, target_name: str) -> None:
    def picked(seed: str) -> tuple[str, ...]:
        with open_source(source.url) as connection:
            _, keys = plan_pilot(connection, source.options(target_name, seed=seed),
                                 chunk_size=CHUNK_SIZE)
        return keys.vehicles

    assert picked(source.seed) == picked(source.seed)
    assert set(picked(source.seed)) == set(source.sample(source.seed))
    assert any(picked(f"another-seed-{number}") != picked(source.seed) for number in range(5))


def test_every_ts_record_of_a_slice_vehicle_comes_along(source: Source, pilot: Pilot) -> None:
    vehicle = source.vehicle(TWO_RECORD_PLATE)
    linked = [int(row[0]) for row in _rows(
        pilot.connection,
        "SELECT source_record_key FROM core.vehicle_source_links "
        "WHERE vehicle_id = %s AND source_system = 'transportstyrelsen'", (vehicle,))]

    assert len(linked) == 2
    for table, column in (("staging.transportstyrelsen_raw", "id"),
                          ("core.normalization_results", "source_record_id"),
                          ("core.match_field_resolutions", "source_record_id"),
                          ("core.match_chunk_members", "source_record_id")):
        found = _rows(pilot.connection,
                      f"SELECT DISTINCT {column} FROM {table} WHERE {column} = ANY(%s)", (linked,))
        assert sorted(row[0] for row in found) == sorted(linked), table
    # The dedupe keeps one facts row per plate: the pilot has that one, as the source does.
    assert _one(pilot.connection, "SELECT count(*) FROM core.vehicle_facts "
                "WHERE source_record_id = ANY(%s)", (linked,)) == 1


def test_rows_of_other_cars_stay_behind(source: Source, pilot: Pilot) -> None:
    slice_records = {int(row[0]) for row in _rows(
        pilot.connection, "SELECT source_record_key FROM core.vehicle_source_links "
        "WHERE source_system = 'transportstyrelsen'")}
    all_records = {row[0] for row in _rows(source.connection,
                                           "SELECT id FROM staging.transportstyrelsen_raw")}
    source.connection.rollback()

    assert slice_records < all_records
    for table, column in (("staging.transportstyrelsen_raw", "id"),
                          ("core.normalization_results", "source_record_id"),
                          ("core.match_field_resolutions", "source_record_id"),
                          ("core.match_chunk_members", "source_record_id")):
        held = {row[0] for row in _rows(pilot.connection, f"SELECT {column} FROM {table}")}
        assert held == slice_records, table
    for table in ("core.vehicle_facts", "core.review_queue"):
        held = {row[0] for row in _rows(pilot.connection,
                                        f"SELECT source_record_id FROM {table}")}
        assert held and held <= slice_records, table
    vehicles = {row[0] for row in _rows(pilot.connection, "SELECT vehicle_id FROM core.vehicles")}
    for table, column in (("core.vehicle_identifiers", "vehicle_id"),
                          ("core.vehicle_source_links", "vehicle_id")):
        held = {row[0] for row in _rows(pilot.connection, f"SELECT {column} FROM {table}")}
        assert held == vehicles, table
    # Not every car has a ledger entry; those that do are slice cars.
    ledger = {row[0] for row in _rows(pilot.connection,
                                      "SELECT target_node_id FROM core.enrichment_ledger")}
    assert ledger and ledger <= vehicles
    # The car only AIS knows is in the slice with its VIN-keyed link and no TS record.
    assert _one(pilot.connection, "SELECT count(*) FROM core.vehicle_source_links "
                "WHERE source_system = 'ais' AND source_record_key = %s", (AIS_ONLY_VIN,)) == 1


@pytest.mark.parametrize("table", sorted(RULE_TABLES))
def test_rules_and_reviewer_decisions_are_copied_whole(
    source: Source, pilot: Pilot, table: str
) -> None:
    query = f"SELECT to_jsonb(t) FROM {table} AS t ORDER BY {RULE_TABLES[table]}::text"
    expected = _rows(source.connection, query)
    source.connection.rollback()

    assert expected, f"the fixture has no rows in {table}"
    assert _rows(pilot.connection, query) == expected


def test_a_sealed_rule_version_arrives_sealed_with_its_rules(pilot: Pilot) -> None:
    assert _rows(pilot.connection, "SELECT rule_version, sealed, (SELECT count(*) FROM "
                 "core.tecdoc_rules AS r WHERE r.rule_version = v.rule_version) "
                 "FROM core.tecdoc_rule_versions AS v") == [("rules-v1", True, 2)]


def test_only_the_pinned_catalog_batch_is_copied(source: Source, pilot: Pilot) -> None:
    query = ("SELECT to_jsonb(t) FROM core.tecdoc_canonical_candidates AS t "
             "WHERE batch_id = %s ORDER BY entity_type, source_key")
    expected = _rows(source.connection, query, (PINNED_BATCH,))
    source.connection.rollback()

    assert _rows(pilot.connection, "SELECT batch_id, status FROM core.tecdoc_source_batches") == [
        (PINNED_BATCH, "completed")
    ]
    assert _rows(pilot.connection, query, (PINNED_BATCH,)) == expected
    assert _rows(pilot.connection, "SELECT entity_type, count(*) FROM "
                 "core.tecdoc_canonical_candidates GROUP BY 1 ORDER BY 1") == [
        ("engine", 3), ("vehicle_variant", 3)
    ]
    assert _one(pilot.connection, "SELECT count(*) FROM core.tecdoc_candidate_relationships") == 3
    # The registry is whole: it also remembers the ids of batches left behind.
    assert _one(pilot.connection, "SELECT count(*) FROM core.tecdoc_identity_registry") == 6


def test_chunk_counters_describe_the_slice(source: Source, pilot: Pilot) -> None:
    members = _one(pilot.connection, "SELECT count(*) FROM core.match_chunk_members")
    chunks = _one(pilot.connection, "SELECT count(*) FROM core.match_chunks")

    # One chunk per slice record, plus the proposal's chunk whose car is not in the slice.
    assert chunks == members + 1
    assert _rows(pilot.connection, "SELECT member_count, reason_profile FROM core.match_chunks "
                 "WHERE chunk_id = %s", (source.proposal_chunk,)) == [(0, {})]
    assert _rows(pilot.connection, "SELECT DISTINCT member_count, reason_profile "
                 "FROM core.match_chunks WHERE chunk_id <> %s", (source.proposal_chunk,)) == [
        (1, {"missing_engine_code": 1})
    ]
    assert _rows(pilot.connection, "SELECT build_id, row_count, chunk_count, status "
                 "FROM core.match_chunk_builds") == [
        (source.build_id, members, chunks, "completed")
    ]
    # Every reviewer rule still hangs on its build.
    assert _one(pilot.connection, "SELECT count(*) FROM core.match_resolution_rules "
                "WHERE build_id = %s", (source.build_id,)) == 1


def test_no_reference_dangles_and_every_check_passes(pilot: Pilot) -> None:
    failed = [check for check in pilot.outcome.checks if not check.ok]

    assert pilot.outcome.built
    assert failed == []
    assert pilot.outcome.ok
    names = {check.name for check in pilot.outcome.checks}
    assert {"slice size", "slice population", "foreign keys", "sequences", "catalog batch",
            "core.vehicles (class B)", "core.match_resolution_rules (class A)",
            "core.match_run_checkpoints (class C)"} <= names
    for _, child, parent, child_columns, parent_columns in foreign_keys(pilot.connection):
        joined = " AND ".join(f"p.{parent_column} = c.{column}" for column, parent_column
                              in zip(child_columns, parent_columns, strict=True))
        present = " AND ".join(f"c.{column} IS NOT NULL" for column in child_columns)
        assert _one(pilot.connection, f"SELECT count(*) FROM {child} AS c WHERE {present} "
                    f"AND NOT EXISTS (SELECT 1 FROM {parent} AS p WHERE {joined})") == 0, child
    # A learned rule filled an engine code: the vehicle's source names a rule the pilot holds.
    assert _one(pilot.connection, "SELECT count(*) FROM core.vehicle_enrichment_rules") >= 1


def test_sequences_continue_after_the_full_builds_ids(source: Source, pilot: Pilot) -> None:
    query = ("SELECT last_value FROM pg_sequences WHERE schemaname = 'staging' "
             "AND sequencename = 'transportstyrelsen_raw_id_seq'")
    source_last = _one(source.connection, query)
    source.connection.rollback()

    assert _one(pilot.connection, query) == source_last
    new_id = _one(pilot.connection, "INSERT INTO staging.transportstyrelsen_raw "
                  "(source_batch_id, raw_record) VALUES ('after-pilot', '{}') RETURNING id")
    pilot.connection.rollback()
    assert new_id > source_last
    # Identity columns continue too: a new identifier does not collide with a copied id.
    assert _one(pilot.connection, "SELECT last_value >= (SELECT max(id) FROM "
                "core.vehicle_identifiers) FROM pg_sequences "
                "WHERE sequencename = 'vehicle_identifiers_id_seq'")


def test_the_migrations_classify_every_table_and_parents_load_first(pilot: Pilot) -> None:
    created = {f"{schema}.{table}" for schema, table in _rows(
        pilot.connection, "SELECT schemaname, tablename FROM pg_tables "
        "WHERE schemaname IN ('core', 'staging')")}
    order = [spec.table for spec in PILOT_TABLES]

    assert created <= set(order)
    # Only tables no migration creates may be missing from a fresh schema.
    assert {spec_by_table()[table].table_class for table in set(order) - created} == {"C"}
    for _, child, parent, _, _ in foreign_keys(pilot.connection):
        if child != parent:
            assert order.index(parent) < order.index(child), (child, parent)


def test_nothing_reported_names_a_car(source: Source, pilot: Pilot) -> None:
    secrets = [str(value) for row in _rows(
        source.connection, "SELECT vehicle_id, plate, vin FROM core.vehicles") for value in row
        if value]
    source.connection.rollback()

    assert "Verification" not in pilot.output  # the summary is the caller's to print
    assert "core.vehicles" in pilot.output
    for secret in secrets:
        assert secret not in pilot.output


def test_a_changed_row_fails_verification(source: Source, pilot: Pilot) -> None:
    options = source.options(pilot.name)
    with open_source(source.url) as reader, psycopg.connect(_url(pilot.name)) as target:
        plan, keys = plan_pilot(reader, options, chunk_size=CHUNK_SIZE)
        columns = shared_columns(reader, target, plan)
        target.execute("UPDATE core.tecdoc_resolution_rules SET note = 'edited on the pilot'")
        target.execute("UPDATE core.vehicles SET engine_code = 'CHANGED' WHERE vehicle_id = "
                       "(SELECT min(vehicle_id) FROM core.vehicles)")
        checks = verify_pilot(reader, target, plan, keys, columns, (), chunk_size=CHUNK_SIZE)

    failed = {check.name for check in checks if not check.ok}
    assert failed == {"core.tecdoc_resolution_rules (class A)", "core.vehicles (class B)"}
    # verify_pilot rolled the edits back: the pilot is as it was built.
    assert _one(pilot.connection, "SELECT count(*) FROM core.tecdoc_resolution_rules "
                "WHERE note = 'edited on the pilot'") == 0


def test_the_source_connection_cannot_write(source: Source) -> None:
    with open_source(source.url) as connection, pytest.raises(ReadOnlySqlTransaction):
        connection.execute("UPDATE core.vehicles SET engine_code = 'CHANGED'")
    with open_source(source.url) as connection:
        connection.execute("SET default_transaction_read_only = off")
        with pytest.raises(ReadOnlySqlTransaction):
            connection.execute("CREATE TABLE core.pilot_scratch (id int)")


def test_it_refuses_when_the_target_exists(source: Source, pilot: Pilot) -> None:
    before = _one(pilot.connection, "SELECT count(*) FROM core.vehicles")
    pilot.connection.rollback()

    with pytest.raises(PilotBuildError, match="pass --replace"):
        build_pilot_database(source.url, source.options(pilot.name), commit=True,
                             chunk_size=CHUNK_SIZE)

    assert _one(pilot.connection, "SELECT count(*) FROM core.vehicles") == before
    pilot.connection.rollback()


def test_it_never_drops_a_database_it_did_not_build(source: Source, target_name: str) -> None:
    with psycopg.connect(get_ingestion_settings().database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_name)))

    with pytest.raises(PilotBuildError, match="not a pilot build"):
        build_pilot_database(source.url, source.options(target_name), commit=True, replace=True,
                             chunk_size=CHUNK_SIZE)

    assert _exists(target_name)


def _decide(connection: Connection, event: str) -> Any:
    """One stored decision about many cars: its root event."""

    event_id = uuid4()
    connection.execute(
        "INSERT INTO core.vehicle_correction_decisions (event_id, decision_id, chain_position, "
        "event, field, action, value, scope, scope_label, manufacturer, reviewer, reason, "
        "catalog_batch, code_version, measurement) VALUES (%s, %s, 0, %s, 'engine_code', 'set', "
        "'D4204T23', %s, 'All made-up cars', 'VOLVO', 'Ada', %s, %s, 'test', %s)",
        (event_id, event_id, event,
         Jsonb({"kind": "like_this", "conditions": [], "anchor_value": None}),
         None if event == "propose" else "seen", PINNED_BATCH,
         Jsonb({"affected": 1, "checked": 1, "complete": event == "apply", "gained": 1,
                "lost": 0, "moved": 0, "worse": 0})),
    )
    return event_id


def _correct(connection: Connection, vehicle_id: str, position: int, supersedes: Any,
             *, group: Any = None) -> Any:
    """One stored correction of a car's engine code, the way the table itself accepts it."""

    correction_id = uuid4()
    connection.execute(
        "INSERT INTO core.vehicle_fact_corrections (correction_id, vehicle_id, field, "
        "chain_position, action, value, supersedes_correction_id, group_id, reviewer, reason, "
        "previous_source, catalog_batch, automatic_terminal, code_version, "
        "evidence_fingerprint, evidence) VALUES (%s, %s, 'engine_code', %s, 'set', %s, %s, %s, "
        "'Ada', 'seen', 'registry', %s, 'review_required', 'test', %s, %s)",
        (correction_id, vehicle_id, position, f"D4204T{position}", supersedes, group,
         PINNED_BATCH, "a" * 64, Jsonb({"schema": "x", "automatic": {}})),
    )
    return correction_id


def test_it_refuses_the_source_as_target(source: Source) -> None:
    name = str(source.connection.info.dbname)

    with pytest.raises(PilotBuildError, match="is the source database"):
        build_pilot_database(source.url, source.options(name), commit=True, replace=True)

    assert _one(source.connection, "SELECT count(*) FROM core.vehicles") > SLICE_SIZE
    source.connection.rollback()


def test_replace_rebuilds_a_pilot_build(source: Source, target_name: str) -> None:
    # The smallest slice that still holds the car a person decided: a cut that
    # would leave its choices behind is refused.
    smaller = source.sample(source.seed).index(source.vehicle(TWO_RECORD_PLATE)) + 1
    first = build_pilot_database(source.url, source.options(target_name, size=smaller),
                                 commit=True, chunk_size=CHUNK_SIZE)
    second = build_pilot_database(source.url, source.options(target_name), commit=True,
                                  replace=True, chunk_size=CHUNK_SIZE)

    assert first.ok and second.ok
    with psycopg.connect(_url(target_name)) as connection:
        assert _one(connection, "SELECT count(*) FROM core.vehicles") == SLICE_SIZE
    with psycopg.connect(get_ingestion_settings().database_url) as admin:
        marker = _one(admin, "SELECT shobj_description(oid, 'pg_database') FROM pg_database "
                      "WHERE datname = %s", (target_name,))
    assert '"state": "verified"' in marker


def test_an_unknown_catalog_batch_or_too_large_a_slice_stops_the_build(
    source: Source, target_name: str
) -> None:
    plan = build_pilot_database(
        source.url, source.options(target_name, catalog_batch="no-such-batch", size=500),
        chunk_size=CHUNK_SIZE,
    ).plan
    assert len(plan.problems) == 2

    with pytest.raises(PilotBuildError, match="no-such-batch"):
        build_pilot_database(source.url, source.options(target_name, catalog_batch="no-such-batch"),
                             commit=True, chunk_size=CHUNK_SIZE)
    assert not _exists(target_name)


def test_a_persons_ktype_choices_come_along_with_their_car(source: Source, pilot: Pilot) -> None:
    decided = source.vehicle(TWO_RECORD_PLATE)
    query = ("SELECT to_jsonb(c) FROM core.vehicle_ktype_choices AS c "
             "WHERE vehicle_id = %s ORDER BY chain_position")
    kept = _rows(source.connection, query, (decided,))
    source.connection.rollback()

    assert len(kept) == 2
    assert _rows(pilot.connection, query, (decided,)) == kept
    assert _one(pilot.connection, "SELECT count(*) FROM core.vehicle_ktype_choices") == 2
    # The vehicle's copy of the current choice came with the vehicle row.
    assert _rows(pilot.connection, "SELECT ktype, match_state FROM core.vehicles "
                 "WHERE vehicle_id = %s", (decided,)) == [("K1", "manual")]
    # The pilot's table carries every invariant, so the API's lookups work on it.
    verify_vehicle_ktype_choice_schema_contract(pilot.connection)
    pilot.connection.rollback()
    check = next(item for item in pilot.outcome.checks
                 if item.name.startswith("core.vehicle_ktype_choices"))
    assert check.ok and "2 rows" in check.detail


def test_a_persons_corrections_come_along_and_every_decision_does(
    source: Source, pilot: Pilot
) -> None:
    corrected = source.vehicle(TWO_RECORD_PLATE)
    rows = ("SELECT to_jsonb(c) FROM core.vehicle_fact_corrections AS c "
            "WHERE vehicle_id = %s ORDER BY chain_position")
    decisions = "SELECT to_jsonb(d) FROM core.vehicle_correction_decisions AS d ORDER BY event_id"
    kept = _rows(source.connection, rows, (corrected,))
    decided = _rows(source.connection, decisions)
    source.connection.rollback()

    assert (len(kept), len(decided)) == (2, 2)
    # The car's whole chain, and the decisions whole: a proposal has no car at all.
    assert _rows(pilot.connection, rows, (corrected,)) == kept
    assert _one(pilot.connection, "SELECT count(*) FROM core.vehicle_fact_corrections") == 2
    assert _rows(pilot.connection, decisions) == decided
    # Both tables carry every invariant on the pilot, the foreign key between them too.
    verify_vehicle_correction_decision_schema_contract(pilot.connection)
    verify_vehicle_fact_correction_schema_contract(pilot.connection)
    pilot.connection.rollback()
    for table, expected in (("core.vehicle_fact_corrections", "2 rows"),
                            ("core.vehicle_correction_decisions", "2 rows")):
        check = next(item for item in pilot.outcome.checks if item.name.startswith(table))
        assert check.ok and expected in check.detail


def test_a_correction_on_a_car_outside_the_slice_stops_the_build(
    source: Source, target_name: str
) -> None:
    """Never silently left behind: the cut is refused until corrected cars can be pinned."""

    outside = source.vehicle(PROPOSAL_PLATE)
    correction_id = _correct(source.connection, outside, 0, None)
    source.connection.commit()
    try:
        plan = build_pilot_database(source.url, source.options(target_name),
                                    chunk_size=CHUNK_SIZE).plan
        with pytest.raises(PilotBuildError, match="person's correction"):
            build_pilot_database(source.url, source.options(target_name), commit=True,
                                 chunk_size=CHUNK_SIZE)
    finally:
        # The table is append-only; only a test may switch its triggers off.
        source.connection.rollback()
        source.connection.execute("ALTER TABLE core.vehicle_fact_corrections DISABLE TRIGGER USER")
        source.connection.execute(
            "DELETE FROM core.vehicle_fact_corrections WHERE correction_id = %s",
            (correction_id,))
        source.connection.execute("ALTER TABLE core.vehicle_fact_corrections ENABLE TRIGGER USER")
        source.connection.commit()

    (problem,) = plan.problems
    assert problem == (
        "1 cars with a person's correction are outside the slice; their corrections would be "
        "left behind. Pinning corrected cars into the slice is not built yet "
        "(docs/vehicle-fact-corrections.md)"
    )
    assert not _exists(target_name)


def test_a_choice_on_a_car_outside_the_slice_stops_the_build(
    source: Source, target_name: str
) -> None:
    """Never silently left behind: the cut is refused until decided cars can be pinned."""

    outside = source.vehicle(PROPOSAL_PLATE)
    choice_id = _choose(source.connection, outside, 0, None)
    source.connection.commit()
    try:
        plan = build_pilot_database(source.url, source.options(target_name),
                                    chunk_size=CHUNK_SIZE).plan
        with pytest.raises(PilotBuildError, match="KType choice"):
            build_pilot_database(source.url, source.options(target_name), commit=True,
                                 chunk_size=CHUNK_SIZE)
    finally:
        # The table is append-only; only a test may switch its triggers off.
        source.connection.rollback()
        source.connection.execute("ALTER TABLE core.vehicle_ktype_choices DISABLE TRIGGER USER")
        source.connection.execute(
            "DELETE FROM core.vehicle_ktype_choices WHERE choice_id = %s", (choice_id,))
        source.connection.execute("ALTER TABLE core.vehicle_ktype_choices ENABLE TRIGGER USER")
        source.connection.commit()

    (problem,) = plan.problems
    assert problem == (
        "1 cars with a person's KType choice are outside the slice; their choices would be "
        "left behind. Pinning decided cars into the slice is not built yet "
        "(docs/vehicle-ktype-choices.md)"
    )
    assert not _exists(target_name)


def test_a_table_without_a_class_stops_the_build(source: Source, target_name: str) -> None:
    source.connection.execute("CREATE TABLE core.reviewer_notes (note text)")
    source.connection.execute("INSERT INTO core.reviewer_notes VALUES ('keep me')")
    source.connection.commit()
    try:
        plan = build_pilot_database(source.url, source.options(target_name),
                                    chunk_size=CHUNK_SIZE).plan
        with pytest.raises(PilotBuildError, match="core.reviewer_notes"):
            build_pilot_database(source.url, source.options(target_name), commit=True,
                                 chunk_size=CHUNK_SIZE)
    finally:
        source.connection.execute("DROP TABLE core.reviewer_notes")
        source.connection.commit()

    assert len(plan.problems) == 1
    assert plan.problems[0].startswith("core.reviewer_notes holds 1 rows and has no class")
    assert not _exists(target_name)


def test_a_column_the_migrations_do_not_create_stops_the_build(
    source: Source, target_name: str
) -> None:
    source.connection.execute("ALTER TABLE core.vehicle_enrichment_rules ADD COLUMN extra text")
    source.connection.commit()
    try:
        with pytest.raises(PilotBuildError, match=r"core.vehicle_enrichment_rules: columns only "
                           r"in the source \['extra'\]"):
            build_pilot_database(source.url, source.options(target_name), commit=True,
                                 chunk_size=CHUNK_SIZE)
    finally:
        source.connection.execute("ALTER TABLE core.vehicle_enrichment_rules DROP COLUMN extra")
        source.connection.commit()

    # The half-built target stays, marked as a pilot build, for --replace to drop.
    with pytest.raises(PilotBuildError, match="state 'building'"):
        build_pilot_database(source.url, source.options(target_name), commit=True,
                             chunk_size=CHUNK_SIZE)
    assert build_pilot_database(source.url, source.options(target_name), commit=True,
                                replace=True, chunk_size=CHUNK_SIZE).ok


def test_a_closed_review_item_outside_the_slice_is_named_in_the_plan(
    source: Source, target_name: str
) -> None:
    outside = _one(source.connection, "SELECT ts_record_id FROM core.vehicles WHERE plate = %s",
                   (PROPOSAL_PLATE,))
    review_id = uuid4()
    source.connection.execute(
        "INSERT INTO core.review_queue (review_id, source_system, source_table, "
        "source_record_id, reason_code, status, resolved_at, resolved_by, resolution) VALUES "
        "(%s, 'transportstyrelsen', 'staging.transportstyrelsen_raw', %s, 'fixture', 'resolved', "
        "now(), 'reviewer', %s)",
        (review_id, outside, Jsonb({"decision": "kept"})),
    )
    source.connection.commit()
    try:
        plan = build_pilot_database(source.url, source.options(target_name),
                                    chunk_size=CHUNK_SIZE).plan
    finally:
        source.connection.execute("DELETE FROM core.review_queue WHERE review_id = %s",
                                  (review_id,))
        source.connection.commit()

    assert plan.problems == ()
    assert len(plan.warnings) == 1
    assert plan.warnings[0].startswith("1 review items a reviewer closed are on TS records outside")


def test_the_script_prints_the_plan_and_reports_refusals(
    source: Source, pilot: Pilot, target_name: str, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(script, "get_ingestion_settings",
                        lambda: type("Settings", (), {"database_url": source.url})())
    arguments = ["--catalog-batch", PINNED_BATCH, "--size", str(SLICE_SIZE), "--seed", source.seed,
                 "--chunk-size", str(CHUNK_SIZE)]

    assert script.main(["--target-database", target_name, *arguments]) == 0
    printed = capsys.readouterr().out
    assert "Pilot database plan" in printed and "Dry run" in printed
    assert not _exists(target_name)

    assert script.main(["--target-database", pilot.name, "--commit", *arguments]) == 2
    assert "pass --replace" in capsys.readouterr().out


def test_the_default_seed_makes_the_match_impact_sample_a_prefix_of_the_slice(
    source: Source, target_name: str
) -> None:
    default = PilotOptions(target_database=target_name, catalog_batch=PINNED_BATCH)
    assert default.seed is SAMPLE_SEED

    with open_source(source.url) as connection:
        plan, keys = plan_pilot(connection, source.options(target_name, seed=default.seed),
                                chunk_size=CHUNK_SIZE)
    # What the match impact report measures: the API's own sample, a smaller cut.
    report_sample = source.sample(SAMPLE_SEED, size=SLICE_SIZE // 2)
    picked_in_order = source.sample(SAMPLE_SEED, size=SLICE_SIZE)

    assert report_sample == picked_in_order[: SLICE_SIZE // 2]
    assert set(keys.vehicles) == set(picked_in_order)
    assert set(report_sample) < set(keys.vehicles)
    # The plan counts the report's sample (here: every eligible car) against the slice.
    assert (plan.sample_vehicles, plan.sample_in_slice) == (CARS + 1, SLICE_SIZE)
    # Another seed does not have the property.
    assert any(
        not set(report_sample) <= set(source.sample(f"another-seed-{number}"))
        for number in range(5)
    )


def test_history_rows_of_slice_cars_are_in_the_pilot(source: Source, pilot: Pilot) -> None:
    vehicle = source.vehicle(TWO_RECORD_PLATE)
    record = _one(pilot.connection, "SELECT ts_record_id FROM core.vehicles "
                  "WHERE vehicle_id = %s", (vehicle,))

    # The closed identifier: the car stays findable by the plate it used to carry.
    assert _rows(pilot.connection, "SELECT vehicle_id, valid_to IS NOT NULL FROM "
                 "core.vehicle_identifiers WHERE kind = 'plate' AND value = %s",
                 (FORMER_PLATE,)) == [(vehicle, True)]
    # The superseded resolution next to the active one.
    assert _rows(pilot.connection, "SELECT target_value, superseded_at IS NOT NULL FROM "
                 "core.match_field_resolutions WHERE source_record_id = %s "
                 "ORDER BY applied_at", (record,)) == [("saloon", True), ("estate", False)]


def test_a_verified_build_stores_its_manifest(source: Source, pilot: Pilot) -> None:
    manifest = pilot.outcome.manifest

    assert manifest is not None
    assert manifest[MANIFEST_KEY] == 1
    assert (manifest["seed"], manifest["size"], manifest["catalog_batch"]) == (
        source.seed, SLICE_SIZE, PINNED_BATCH
    )
    assert datetime.fromisoformat(manifest["source_snapshot_at"]) <= datetime.now(UTC)
    tables = manifest["tables"]
    assert set(tables) == {spec.table for spec in PILOT_TABLES} - {
        "core.tecdoc_gap_suggestions", "core.remote_passenger_import_parts"
    }
    assert tables["core.vehicles"]["rows"] == SLICE_SIZE
    assert tables["core.vehicles"]["class"] == "B"
    assert "plate" in tables["core.vehicles"]["columns"]
    assert "member_count" not in tables["core.match_chunks"]["columns"]
    assert tables["core.match_run_checkpoints"] == {
        "class": "C", "rows": 0, "checksum": "0:0", "columns": []
    }
    for table, entry in tables.items():
        assert entry["rows"] == _one(pilot.connection, f"SELECT count(*) FROM {table}"), table
    assert stored_manifest(pilot.connection) == manifest
    pilot.connection.rollback()
    # Counts and checksums only: no car is named.
    text = json.dumps(manifest)
    for row in _rows(source.connection, "SELECT vehicle_id, plate, vin FROM core.vehicles"):
        assert not any(value and str(value) in text for value in row)
    source.connection.rollback()


def test_an_untouched_pilot_equals_its_manifest(source: Source, pilot: Pilot) -> None:
    assert pilot.outcome.manifest is not None

    checks = verify_pilot_database(source.url, pilot.name, pilot.outcome.manifest)

    assert [check for check in checks if not check.ok] == []
    names = {check.name for check in checks}
    assert {"core.vehicles (class B)", "core.match_resolution_rules (class A)",
            "core.match_run_checkpoints (class C)", "tables outside the manifest",
            "manifest stored in the database"} <= names
    assert len(checks) == len(pilot.outcome.manifest["tables"]) + 2


def _pg_command(tool: str, database: str) -> list[str] | None:
    """`pg_dump`/`pg_restore` on this machine, or in the server's Docker container."""

    if shutil.which(tool):
        return [tool, "-d", _url(database)]
    if shutil.which("docker") is None:
        return None
    params = conninfo_to_dict(get_ingestion_settings().database_url)
    listed = subprocess.run(
        ["docker", "ps", "--filter", f"publish={params.get('port', 5432)}",
         "--format", "{{.Names}}"],
        capture_output=True, text=True, check=False,
    )
    containers = listed.stdout.split()
    if listed.returncode != 0 or len(containers) != 1:
        return None
    return ["docker", "exec", "-i", containers[0], tool, "-U", str(params["user"]),
            "-d", database]


def test_a_dump_restored_copy_equals_the_manifest(
    source: Source, pilot: Pilot, target_name: str
) -> None:
    dump_command = _pg_command("pg_dump", pilot.name)
    restore_command = _pg_command("pg_restore", target_name)
    if dump_command is None or restore_command is None:
        pytest.skip("pg_dump and pg_restore are not available here")
    assert pilot.outcome.manifest is not None
    options = ["--no-owner", "--no-privileges"]
    dumped = subprocess.run([*dump_command, "-Fc", *options], capture_output=True, check=False)
    if dumped.returncode != 0:
        pytest.skip("this pg_dump cannot dump the test server (version mismatch?)")
    with psycopg.connect(get_ingestion_settings().database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
            sql.Identifier(target_name)))
    restored = subprocess.run([*restore_command, "--exit-on-error", *options],
                              input=dumped.stdout, capture_output=True, check=False)
    assert restored.returncode == 0

    checks = verify_pilot_database(source.url, target_name, pilot.outcome.manifest)

    assert [check for check in checks if not check.ok] == []
    # A restore that lost a table's rows is caught, with the table named.
    with psycopg.connect(_url(target_name)) as connection:
        connection.execute("TRUNCATE core.review_queue")
        connection.commit()
    checks = verify_pilot_database(source.url, target_name, pilot.outcome.manifest)
    assert [check.name for check in checks if not check.ok] == ["core.review_queue (class B)"]


def _marker(database: str) -> str:
    with psycopg.connect(get_ingestion_settings().database_url) as admin:
        return str(_one(admin, "SELECT shobj_description(oid, 'pg_database') FROM pg_database "
                        "WHERE datname = %s", (database,)))


def _script(source: Source, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(script, "get_ingestion_settings",
                        lambda: type("Settings", (), {"database_url": source.url})())
    return ["--catalog-batch", PINNED_BATCH, "--size", str(SLICE_SIZE), "--seed", source.seed,
            "--chunk-size", str(CHUNK_SIZE)]


def test_verify_passes_on_a_built_pilot_and_fails_on_a_changed_or_deleted_row(
    source: Source, target_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = _script(source, monkeypatch)
    manifest_file = tmp_path / "pilot.manifest.json"
    verify = ["--verify", target_name, "--manifest", str(manifest_file)]

    assert script.main(["--target-database", target_name, "--commit", "--manifest",
                        str(manifest_file), *arguments]) == 0
    assert "Manifest written to" in capsys.readouterr().out
    with psycopg.connect(_url(target_name)) as connection:
        assert json.loads(manifest_file.read_text()) == stored_manifest(connection)

    assert script.main(verify) == 0
    printed = capsys.readouterr().out
    assert "FAIL" not in printed and "checks passed" in printed

    with psycopg.connect(_url(target_name)) as connection:
        connection.execute("UPDATE core.vehicles SET engine_code = 'CHANGED' WHERE vehicle_id = "
                           "(SELECT min(vehicle_id) FROM core.vehicles)")
        connection.commit()
    assert script.main(verify) == 1
    printed = capsys.readouterr().out
    assert "FAIL  core.vehicles (class B)" in printed
    assert "content differs from the manifest" in printed
    assert printed.count("FAIL  ") == 1

    with psycopg.connect(_url(target_name)) as connection:
        connection.execute("DELETE FROM core.review_queue WHERE review_id = "
                           "(SELECT review_id FROM core.review_queue LIMIT 1)")
        connection.execute("INSERT INTO core.tecdoc_source_batches SELECT (jsonb_populate_record("
                           "b, jsonb_build_object('batch_id', 'added-on-live'))).* "
                           "FROM core.tecdoc_source_batches AS b")
        connection.commit()
    assert script.main(verify) == 1
    printed = capsys.readouterr().out
    assert "FAIL  core.review_queue (class B)" in printed
    assert "FAIL  core.tecdoc_source_batches (class A): 2 rows, the manifest has 1" in printed
    assert printed.rstrip().endswith(
        "core.review_queue (class B), core.tecdoc_source_batches (class A), "
        "core.vehicles (class B)"
    )
    # The manifest of another build is not accepted for this database.
    other = json.loads(manifest_file.read_text())
    other["built_at"] = "2020-01-01T00:00:00+00:00"
    manifest_file.write_text(json.dumps(other))
    assert script.main(verify) == 1
    assert "FAIL  manifest stored in the database" in capsys.readouterr().out
    # The verification wrote nothing: the build's marker is as it was.
    assert _marker(target_name).count('"state": "verified"') == 1


def test_a_failed_check_exits_with_1_and_is_never_marked_verified(
    source: Source, target_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = _script(source, monkeypatch)
    manifest_file = tmp_path / "pilot.manifest.json"
    real_verify = pilot_database.verify_pilot

    def one_check_fails(*args: Any, **kwargs: Any) -> tuple[Check, ...]:
        return (*real_verify(*args, **kwargs),
                Check("core.vehicles (class B)", False, "5 rows, the source has 6"))

    monkeypatch.setattr(pilot_database, "verify_pilot", one_check_fails)

    status = script.main(["--target-database", target_name, "--commit", "--manifest",
                          str(manifest_file), *arguments])

    assert status == 1
    printed = capsys.readouterr().out
    assert "1 FAILED -- do not ship this database" in printed
    marker = _marker(target_name)
    assert '"state": "verification-failed"' in marker
    assert '"state": "verified"' not in marker
    # A build that failed has no manifest to verify a restore against.
    assert not manifest_file.exists()
    with psycopg.connect(_url(target_name)) as connection:
        assert stored_manifest(connection) is None


def test_a_dry_run_exits_with_1_when_the_target_is_not_a_pilot_build(
    source: Source, pilot: Pilot, target_name: str, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = _script(source, monkeypatch)
    with psycopg.connect(get_ingestion_settings().database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_name)))

    assert script.main(["--target-database", target_name, *arguments]) == 1
    printed = capsys.readouterr().out
    assert "Problems (the build refuses to start)" in printed
    assert "exists and is not a pilot build" in printed
    assert _exists(target_name)

    # An earlier pilot build can be replaced, so a dry run over it is still clean.
    assert script.main(["--target-database", pilot.name, *arguments]) == 0
    assert "exists: pilot build, state 'verified'" in capsys.readouterr().out
