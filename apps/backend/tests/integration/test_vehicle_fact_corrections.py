"""A person's corrections of one car's data: what the database itself guarantees.

Runs on a throwaway database built the way production builds vehicles. Covers
the migration and its definition-level verifier, database-enforced immutability,
retry ambiguity (one operation id, one row), invalid correction links (each
refused by a named constraint, also when several rows arrive in one statement),
concurrency on one car, the copy on `core.vehicles` (its own source, above a
reviewer's rule) and its restore, the repair of that copy, and the API services
end to end over the real repositories with the real matcher on a small made-up
catalog: a correction is evidence for the matcher, so what it does to an
evaluation is what these tests assert.
"""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import Connection
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from api.app.features.vehicle_corrections.fields import NORMALIZATION_STOP, SPECS, vehicle_copy
from api.app.features.vehicle_corrections.repository import (
    CorrectionRejectedError,
    CorrectionRepository,
    CorrectionVehicleNotFoundError,
    VehicleBusyError,
)
from api.app.features.vehicle_corrections.schemas import CorrectionRequest
from api.app.features.vehicle_corrections.service import (
    CorrectionService,
    EvidenceChangedError,
    InvalidValueError,
    NothingToIgnoreError,
    ReasonRequiredError,
    ValueUnchangedError,
)
from api.app.features.vehicle_ktype_choices.repository import KTypeChoiceRepository
from api.app.features.vehicle_ktype_choices.schemas import KTypeChoiceRequest
from api.app.features.vehicle_ktype_choices.service import KTypeChoiceService
from api.app.features.vehicle_matching.repository import VehicleMatchingRepository
from api.app.features.vehicle_matching.schemas import VehicleMatchLookup
from api.app.features.vehicle_matching.service import (
    Matcher,
    SummaryJobs,
    VehicleMatchingService,
)
from api.app.features.vehicles.repository import VehicleRepository
from api.app.features.vehicles.service import VehicleService
from ingestion.confidence_routing_migrations import run_confidence_routing_migrations
from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator
from ingestion.vehicle_core_merge import VehicleState
from ingestion.vehicle_core_review import sync_applied_review, sync_retired_review
from ingestion.vehicle_core_store import load_vehicle, load_vehicles, save_vehicles
from ingestion.vehicle_core_ts import backfill_vehicle_core
from ingestion.vehicle_fact_correction_migrations import (
    COLUMNS,
    VEHICLE_FACT_CORRECTION_MIGRATION_STATEMENTS,
    VehicleFactCorrectionSchemaContractError,
    run_vehicle_fact_correction_migrations,
    verify_vehicle_fact_correction_schema_contract,
)
from ingestion.vehicle_fact_corrections import (
    CorrectionChangedError,
    CorrectionHead,
    NewCorrection,
    NothingToWithdrawError,
    OperationReusedError,
    append_correction,
    chains,
    correction_heads,
    current_corrections,
    project_correction,
    reproject_corrections,
    standing_corrections,
)
from ingestion.vehicle_facts_query import compile_predicate
from ingestion.vehicle_facts_rules import apply_rule, retire_rule
from ingestion.vehicle_ktype_choice_migrations import verify_vehicle_ktype_choice_schema_contract
from ingestion.vehicle_ktype_choices import project_choices
from tests.integration.throwaway_database import throwaway_database
from tests.integration.vehicle_core_fixtures import (
    insert_ts_record,
    prepare_schema,
    project,
    volvo,
)

TABLE = "core.vehicle_fact_corrections"
CHOICES = "core.vehicle_ktype_choices"
FINGERPRINT = "a" * 64

# Made-up cars. The first two are the ones most tests correct; the others each
# bring one thing: a plug-in hybrid, a record stopped for normalization review,
# and one a policy keeps out of matching.
VOLVO = "YV1BW84S1F1234567"
GOLF = "WVWZZZ1KZ8W123456"
HYBRID = "YV1BW84S1F1234777"
STOPPED = "YV1BW84S1F1234999"
MOTORHOME = "YV1BW84S1F1234888"

_PRISTINE: dict[str, VehicleState] = {}


@pytest.fixture(scope="module")
def db() -> Iterator[Connection]:
    with throwaway_database("fact_corrections") as connection:
        prepare_schema(connection)
        run_confidence_routing_migrations(connection)
        insert_ts_record(connection, volvo(plate="ABC123"))
        insert_ts_record(
            connection,
            volvo(vin=GOLF, plate="GLF001", brand="VOLKSWAGEN", model="GOLF",
                  fab_code="VW", variant=None, version=None, kw="77", ccm="1390",
                  vehicle_year=2008, registration_date="20080415", build_month="200803"),
        )
        insert_ts_record(
            connection, volvo(vin=HYBRID, plate="HYB001", fuel1="1", fuel2="3", ev_config="LADDHYBRID")
        )
        insert_ts_record(connection, volvo(vin=STOPPED, plate="STP001", tyre_front="XYZ"))
        insert_ts_record(
            connection, volvo(vin=MOTORHOME, plate="MOT001", body_code="SA", vehicle_class="II")
        )
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        connection.commit()
        ids = [str(row[0]) for row in connection.execute("SELECT vehicle_id FROM core.vehicles")]
        _PRISTINE.clear()
        _PRISTINE.update(load_vehicles(connection, ids))
        connection.commit()
        yield connection


@pytest.fixture(autouse=True)
def _clean(db: Connection) -> Iterator[None]:
    """Each test starts with no corrections and untouched cars. Only a test may switch triggers off."""

    yield
    db.rollback()
    for table in (TABLE, CHOICES):
        db.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
        db.execute(f"DELETE FROM {table}")
        db.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")
    save_vehicles(db, copy.deepcopy(list(_PRISTINE.values())))
    project_choices(db, None)
    db.commit()
    verify_vehicle_fact_correction_schema_contract(db)
    verify_vehicle_ktype_choice_schema_contract(db)
    db.commit()


def _vehicle(db: Connection, vin: str = VOLVO) -> str:
    row = db.execute("SELECT vehicle_id FROM core.vehicles WHERE vin = %s", (vin,)).fetchone()
    assert row is not None
    db.commit()
    return str(row[0])


def _other_vehicle(db: Connection) -> str:
    return _vehicle(db, GOLF)


def _evidence() -> dict[str, Any]:
    return {
        "schema": "vehicle-fact-correction-evidence-v1",
        "automatic": {"terminal": "review_required", "top_ktype": None, "reason_codes": []},
        "inputs": {"power_kw": 133},
        "overlaid_fields": {},
    }


def _new(vehicle_id: str, **overrides: Any) -> NewCorrection:
    values: dict[str, Any] = {
        "correction_id": uuid4(),
        "vehicle_id": vehicle_id,
        "field": "engine_code",
        "action": "set",
        "value": "DFGA",
        "supersedes_correction_id": None,
        "group_id": None,
        "reviewer": "Ada",
        "reason": None,
        "previous_value": None,
        "previous_source": "registry",
        "catalog_batch": "batch-1",
        "automatic_terminal": "review_required",
        "automatic_ktype": None,
        "code_version": "test",
        "evidence_fingerprint": FINGERPRINT,
        "evidence": _evidence(),
    }
    values.update(overrides)
    if values["action"] != "set" and "value" not in overrides:
        values["value"] = None
    return NewCorrection(**values)


def _append(db: Connection, new: NewCorrection) -> UUID:
    """Append and keep the vehicle's copy, the way the repository's transaction does."""

    row, created, superseded = append_correction(db, new, new.supersedes_correction_id)
    assert created
    project_correction(db, new.vehicle_id, row, superseded, vehicle_copy)
    db.commit()
    return row.correction_id


def _count(db: Connection) -> int:
    row = db.execute(f"SELECT count(*) FROM {TABLE}").fetchone()
    assert row is not None
    db.commit()
    return int(row[0])


_COLUMNS = tuple(column for column in COLUMNS if column != "created_at")


def _raw_values(vehicle_id: str, **overrides: Any) -> list[Any]:
    """One row's values in `_COLUMNS` order; position 0 unless the test says otherwise."""

    new = _new(vehicle_id)
    values = {column: getattr(new, column, 0) for column in _COLUMNS}
    values.update(overrides)
    values["evidence"] = Jsonb(values["evidence"])
    return [values[column] for column in _COLUMNS]


def _raw_insert(db: Connection, vehicle_id: str, **overrides: Any) -> UUID:
    """INSERT by plain SQL, past every Python check: only the database decides."""

    values = _raw_values(vehicle_id, **overrides)
    db.execute(
        f"INSERT INTO {TABLE} ({', '.join(_COLUMNS)}) "
        f"VALUES ({', '.join(['%s'] * len(_COLUMNS))})",
        values,
    )
    return values[0]  # type: ignore[no-any-return]


def _raw_insert_many(db: Connection, rows: list[list[Any]]) -> None:
    """Several rows in ONE statement, the way a bulk load or an import writes them."""

    row = f"({', '.join(['%s'] * len(_COLUMNS))})"
    db.execute(
        f"INSERT INTO {TABLE} ({', '.join(_COLUMNS)}) VALUES {', '.join([row] * len(rows))}",
        [value for values in rows for value in values],
    )


def _refused(db: Connection, vehicle_id: str, **overrides: Any) -> str | None:
    with pytest.raises(psycopg.errors.IntegrityError) as caught:
        _raw_insert(db, vehicle_id, **overrides)
    db.rollback()
    return caught.value.diag.constraint_name


# ------------------------------------------------------------------ migration and drift


def test_the_migration_is_idempotent_and_its_contract_verifies(db: Connection) -> None:
    names = tuple(statement.name for statement in VEHICLE_FACT_CORRECTION_MIGRATION_STATEMENTS)

    assert run_vehicle_fact_correction_migrations(db) == names
    assert run_vehicle_fact_correction_migrations(db) == names
    verify_vehicle_fact_correction_schema_contract(db)


_FUNCTION = "core.vehicle_fact_corrections_block_mutation"
_ROW_TRIGGER = "vehicle_fact_corrections_append_only"
_LINK = "vehicle_fact_corrections_supersedes_previous_fkey"
_POSITION_KEY = "vehicle_fact_corrections_position_key"
_ROOT_RULE = "vehicle_fact_corrections_root_supersedes_nothing"
_LINK_DEFINITION = (
    "FOREIGN KEY (supersedes_correction_id, vehicle_id, field, supersedes_position) "
    f"REFERENCES {TABLE} (correction_id, vehicle_id, field, chain_position)"
)

