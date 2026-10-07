"""The stored outcome of matching per car: schema, refresh and the reads over it.

Runs on a throwaway database built the way production builds vehicles. The
matcher's outcome is scripted per manufacturer: these tests are about what is
stored, when a car is matched again, and what the overview reads back -- not
about scoring.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, nullcontext
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import Connection

from api.app.features.vehicle_match_results.refresh import MatchResultRefresher
from api.app.features.vehicle_match_results.repository import MatchResultRepository
from api.app.features.vehicle_match_results.schemas import MatchResultCarsRequest
from api.app.features.vehicle_match_results.service import MatchResultService
from api.app.features.vehicle_match_results.sync import MatchResultSync
from api.app.features.vehicle_matching.service import Matcher
from api.app.features.vehicles.repository import VehicleRepository
from api.app.features.vehicles.schemas import VehicleCondition, VehicleFilter
from api.app.features.vehicles.service import VehicleService
from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.match_run_service import MatchSourceRecord
from ingestion.tecdoc.match_run_adapters import MatchEvaluation, ResolvedMatchQuery
from ingestion.vehicle_core_ts import backfill_vehicle_core
from ingestion.vehicle_match_result_migrations import (
    VEHICLE_MATCH_RESULT_MIGRATIONS,
    VehicleMatchResultSchemaContractError,
    run_vehicle_match_result_migrations,
    verify_vehicle_match_result_schema_contract,
)
from tests.integration.throwaway_database import throwaway_database
from tests.integration.vehicle_core_fixtures import (
    insert_ts_record,
    prepare_schema,
    project,
    volvo,
)

RESULTS = "core.vehicle_match_results"
RUNS = "core.vehicle_match_runs"
VOLVO_VIN = "YV1BW84S1F1234567"
GOLF_VIN = "WVWZZZ1KZ8W123456"
AUDI_VIN = "WAUZZZ8K9BA123456"


@pytest.fixture(scope="module")
def db() -> Iterator[Connection]:
    with throwaway_database("match_results") as connection:
        prepare_schema(connection)
        insert_ts_record(connection, volvo(plate="ABC123"))
        insert_ts_record(
            connection,
            volvo(vin=GOLF_VIN, plate="GLF001", brand="VOLKSWAGEN", model="GOLF", fab_code="VW",
                  variant=None, version=None, kw="77", ccm="1390", vehicle_year=2008,
                  registration_date="20080415", build_month="200803"),
        )
        insert_ts_record(
            connection,
            volvo(vin=AUDI_VIN, plate="AUD001", brand="AUDI", model="A4", fab_code="AU",
                  variant=None, version=None, kw="110", ccm="1910", vehicle_year=2008,
                  registration_date="20080901", build_month="200808"),
        )
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        connection.commit()
        yield connection


@pytest.fixture(autouse=True)
def _clean(db: Connection) -> Iterator[None]:
    yield
    db.rollback()
    db.execute(f"DELETE FROM {RESULTS}")
    db.execute(f"DELETE FROM {RUNS}")
    db.execute("UPDATE core.vehicles SET match_state = NULL, ktype = NULL "
               "WHERE match_state IS NOT NULL OR ktype IS NOT NULL")
    db.commit()


def _vehicle(db: Connection, vin: str = VOLVO_VIN) -> str:
    row = db.execute("SELECT vehicle_id FROM core.vehicles WHERE vin = %s", (vin,)).fetchone()
    assert row is not None
    db.commit()
    return str(row[0])


def _row(db: Connection, vehicle_id: str) -> dict[str, Any]:
    row = db.execute(
        f"SELECT to_jsonb(m) FROM {RESULTS} AS m WHERE vehicle_id = %s", (vehicle_id,)
    ).fetchone()
    db.commit()
    assert row is not None
    return dict(row[0])


def _candidate(reference: str, *conflicting: str) -> dict[str, Any]:
    return {
        "candidate_reference": reference,
        "candidate_type": "TecDocKType",
        "confidence": 0.9,
        "evidence": {"conflicting_fields": list(conflicting)},
    }


class _Scripted:
    """The matcher's outcome per manufacturer, set by the test; inputs come from the database."""

    def __init__(self) -> None:
        self.calls = 0
        self.script: dict[str, MatchEvaluation] = {
            "Volvo": MatchEvaluation(
                "review_required", ("match:scored", "candidate_margin_below_gate"),
                top_candidate_reference="A",
                candidate_matches=(_candidate("A"), _candidate("B")), confidence=0.9,
            ),
            "Volkswagen": MatchEvaluation(
                "resolved", ("match:automatic",), top_candidate_reference="G",
                candidate_matches=(_candidate("G"),), confidence=0.97,
            ),
            "Audi": MatchEvaluation(
                "hard_conflict", ("match:scored", "hard_conflict:power_kw"),
                top_candidate_reference="S",
                candidate_matches=(_candidate("S", "power_kw"),), confidence=0.4,
            ),
        }

    def _maker(self, record: MatchSourceRecord) -> str:
        normalized: dict[str, Any] = record.payload["normalized"]  # type: ignore[assignment]
        return str(normalized.get("manufacturer"))

    def evaluate(self, record: MatchSourceRecord) -> MatchEvaluation:
        self.calls += 1
        return self.script[self._maker(record)]

    def resolved_query(self, record: MatchSourceRecord) -> ResolvedMatchQuery:
        return ResolvedMatchQuery(
            key=(self._maker(record),), scope_manufacturer=self._maker(record),
            model_values=("x",), year=2015, fuels=frozenset({"diesel"}), engine_code=None,
            displacement_cc=1969, power_kw=133, drive_type=None, bodywork=None,
            recovery_reason=None, source_context=(), source_model_resolution=None,
        )