_DRIFTS: dict[str, tuple[str, ...]] = {
    "a dropped check": (
        f"ALTER TABLE {TABLE} DROP CONSTRAINT vehicle_fact_corrections_value_matches_action",
    ),
    "a disabled row trigger": (f"ALTER TABLE {TABLE} DISABLE TRIGGER {_ROW_TRIGGER}",),
    "a disabled truncate trigger": (
        f"ALTER TABLE {TABLE} DISABLE TRIGGER vehicle_fact_corrections_append_only_truncate",
    ),
    "the position no longer unique per vehicle and field": (
        f"ALTER TABLE {TABLE} DROP CONSTRAINT {_LINK}",
        f"ALTER TABLE {TABLE} DROP CONSTRAINT {_POSITION_KEY}",
    ),
    "the root rule dropped": (f"ALTER TABLE {TABLE} DROP CONSTRAINT {_ROOT_RULE}",),
    "a column made mandatory": (f"ALTER TABLE {TABLE} ALTER COLUMN reason SET NOT NULL",),
    "a dropped default": (f"ALTER TABLE {TABLE} ALTER COLUMN created_at DROP DEFAULT",),
    "an extra column": (f"ALTER TABLE {TABLE} ADD COLUMN note TEXT",),
    "a dropped link foreign key": (f"ALTER TABLE {TABLE} DROP CONSTRAINT {_LINK}",),
    "a dropped group index": ("DROP INDEX core.vehicle_fact_corrections_group_idx",),
    # Same-named but weaker objects: caught by definition, not by name.
    "a position unique per vehicle only": (
        f"ALTER TABLE {TABLE} DROP CONSTRAINT {_LINK}",
        f"ALTER TABLE {TABLE} DROP CONSTRAINT {_POSITION_KEY}",
        f"ALTER TABLE {TABLE} ADD CONSTRAINT {_POSITION_KEY} UNIQUE (vehicle_id, chain_position)",
    ),
    "a link that does not carry the field": (
        f"ALTER TABLE {TABLE} DROP CONSTRAINT {_LINK}",
        (
            f"ALTER TABLE {TABLE} ADD CONSTRAINT {_LINK} "
            f"FOREIGN KEY (supersedes_correction_id) REFERENCES {TABLE} (correction_id)"
        ),
    ),
    "a deferrable link": (
        f"ALTER TABLE {TABLE} DROP CONSTRAINT {_LINK}",
        (
            f"ALTER TABLE {TABLE} ADD CONSTRAINT {_LINK} {_LINK_DEFINITION} "
            "DEFERRABLE INITIALLY DEFERRED"
        ),
    ),
    "a weaker check under the same name": (
        f"ALTER TABLE {TABLE} DROP CONSTRAINT vehicle_fact_corrections_action_values",
        (
            f"ALTER TABLE {TABLE} ADD CONSTRAINT vehicle_fact_corrections_action_values "
            "CHECK (action <> '')"
        ),
    ),
    "a group index that is not partial": (
        "DROP INDEX core.vehicle_fact_corrections_group_idx",
        f"CREATE INDEX vehicle_fact_corrections_group_idx ON {TABLE} (reviewer)",
    ),
    "a trigger that never fires": (
        (
            f"CREATE OR REPLACE TRIGGER {_ROW_TRIGGER} BEFORE UPDATE OR DELETE ON {TABLE} "
            f"FOR EACH ROW WHEN (false) EXECUTE FUNCTION {_FUNCTION}()"
        ),
    ),
    "a trigger guarding one column only": (
        (
            f"CREATE OR REPLACE TRIGGER {_ROW_TRIGGER} BEFORE UPDATE OF reason OR DELETE "
            f"ON {TABLE} FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}()"
        ),
    ),
    "a trigger function that no longer refuses": (
        (
            f"CREATE OR REPLACE FUNCTION {_FUNCTION}() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN RETURN NEW; END $$"
        ),
    ),
    "an extra trigger": (
        (
            f"CREATE TRIGGER vehicle_fact_corrections_rewrite BEFORE INSERT ON {TABLE} "
            "FOR EACH ROW EXECUTE FUNCTION suppress_redundant_updates_trigger()"
        ),
    ),
    "a rule that swallows deletes": (
        f"CREATE RULE quiet_delete AS ON DELETE TO {TABLE} DO INSTEAD NOTHING",
    ),
    "an unlogged table": (f"ALTER TABLE {TABLE} SET UNLOGGED",),
}


@pytest.mark.parametrize("drift", sorted(_DRIFTS))
def test_schema_drift_is_caught_by_definition(db: Connection, drift: str) -> None:
    for statement in _DRIFTS[drift]:
        db.execute(statement)

    with pytest.raises(VehicleFactCorrectionSchemaContractError):
        verify_vehicle_fact_correction_schema_contract(db)
    db.rollback()

    verify_vehicle_fact_correction_schema_contract(db)


def test_a_migration_rerun_restores_the_triggers_and_their_function(db: Connection) -> None:
    """The deploy step reruns the migration, so a weakened trigger does not survive it."""

    db.execute(_DRIFTS["a trigger that never fires"][0])
    db.execute(_DRIFTS["a trigger function that no longer refuses"][0])
    db.commit()

    run_vehicle_fact_correction_migrations(db)

    _append(db, _new(_vehicle(db)))
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        db.execute(f"DELETE FROM {TABLE}")
    db.rollback()


def test_a_failed_verification_rolls_the_migration_back(db: Connection) -> None:
    db.execute(f"ALTER TABLE {TABLE} ALTER COLUMN reason SET NOT NULL")
    db.commit()
    try:
        with pytest.raises(VehicleFactCorrectionSchemaContractError):
            run_vehicle_fact_correction_migrations(db)
    finally:
        db.execute(f"ALTER TABLE {TABLE} ALTER COLUMN reason DROP NOT NULL")
        db.commit()


# ------------------------------------------------------------------------ immutability


@pytest.mark.parametrize(
    "statement",
    [
        f"UPDATE {TABLE} SET reviewer = 'someone else'",
        f"UPDATE {TABLE} SET value = 'DTSA'",
        f"DELETE FROM {TABLE}",
        f"TRUNCATE {TABLE}",
        "TRUNCATE core.vehicles CASCADE",
        (
            f"INSERT INTO {TABLE} ({', '.join(_COLUMNS)}) "
            f"SELECT {', '.join(_COLUMNS)} FROM {TABLE} "
            "ON CONFLICT (correction_id) DO UPDATE SET reviewer = 'someone else'"
        ),
        (
            f"MERGE INTO {TABLE} AS t USING (SELECT 1) AS s ON true "
            "WHEN MATCHED THEN UPDATE SET reason = 'rewritten'"
        ),
    ],
)
def test_the_database_refuses_to_change_or_remove_a_correction(
    db: Connection, statement: str
) -> None:
    _append(db, _new(_vehicle(db)))

    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        db.execute(statement)
    db.rollback()

    assert _count(db) == 1


# --------------------------------------------------------------------- retry ambiguity


def test_the_same_operation_and_content_twice_is_one_row(db: Connection) -> None:
    new = _new(_vehicle(db))
    first, created, superseded = append_correction(db, new, None)
    db.commit()

    again, created_again, _ = append_correction(db, new, None)
    db.commit()

    assert (created, superseded, first.chain_position) == (True, None, 0)
    assert created_again is False
    assert again == first
    assert _count(db) == 1


def test_different_content_for_a_used_operation_id_is_rejected(db: Connection) -> None:
    vehicle_id = _vehicle(db)
    new = _new(vehicle_id)
    stored, _, _ = append_correction(db, new, None)
    db.commit()

    for changed in (
        {"value": "DTSA"},
        {"reviewer": "Bob"},
        {"reason": "another reason"},
        {"action": "ignore"},
        {"field": "model_family"},
        {"vehicle_id": _other_vehicle(db)},
    ):
        with pytest.raises(OperationReusedError):
            values: dict[str, Any] = {
                "vehicle_id": vehicle_id,
                "correction_id": new.correction_id,
                **changed,
            }
            append_correction(db, _new(**values), None)
        db.rollback()

    assert _count(db) == 1
    assert current_corrections(db, vehicle_id) == {"engine_code": (stored, 1)}
    db.rollback()


def test_the_primary_key_refuses_a_second_row_for_an_operation_id(db: Connection) -> None:
    vehicle_id = _vehicle(db)
    correction_id = _append(db, _new(vehicle_id))

    assert _refused(db, _other_vehicle(db), correction_id=correction_id) == (
        "vehicle_fact_corrections_pkey"
    )


@contextmanager
def _connection(db: Connection) -> Iterator[Connection]:
    with psycopg.connect(make_conninfo(db.info.dsn, password=db.info.password)) as connection:
        yield connection


def _own_connections(db: Connection, **options: Any) -> CorrectionRepository:
    return CorrectionRepository(lambda: _connection(db), **options)