class _World:
    def __init__(self, db: Connection) -> None:
        def factory() -> AbstractContextManager[Connection]:
            return nullcontext(db)

        self.db = db
        self.evaluator = _Scripted()
        self.batch = "batch-1"
        self.catalog = {
            "A": VehicleCandidate(candidate_reference="A", manufacturer="VOLVO", model="V70",
                                  power_kw=133, drive_type="fwd"),
            "B": VehicleCandidate(candidate_reference="B", manufacturer="VOLVO", model="V70",
                                  power_kw=133, drive_type="awd"),
        }
        self.refresher = MatchResultRefresher(factory, self._matcher, "build-1", page_size=2)
        self.service = MatchResultService(MatchResultRepository(factory))
        self.vehicles = VehicleService(VehicleRepository(factory))
        self.sync = MatchResultSync(lambda: self.refresher, factory, run_in_background=False)

    def _matcher(self) -> Matcher:
        return Matcher(self.batch, self.evaluator, self.catalog, "rules-1")  # type: ignore[arg-type]

    def states(self, vehicle_filter: VehicleFilter | None = None) -> dict[str, int]:
        overview = self.service.overview(vehicle_filter or VehicleFilter())
        return {item.state: item.cars for item in overview.states if item.cars}


@pytest.fixture
def world(db: Connection) -> _World:
    return _World(db)


# ------------------------------------------------------------------------------ schema


def test_the_migration_is_idempotent_and_verified(db: Connection) -> None:
    names = tuple(name for name, _ in VEHICLE_MATCH_RESULT_MIGRATIONS)
    assert run_vehicle_match_result_migrations(db) == names
    assert run_vehicle_match_result_migrations(db) == names


def test_the_verifier_refuses_a_table_that_lost_a_constraint(db: Connection) -> None:
    db.execute(f"ALTER TABLE {RESULTS} DROP CONSTRAINT vehicle_match_results_ktype_only_when_resolved")
    with pytest.raises(VehicleMatchResultSchemaContractError, match="ktype_only_when_resolved"):
        verify_vehicle_match_result_schema_contract(db)
    db.rollback()
    verify_vehicle_match_result_schema_contract(db)
    db.commit()


def test_the_verifier_refuses_a_missing_index(db: Connection) -> None:
    db.execute("DROP INDEX core.vehicle_match_results_state_idx")
    with pytest.raises(VehicleMatchResultSchemaContractError, match="state_idx"):
        verify_vehicle_match_result_schema_contract(db)
    db.rollback()


@pytest.mark.parametrize(
    ("change", "constraint"),
    [
        ("ktype = NULL", "vehicle_match_results_ktype_only_when_resolved"),
        ("state = 'several'", "vehicle_match_results_ktype_only_when_resolved"),
        ("candidate_count = 3", "vehicle_match_results_candidates_counted"),
        ("state = 'solved', ktype = NULL", "vehicle_match_results_state_values"),
        ("confidence = 1.5", "vehicle_match_results_confidence_range"),
        ("input_hash = 'abc'", "vehicle_match_results_input_hash_format"),
        ("vehicle_id = 'NOR-01ARZ3NDEKTSV4RRFFQ69G5FAV'", "vehicle_match_results_vehicle_fkey"),
    ],
)
def test_the_database_refuses_a_row_that_contradicts_itself(
    db: Connection, world: _World, change: str, constraint: str
) -> None:
    golf = _vehicle(db, GOLF_VIN)
    world.refresher.refresh_vehicles([golf])
    with pytest.raises(psycopg.errors.IntegrityError) as refused:
        db.execute(f"UPDATE {RESULTS} SET {change} WHERE vehicle_id = %s", (golf,))
    assert refused.value.diag.constraint_name == constraint
    db.rollback()


def test_a_tie_needs_two_candidates(db: Connection, world: _World) -> None:
    volvo_id = _vehicle(db)
    world.refresher.refresh_vehicles([volvo_id])
    with pytest.raises(psycopg.errors.CheckViolation) as refused:
        db.execute(
            f"UPDATE {RESULTS} SET candidate_count = 1, candidate_ktypes = '{{A}}', "
            "candidate_confidences = '{0.9}' WHERE vehicle_id = %s", (volvo_id,),
        )
    assert refused.value.diag.constraint_name == "vehicle_match_results_state_fits_candidates"
    db.rollback()


# ----------------------------------------------------------------------------- refresh


def test_a_run_stores_one_row_per_car_with_what_the_matcher_concluded(
    db: Connection, world: _World
) -> None:
    counts = world.refresher.refresh_scope()
    assert (counts.target, counts.evaluated, counts.unchanged) == (3, 3, 0)

    tie = _row(db, _vehicle(db))
    assert tie["state"] == "several"
    assert tie["ktype"] is None
    assert tie["best_candidate_ktype"] == "A"
    assert tie["candidate_ktypes"] == ["A", "B"]
    assert tie["candidate_count"] == 2
    assert tie["separating_fields"] == ["drive_type"]
    assert tie["missing_fields"] == ["drive_type"]
    assert tie["reason_codes"] == ["match:scored", "candidate_margin_below_gate"]
    assert (tie["catalog_batch"], tie["matcher_version"]) == ("batch-1", "build-1")

    resolved = _row(db, _vehicle(db, GOLF_VIN))
    assert (resolved["state"], resolved["ktype"], resolved["terminal"]) == ("resolved", "G", "resolved")
    assert resolved["confidence"] == pytest.approx(0.97)

    conflict = _row(db, _vehicle(db, AUDI_VIN))
    assert (conflict["state"], conflict["terminal"]) == ("none", "hard_conflict")
    assert conflict["conflicting_fields"] == ["power_kw"]
    assert conflict["best_candidate_ktype"] == "S"

    run = db.execute(f"SELECT mode, status, target, evaluated, unchanged, catalog_batch, "
                     f"rule_set_version, finished_at IS NOT NULL FROM {RUNS}").fetchall()
    db.commit()
    assert run == [("stale", "completed", 3, 3, 0, "batch-1", "rules-1", True)]


def test_a_second_run_finds_nothing_to_match(db: Connection, world: _World) -> None:
    world.refresher.refresh_scope()
    calls = world.evaluator.calls
    counts = world.refresher.refresh_scope()
    assert (counts.target, counts.evaluated, counts.unchanged) == (0, 0, 0)
    assert world.evaluator.calls == calls


def test_a_changed_car_is_matched_again_and_only_that_car(db: Connection, world: _World) -> None:
    world.refresher.refresh_scope()
    volvo_id = _vehicle(db)
    db.execute("UPDATE core.vehicles SET power_kw = 140, updated_at = clock_timestamp() "
               "WHERE vehicle_id = %s", (volvo_id,))
    db.commit()
    before = _row(db, volvo_id)
    world.evaluator.script["Volvo"] = MatchEvaluation(
        "resolved", ("match:automatic",), top_candidate_reference="B",
        candidate_matches=(_candidate("B"),), confidence=0.95,
    )
    calls = world.evaluator.calls
    counts = world.refresher.refresh_scope()
    assert (counts.target, counts.evaluated, counts.unchanged) == (1, 1, 0)
    assert world.evaluator.calls == calls + 1
    after = _row(db, volvo_id)
    assert (after["state"], after["ktype"]) == ("resolved", "B")
    assert after["input_hash"] != before["input_hash"]
    assert world.states() == {"resolved": 2, "none": 1}