def _race(calls: list[Callable[[], Any]]) -> list[Any]:
    """Run the calls at once; each slot holds the result or the exception raised."""

    results: list[Any] = [None] * len(calls)
    start = threading.Barrier(len(calls))

    def run(index: int) -> None:
        start.wait()
        try:
            results[index] = calls[index]()
        except Exception as error:  # noqa: BLE001 -- the test asserts on it
            results[index] = error

    threads = [threading.Thread(target=run, args=(index,)) for index in range(len(calls))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


def test_two_connections_sending_one_operation_record_it_once(db: Connection) -> None:
    repository = _own_connections(db)
    new = _new(_vehicle(db))

    results = _race([lambda: repository.record(new)] * 4)

    assert not [result for result in results if isinstance(result, Exception)]
    assert sorted(created for _, created in results) == [False, False, False, True]
    assert len({row.correction_id for row, _ in results}) == 1
    assert _count(db) == 1


# ----------------------------------------------------------------------- concurrency


def test_two_people_correcting_one_field_at_once_exactly_one_wins(db: Connection) -> None:
    vehicle_id = _vehicle(db)
    repository = _own_connections(db)
    first, second = _new(vehicle_id, value="DFGA"), _new(vehicle_id, value="DTSA", reviewer="Bob")

    results = _race([lambda: repository.record(first), lambda: repository.record(second)])

    lost = [result for result in results if isinstance(result, Exception)]
    assert len(lost) == 1 and isinstance(lost[0], CorrectionChangedError)
    assert _count(db) == 1
    winner = next(result for result in results if not isinstance(result, Exception))
    row = db.execute("SELECT engine_code FROM core.vehicles WHERE vehicle_id = %s",
                     (vehicle_id,)).fetchone()
    assert row == (winner[0].value,)


def test_two_people_replacing_one_correction_at_once_exactly_one_wins(db: Connection) -> None:
    vehicle_id = _vehicle(db)
    head = _append(db, _new(vehicle_id))
    repository = _own_connections(db)
    calls = [
        lambda: repository.record(_new(vehicle_id, value="DTSA", supersedes_correction_id=head)),
        lambda: repository.record(_new(vehicle_id, action="ignore", supersedes_correction_id=head)),
    ]

    results = _race(calls)

    lost = [result for result in results if isinstance(result, Exception)]
    assert len(lost) == 1 and isinstance(lost[0], CorrectionChangedError)
    assert _count(db) == 2


def test_two_people_correcting_two_fields_of_one_car_at_once_both_succeed(db: Connection) -> None:
    """The lock is the car's, the chain the field's: neither person is refused."""

    vehicle_id = _vehicle(db)
    repository = _own_connections(db)
    calls = [
        lambda: repository.record(_new(vehicle_id, field="engine_code", value="DFGA")),
        lambda: repository.record(_new(vehicle_id, field="power_kw", value="150", reviewer="Bob")),
        lambda: repository.record(_new(vehicle_id, field="fuel", value="diesel,electricity")),
    ]

    results = _race(calls)

    assert not [result for result in results if isinstance(result, Exception)]
    state = load_vehicle(db, vehicle_id)
    db.commit()
    assert state is not None
    assert (state.values["engine_code"], state.values["power_kw"], state.values["fuel"],
            state.values["fuel_secondary"]) == ("DFGA", 150, "diesel", "electricity")
    assert sorted(current_corrections(db, vehicle_id)) == ["engine_code", "fuel", "power_kw"]
    db.rollback()


def test_a_locked_vehicle_answers_busy_and_writes_nothing(db: Connection) -> None:
    vehicle_id = _vehicle(db)
    repository = _own_connections(db, lock_timeout="200ms")
    db.execute("SELECT 1 FROM core.vehicles WHERE vehicle_id = %s FOR UPDATE", (vehicle_id,))
    try:
        with pytest.raises(VehicleBusyError):
            repository.record(_new(vehicle_id))
    finally:
        db.rollback()

    assert _count(db) == 0
    # The same operation succeeds once the lock is gone.
    assert _own_connections(db).record(_new(vehicle_id))[1] is True


def test_a_correction_and_a_ktype_choice_wait_for_the_same_lock(db: Connection) -> None:
    """The two kinds of decision on one car are serialized by the vehicle row."""

    vehicle_id = _vehicle(db)
    db.execute(
        "SELECT 1 FROM core.vehicles WHERE vehicle_id = %s FOR NO KEY UPDATE", (vehicle_id,)
    )
    try:
        with pytest.raises(VehicleBusyError):
            _own_connections(db, lock_timeout="200ms").record(_new(vehicle_id))
    finally:
        db.rollback()
    assert _count(db) == 0


def test_an_unknown_vehicle_is_not_found(db: Connection) -> None:
    with pytest.raises(CorrectionVehicleNotFoundError):
        _own_connections(db).record(_new("NOR-00000000000000000000000000"))
    assert _count(db) == 0


def test_a_value_the_database_cannot_store_is_rejected_for_good(db: Connection) -> None:
    """Not "try again": the same request would fail the same way every time."""

    vehicle_id = _vehicle(db)
    repository = _own_connections(db)
    untouched = _state(db, vehicle_id)

    with pytest.raises(CorrectionRejectedError):
        repository.record(_new(vehicle_id, reason="bad\x00byte"))
    with pytest.raises(CorrectionRejectedError):
        repository.record(_new(vehicle_id, value="x" * 201))  # the value check
    with pytest.raises(CorrectionRejectedError):
        repository.record(_new(vehicle_id, field="Engine Code"))  # the field name check

    assert _count(db) == 0
    assert _state(db, vehicle_id) == untouched


# ------------------------------------------------------------ invalid correction links


def test_the_chain_is_linear_and_stays_on_one_field_of_one_vehicle(db: Connection) -> None:
    vehicle_id, other = _vehicle(db), _other_vehicle(db)
    root = _append(db, _new(vehicle_id))
    second = _append(db, _new(vehicle_id, value="DTSA", supersedes_correction_id=root))

    # Superseding another vehicle's row, or another field's row of the same vehicle.
    assert _refused(db, other, chain_position=2, supersedes_correction_id=second) == _LINK
    assert _refused(db, vehicle_id, field="model_family", chain_position=2,
                    supersedes_correction_id=second) == _LINK
    # A second successor of a row already superseded.
    assert _refused(db, vehicle_id, chain_position=1, supersedes_correction_id=root) == (
        _POSITION_KEY)
    # A link that skips a row: position 2 must supersede position 1, not the root.
    assert _refused(db, vehicle_id, chain_position=2, supersedes_correction_id=root) == _LINK
    # A second root for the field.
    assert _refused(db, vehicle_id) == _POSITION_KEY
    # A row with a predecessor claiming to be a root, and a root claiming a predecessor.
    assert _refused(db, other, supersedes_correction_id=second) == _ROOT_RULE
    assert _refused(db, vehicle_id, chain_position=2) == _ROOT_RULE
    # A row superseding itself, and one superseding a row that does not exist.
    own = uuid4()
    assert _refused(db, other, correction_id=own, chain_position=1,
                    supersedes_correction_id=own) == _LINK
    assert _refused(db, vehicle_id, chain_position=2, supersedes_correction_id=uuid4()) == _LINK
    assert _refused(db, other, chain_position=-1, supersedes_correction_id=uuid4()) == (
        "vehicle_fact_corrections_position_nonnegative"
    )
    assert _count(db) == 2


def test_two_fields_of_one_car_have_independent_chains(db: Connection) -> None:
    vehicle_id = _vehicle(db)
    engine = _append(db, _new(vehicle_id))
    # Another field starts its own chain at its own root, whatever the first holds.
    power = _append(db, _new(vehicle_id, field="power_kw", value="150"))
    engine_2 = _append(db, _new(vehicle_id, value="DTSA", supersedes_correction_id=engine))
    _append(db, _new(vehicle_id, field="power_kw", action="withdraw",
                     supersedes_correction_id=power))

    heads = current_corrections(db, vehicle_id)
    assert list(heads) == ["engine_code", "power_kw"]
    assert (heads["engine_code"][0].correction_id, heads["engine_code"][1]) == (engine_2, 2)
    assert (heads["power_kw"][0].action, heads["power_kw"][1]) == ("withdraw", 2)
    listed = chains(db, vehicle_id)
    assert [row.correction_id for row in listed["engine_code"]] == [engine_2, engine]
    assert [row.chain_position for row in listed["power_kw"]] == [1, 0]
    # Only what is in force stands; another car is not involved.
    assert standing_corrections(db, [vehicle_id, _other_vehicle(db)]) == {
        vehicle_id: {"engine_code": CorrectionHead("set", "DTSA", engine_2)}
    }
    assert set(correction_heads(db, [vehicle_id])[vehicle_id]) == {"engine_code", "power_kw"}
    assert correction_heads(db, []) == {} == standing_corrections(db, [])
    db.rollback()


def test_one_statement_cannot_store_a_cycle_or_a_detached_chain(db: Connection) -> None:
    """What an import or a bulk copy could send: several rows checked together at the end."""

    vehicle_id, other = _vehicle(db), _other_vehicle(db)
    first, second, third = uuid4(), uuid4(), uuid4()

    def refused(rows: list[list[Any]]) -> str | None:
        with pytest.raises(psycopg.errors.IntegrityError) as caught:
            _raw_insert_many(db, rows)
        db.rollback()
        return caught.value.diag.constraint_name

    def row(correction_id: UUID, position: int = 0, supersedes: UUID | None = None,
            *, car: str = vehicle_id, **overrides: Any) -> list[Any]:
        return _raw_values(car, correction_id=correction_id, chain_position=position,
                           supersedes_correction_id=supersedes, **overrides)

    # Two rows superseding each other: no root, no head.
    assert refused([row(first, 1, second), row(second, 2, first)]) == _LINK
    assert refused([row(first, 1, second), row(second, 1, first)]) == _POSITION_KEY
    # A cycle beside a real chain.
    root = _append(db, _new(vehicle_id))
    assert refused([row(first, 5, second), row(second, 4, third), row(third, 3, first)]) == _LINK
    # A detached chain: position 2 with nothing at position 1.
    assert refused([row(first, 2, root)]) == _LINK
    assert refused([row(first, 3, second), row(second, 2, root)]) == _LINK
    # A second root, and a second successor of the root.
    assert refused([row(first), row(second, 1, first)]) == _POSITION_KEY
    assert refused([row(first, 1, root), row(second, 1, root)]) == _POSITION_KEY
    # A chain whose links cross into another vehicle ...
    assert refused([row(first, car=other), row(second, 1, first)]) == _LINK
    # ... or into another field of the same vehicle.
    assert refused([
        row(first, field="power_kw", value="150"),
        row(second, 1, first, field="displacement_cc", value="1969"),
    ]) == _LINK
    assert refused([row(first, 1, root, field="power_kw", value="150")]) == _LINK
    assert _count(db) == 1

    # A whole valid chain in one statement passes in any row order (newest first here),
    # and so does a second field's chain beside it.
    _raw_insert_many(db, [
        row(third, 2, second, car=other, action="withdraw", value=None),
        row(second, 1, first, car=other, value="DTSA"),
        row(first, car=other),
        row(uuid4(), car=other, field="power_kw", value="150"),
    ])
    db.commit()
    assert [item.correction_id for item in chains(db, other)["engine_code"]] == [
        third, second, first]
    head, history_count = current_corrections(db, other)["engine_code"]
    assert (head.correction_id, history_count) == (third, 3)
    assert set(standing_corrections(db, [other])[other]) == {"power_kw"}
    db.rollback()


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"action": "withdraw", "value": None}, "vehicle_fact_corrections_withdraw_supersedes"),
        ({"chain_position": 1}, _ROOT_RULE),
        ({"action": "set", "value": None}, "vehicle_fact_corrections_value_matches_action"),
        ({"action": "set", "value": " "}, "vehicle_fact_corrections_value_matches_action"),
        ({"action": "set", "value": "x" * 201}, "vehicle_fact_corrections_value_matches_action"),
        ({"action": "ignore", "value": "DFGA"}, "vehicle_fact_corrections_value_matches_action"),
        ({"action": "replace"}, "vehicle_fact_corrections_action_values"),
        ({"field": "Engine_Code"}, "vehicle_fact_corrections_field_format"),
        ({"field": "engine code"}, "vehicle_fact_corrections_field_format"),
        ({"field": ""}, "vehicle_fact_corrections_field_format"),
        ({"reviewer": "  "}, "vehicle_fact_corrections_reviewer_nonempty"),
        ({"reviewer": "x" * 121}, "vehicle_fact_corrections_reviewer_nonempty"),
        ({"reason": ""}, "vehicle_fact_corrections_reason_nonempty"),
        ({"reason": "x" * 1001}, "vehicle_fact_corrections_reason_nonempty"),
        ({"catalog_batch": ""}, "vehicle_fact_corrections_provenance_nonempty"),
        ({"code_version": " "}, "vehicle_fact_corrections_provenance_nonempty"),
        ({"evidence_fingerprint": "ABC"}, "vehicle_fact_corrections_fingerprint_format"),
        ({"evidence": {"automatic": {}}}, "vehicle_fact_corrections_evidence_shape"),
        ({"evidence": {"schema": "x", "automatic": []}}, "vehicle_fact_corrections_evidence_shape"),
        ({"evidence": ["schema"]}, "vehicle_fact_corrections_evidence_shape"),
    ],
)
def test_a_malformed_row_is_refused_by_a_named_constraint(
    db: Connection, overrides: dict[str, Any], constraint: str
) -> None:
    assert _refused(db, _vehicle(db), **overrides) == constraint
    assert _count(db) == 0


def test_the_table_takes_any_field_name_and_a_reserved_group_id(db: Connection) -> None:
    """Which fields can be corrected is the service's rule; a later phase adds without a migration."""

    vehicle_id, group = _vehicle(db), uuid4()

    _raw_insert(db, vehicle_id, field="some_later_field", value="x", group_id=group)
    db.commit()

    head, _ = current_corrections(db, vehicle_id)["some_later_field"]
    assert (head.group_id, head.previous_value, head.previous_source) == (group, None, "registry")
    db.rollback()


def test_a_correction_for_a_vehicle_that_does_not_exist_is_refused(db: Connection) -> None:
    assert _refused(db, "NOR-00000000000000000000000000") == "vehicle_fact_corrections_vehicle_fkey"