def test_a_touched_car_the_matcher_would_see_unchanged_is_not_matched_again(
    db: Connection, world: _World
) -> None:
    world.refresher.refresh_scope()
    volvo_id = _vehicle(db)
    before = _row(db, volvo_id)
    # A column the matcher does not read.
    db.execute("UPDATE core.vehicles SET colour = 'RED', updated_at = clock_timestamp() "
               "WHERE vehicle_id = %s", (volvo_id,))
    db.commit()
    assert world.service.overview(VehicleFilter()).changed_since_matched == 1
    calls = world.evaluator.calls
    counts = world.refresher.refresh_scope()
    assert (counts.target, counts.evaluated, counts.unchanged) == (1, 0, 1)
    assert world.evaluator.calls == calls
    after = _row(db, volvo_id)
    assert after["run_id"] == before["run_id"]
    assert after["evaluated_at"] > before["evaluated_at"]
    assert world.refresher.refresh_scope().target == 0
    assert world.service.overview(VehicleFilter()).changed_since_matched == 0


def test_another_catalog_batch_makes_every_row_stale(db: Connection, world: _World) -> None:
    world.refresher.refresh_scope()
    world.batch = "batch-2"
    counts = world.refresher.refresh_scope()
    assert (counts.target, counts.evaluated) == (3, 3)
    overview = world.service.overview(VehicleFilter())
    assert [(item.value, item.cars) for item in overview.catalog_batches] == [("batch-2", 3)]


def test_a_rebuild_matches_every_car_again(db: Connection, world: _World) -> None:
    world.refresher.refresh_scope()
    calls = world.evaluator.calls
    counts = world.refresher.refresh_scope(rebuild=True)
    assert (counts.target, counts.evaluated, counts.unchanged) == (3, 3, 0)
    assert world.evaluator.calls == calls + 3
    modes = [row[0] for row in db.execute(f"SELECT mode FROM {RUNS} ORDER BY started_at")]
    db.commit()
    assert modes == ["stale", "all"]


def test_a_limit_stops_a_run_and_the_next_one_continues(db: Connection, world: _World) -> None:
    first = world.refresher.refresh_scope(limit=2)
    assert (first.target, first.evaluated) == (2, 2)
    assert world.states()["not_evaluated"] == 1
    second = world.refresher.refresh_scope()
    assert (second.target, second.evaluated) == (1, 1)
    assert "not_evaluated" not in world.states()


def test_a_failing_run_is_recorded_and_keeps_the_pages_it_finished(
    db: Connection, world: _World
) -> None:
    del world.evaluator.script["Audi"]
    ordered = sorted(_vehicle(db, vin) for vin in (VOLVO_VIN, GOLF_VIN, AUDI_VIN))
    audi = _vehicle(db, AUDI_VIN)
    with pytest.raises(KeyError):
        world.refresher.refresh_vehicles(ordered)
    db.rollback()
    status = db.execute(f"SELECT status, error, finished_at IS NOT NULL FROM {RUNS}").fetchall()
    stored = {str(row[0]) for row in db.execute(f"SELECT vehicle_id FROM {RESULTS}")}
    db.commit()
    assert status == [("failed", "KeyError", True)]
    # Pages hold two cars and are committed one by one: what came before the
    # Audi's page is stored, its own page and what follows are not.
    assert stored == (set() if audi in ordered[:2] else set(ordered[:2]))


# ------------------------------------------------------------------------------- reads