def test_the_writer_refuses_a_stale_head_and_an_empty_withdrawal(db: Connection) -> None:
    vehicle_id = _vehicle(db)
    with pytest.raises(NothingToWithdrawError):
        append_correction(db, _new(vehicle_id, action="withdraw"), None)
    root = _append(db, _new(vehicle_id))

    with pytest.raises(CorrectionChangedError):
        append_correction(db, _new(vehicle_id, value="DTSA"), None)
    with pytest.raises(CorrectionChangedError):
        append_correction(db, _new(vehicle_id, value="DTSA", supersedes_correction_id=uuid4()),
                          uuid4())
    with pytest.raises(CorrectionChangedError):
        append_correction(db, _new(vehicle_id, value="DTSA", supersedes_correction_id=None), root)
    # Another field of the car has no correction to withdraw, whatever this one holds.
    with pytest.raises(NothingToWithdrawError):
        append_correction(db, _new(vehicle_id, field="power_kw", action="withdraw"), None)
    withdrawn = _append(db, _new(vehicle_id, action="withdraw", supersedes_correction_id=root))
    with pytest.raises(NothingToWithdrawError):
        append_correction(
            db, _new(vehicle_id, action="withdraw", supersedes_correction_id=withdrawn), withdrawn
        )
    db.rollback()

    assert [row.correction_id for row in chains(db, vehicle_id)["engine_code"]] == [withdrawn, root]
    db.rollback()


def test_the_heads_are_read_by_the_position_key_never_by_a_scan(db: Connection) -> None:
    vehicle_id = _vehicle(db)
    _append(db, _new(vehicle_id))
    queries = {
        "the matcher's read": (
            (
                "SELECT DISTINCT ON (c.vehicle_id, c.field) c.vehicle_id, c.field, c.action "
                f"FROM {TABLE} AS c WHERE c.vehicle_id = ANY(%s) "
                "ORDER BY c.vehicle_id DESC, c.field DESC, c.chain_position DESC"
            ),
            ([vehicle_id],),
        ),
        "the lookup's detail read": (
            (
                f"SELECT DISTINCT ON (c.field) c.* FROM {TABLE} AS c WHERE c.vehicle_id = %s "
                "ORDER BY c.field DESC, c.chain_position DESC"
            ),
            (vehicle_id,),
        ),
    }
    for name, (query, parameters) in queries.items():
        with db.cursor() as cursor:
            cursor.execute("SET LOCAL enable_seqscan = off")
            cursor.execute("SET LOCAL enable_sort = off")
            cursor.execute(f"EXPLAIN {query}", parameters)
            plan = "\n".join(str(row[0]) for row in cursor.fetchall())
        db.rollback()

        assert _POSITION_KEY in plan and "Seq Scan" not in plan, name
        assert "Sort" not in plan, name


# ---------------------------------------------------------------- the copy on vehicles

_COPIED = ("engine_code", "power_kw", "production_year", "production_month", "fuel",
           "fuel_secondary", "fuel_match_tokens", "electrification_type", "bodywork_form")


def _state(db: Connection, vehicle_id: str) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    """The vehicle's values, sources and alternatives as stored."""

    state = load_vehicle(db, vehicle_id)
    db.commit()
    assert state is not None
    return dict(state.values), dict(state.field_sources), dict(state.field_alternatives)


def _updated_at(db: Connection, vehicle_id: str) -> Any:
    row = db.execute("SELECT updated_at FROM core.vehicles WHERE vehicle_id = %s",
                     (vehicle_id,)).fetchone()
    db.commit()
    assert row is not None
    return row[0]


def test_a_set_is_copied_onto_the_vehicle_under_its_own_source(db: Connection) -> None:
    vehicle_id, other = _vehicle(db), _other_vehicle(db)
    pristine, other_before = _state(db, vehicle_id), _state(db, other)
    assert (pristine[0]["engine_code"], pristine[0]["power_kw"]) == (None, 133)

    filled = _append(db, _new(vehicle_id))
    replaced = _append(db, _new(vehicle_id, field="power_kw", value="150"))

    values, sources, alternatives = _state(db, vehicle_id)
    assert (values["engine_code"], values["power_kw"]) == ("DFGA", 150)
    assert sources == {
        "engine_code": f"correction:{filled}", "power_kw": f"correction:{replaced}"}
    # What a set displaces is kept beside it; a filled gap displaced nothing.
    assert list(alternatives) == ["power_kw"]
    assert alternatives["power_kw"][0]["value"] == 133
    assert alternatives["power_kw"][0]["source"].startswith("transportstyrelsen")
    untouched = {name: value for name, value in values.items()
                 if name not in ("engine_code", "power_kw")}
    assert untouched == {name: value for name, value in pristine[0].items()
                         if name not in ("engine_code", "power_kw")}
    assert _state(db, other) == other_before


def test_a_set_on_a_set_replaces_the_copy_and_keeps_what_the_first_displaced(
    db: Connection,
) -> None:
    vehicle_id = _vehicle(db)
    first = _append(db, _new(vehicle_id, field="power_kw", value="150"))
    second = _append(
        db, _new(vehicle_id, field="power_kw", value="140", supersedes_correction_id=first)
    )

    values, sources, alternatives = _state(db, vehicle_id)

    assert (values["power_kw"], sources["power_kw"]) == (140, f"correction:{second}")
    assert [entry["value"] for entry in alternatives["power_kw"]] == [133]


def test_a_withdrawal_restores_the_previous_value_and_its_source(db: Connection) -> None:
    vehicle_id = _vehicle(db)
    pristine = _state(db, vehicle_id)

    first = _append(db, _new(vehicle_id, field="power_kw", value="150"))
    second = _append(
        db, _new(vehicle_id, field="power_kw", value="140", supersedes_correction_id=first)
    )
    filled = _append(db, _new(vehicle_id))
    _append(db, _new(vehicle_id, field="power_kw", action="withdraw",
                     supersedes_correction_id=second))
    _append(db, _new(vehicle_id, action="withdraw", supersedes_correction_id=filled))

    # Values, sources and alternatives: exactly the vehicle as it was.
    assert _state(db, vehicle_id) == pristine


def test_an_ignore_leaves_the_vehicle_untouched(db: Connection) -> None:
    """The source data stays visible; the matcher just is not handed it."""

    vehicle_id = _vehicle(db)
    pristine, written = _state(db, vehicle_id), _updated_at(db, vehicle_id)

    ignored = _append(db, _new(vehicle_id, field="power_kw", action="ignore"))
    assert (_state(db, vehicle_id), _updated_at(db, vehicle_id)) == (pristine, written)

    _append(db, _new(vehicle_id, field="power_kw", action="withdraw",
                     supersedes_correction_id=ignored))
    assert (_state(db, vehicle_id), _updated_at(db, vehicle_id)) == (pristine, written)


def test_an_ignore_after_a_set_takes_the_set_back_off_the_vehicle(db: Connection) -> None:
    vehicle_id = _vehicle(db)
    pristine = _state(db, vehicle_id)
    corrected = _append(db, _new(vehicle_id, field="power_kw", value="150"))

    _append(db, _new(vehicle_id, field="power_kw", action="ignore",
                     supersedes_correction_id=corrected))

    assert _state(db, vehicle_id) == pristine
    assert standing_corrections(db, [vehicle_id])[vehicle_id]["power_kw"].action == "ignore"
    db.rollback()


@pytest.mark.parametrize(
    ("field", "value", "copied"),
    [
        ("production_month", "11", {"production_month": 11}),
        ("electrification_type", "hybrid", {"electrification_type": "hybrid"}),
        ("fuel", "diesel",
         {"fuel": "diesel", "fuel_secondary": None, "fuel_match_tokens": ["diesel"]}),
        ("fuel", "diesel,electricity",
         {"fuel": "diesel", "fuel_secondary": "electricity",
          "fuel_match_tokens": ["diesel", "electricity", "hybrid_diesel"]}),
        ("fuel", "petrol,cng,e85",
         {"fuel": "petrol", "fuel_secondary": "cng", "fuel_match_tokens": ["petrol", "cng", "e85"]}),
    ],
)
def test_the_month_the_fuel_and_the_electrification_are_copied_and_restored(
    db: Connection, field: str, value: str, copied: dict[str, Any]
) -> None:
    """On the plug-in hybrid: petrol and electricity, built in month 2."""

    vehicle_id = _vehicle(db, HYBRID)
    pristine = _state(db, vehicle_id)
    assert {name: pristine[0][name] for name in _COPIED[3:8]} == {
        "production_month": 2, "fuel": "petrol", "fuel_secondary": "electricity",
        "fuel_match_tokens": ["petrol", "electricity", "hybrid_petrol"],
        "electrification_type": "plug_in_hybrid",
    }

    corrected = _append(db, _new(vehicle_id, field=field, value=value))

    values, sources, _ = _state(db, vehicle_id)
    assert {name: values[name] for name in copied} == copied
    # Every column the correction changed carries the same reference. An emptied
    # one carries none, and a value the car had already is confirmed, not re-sourced.
    assert sources == {
        name: f"correction:{corrected}"
        for name, stored in copied.items()
        if stored is not None and stored != pristine[0][name]
    }
    assert {name: values[name] for name in _COPIED if name not in copied} == {
        name: pristine[0][name] for name in _COPIED if name not in copied
    }

    _append(db, _new(vehicle_id, field=field, action="withdraw",
                     supersedes_correction_id=corrected))
    assert _state(db, vehicle_id) == pristine


def test_a_fuel_set_on_a_single_fuel_car_adds_and_removes_the_second_fuel(
    db: Connection,
) -> None:
    vehicle_id = _vehicle(db)  # diesel only
    pristine = _state(db, vehicle_id)
    assert (pristine[0]["fuel"], pristine[0]["fuel_secondary"]) == ("diesel", None)

    hybrid = _append(db, _new(vehicle_id, field="fuel", value="diesel,electricity"))
    values, sources, _ = _state(db, vehicle_id)
    assert (values["fuel"], values["fuel_secondary"], values["fuel_match_tokens"]) == (
        "diesel", "electricity", ["diesel", "electricity", "hybrid_diesel"])
    # The first carrier is the car's own already: confirmed, not re-sourced.
    assert sources == {
        "fuel_secondary": f"correction:{hybrid}", "fuel_match_tokens": f"correction:{hybrid}"}

    # Then a single other fuel: the second one is emptied again.
    petrol = _append(db, _new(vehicle_id, field="fuel", value="petrol",
                              supersedes_correction_id=hybrid))
    values, _, _ = _state(db, vehicle_id)
    assert (values["fuel"], values["fuel_secondary"], values["fuel_match_tokens"]) == (
        "petrol", None, ["petrol"])

    _append(db, _new(vehicle_id, field="fuel", action="ignore", supersedes_correction_id=petrol))
    assert _state(db, vehicle_id) == pristine


def test_a_later_import_can_refill_a_second_fuel_a_correction_emptied(db: Connection) -> None:
    """The table stays the truth for matching; the copy is the merge's, with its limits."""

    from ingestion.vehicle_core_fields import SOURCE_AIS, SourceRef
    from ingestion.vehicle_core_merge import Observation, merge

    vehicle_id = _vehicle(db, HYBRID)
    corrected = _append(db, _new(vehicle_id, field="fuel", value="diesel"))
    state = load_vehicle(db, vehicle_id)
    assert state is not None and state.values["fuel_secondary"] is None

    # An import states a second fuel again: the emptied column carries no source to outrank it.
    merge(state, {"fuel_secondary": Observation("electricity", SourceRef(SOURCE_AIS, "extract-9"))})
    save_vehicles(db, [state])
    db.commit()

    values, _, _ = _state(db, vehicle_id)
    assert (values["fuel"], values["fuel_secondary"]) == ("diesel", "electricity")
    # The matcher is handed the correction all the same.
    (car,) = VehicleMatchingRepository(lambda: nullcontext(db)).vehicle_car_records([vehicle_id])
    db.rollback()
    normalized: dict[str, Any] = car.record.payload["normalized"]  # type: ignore[assignment]
    assert (normalized["energy_sources"], normalized["fuel_match_tokens"]) == (
        ["diesel"], ["diesel"])
    assert standing_corrections(db, [vehicle_id])[vehicle_id]["fuel"].correction_id == corrected
    db.rollback()


def _rule(db: Connection, target_field: str, target_value: str, *, model: str = "V70") -> tuple:
    build_id, rule_id = uuid4(), uuid4()
    with db.cursor() as cursor:
        cursor.execute(
            "INSERT INTO core.match_chunk_builds (build_id, source_batch_id, signature_version, "
            "status_filter, status, finished_at) VALUES (%s, 'test', 'v1', ARRAY['provisional'], "
            "'completed', now())",
            (build_id,),
        )
        cursor.execute(
            "INSERT INTO core.match_resolution_rules (rule_id, build_id, source_field, source_value, "
            "target_field, target_value, conditions, author, matched_rows, would_resolve, "
            "already_resolved, override) VALUES (%s, %s, 'model', %s, %s, %s, %s, 'test', 1, 1, 0, "
            "true)",
            (rule_id, build_id, model, target_field, target_value,
             Jsonb([{"field": "model", "values": [model]}])),
        )
    db.commit()
    return rule_id, build_id


def _apply(db: Connection, rule: tuple, target_field: str, target_value: str,
           *, model: str = "V70") -> None:
    apply_rule(
        db, rule_id=rule[0], build_id=rule[1],
        predicate=compile_predicate([("source", "model", "equals", (model,))]),
        target_field=target_field, target_value=target_value, override=True,
        on_batch=sync_applied_review,
    )


def _matcher_reads(db: Connection, vehicle_id: str, field: str) -> Any:
    """What the matcher is handed for a field: the lookup's input, read through the real seam."""

    (car,) = VehicleMatchingRepository(lambda: nullcontext(db)).vehicle_car_records([vehicle_id])
    db.rollback()
    normalized: dict[str, Any] = car.record.payload["normalized"]  # type: ignore[assignment]
    return normalized.get(field), car.overlaid.get(field)


def test_undoing_a_correction_of_a_rules_value_restores_the_rules_value(db: Connection) -> None:
    """Not the registry's: the car must match after an undo as it did before the correction."""

    vehicle_id = _vehicle(db, GOLF)
    registry = _state(db, vehicle_id)[0]["bodywork_form"]
    rule = _rule(db, "bodywork_form", "suv", model="GOLF")
    _apply(db, rule, "bodywork_form", "suv", model="GOLF")
    try:
        with_rule = _state(db, vehicle_id)
        assert registry == "estate"
        assert (with_rule[0]["bodywork_form"], with_rule[1]["bodywork_form"]) == (
            "suv", f"review:{rule[0]}")
        assert _matcher_reads(db, vehicle_id, "bodywork_form") == ("suv", "review")

        corrected = _append(db, _new(vehicle_id, field="bodywork_form", value="hatchback"))
        values, sources, alternatives = _state(db, vehicle_id)
        assert (values["bodywork_form"], sources["bodywork_form"]) == (
            "hatchback", f"correction:{corrected}")
        # Both what the rule said and what the registry said wait behind the correction.
        assert {entry["source"].split("@")[0].split(":")[0]: entry["value"]
                for entry in alternatives["bodywork_form"]} == {
            "review": "suv", "transportstyrelsen": registry}
        assert _matcher_reads(db, vehicle_id, "bodywork_form") == ("hatchback", "correction")

        _append(db, _new(vehicle_id, field="bodywork_form", action="withdraw",
                         supersedes_correction_id=corrected))
        assert _state(db, vehicle_id) == with_rule
        assert _matcher_reads(db, vehicle_id, "bodywork_form") == ("suv", "review")
    finally:
        retire_rule(db, rule_id=rule[0], target_field="bodywork_form",
                    on_retire=sync_retired_review)
        db.commit()


def test_a_rule_applied_while_a_correction_stands_waits_behind_it(db: Connection) -> None:
    """A many-car rule never overwrites what a person said about one car."""

    vehicle_id = _vehicle(db, GOLF)
    registry = _state(db, vehicle_id)[0]["bodywork_form"]
    corrected = _append(db, _new(vehicle_id, field="bodywork_form", value="hatchback"))
    rule = _rule(db, "bodywork_form", "suv", model="GOLF")

    _apply(db, rule, "bodywork_form", "suv", model="GOLF")

    try:
        values, sources, alternatives = _state(db, vehicle_id)
        assert (values["bodywork_form"], sources["bodywork_form"]) == (
            "hatchback", f"correction:{corrected}")
        assert {"value": "suv", "source": f"review:{rule[0]}"} in alternatives["bodywork_form"]
        assert _matcher_reads(db, vehicle_id, "bodywork_form") == ("hatchback", "correction")
        # Applying the rule again changes nothing and loses nothing.
        _apply(db, rule, "bodywork_form", "suv", model="GOLF")
        assert _state(db, vehicle_id) == (values, sources, alternatives)

        # The undo then shows the rule's value, with the registry's behind it.
        _append(db, _new(vehicle_id, field="bodywork_form", action="withdraw",
                         supersedes_correction_id=corrected))
        values, sources, alternatives = _state(db, vehicle_id)
        assert (values["bodywork_form"], sources["bodywork_form"]) == (
            "suv", f"review:{rule[0]}")
        assert [entry["value"] for entry in alternatives["bodywork_form"]] == [registry]
        assert _matcher_reads(db, vehicle_id, "bodywork_form") == ("suv", "review")
    finally:
        retire_rule(db, rule_id=rule[0], target_field="bodywork_form",
                    on_retire=sync_retired_review)
        db.commit()
    assert _state(db, vehicle_id)[0]["bodywork_form"] == registry


def test_a_rule_retired_while_a_correction_stands_is_not_what_an_undo_restores(
    db: Connection,
) -> None:
    vehicle_id = _vehicle(db, GOLF)
    pristine = _state(db, vehicle_id)
    rule = _rule(db, "bodywork_form", "suv", model="GOLF")
    _apply(db, rule, "bodywork_form", "suv", model="GOLF")
    corrected = _append(db, _new(vehicle_id, field="bodywork_form", value="hatchback"))

    retire_rule(db, rule_id=rule[0], target_field="bodywork_form", on_retire=sync_retired_review)
    db.commit()
    values, sources, alternatives = _state(db, vehicle_id)
    # The person's value stays; the retired rule's is gone from behind it.
    assert (values["bodywork_form"], sources["bodywork_form"]) == (
        "hatchback", f"correction:{corrected}")
    assert [entry["value"] for entry in alternatives["bodywork_form"]] == [
        pristine[0]["bodywork_form"]]

    _append(db, _new(vehicle_id, field="bodywork_form", action="withdraw",
                     supersedes_correction_id=corrected))
    assert _state(db, vehicle_id) == pristine


def test_a_provider_reimport_leaves_a_standing_correction_on_top(db: Connection) -> None:
    """The TS backfill run again, and an AIS observation, while a person's value stands."""

    from ingestion.vehicle_core_fields import SOURCE_AIS, SourceRef
    from ingestion.vehicle_core_merge import Observation, merge

    vehicle_id = _vehicle(db)
    pristine = _state(db, vehicle_id)
    power = _append(db, _new(vehicle_id, field="power_kw", value="150"))
    engine = _append(db, _new(vehicle_id))

    backfill_vehicle_core(db, min_free_bytes=None)
    db.commit()
    state = load_vehicle(db, vehicle_id)
    assert state is not None
    observed = SourceRef(SOURCE_AIS, "extract-9")
    merge(state, {"power_kw": Observation(140, observed),
                  "engine_code": Observation("D4204T14", observed)})
    save_vehicles(db, [state])
    db.commit()

    values, sources, alternatives = _state(db, vehicle_id)
    assert (values["power_kw"], values["engine_code"]) == (150, "DFGA")
    assert (sources["power_kw"], sources["engine_code"]) == (
        f"correction:{power}", f"correction:{engine}")
    assert _matcher_reads(db, vehicle_id, "power_kw") == (150, "correction")
    assert _matcher_reads(db, vehicle_id, "engine_code") == ("DFGA", "correction")
    # What the providers said waits behind; the undo falls back to the best of it.
    assert sorted(entry["value"] for entry in alternatives["power_kw"]) == [133, 140]
    _append(db, _new(vehicle_id, action="withdraw", supersedes_correction_id=engine))
    _append(db, _new(vehicle_id, field="power_kw", action="withdraw",
                     supersedes_correction_id=power))
    values, sources, _ = _state(db, vehicle_id)
    assert values["engine_code"] == "D4204T14" and sources["engine_code"].startswith("ais:")
    # Power is a "newest wins" field: the dated registry record outranks an undated extract.
    assert values["power_kw"] == pristine[0]["power_kw"] == 133
    assert "power_kw" not in sources


def test_the_copy_is_put_back_after_another_writer_overwrote_it(db: Connection) -> None:
    """A job holding an older state writes it back; the repair recomputes from the table."""

    vehicle_id, other = _vehicle(db), _vehicle(db, HYBRID)
    stale = load_vehicle(db, vehicle_id)
    assert stale is not None
    db.commit()
    _append(db, _new(vehicle_id))
    _append(db, _new(vehicle_id, field="power_kw", value="150"))
    _append(db, _new(vehicle_id, field="bodywork_form", action="ignore"))
    _append(db, _new(other, field="fuel", value="diesel"))
    right, other_right = _state(db, vehicle_id), _state(db, other)

    stale.values["colour"] = "RÖD"
    save_vehicles(db, [stale])
    db.execute("UPDATE core.vehicles SET fuel = 'petrol', fuel_match_tokens = NULL, "
               "field_sources = '{}'::jsonb WHERE vehicle_id = %s", (other,))
    db.commit()
    lost = _state(db, vehicle_id)
    assert (lost[0]["engine_code"], lost[0]["power_kw"], lost[1]) == (None, 133, {})
    # The matcher never noticed: it reads the table.
    (car,) = VehicleMatchingRepository(lambda: nullcontext(db)).vehicle_car_records([vehicle_id])
    db.rollback()
    normalized: dict[str, Any] = car.record.payload["normalized"]  # type: ignore[assignment]
    assert (normalized["engine_code"], normalized["power_kw"]) == ("DFGA", 150)
    assert "bodywork_form" not in normalized

    assert reproject_corrections(db, [vehicle_id], vehicle_copy) == 1
    db.commit()
    values, sources, alternatives = _state(db, vehicle_id)
    assert (values["engine_code"], values["power_kw"], values["colour"]) == ("DFGA", 150, "RÖD")
    assert (sources, alternatives) == (right[1], right[2])

    # Without ids: every corrected vehicle. Already right: nothing is written.
    assert reproject_corrections(db, None, vehicle_copy) == 1
    db.commit()
    assert _state(db, other)[0]["fuel_match_tokens"] == other_right[0]["fuel_match_tokens"]
    assert reproject_corrections(db, None, vehicle_copy) == 0
    assert reproject_corrections(db, [], vehicle_copy) == 0
    db.rollback()


def _waits_for_a_lock(db: Connection) -> bool:
    row = db.execute("SELECT count(*) FROM pg_locks WHERE NOT granted").fetchone()
    db.commit()
    return bool(row and row[0])