def test_the_overview_counts_every_car_and_names_the_causes(db: Connection, world: _World) -> None:
    assert world.states() == {"not_evaluated": 3}
    world.refresher.refresh_scope()
    overview = world.service.overview(VehicleFilter())
    assert overview.total == 3
    assert {item.state: item.cars for item in overview.states} == {
        "resolved": 1, "several": 1, "one_unconfirmed": 0, "none": 1, "not_matchable": 0,
        "chosen": 0, "chosen_none": 0, "not_evaluated": 0,
    }
    assert overview.several_candidate_counts == {"2": 1}
    assert [(f.field, f.cars) for f in overview.several_separating_fields] == [("drive_type", 1)]
    assert [(f.field, f.cars) for f in overview.several_missing_fields] == [("drive_type", 1)]
    assert [(f.field, f.cars) for f in overview.none_conflicting_fields] == [("power_kw", 1)]
    assert overview.none_without_candidates == 0
    assert overview.resolved_only_fit == 0
    assert {item.value: item.cars for item in overview.terminals} == {
        "resolved": 1, "review_required": 1, "hard_conflict": 1,
    }
    # A car matched to a candidate-only KType as its only fit is counted on its own.
    db.execute(
        "UPDATE core.vehicle_match_results SET reason_codes = reason_codes || ARRAY['candidate_only_sole_fit'] "
        "WHERE state = 'resolved'"
    )
    assert world.service.overview(VehicleFilter()).resolved_only_fit == 1
    assert overview.latest_run is not None
    assert (overview.latest_run.status, overview.latest_run.evaluated) == ("completed", 3)
    assert [(v.value, v.cars) for v in overview.matcher_versions] == [("build-1", 3)]


def test_the_overview_follows_the_vehicles_filter(db: Connection, world: _World) -> None:
    world.refresher.refresh_scope()
    only_volvo = VehicleFilter(conditions=[VehicleCondition(field="manufacturer", values=["Volvo"])])
    assert world.states(only_volvo) == {"several": 1}
    assert world.states(VehicleFilter(text="GLF001")) == {"resolved": 1}


def test_a_persons_choice_outranks_the_matchers_state(db: Connection, world: _World) -> None:
    world.refresher.refresh_scope()
    volvo_id = _vehicle(db)
    db.execute("UPDATE core.vehicles SET match_state = 'manual', ktype = 'B' WHERE vehicle_id = %s",
               (volvo_id,))
    db.commit()
    assert world.states() == {"resolved": 1, "none": 1, "chosen": 1}
    page = world.service.cars(MatchResultCarsRequest(state="chosen"))
    assert [(car.vehicle_id, car.ktype, car.automatic_state, car.automatic_ktype)
            for car in page.cars] == [(volvo_id, "B", "several", None)]
    assert world.service.cars(MatchResultCarsRequest(state="several")).total == 0


def test_the_cars_behind_a_number_carry_their_possible_ktypes(db: Connection, world: _World) -> None:
    world.refresher.refresh_scope()
    page = world.service.cars(MatchResultCarsRequest(state="several"))
    assert page.total == 1 and page.next_after is None
    car = page.cars[0]
    assert (car.plate, car.manufacturer, car.state) == ("ABC123", "Volvo", "several")
    assert car.candidate_ktypes == ["A", "B"]
    assert car.candidate_confidences == pytest.approx([0.9, 0.9])
    assert car.ktype is None and car.best_candidate_ktype == "A"
    assert car.missing_fields == ["drive_type"]
    assert car.changed_since_matched is False

    resolved = world.service.cars(MatchResultCarsRequest(state="resolved")).cars
    assert [(car.plate, car.ktype) for car in resolved] == [("GLF001", "G")]


def test_a_state_list_can_be_narrowed_to_one_cause(db: Connection, world: _World) -> None:
    world.refresher.refresh_scope()

    def total(**narrowing: Any) -> int:
        return world.service.cars(MatchResultCarsRequest(**narrowing)).total

    assert total(state="several", missing_field="drive_type") == 1
    assert total(state="several", missing_field="engine_code") == 0
    assert total(state="several", ktype="B") == 1
    assert total(state="several", ktype="G") == 0
    assert total(state="several", candidate_count=2) == 1
    assert total(state="several", reason="candidate_margin_below_gate") == 1
    assert total(state="none", conflicting_field="power_kw") == 1
    assert total(state="resolved", ktype="G") == 1


def test_a_state_list_pages_in_nor_id_order(db: Connection, world: _World) -> None:
    world.evaluator.script["Volkswagen"] = world.evaluator.script["Volvo"]
    world.evaluator.script["Audi"] = world.evaluator.script["Volvo"]
    world.refresher.refresh_scope()
    first = world.service.cars(MatchResultCarsRequest(state="several", limit=2))
    assert first.total == 3 and len(first.cars) == 2 and first.next_after == first.cars[-1].vehicle_id
    second = world.service.cars(
        MatchResultCarsRequest(state="several", limit=2, after=first.next_after)
    )
    assert len(second.cars) == 1 and second.next_after is None
    ids = [car.vehicle_id for car in (*first.cars, *second.cars)]
    assert ids == sorted(ids) and len(set(ids)) == 3