def test_the_repair_never_writes_back_a_state_older_than_another_writers(
    db: Connection,
) -> None:
    """It locks the vehicle row before it reads, as a correction does."""

    vehicle_id = _vehicle(db)
    _append(db, _new(vehicle_id))
    db.execute("UPDATE core.vehicles SET engine_code = NULL, field_sources = '{}'::jsonb "
               "WHERE vehicle_id = %s", (vehicle_id,))
    db.commit()
    done: list[Any] = []

    def repair() -> None:
        with _connection(db) as connection:
            done.append(reproject_corrections(connection, [vehicle_id], vehicle_copy))
            connection.commit()

    with _connection(db) as writer:
        writer.execute(
            "SELECT 1 FROM core.vehicles WHERE vehicle_id = %s FOR NO KEY UPDATE", (vehicle_id,)
        )
        thread = threading.Thread(target=repair)
        thread.start()
        deadline = time.monotonic() + 10
        while not _waits_for_a_lock(db) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert _waits_for_a_lock(db), "the repair should be waiting for the writer's row"
        assert done == []
        # What the other writer changes while the repair waits must survive it.
        writer.execute("UPDATE core.vehicles SET colour = 'BLÅ' WHERE vehicle_id = %s",
                       (vehicle_id,))
        writer.commit()
        thread.join(timeout=30)

    assert done == [1]
    values, sources, _ = _state(db, vehicle_id)
    assert (values["colour"], values["engine_code"]) == ("BLÅ", "DFGA")
    assert sources["engine_code"].startswith("correction:")


# ------------------------------------------------------------- the services, end to end


def _ktype(reference: str, **overrides: Any) -> VehicleCandidate:
    values: dict[str, Any] = {
        "candidate_type": "TecDocKType", "year_from": 2007, "year_to": 2016,
        "fuels": frozenset({"diesel"}), "displacement_cc": 1969, "power_kw": 133,
        "bodyworks": frozenset({"estate"}),
    }
    values.update(overrides)
    return VehicleCandidate(reference, "VOLVO", "V70", **values)


# A made-up catalog: two KTypes only an engine code tells apart, and a stronger one.
CATALOG = (
    _ktype("K1", engine_codes=frozenset({"D4204T14"})),
    _ktype("K2", engine_codes=frozenset({"D4204T23"})),
    _ktype("K3", engine_codes=frozenset({"D5244T4"}), displacement_cc=2400, power_kw=158),
)


class _CountingCorrections(CorrectionRepository):
    """The real repository, counting the lookup's detail reads."""

    reads = 0

    def current(self, vehicle_id: str) -> Any:
        self.reads += 1
        return super().current(vehicle_id)


class _Api:
    def __init__(self, db: Connection) -> None:
        def factory() -> AbstractContextManager[Connection]:
            return nullcontext(db)

        self.evaluator = TecDocDryRunEvaluator(CATALOG)
        self.catalog = {candidate.candidate_reference: candidate for candidate in CATALOG}
        self.corrections = _CountingCorrections(factory)
        self.choices = KTypeChoiceRepository(factory)
        self.repository = VehicleMatchingRepository(factory)
        self.matching = VehicleMatchingService(
            self.repository, self._matcher, SummaryJobs(),
            choices=self.choices, corrections=self.corrections,
        )
        self.service = CorrectionService(self.corrections, self.matching.lookup_vehicle, "abc1234")
        self.choice_service = KTypeChoiceService(
            self.choices, self.matching.lookup_vehicle, "abc1234"
        )
        self.vehicles = VehicleService(VehicleRepository(factory))

    def _matcher(self) -> Matcher:
        return Matcher("batch-1", self.evaluator, self.catalog)

    def send(self, vehicle_id: str, action: str = "set", field: str = "engine_code",
             **body: Any) -> CorrectionRequest:
        """A request built from the lookup, the way the screen builds it."""

        lookup = self.matching.lookup_vehicle(vehicle_id)
        head = next((item for item in lookup.corrections if item.field == field), None)
        values: dict[str, Any] = {
            "operation_id": uuid4(),
            "field": field,
            "action": action,
            "value": "D4204T23" if action == "set" else None,
            "reviewer": "Ada",
            "supersedes_correction_id": head.correction_id if head else None,
            "evidence_fingerprint": lookup.evidence_fingerprint,
        }
        values.update(body)
        return CorrectionRequest(**values)

    def correct(self, vehicle_id: str, action: str = "set", field: str = "engine_code",
                **body: Any) -> VehicleMatchLookup:
        answer, created = self.service.record(vehicle_id, self.send(vehicle_id, action, field, **body))
        assert created
        return answer


@pytest.fixture
def api(db: Connection) -> _Api:
    return _Api(db)


def _shown(lookup: VehicleMatchLookup, field: str) -> Any:
    return next(item for item in lookup.correctable_fields if item.field == field)


def test_a_correction_reaches_the_matcher_at_once_and_is_stored_with_its_evidence(
    api: _Api, db: Connection
) -> None:
    vehicle_id = _vehicle(db)
    before = api.matching.lookup_vehicle(vehicle_id)
    # Two KTypes fit and only the engine code tells them apart; the car has none.
    assert (before.terminal, before.bucket, before.top_ktype) == ("review_required", "several", "K1")
    assert before.missing_separating_fields == ["engine_code"]
    assert (before.corrections, api.corrections.reads) == ([], 0)
    assert [item.field for item in before.correctable_fields] == list(SPECS)
    engine = _shown(before, "engine_code")
    assert (engine.current_value, engine.current_source) == (None, "registry")
    assert engine.suggestions == ["D4204T14", "D4204T23", "D5244T4"]

    answer = api.correct(vehicle_id, value=" D4204T23 ", reason=" seen on the engine ")

    # The answer is the car matched again, with every guard on: now one KType fits.
    assert (answer.terminal, answer.bucket, answer.top_ktype) == ("resolved", "one", "K2")
    assert answer.inputs is not None and answer.inputs.engine_code == "D4204T23"
    assert answer.overlaid_fields["engine_code"] == "correction"
    assert answer.evidence_fingerprint != before.evidence_fingerprint
    assert answer == api.matching.lookup_vehicle(vehicle_id)
    (state,) = answer.corrections
    assert (state.field, state.status, state.value, state.history_count) == (
        "engine_code", "set", "D4204T23", 1)
    assert (state.reviewer, state.reason) == ("Ada", "seen on the engine")
    assert (state.previous_value, state.previous_source, state.group_id) == (None, "registry", None)
    engine = _shown(answer, "engine_code")
    assert (engine.current_value, engine.current_source) == ("D4204T23", "correction")
    assert (answer.effective_ktype, answer.effective_source) == ("K2", "matcher")

    (stored,) = chains(db, vehicle_id)["engine_code"]
    assert (stored.catalog_batch, stored.code_version) == ("batch-1", "abc1234")
    # The evidence is the evaluation the person saw, not the one the correction produced.
    assert (stored.automatic_terminal, stored.automatic_ktype) == ("review_required", "K1")
    assert stored.evidence_fingerprint == before.evidence_fingerprint
    evidence = stored.evidence
    assert set(evidence) == {"schema", "automatic", "inputs", "overlaid_fields"}
    assert evidence["automatic"]["terminal"] == "review_required"
    assert (evidence["inputs"]["engine_code"], evidence["inputs"]["power_kw"]) == (None, 133)
    text = repr(evidence)
    assert vehicle_id not in text and "ABC123" not in text and VOLVO not in text
    db.rollback()

    # The list and the record show it, from the copy: a value a person asserted.
    row = api.vehicles.search([], "ABC123", cursor=None, limit=1).items[0]
    assert (row.engine_code, row.review_fields, row.rule_fields) == (
        "D4204T23", ["engine_code"], [])
    field = next(
        item for item in api.vehicles.record(vehicle_id).fields if item.field == "engine_code"
    )
    assert field.source is not None
    assert (field.source.source, field.source.ref) == ("correction", str(state.correction_id))
    db.rollback()


def test_an_ignored_value_is_not_handed_to_the_matcher_and_a_withdrawal_brings_it_back(
    api: _Api, db: Connection
) -> None:
    vehicle_id = _vehicle(db)
    before = api.matching.lookup_vehicle(vehicle_id)

    ignored = api.correct(vehicle_id, "ignore", "power_kw", reason="a typo in the register")

    assert ignored.inputs is not None and ignored.inputs.power_kw is None
    assert ignored.overlaid_fields["power_kw"] == "correction"
    power = _shown(ignored, "power_kw")
    assert (power.current_value, power.current_source) == (None, "correction")
    assert (ignored.corrections[0].status, ignored.corrections[0].previous_value) == (
        "ignored", "133")
    # The stronger KType no longer conflicts on a power the matcher is not handed.
    def conflicts(lookup: VehicleMatchLookup) -> list[str]:
        (stronger,) = [item for item in lookup.candidates if item.ktype == "K3"]
        return stronger.conflicting_fields

    assert conflicts(before) == ["displacement_cc", "power_kw"]
    assert conflicts(ignored) == ["displacement_cc"]

    withdrawn = api.correct(vehicle_id, "withdraw", "power_kw")

    assert withdrawn.inputs == before.inputs
    assert withdrawn.evidence_fingerprint == before.evidence_fingerprint
    assert "power_kw" not in withdrawn.overlaid_fields
    (state,) = withdrawn.corrections
    assert (state.status, state.history_count, state.previous_source) == (
        "withdrawn", 2, "correction")
    # A correction after a withdrawal supersedes it: one chain per field.
    again = api.correct(vehicle_id, field="power_kw", value="158")
    assert again.corrections[0].history_count == 3
    assert again.inputs is not None and again.inputs.power_kw == 158

    history = api.service.history(vehicle_id)
    (field,) = history.fields
    assert (field.field, field.current_correction_id) == (
        "power_kw", again.corrections[0].correction_id)
    assert [(entry.action, entry.value) for entry in field.entries] == [
        ("set", "158"), ("withdraw", None), ("ignore", None)]
    assert api.service.history(_other_vehicle(db)).fields == []
    db.rollback()


def test_two_fields_of_one_car_are_corrected_independently(api: _Api, db: Connection) -> None:
    vehicle_id = _vehicle(db)

    api.correct(vehicle_id)
    both = api.correct(vehicle_id, field="power_kw", value="158")
    api.correct(vehicle_id, "withdraw")

    assert [(item.field, item.status) for item in both.corrections] == [
        ("engine_code", "set"), ("power_kw", "set")]
    after = api.matching.lookup_vehicle(vehicle_id)
    assert [(item.field, item.status, item.history_count) for item in after.corrections] == [
        ("engine_code", "withdrawn", 2), ("power_kw", "set", 1)]
    assert after.inputs is not None
    assert (after.inputs.engine_code, after.inputs.power_kw) == (None, 158)
    db.rollback()