def test_a_car_without_a_result_is_listed_as_not_evaluated(db: Connection, world: _World) -> None:
    golf = _vehicle(db, GOLF_VIN)
    world.refresher.refresh_vehicles([golf])
    page = world.service.cars(MatchResultCarsRequest(state="not_evaluated"))
    assert page.total == 2
    assert all(car.automatic_state is None and car.evaluated_at is None for car in page.cars)
    assert golf not in {car.vehicle_id for car in page.cars}


# ------------------------------------------------------------ kept current on a save


def test_a_saved_change_refreshes_that_cars_row_at_once(db: Connection, world: _World) -> None:
    world.refresher.refresh_scope()
    volvo_id = _vehicle(db)
    # A value no earlier test left on the car: vehicles keep their changes in this module.
    db.execute("UPDATE core.vehicles SET power_kw = 155, updated_at = clock_timestamp() "
               "WHERE vehicle_id = %s", (volvo_id,))
    db.commit()
    world.evaluator.script["Volvo"] = MatchEvaluation(
        "resolved", ("match:automatic",), top_candidate_reference="B",
        candidate_matches=(_candidate("B"),), confidence=0.95,
    )
    assert world.service.overview(VehicleFilter()).changed_since_matched == 1

    world.sync.vehicle_changed(volvo_id)

    overview = world.service.overview(VehicleFilter())
    assert overview.changed_since_matched == 0
    assert world.states() == {"resolved": 2, "none": 1}
    # The run a save makes for its car does not hide the last run over the table.
    assert overview.latest_run is not None and overview.latest_run.mode == "stale"


def test_a_decision_without_cars_reads_the_corrections_table_and_refreshes_nothing(
    db: Connection, world: _World
) -> None:
    calls = world.evaluator.calls
    world.sync.decision_changed(uuid4())
    db.commit()
    assert world.evaluator.calls == calls
    assert db.execute(f"SELECT count(*) FROM {RUNS}").fetchone() == (0,)
    db.commit()


# ----------------------------------------------------- the main Vehicles list's filter


def _listed(world: _World, *states: str, operator: str = "equals") -> list[tuple[str | None, str | None, str | None]]:
    page = world.vehicles.search(
        [VehicleCondition(field="match_result", values=list(states), operator=operator)],  # type: ignore[arg-type]
        "", cursor=None, limit=50,
    )
    assert page.matched_rows == len(page.items)
    return sorted((item.plate, item.match_result, item.automatic_ktype) for item in page.items)


def test_the_vehicles_list_filters_on_the_stored_state(db: Connection, world: _World) -> None:
    assert len(_listed(world, "not_evaluated")) == 3
    world.refresher.refresh_scope()
    assert _listed(world, "resolved") == [("GLF001", "resolved", "G")]
    assert _listed(world, "several") == [("ABC123", "several", None)]
    assert _listed(world, "several", "none") == [
        ("ABC123", "several", None), ("AUD001", "none", None),
    ]
    assert _listed(world, "not_evaluated") == []
    assert _listed(world, "resolved", operator="not_equals") == [
        ("ABC123", "several", None), ("AUD001", "none", None),
    ]


def test_the_vehicles_list_counts_a_decided_car_as_chosen(db: Connection, world: _World) -> None:
    world.refresher.refresh_scope()
    db.execute("UPDATE core.vehicles SET match_state = 'manual', ktype = 'B' WHERE vehicle_id = %s",
               (_vehicle(db),))
    db.commit()
    assert _listed(world, "several") == []
    assert _listed(world, "chosen") == [("ABC123", "several", None)]


def test_the_vehicles_list_combines_the_state_with_its_other_filters(
    db: Connection, world: _World
) -> None:
    world.refresher.refresh_scope()
    page = world.vehicles.search(
        [
            VehicleCondition(field="match_result", values=["several", "resolved"]),
            VehicleCondition(field="manufacturer", values=["Volvo"]),
        ],
        "", cursor=None, limit=50,
    )
    assert [item.plate for item in page.items] == ["ABC123"]
    facet = world.vehicles.facet(
        [VehicleCondition(field="match_result", values=["resolved"])], "",
        field="manufacturer", limit=10,
    )
    assert [(value.value, value.count) for value in facet.values] == [("Volkswagen", 1)]