def test_the_build_month_is_corrected_ignored_and_restored(api: _Api, db: Connection) -> None:
    vehicle_id = _vehicle(db)
    before = api.matching.lookup_vehicle(vehicle_id)
    assert before.inputs is not None and before.inputs.build_month == 201502
    month = _shown(before, "production_month")
    assert (month.label, month.type, month.current_value, month.current_source) == (
        "Build month", "integer", "2", "registry")

    corrected = api.correct(vehicle_id, field="production_month", value="11")
    assert corrected.inputs is not None and corrected.inputs.build_month == 201511
    assert (corrected.corrections[0].previous_value, corrected.corrections[0].previous_source) == (
        "2", "registry")
    assert _shown(corrected, "production_month").current_value == "11"

    # The year and the month the matcher is handed make the build month together.
    year = api.correct(vehicle_id, field="production_year", value="2014")
    assert year.inputs is not None
    assert (year.inputs.production_year, year.inputs.build_month) == (2014, 201411)

    ignored = api.correct(vehicle_id, "ignore", "production_month")
    assert ignored.inputs is not None
    assert (ignored.inputs.production_year, ignored.inputs.build_month) == (2014, None)
    assert _shown(ignored, "production_month").current_value is None
    with pytest.raises(NothingToIgnoreError):
        api.service.record(vehicle_id, api.send(vehicle_id, "ignore", "production_month"))
    db.rollback()

    api.correct(vehicle_id, "withdraw", "production_month")
    restored = api.correct(vehicle_id, "withdraw", "production_year")
    assert restored.inputs == before.inputs
    db.rollback()


def test_the_fuel_and_the_electrification_are_corrected_ignored_and_restored(
    api: _Api, db: Connection
) -> None:
    vehicle_id = _vehicle(db, HYBRID)
    before = api.matching.lookup_vehicle(vehicle_id)
    assert before.inputs is not None
    assert (before.inputs.fuels, before.inputs.electrification) == (
        ["electricity", "hybrid_petrol", "petrol"], "plug_in_hybrid")
    fuel, electrification = _shown(before, "fuel"), _shown(before, "electrification_type")
    assert (fuel.type, fuel.current_value, fuel.current_source) == (
        "list", "petrol,electricity", "registry")
    assert (electrification.current_value, electrification.current_source) == (
        "plug_in_hybrid", "registry")
    # The same carriers in another order are no change.
    with pytest.raises(ValueUnchangedError):
        api.service.record(vehicle_id, api.send(vehicle_id, field="fuel", value="electricity,petrol"))
    db.rollback()

    diesel = api.correct(vehicle_id, field="fuel", value="diesel")
    assert diesel.inputs is not None and diesel.inputs.fuels == ["diesel"]
    assert (diesel.corrections[0].value, diesel.corrections[0].previous_value,
            diesel.corrections[0].previous_source) == ("diesel", "petrol,electricity", "registry")
    assert (diesel.overlaid_fields["fuel"], _shown(diesel, "fuel").current_source) == (
        "correction", "correction")

    mild = api.correct(vehicle_id, field="electrification_type", value="hybrid")
    assert mild.inputs is not None and mild.inputs.electrification == "hybrid"
    assert (mild.corrections[0].previous_value, mild.corrections[0].previous_source) == (
        "plug_in_hybrid", "registry")

    no_fuel = api.correct(vehicle_id, "ignore", "fuel")
    assert no_fuel.inputs is not None and no_fuel.inputs.fuels == []
    assert _shown(no_fuel, "fuel").current_value is None
    unknown = api.correct(vehicle_id, "ignore", "electrification_type")
    assert unknown.inputs is not None and unknown.inputs.electrification is None

    api.correct(vehicle_id, "withdraw", "fuel")
    restored = api.correct(vehicle_id, "withdraw", "electrification_type")
    assert restored.inputs == before.inputs
    assert restored.evidence_fingerprint == before.evidence_fingerprint
    db.rollback()


def test_a_retry_is_answered_and_a_stale_or_pointless_correction_refused(
    api: _Api, db: Connection
) -> None:
    vehicle_id = _vehicle(db)
    request = api.send(vehicle_id)
    stale_screen = api.send(vehicle_id, field="power_kw", value="158")
    api.service.record(vehicle_id, request)
    db.execute("UPDATE core.vehicles SET power_kw = 110 WHERE vehicle_id = %s", (vehicle_id,))
    db.commit()

    # The same operation again: answered from what is stored, with the car as it is now.
    answer, created = api.service.record(vehicle_id, request)
    assert not created and _count(db) == 1
    assert answer.corrections[0].correction_id == request.operation_id
    assert answer.inputs is not None and answer.inputs.power_kw == 110
    # A correction built on the screen from before is refused, whatever its field.
    with pytest.raises(EvidenceChangedError):
        api.service.record(vehicle_id, stale_screen)
    # So is one that changes nothing, and an ignore of nothing.
    with pytest.raises(ValueUnchangedError):
        api.service.record(vehicle_id, api.send(vehicle_id, field="power_kw", value="110"))
    with pytest.raises(NothingToIgnoreError):
        api.service.record(vehicle_id, api.send(vehicle_id, "ignore", "electrification_type"))
    with pytest.raises(InvalidValueError):
        api.service.record(vehicle_id, api.send(vehicle_id, field="drive_type", value="4wd"))
    # And one that names another head than the field's own.
    with pytest.raises(CorrectionChangedError):
        api.service.record(vehicle_id, api.send(vehicle_id, value="D4204T14",
                                                supersedes_correction_id=None))
    db.rollback()
    assert _count(db) == 1


def test_a_ktype_choice_made_before_a_correction_is_flagged_and_never_changed(
    api: _Api, db: Connection
) -> None:
    vehicle_id = _vehicle(db)
    shown = api.matching.lookup_vehicle(vehicle_id)
    api.choice_service.record(
        vehicle_id,
        KTypeChoiceRequest(
            operation_id=uuid4(), action="choose", ktype="K1", reviewer="Ada",
            evidence_fingerprint=shown.evidence_fingerprint,
        ),
    )
    chosen = api.matching.lookup_vehicle(vehicle_id).choice
    assert chosen is not None and (chosen.ktype, chosen.needs_review) == ("K1", False)

    answer = api.correct(vehicle_id)  # the engine code of the other KType

    choice = answer.choice
    assert choice is not None and (choice.status, choice.ktype) == ("chosen", "K1")
    assert (choice.needs_review, choice.stale_reasons) == (True, ["evidence_changed"])
    assert [(item.field, item.then, item.now) for item in choice.changed_inputs] == [
        ("engine_code", None, "D4204T23")]
    # The person's choice stands until a person changes it; the matcher says otherwise.
    assert (answer.effective_ktype, answer.effective_source) == ("K1", "person")
    assert (answer.terminal, answer.top_ktype) == ("resolved", "K2")
    row = db.execute("SELECT ktype, match_state, engine_code FROM core.vehicles "
                     "WHERE vehicle_id = %s", (vehicle_id,)).fetchone()
    db.rollback()
    assert row == ("K1", "manual", "D4204T23")
    # A choice made on the screen from before the correction is refused.
    with pytest.raises(Exception, match="changed since it was shown"):
        api.choice_service.record(
            vehicle_id,
            KTypeChoiceRequest(
                operation_id=uuid4(), action="choose", ktype="K2", reviewer="Bob",
                supersedes_choice_id=chosen.choice_id,
                evidence_fingerprint=shown.evidence_fingerprint,
            ),
        )
    db.rollback()

    # Withdrawing the correction makes the choice fit again.
    restored = api.correct(vehicle_id, "withdraw")
    assert restored.choice is not None and restored.choice.needs_review is False
    db.rollback()


def test_the_details_are_read_only_for_a_car_that_has_corrections(
    api: _Api, db: Connection
) -> None:
    vehicle_id, other = _vehicle(db), _other_vehicle(db)

    api.matching.lookup_vehicle(vehicle_id)
    api.matching.lookup_vehicle(other)
    assert api.corrections.reads == 0

    api.correct(vehicle_id)
    reads = api.corrections.reads
    api.matching.lookup_vehicle(other)
    assert api.corrections.reads == reads
    api.matching.lookup_vehicle(vehicle_id)
    assert api.corrections.reads == reads + 1
    # A withdrawn correction is still shown, so the next one can supersede it.
    api.correct(vehicle_id, "withdraw")
    reads = api.corrections.reads
    assert api.matching.lookup_vehicle(vehicle_id).corrections[0].status == "withdrawn"
    assert api.corrections.reads == reads + 1
    db.rollback()


def test_a_summary_and_the_impact_report_see_corrections_through_the_same_read(
    api: _Api, db: Connection
) -> None:
    vehicle_id, other = _vehicle(db), _other_vehicle(db)
    api.correct(vehicle_id)
    api.correct(vehicle_id, "ignore", "power_kw")
    reads = api.corrections.reads

    cars = api.repository.vehicle_car_records([other, vehicle_id])
    db.rollback()

    assert [car.vehicle_id for car in cars] == [other, vehicle_id]
    plain, corrected = cars
    assert (plain.corrections, plain.has_corrections) == ({}, False)
    assert {field: head.action for field, head in corrected.corrections.items()} == {
        "engine_code": "set", "power_kw": "ignore"}
    assert corrected.has_corrections
    normalized: dict[str, Any] = corrected.record.payload["normalized"]  # type: ignore[assignment]
    assert normalized["engine_code"] == "D4204T23" and "power_kw" not in normalized
    assert corrected.record.payload["inferred_fields"] == []
    assert "engine_code" not in corrected.rule_filled
    assert api.evaluator.evaluate(corrected.record).terminal == "resolved"
    # The whole summary job runs on the same records, and never reads the details.
    job = api.matching.start_summary([], "", 10, run_in_background=False)
    assert job.status == "done" and job.evaluated == 5
    assert api.corrections.reads == reads
    db.rollback()


def test_a_correction_writes_nothing_that_promotion_or_the_ledger_reads(
    api: _Api, db: Connection
) -> None:
    def counts() -> list[int]:
        found = []
        for table in ("core.match_routing_decisions", "core.match_decision_heads",
                      "core.enrichment_ledger", CHOICES):
            row = db.execute(f"SELECT count(*) FROM {table}").fetchone()
            assert row is not None
            found.append(int(row[0]))
        db.commit()
        return found

    vehicle_id = _vehicle(db)
    matching = db.execute(
        "SELECT ktype, match_state, vehicle_variant_id FROM core.vehicles WHERE vehicle_id = %s",
        (vehicle_id,)).fetchone()
    before = counts()
    api.correct(vehicle_id)
    api.correct(vehicle_id, "ignore")
    api.correct(vehicle_id, "withdraw")

    assert counts() == before
    assert before[:2] == [0, 0]
    assert matching == db.execute(
        "SELECT ktype, match_state, vehicle_variant_id FROM core.vehicles WHERE vehicle_id = %s",
        (vehicle_id,)).fetchone() == (None, None, None)
    db.rollback()


# ------------------------------------------------- releasing a car stopped before matching


def _record_status(db: Connection, vehicle_id: str) -> tuple[Any, ...]:
    """The stop as the database holds it: the record's result and the vehicle's own status."""

    row = db.execute(
        "SELECT result.status, result.review_reasons, vehicle.normalization_status, "
        "vehicle.updated_at, vehicle.field_sources, vehicle.field_alternatives "
        "FROM core.vehicles AS vehicle JOIN core.normalization_results AS result "
        "ON result.source_record_id = vehicle.ts_record_id WHERE vehicle.vehicle_id = %s",
        (vehicle_id,),
    ).fetchone()
    db.commit()
    assert row is not None
    return tuple(row)