def test_the_counts_strip_reads_the_states_alone(db: Connection, world: _World) -> None:
    world.refresher.refresh_scope()
    counts = world.service.counts(VehicleFilter())
    assert counts.total == 3
    assert {item.state: item.cars for item in counts.states if item.cars} == {
        "resolved": 1, "several": 1, "none": 1,
    }
    assert len(counts.states) == 8 and counts.changed_since_matched == 0
    only = world.service.counts(VehicleFilter(text="GLF001"))
    assert (only.total, {i.state: i.cars for i in only.states if i.cars}) == (1, {"resolved": 1})


def test_the_vehicles_list_shows_possible_ktypes_and_filters_on_the_cause(
    db: Connection, world: _World
) -> None:
    world.refresher.refresh_scope()

    def plates(*conditions: VehicleCondition) -> list[str | None]:
        page = world.vehicles.search(list(conditions), "", cursor=None, limit=50)
        assert page.matched_rows == len(page.items)
        return sorted(item.plate for item in page.items)

    tied = world.vehicles.search(
        [VehicleCondition(field="match_result", values=["several"])], "", cursor=None, limit=50
    ).items[0]
    assert tied.candidate_ktypes == ["A", "B"]
    assert tied.candidate_confidences == pytest.approx([0.9, 0.9])

    assert plates(VehicleCondition(field="match_missing_field", values=["drive_type"])) == ["ABC123"]
    assert plates(VehicleCondition(field="match_missing_field", values=["engine_code"])) == []
    assert plates(VehicleCondition(field="match_separating_field", values=["drive_type"])) == ["ABC123"]
    assert plates(VehicleCondition(field="match_conflicting_field", values=["power_kw"])) == ["AUD001"]
    assert plates(VehicleCondition(field="match_reason", values=["match:automatic"])) == ["GLF001"]
    assert plates(VehicleCondition(field="match_candidate_count", values=["2"])) == ["ABC123"]
    assert plates(VehicleCondition(field="match_candidate_count", values=["1", "2"])) == [
        "ABC123", "GLF001",
    ]
    # A KType finds the cars it is accepted for and the cars it is possible for ...
    assert plates(VehicleCondition(field="match_ktype", values=["G"])) == ["GLF001"]
    assert plates(VehicleCondition(field="match_ktype", values=["B"])) == ["ABC123"]
    assert plates(VehicleCondition(field="match_ktype", values=["Z"])) == []
    # ... and the cars a person chose it for.
    db.execute("UPDATE core.vehicles SET match_state = 'manual', ktype = 'Z' WHERE vehicle_id = %s",
               (_vehicle(db, AUDI_VIN),))
    db.commit()
    assert plates(VehicleCondition(field="match_ktype", values=["Z"])) == ["AUD001"]


@pytest.mark.parametrize(
    ("field", "operator", "values"),
    [
        ("match_candidate_count", "equals", ["two"]),
        ("match_missing_field", "not_equals", ["drive_type"]),
        ("match_ktype", "contains", ["A"]),
    ],
)
def test_a_cause_filter_the_list_cannot_answer_is_refused(
    world: _World, field: str, operator: str, values: list[str]
) -> None:
    with pytest.raises(ValueError, match=field):
        world.vehicles.search(
            [VehicleCondition(field=field, values=values, operator=operator)],  # type: ignore[arg-type]
            "", cursor=None, limit=50,
        )


# ------------------------------------------------- changes to many cars by a reviewer rule


def test_asking_for_a_refresh_matches_the_changed_cars_again(db: Connection, world: _World) -> None:
    world.refresher.refresh_scope()
    # What a reviewer rule does: it changes cars without touching their stored results.
    db.execute("UPDATE core.vehicles SET power_kw = 161, updated_at = clock_timestamp() "
               "WHERE vin = ANY(%s)", ([VOLVO_VIN, GOLF_VIN],))
    db.commit()
    assert world.service.counts(VehicleFilter()).changed_since_matched == 2
    calls = world.evaluator.calls

    assert world.sync.population_changed() is True

    assert world.evaluator.calls == calls + 2
    assert world.service.counts(VehicleFilter()).changed_since_matched == 0
    assert world.sync.refreshing is False


def test_the_reviewer_rules_list_reads_the_rule_tables(db: Connection, world: _World) -> None:
    # No rule was made in this database: the query runs against the real tables.
    assert world.service.reviewer_rules(50).rules == []
    db.commit()