def test_a_person_releases_a_stopped_car_and_the_matcher_runs(api: _Api, db: Connection) -> None:
    vehicle_id = _vehicle(db, STOPPED)
    stored = _record_status(db, vehicle_id)
    assert stored[:3] == ("review_required", ["tyre_size_unrecognized"], "review_required")
    stopped = api.matching.lookup_vehicle(vehicle_id)
    assert (stopped.terminal, stopped.bucket, stopped.inputs) == (
        "normalization_review", "not_matchable", None)
    assert stopped.reason_codes == ["normalization:tyre_size_unrecognized"]
    assert stopped.stop_reasons == ["tyre_size_unrecognized"]

    released = api.correct(vehicle_id, "ignore", NORMALIZATION_STOP, reason="tyres read by hand")

    # The real matcher no longer stops: the car is a tie like its sibling.
    assert (released.terminal, released.bucket, released.top_ktype) == (
        "review_required", "several", "K1")
    assert released.inputs is not None and released.inputs.power_kw == 133
    assert released.stop_reasons == ["tyre_size_unrecognized"]
    (release,) = released.corrections
    assert (release.field, release.status, release.value, release.reason) == (
        NORMALIZATION_STOP, "ignored", None, "tyres read by hand")
    assert (release.previous_value, release.previous_source) == (
        "tyre_size_unrecognized", "registry")
    assert NORMALIZATION_STOP not in [item.field for item in released.correctable_fields]
    (row,) = chains(db, vehicle_id)[NORMALIZATION_STOP]
    assert (row.automatic_terminal, row.automatic_ktype, row.evidence["inputs"]) == (
        "normalization_review", None, None)
    db.rollback()
    # The record keeps its status and its reasons, and the vehicle is not written at all.
    assert _record_status(db, vehicle_id) == stored

    # The person carries on: the engine code settles it.
    settled = api.correct(vehicle_id, value="D4204T14")
    assert (settled.terminal, settled.top_ktype) == ("resolved", "K1")
    assert [item.field for item in settled.corrections] == ["engine_code", NORMALIZATION_STOP]

    # Taking the release back stops the car again; the other correction stays.
    again = api.correct(vehicle_id, "withdraw", NORMALIZATION_STOP)
    assert (again.terminal, again.inputs) == ("normalization_review", None)
    assert [(item.field, item.status) for item in again.corrections] == [
        ("engine_code", "set"), (NORMALIZATION_STOP, "withdrawn")]
    assert again.stop_reasons == ["tyre_size_unrecognized"]
    assert _record_status(db, vehicle_id)[:3] == stored[:3]


def test_a_release_is_seen_by_every_reader_of_the_cars_record(api: _Api, db: Connection) -> None:
    vehicle_id = _vehicle(db, STOPPED)
    (stopped,) = api.repository.vehicle_car_records([vehicle_id])
    assert api.evaluator.evaluate(stopped.record).terminal == "normalization_review"
    left_out = api.matching.start_summary([], "", 10, run_in_background=False).summary.buckets

    api.correct(vehicle_id, "ignore", NORMALIZATION_STOP, reason="checked")
    (released,) = api.repository.vehicle_car_records([vehicle_id])
    db.rollback()

    assert released.record.payload["normalization_status"] == "resolved"
    assert released.record.payload["review_reasons"] == []
    assert released.record.payload["normalized"] == stopped.record.payload["normalized"]
    assert released.stop_reasons == ("tyre_size_unrecognized",)
    assert set(released.corrections) == {NORMALIZATION_STOP}
    assert api.evaluator.evaluate(released.record).terminal == "review_required"
    # A summary counts the car among the scored ones from now on.
    job = api.matching.start_summary([], "", 10, run_in_background=False)
    assert job.summary.buckets["not_matchable"] == left_out["not_matchable"] - 1
    assert job.summary.buckets["several"] == left_out["several"] + 1
    db.rollback()


def test_a_release_needs_a_reason_and_a_car_that_is_stopped(api: _Api, db: Connection) -> None:
    stopped, matched, motorhome = _vehicle(db, STOPPED), _vehicle(db), _vehicle(db, MOTORHOME)

    with pytest.raises(ReasonRequiredError):
        api.service.record(stopped, api.send(stopped, "ignore", NORMALIZATION_STOP))
    with pytest.raises(ReasonRequiredError):
        api.service.record(stopped, api.send(stopped, "ignore", NORMALIZATION_STOP, reason="  "))
    # The stop is not a value.
    with pytest.raises(InvalidValueError):
        api.service.record(stopped, api.send(stopped, "set", NORMALIZATION_STOP, value="resolved"))
    # A car the matcher already runs on has nothing to release.
    with pytest.raises(NothingToIgnoreError):
        api.service.record(matched, api.send(matched, "ignore", NORMALIZATION_STOP, reason="why not"))
    # A policy keeps the motorhome out: that is no normalization stop.
    excluded = api.matching.lookup_vehicle(motorhome)
    assert (excluded.terminal, excluded.stop_reasons) == ("policy_excluded", [])
    assert excluded.reason_codes == ["policy:exclude_from_passenger_car_dataset"]
    with pytest.raises(NothingToIgnoreError):
        api.service.record(motorhome, api.send(motorhome, "ignore", NORMALIZATION_STOP, reason="x"))
    with pytest.raises(NothingToWithdrawError):
        api.service.record(stopped, api.send(stopped, "withdraw", NORMALIZATION_STOP))
    db.rollback()
    assert _count(db) == 0

    api.correct(stopped, "ignore", NORMALIZATION_STOP, reason="checked")
    with pytest.raises(NothingToIgnoreError):  # released already
        api.service.record(stopped, api.send(stopped, "ignore", NORMALIZATION_STOP, reason="again"))
    db.rollback()
    assert _count(db) == 1


# ---------------------------------------------------- a database without the table


def test_a_database_without_the_table_fails_the_read_loudly() -> None:
    """Never a car matched as if nobody had corrected it."""

    with throwaway_database("fact_corrections_bare") as bare:
        prepare_schema(bare)
        insert_ts_record(bare, volvo(plate="ABC123"))
        bare.commit()
        project(bare)
        backfill_vehicle_core(bare, min_free_bytes=None)
        bare.execute(f"DROP TABLE {TABLE}")
        bare.commit()
        vehicle_id = _vehicle(bare)
        repository = VehicleMatchingRepository(lambda: nullcontext(bare))

        with pytest.raises(psycopg.errors.UndefinedTable):
            repository.vehicle_car_records([vehicle_id])
        bare.rollback()
        with pytest.raises(psycopg.errors.UndefinedTable):
            CorrectionRepository(lambda: nullcontext(bare)).current(vehicle_id)
        bare.rollback()
        # A reviewer's rule does not read the table: it still reaches the car.
        rule = _rule(bare, "engine_code", "DPCA")
        _apply(bare, rule, "engine_code", "DPCA")
        assert _state(bare, vehicle_id)[0]["engine_code"] == "DPCA"


# ------------------------------------------------------------------- the HTTP contract


def test_the_http_contract_end_to_end(api: _Api, db: Connection) -> None:
    """The routes as the web calls them, over the real services and the real database."""

    from fastapi.testclient import TestClient

    from api.app.features.vehicle_corrections.router import get_correction_service
    from api.app.features.vehicle_matching.router import get_service
    from api.app.main import create_app

    app = create_app()
    app.dependency_overrides[get_service] = lambda: api.matching
    app.dependency_overrides[get_correction_service] = lambda: api.service
    client = TestClient(app)
    vehicle_id = _vehicle(db)
    url = f"/v1/vehicles/{vehicle_id}/corrections"

    def code(response: Any) -> tuple[int, str]:
        return response.status_code, response.json()["detail"]["code"]

    shown = client.get("/v1/vehicles/matching/lookup", params={"vehicle_id": vehicle_id}).json()
    assert (shown["terminal"], shown["corrections"], shown["stop_reasons"]) == (
        "review_required", [], [])
    assert [item["field"] for item in shown["correctable_fields"]] == list(SPECS)
    assert client.get(url).json() == {"vehicle_id": vehicle_id, "fields": []}

    body = {
        "operation_id": str(uuid4()), "field": "engine_code", "action": "set",
        "value": "D4204T23", "reviewer": "Ada", "reason": None,
        "supersedes_correction_id": None, "evidence_fingerprint": shown["evidence_fingerprint"],
    }
    recorded = client.post(url, json=body)
    assert recorded.status_code == 201
    after = recorded.json()
    assert (after["terminal"], after["top_ktype"]) == ("resolved", "K2")
    assert after["overlaid_fields"]["engine_code"] == "correction"
    (head,) = after["corrections"]
    assert (head["correction_id"], head["status"], head["value"], head["history_count"]) == (
        body["operation_id"], "set", "D4204T23", 1)
    engine = after["correctable_fields"][2]
    assert (engine["current_value"], engine["current_source"]) == ("D4204T23", "correction")

    # The same body again is a replay; the same id with other content is refused.
    replay = client.post(url, json=body)
    assert (replay.status_code, replay.json()["corrections"]) == (200, after["corrections"])
    assert code(client.post(url, json={**body, "value": "D4204T14"})) == (
        409, "operation_id_reused")
    # A screen from before the correction, and a head that is not the field's.
    stale = {**body, "operation_id": str(uuid4()), "field": "power_kw", "value": "158"}
    assert code(client.post(url, json=stale)) == (409, "evidence_changed")
    fresh = {**stale, "evidence_fingerprint": after["evidence_fingerprint"]}
    assert code(client.post(url, json={**fresh, "field": "engine_code", "value": "D4204T14"})) == (
        409, "correction_changed")
    # What the request itself gets wrong.
    assert code(client.post(url, json={**fresh, "field": "colour"})) == (
        422, "field_not_correctable")
    assert code(client.post(url, json={**fresh, "value": "many"})) == (422, "invalid_value")
    assert code(client.post(url, json={**fresh, "value": "133"})) == (422, "value_unchanged")
    assert code(client.post(url, json={
        **fresh, "action": "ignore", "field": "electrification_type", "value": None,
    })) == (422, "nothing_to_ignore")
    assert code(client.post(url, json={
        **fresh, "action": "withdraw", "field": "power_kw", "value": None,
    })) == (422, "nothing_to_withdraw")
    assert code(client.post(url, json={
        **fresh, "action": "ignore", "field": NORMALIZATION_STOP, "value": None,
    })) == (422, "reason_required")
    assert code(client.post(url, json={
        **fresh, "action": "ignore", "field": NORMALIZATION_STOP, "value": None, "reason": "why",
    })) == (422, "nothing_to_ignore")
    assert client.post(url, json={**fresh, "value": "15\u00008"}).status_code == 422
    unknown = "/v1/vehicles/NOR-00000000000000000000000000/corrections"
    assert code(client.post(unknown, json=fresh)) == (404, "vehicle_not_found")
    assert code(client.post("/v1/vehicles/ABC123/corrections", json=fresh)) == (
        422, "invalid_vehicle_id")
    db.rollback()
    assert _count(db) == 1

    undone = client.post(url, json={
        "operation_id": str(uuid4()), "field": "engine_code", "action": "withdraw",
        "reviewer": "Ada", "supersedes_correction_id": body["operation_id"],
    })
    assert undone.status_code == 201
    assert undone.json()["evidence_fingerprint"] == shown["evidence_fingerprint"]
    history = client.get(url).json()
    (field,) = history["fields"]
    assert (field["field"], field["current_correction_id"]) == (
        "engine_code", undone.json()["corrections"][0]["correction_id"])
    assert [(entry["action"], entry["value"], entry["previous_value"], entry["previous_source"])
            for entry in field["entries"]] == [
        ("withdraw", None, "D4204T23", "correction"), ("set", "D4204T23", None, "registry")]
    db.rollback()
