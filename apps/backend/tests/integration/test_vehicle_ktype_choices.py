"""A person's KType choice per car: what the database itself guarantees.

Runs on a throwaway database built the way production builds vehicles. Covers
the migration and its definition-level verifier, database-enforced immutability,
retry ambiguity (one operation id, one row), invalid correction links (each
refused by a named constraint), concurrency on one car, the derived copy on
`core.vehicles`, the stale-writer regression in `save_vehicles`, and the API
services end to end over the real repositories. The matcher's outcome is
scripted: these tests are about storage and staleness, not about scoring.
"""

from __future__ import annotations

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

from api.app.features.vehicle_ktype_choices.repository import (
    ChoiceRejectedError,
    ChoiceVehicleNotFoundError,
    KTypeChoiceRepository,
    VehicleBusyError,
)
from api.app.features.vehicle_ktype_choices.schemas import KTypeChoiceRequest
from api.app.features.vehicle_ktype_choices.service import (
    EvidenceChangedError,
    KTypeChoiceService,
)
from api.app.features.vehicle_matching.repository import VehicleMatchingRepository
from api.app.features.vehicle_matching.service import (
    Matcher,
    SummaryJobs,
    VehicleMatchingService,
)
from api.app.features.vehicles.repository import VehicleRepository
from api.app.features.vehicles.schemas import VehicleCondition
from api.app.features.vehicles.service import VehicleService
from ingestion.confidence_routing_migrations import run_confidence_routing_migrations
from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.match_run_service import MatchSourceRecord
from ingestion.tecdoc.match_run_adapters import MatchEvaluation, ResolvedMatchQuery
from ingestion.vehicle_core_store import load_vehicle, save_vehicles
from ingestion.vehicle_core_ts import backfill_vehicle_core
from ingestion.vehicle_ktype_choice_migrations import (
    VEHICLE_KTYPE_CHOICE_MIGRATION_STATEMENTS,
    VehicleKTypeChoiceSchemaContractError,
    run_vehicle_ktype_choice_migrations,
    verify_vehicle_ktype_choice_schema_contract,
)
from ingestion.vehicle_ktype_choices import (
    ChoiceChangedError,
    NewChoice,
    NothingToWithdrawError,
    OperationReusedError,
    append_choice,
    chain,
    current_choice,
    project_choices,
)
from tests.integration.throwaway_database import throwaway_database
from tests.integration.vehicle_core_fixtures import (
    insert_ts_record,
    prepare_schema,
    project,
    volvo,
)

TABLE = "core.vehicle_ktype_choices"
FINGERPRINT = "a" * 64


@pytest.fixture(scope="module")
def db() -> Iterator[Connection]:
    with throwaway_database("ktype_choices") as connection:
        prepare_schema(connection)
        run_confidence_routing_migrations(connection)
        insert_ts_record(connection, volvo(plate="ABC123"))
        insert_ts_record(
            connection,
            volvo(vin="WVWZZZ1KZ8W123456", plate="GLF001", brand="VOLKSWAGEN", model="GOLF",
                  fab_code="VW", variant=None, version=None, kw="77", ccm="1390",
                  vehicle_year=2008, registration_date="20080415", build_month="200803"),
        )
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        connection.commit()
        yield connection


@pytest.fixture(autouse=True)
def _clean(db: Connection) -> Iterator[None]:
    """Each test starts with no choices. Only a test may switch the triggers off."""

    yield
    db.rollback()
    db.execute(f"ALTER TABLE {TABLE} DISABLE TRIGGER USER")
    db.execute(f"DELETE FROM {TABLE}")
    db.execute(f"ALTER TABLE {TABLE} ENABLE TRIGGER USER")
    project_choices(db, None)
    db.commit()
    verify_vehicle_ktype_choice_schema_contract(db)
    db.commit()


def _vehicle(db: Connection, vin: str = "YV1BW84S1F1234567") -> str:
    row = db.execute("SELECT vehicle_id FROM core.vehicles WHERE vin = %s", (vin,)).fetchone()
    assert row is not None
    db.commit()
    return str(row[0])


def _other_vehicle(db: Connection) -> str:
    return _vehicle(db, "WVWZZZ1KZ8W123456")


def _evidence(*ktypes: str) -> dict[str, Any]:
    return {
        "schema": "manual-ktype-choice-evidence-v1",
        "automatic": {"terminal": "review_required"},
        "candidates": [{"ktype": ktype} for ktype in ktypes],
        "inputs": {"power_kw": 133},
    }


def _new(vehicle_id: str, **overrides: Any) -> NewChoice:
    values: dict[str, Any] = {
        "choice_id": uuid4(),
        "vehicle_id": vehicle_id,
        "action": "choose",
        "ktype": "A",
        "supersedes_choice_id": None,
        "reviewer": "Ada",
        "reason": None,
        "catalog_batch": "batch-1",
        "automatic_terminal": "review_required",
        "automatic_ktype": None,
        "code_version": "test",
        "evidence_fingerprint": FINGERPRINT,
        "evidence": _evidence("A", "B"),
    }
    values.update(overrides)
    if values["action"] != "choose" and "ktype" not in overrides:
        values["ktype"] = None
    return NewChoice(**values)


def _append(db: Connection, new: NewChoice) -> UUID:
    row, created, _ = append_choice(db, new, new.supersedes_choice_id)
    assert created
    project_choices(db, [new.vehicle_id])
    db.commit()
    return row.choice_id


def _count(db: Connection) -> int:
    row = db.execute(f"SELECT count(*) FROM {TABLE}").fetchone()
    assert row is not None
    return int(row[0])


_COLUMNS = (
    "choice_id", "vehicle_id", "chain_position", "action", "ktype", "supersedes_choice_id", "reviewer", "reason",
    "catalog_batch", "automatic_terminal", "automatic_ktype", "code_version",
    "evidence_fingerprint", "evidence",
)


def _raw_insert(db: Connection, vehicle_id: str, **overrides: Any) -> UUID:
    """INSERT by plain SQL, past every Python check: only the database decides."""

    values = _raw_values(vehicle_id, **overrides)
    db.execute(
        f"INSERT INTO {TABLE} ({', '.join(_COLUMNS)}) "
        f"VALUES ({', '.join(['%s'] * len(_COLUMNS))})",
        values,
    )
    return values[0]  # type: ignore[no-any-return]


def _raw_values(vehicle_id: str, **overrides: Any) -> list[Any]:
    """One row's values in `_COLUMNS` order; position 0 unless the test says otherwise."""

    new = _new(vehicle_id)
    values = {column: getattr(new, column, 0) for column in _COLUMNS}
    values.update(overrides)
    values["evidence"] = Jsonb(values["evidence"])
    return [values[column] for column in _COLUMNS]


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
    names = tuple(statement.name for statement in VEHICLE_KTYPE_CHOICE_MIGRATION_STATEMENTS)

    assert run_vehicle_ktype_choice_migrations(db) == names
    assert run_vehicle_ktype_choice_migrations(db) == names
    verify_vehicle_ktype_choice_schema_contract(db)


_FUNCTION = "core.vehicle_ktype_choices_block_mutation"
_ROW_TRIGGER = "vehicle_ktype_choices_append_only"
_LINK = "vehicle_ktype_choices_supersedes_previous_fkey"
_LINK_DEFINITION = (
    "FOREIGN KEY (supersedes_choice_id, vehicle_id, supersedes_position) "
    f"REFERENCES {TABLE} (choice_id, vehicle_id, chain_position)"
)

_DRIFTS: dict[str, tuple[str, ...]] = {
    "a dropped check": (
        f"ALTER TABLE {TABLE} DROP CONSTRAINT vehicle_ktype_choices_ktype_was_shown",
    ),
    "a disabled row trigger": (f"ALTER TABLE {TABLE} DISABLE TRIGGER {_ROW_TRIGGER}",),
    "a disabled truncate trigger": (
        f"ALTER TABLE {TABLE} DISABLE TRIGGER vehicle_ktype_choices_append_only_truncate",
    ),
    "the position no longer unique per vehicle": (
        f"ALTER TABLE {TABLE} DROP CONSTRAINT {_LINK}",
        f"ALTER TABLE {TABLE} DROP CONSTRAINT vehicle_ktype_choices_position_key",
    ),
    "the root rule dropped": (
        f"ALTER TABLE {TABLE} DROP CONSTRAINT vehicle_ktype_choices_root_supersedes_nothing",
    ),
    "a column made mandatory": (f"ALTER TABLE {TABLE} ALTER COLUMN reason SET NOT NULL",),
    "a dropped default": (f"ALTER TABLE {TABLE} ALTER COLUMN created_at DROP DEFAULT",),
    "an extra column": (f"ALTER TABLE {TABLE} ADD COLUMN note TEXT",),
    "a dropped link foreign key": (f"ALTER TABLE {TABLE} DROP CONSTRAINT {_LINK}",),
    # Same-named but weaker objects: caught by definition, not by name.
    "a link that no longer carries the position": (
        f"ALTER TABLE {TABLE} DROP CONSTRAINT {_LINK}",
        (
            f"ALTER TABLE {TABLE} ADD CONSTRAINT {_LINK} "
            f"FOREIGN KEY (supersedes_choice_id) REFERENCES {TABLE} (choice_id)"
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
        f"ALTER TABLE {TABLE} DROP CONSTRAINT vehicle_ktype_choices_action_values",
        (
            f"ALTER TABLE {TABLE} ADD CONSTRAINT vehicle_ktype_choices_action_values "
            "CHECK (action <> '')"
        ),
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
            f"CREATE TRIGGER vehicle_ktype_choices_rewrite BEFORE INSERT ON {TABLE} "
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

    with pytest.raises(VehicleKTypeChoiceSchemaContractError):
        verify_vehicle_ktype_choice_schema_contract(db)
    db.rollback()

    verify_vehicle_ktype_choice_schema_contract(db)


def test_a_migration_rerun_restores_the_triggers_and_their_function(db: Connection) -> None:
    """The deploy step reruns the migration, so a weakened trigger does not survive it."""

    db.execute(_DRIFTS["a trigger that never fires"][0])
    db.execute(_DRIFTS["a trigger function that no longer refuses"][0])
    db.commit()

    run_vehicle_ktype_choice_migrations(db)

    _append(db, _new(_vehicle(db)))
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        db.execute(f"DELETE FROM {TABLE}")
    db.rollback()


def test_a_failed_verification_rolls_the_migration_back(db: Connection) -> None:
    db.execute(f"ALTER TABLE {TABLE} ALTER COLUMN reason SET NOT NULL")
    db.commit()
    try:
        with pytest.raises(VehicleKTypeChoiceSchemaContractError):
            run_vehicle_ktype_choice_migrations(db)
    finally:
        db.execute(f"ALTER TABLE {TABLE} ALTER COLUMN reason DROP NOT NULL")
        db.commit()


# ------------------------------------------------------------------------ immutability


@pytest.mark.parametrize(
    "statement",
    [
        f"UPDATE {TABLE} SET reviewer = 'someone else'",
        f"UPDATE {TABLE} SET ktype = 'B'",
        f"DELETE FROM {TABLE}",
        f"TRUNCATE {TABLE}",
        "TRUNCATE core.vehicles CASCADE",
        (
            f"INSERT INTO {TABLE} ({', '.join(_COLUMNS)}) "
            f"SELECT {', '.join(_COLUMNS)} FROM {TABLE} "
            "ON CONFLICT (choice_id) DO UPDATE SET reviewer = 'someone else'"
        ),
        (
            f"MERGE INTO {TABLE} AS t USING (SELECT 1) AS s ON true "
            "WHEN MATCHED THEN UPDATE SET reason = 'rewritten'"
        ),
    ],
)
def test_the_database_refuses_to_change_or_remove_a_choice(db: Connection, statement: str) -> None:
    _append(db, _new(_vehicle(db)))

    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        db.execute(statement)
    db.rollback()

    assert _count(db) == 1


# --------------------------------------------------------------------- retry ambiguity


def test_the_same_operation_and_content_twice_is_one_row(db: Connection) -> None:
    new = _new(_vehicle(db))
    first, created, count = append_choice(db, new, None)
    db.commit()

    again, created_again, count_again = append_choice(db, new, None)
    db.commit()

    assert (created, count) == (True, 1)
    assert (created_again, count_again) == (False, 1)
    assert again == first
    assert _count(db) == 1


def test_different_content_for_a_used_operation_id_is_rejected(db: Connection) -> None:
    vehicle_id = _vehicle(db)
    new = _new(vehicle_id)
    stored, _, _ = append_choice(db, new, None)
    db.commit()

    for changed in (
        {"ktype": "B"},
        {"reviewer": "Bob"},
        {"reason": "another reason"},
        {"action": "none"},
        {"vehicle_id": _other_vehicle(db)},
    ):
        with pytest.raises(OperationReusedError):
            values: dict[str, Any] = {
                "vehicle_id": vehicle_id,
                "choice_id": new.choice_id,
                **changed,
            }
            append_choice(db, _new(**values), None)
        db.rollback()

    assert _count(db) == 1
    found = current_choice(db, vehicle_id)
    assert found is not None and found[0] == stored


def test_the_primary_key_refuses_a_second_row_for_an_operation_id(db: Connection) -> None:
    vehicle_id = _vehicle(db)
    choice_id = _append(db, _new(vehicle_id))

    assert _refused(db, _other_vehicle(db), choice_id=choice_id) == "vehicle_ktype_choices_pkey"


@contextmanager
def _connection(db: Connection) -> Iterator[Connection]:
    with psycopg.connect(make_conninfo(db.info.dsn, password=db.info.password)) as connection:
        yield connection


def _own_connections(db: Connection, **options: Any) -> KTypeChoiceRepository:
    return KTypeChoiceRepository(lambda: _connection(db), **options)


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
    assert sorted(created for _, created, _ in results) == [False, False, False, True]
    assert len({row.choice_id for row, _, _ in results}) == 1
    assert _count(db) == 1


# ----------------------------------------------------------------------- concurrency


def test_two_people_deciding_one_car_at_once_exactly_one_wins(db: Connection) -> None:
    vehicle_id = _vehicle(db)
    repository = _own_connections(db)
    first, second = _new(vehicle_id, ktype="A"), _new(vehicle_id, ktype="B", reviewer="Bob")

    results = _race([lambda: repository.record(first), lambda: repository.record(second)])

    lost = [result for result in results if isinstance(result, Exception)]
    assert len(lost) == 1 and isinstance(lost[0], ChoiceChangedError)
    assert _count(db) == 1
    row = db.execute("SELECT ktype, match_state FROM core.vehicles WHERE vehicle_id = %s",
                     (vehicle_id,)).fetchone()
    winner = next(result for result in results if not isinstance(result, Exception))
    assert row == (winner[0].ktype, "manual")


def test_two_people_replacing_one_choice_at_once_exactly_one_wins(db: Connection) -> None:
    vehicle_id = _vehicle(db)
    head = _append(db, _new(vehicle_id))
    repository = _own_connections(db)
    calls = [
        lambda: repository.record(_new(vehicle_id, ktype="B", supersedes_choice_id=head)),
        lambda: repository.record(_new(vehicle_id, action="none", supersedes_choice_id=head)),
    ]

    results = _race(calls)

    lost = [result for result in results if isinstance(result, Exception)]
    assert len(lost) == 1 and isinstance(lost[0], ChoiceChangedError)
    assert _count(db) == 2


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


def test_an_unknown_vehicle_is_not_found(db: Connection) -> None:
    with pytest.raises(ChoiceVehicleNotFoundError):
        _own_connections(db).record(_new("NOR-00000000000000000000000000"))
    assert _count(db) == 0


# ------------------------------------------------------------ invalid correction links


_POSITION_KEY = "vehicle_ktype_choices_position_key"


def test_the_chain_is_linear_and_stays_on_one_vehicle(db: Connection) -> None:
    vehicle_id, other = _vehicle(db), _other_vehicle(db)
    root = _append(db, _new(vehicle_id))
    second = _append(db, _new(vehicle_id, ktype="B", supersedes_choice_id=root))

    # Superseding another vehicle's row.
    assert _refused(db, other, chain_position=2, supersedes_choice_id=second) == _LINK
    # A second successor of a row already superseded.
    assert _refused(db, vehicle_id, chain_position=1, supersedes_choice_id=root) == _POSITION_KEY
    # A link that skips a row: position 2 must supersede position 1, not the root.
    assert _refused(db, vehicle_id, chain_position=2, supersedes_choice_id=root) == _LINK
    # A second root for the vehicle.
    assert _refused(db, vehicle_id) == _POSITION_KEY
    # A row with a predecessor claiming to be a root, and a root claiming a predecessor.
    assert _refused(db, other, supersedes_choice_id=second) == (
        "vehicle_ktype_choices_root_supersedes_nothing"
    )
    assert _refused(db, vehicle_id, chain_position=2) == (
        "vehicle_ktype_choices_root_supersedes_nothing"
    )
    # A row superseding itself, and one superseding a row that does not exist.
    own = uuid4()
    assert _refused(db, other, choice_id=own, chain_position=1, supersedes_choice_id=own) == _LINK
    assert _refused(db, vehicle_id, chain_position=2, supersedes_choice_id=uuid4()) == _LINK
    assert _refused(db, other, chain_position=-1, supersedes_choice_id=uuid4()) == (
        "vehicle_ktype_choices_position_nonnegative"
    )
    assert _count(db) == 2


def test_one_statement_cannot_store_a_cycle_or_a_detached_chain(db: Connection) -> None:
    """What an import or a bulk copy could send: several rows checked together at the end."""

    vehicle_id, other = _vehicle(db), _other_vehicle(db)
    first, second, third = uuid4(), uuid4(), uuid4()

    def refused(rows: list[list[Any]]) -> str | None:
        with pytest.raises(psycopg.errors.IntegrityError) as caught:
            _raw_insert_many(db, rows)
        db.rollback()
        return caught.value.diag.constraint_name

    # Two rows superseding each other: no root, no head.
    assert refused([
        _raw_values(vehicle_id, choice_id=first, chain_position=1, supersedes_choice_id=second),
        _raw_values(vehicle_id, choice_id=second, chain_position=2, supersedes_choice_id=first),
    ]) == _LINK
    assert refused([
        _raw_values(vehicle_id, choice_id=first, chain_position=1, supersedes_choice_id=second),
        _raw_values(vehicle_id, choice_id=second, chain_position=1, supersedes_choice_id=first),
    ]) == _POSITION_KEY
    # A cycle beside a real chain.
    root = _append(db, _new(vehicle_id))
    assert refused([
        _raw_values(vehicle_id, choice_id=first, chain_position=5, supersedes_choice_id=second),
        _raw_values(vehicle_id, choice_id=second, chain_position=4, supersedes_choice_id=third),
        _raw_values(vehicle_id, choice_id=third, chain_position=3, supersedes_choice_id=first),
    ]) == _LINK
    # A chain with a gap: position 2 with nothing at position 1.
    assert refused([
        _raw_values(vehicle_id, choice_id=first, chain_position=2, supersedes_choice_id=root),
    ]) == _LINK
    # A chain whose links cross into another vehicle.
    assert refused([
        _raw_values(other, choice_id=first),
        _raw_values(vehicle_id, choice_id=second, chain_position=1, supersedes_choice_id=first),
    ]) == _LINK
    assert _count(db) == 1

    # A whole valid chain in one statement passes in any row order (newest first here).
    _raw_insert_many(db, [
        _raw_values(other, choice_id=third, chain_position=2, action="withdraw", ktype=None,
                    supersedes_choice_id=second),
        _raw_values(other, choice_id=second, chain_position=1, ktype="B",
                    supersedes_choice_id=first),
        _raw_values(other, choice_id=first),
    ])
    db.commit()
    assert [row.choice_id for row in chain(db, other)] == [third, second, first]
    found = current_choice(db, other)
    assert found is not None and (found[0].choice_id, found[1]) == (third, 3)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"action": "withdraw", "ktype": None}, "vehicle_ktype_choices_withdraw_supersedes"),
        ({"chain_position": 1}, "vehicle_ktype_choices_root_supersedes_nothing"),
        ({"action": "choose", "ktype": None}, "vehicle_ktype_choices_ktype_matches_action"),
        ({"action": "choose", "ktype": " "}, "vehicle_ktype_choices_ktype_matches_action"),
        ({"action": "none", "ktype": "A"}, "vehicle_ktype_choices_ktype_matches_action"),
        ({"action": "pick"}, "vehicle_ktype_choices_action_values"),
        ({"ktype": "Z"}, "vehicle_ktype_choices_ktype_was_shown"),
        ({"reviewer": "  "}, "vehicle_ktype_choices_reviewer_nonempty"),
        ({"reviewer": "x" * 121}, "vehicle_ktype_choices_reviewer_nonempty"),
        ({"reason": ""}, "vehicle_ktype_choices_reason_nonempty"),
        ({"catalog_batch": ""}, "vehicle_ktype_choices_provenance_nonempty"),
        ({"code_version": " "}, "vehicle_ktype_choices_provenance_nonempty"),
        ({"evidence_fingerprint": "ABC"}, "vehicle_ktype_choices_fingerprint_format"),
        ({"evidence": {"candidates": [{"ktype": "A"}]}}, "vehicle_ktype_choices_evidence_shape"),
        (
            {"evidence": {"schema": "x", "automatic": {}, "candidates": {}}},
            "vehicle_ktype_choices_evidence_shape",
        ),
        # A missing key makes the comparison NULL, and a CHECK passes on NULL: the
        # checks must refuse these, not let them through.
        (
            {"evidence": {"schema": "x", "candidates": [{"ktype": "A"}]}},
            "vehicle_ktype_choices_evidence_shape",
        ),
        (
            {"action": "none", "ktype": None, "evidence": {"schema": "x", "automatic": {}}},
            "vehicle_ktype_choices_evidence_shape",
        ),
    ],
)
def test_a_malformed_row_is_refused_by_a_named_constraint(
    db: Connection, overrides: dict[str, Any], constraint: str
) -> None:
    assert _refused(db, _vehicle(db), **overrides) == constraint
    assert _count(db) == 0


def test_a_choice_for_a_vehicle_that_does_not_exist_is_refused(db: Connection) -> None:
    assert _refused(db, "NOR-00000000000000000000000000") == "vehicle_ktype_choices_vehicle_fkey"


def test_the_writer_refuses_a_stale_head_and_an_empty_withdrawal(db: Connection) -> None:
    vehicle_id = _vehicle(db)
    with pytest.raises(NothingToWithdrawError):
        append_choice(db, _new(vehicle_id, action="withdraw"), None)
    root = _append(db, _new(vehicle_id))

    with pytest.raises(ChoiceChangedError):
        append_choice(db, _new(vehicle_id, ktype="B"), None)
    with pytest.raises(ChoiceChangedError):
        append_choice(db, _new(vehicle_id, ktype="B", supersedes_choice_id=uuid4()), uuid4())
    withdrawn = _append(db, _new(vehicle_id, action="withdraw", supersedes_choice_id=root))
    with pytest.raises(NothingToWithdrawError):
        append_choice(
            db, _new(vehicle_id, action="withdraw", supersedes_choice_id=withdrawn), withdrawn
        )
    db.rollback()

    assert [row.choice_id for row in chain(db, vehicle_id)] == [withdrawn, root]


# ---------------------------------------------------------------- the copy on vehicles


def _copy(db: Connection, vehicle_id: str) -> tuple[Any, Any, Any, Any, dict[str, Any]]:
    row = db.execute(
        "SELECT ktype, match_state, field_sources -> 'ktype', field_sources -> 'match_state', "
        "field_sources - 'ktype' - 'match_state', vehicle_variant_id, field_alternatives "
        "FROM core.vehicles WHERE vehicle_id = %s",
        (vehicle_id,),
    ).fetchone()
    assert row is not None and row[5] is None
    db.commit()
    return row[0], row[1], row[2], row[3], {"sources": row[4], "alternatives": row[6]}


def test_the_vehicle_carries_a_copy_of_the_current_choice(db: Connection) -> None:
    vehicle_id, other = _vehicle(db), _other_vehicle(db)
    *_, untouched = _copy(db, vehicle_id)
    before_other = _copy(db, other)

    chosen = _append(db, _new(vehicle_id))
    ktype, state, ktype_source, state_source, rest = _copy(db, vehicle_id)
    assert (ktype, state) == ("A", "manual")
    assert ktype_source == state_source
    assert ktype_source.startswith(f"review:{chosen}@20")
    assert rest == untouched

    none = _append(db, _new(vehicle_id, action="none", supersedes_choice_id=chosen))
    ktype, state, ktype_source, state_source, rest = _copy(db, vehicle_id)
    assert (ktype, state, ktype_source) == (None, "manual_none", None)
    assert state_source.startswith(f"review:{none}@")
    assert rest == untouched

    changed = _append(db, _new(vehicle_id, ktype="B", supersedes_choice_id=none))
    assert _copy(db, vehicle_id)[:2] == ("B", "manual")

    withdrawn = _append(db, _new(vehicle_id, action="withdraw", supersedes_choice_id=changed))
    assert _copy(db, vehicle_id) == (None, None, None, None, untouched)

    again = _append(db, _new(vehicle_id, supersedes_choice_id=withdrawn))
    ktype, state, ktype_source, _, rest = _copy(db, vehicle_id)
    assert (ktype, state) == ("A", "manual")
    assert ktype_source.startswith(f"review:{again}@")
    assert rest == untouched

    # Already right: nothing is written, `updated_at` stays.
    assert project_choices(db, [vehicle_id]) == 0
    assert project_choices(db, None) == 0
    assert _copy(db, other) == before_other


def test_the_copy_is_recomputed_from_the_choices(db: Connection) -> None:
    vehicle_id, other = _vehicle(db), _other_vehicle(db)
    _append(db, _new(vehicle_id))
    right = _copy(db, vehicle_id)
    db.execute("UPDATE core.vehicles SET ktype = 'X', match_state = NULL WHERE vehicle_id = %s",
               (vehicle_id,))
    db.execute("UPDATE core.vehicles SET match_state = 'manual_none' WHERE vehicle_id = %s",
               (other,))
    db.commit()

    assert project_choices(db, None) == 2
    db.commit()

    assert _copy(db, vehicle_id) == right
    assert _copy(db, other)[:4] == (None, None, None, None)


def test_a_stray_copy_without_any_choice_is_cleared(db: Connection) -> None:
    """Repair scope: a KType or a source key nobody chose, with no match state to find it by."""

    vehicle_id, other = _vehicle(db), _other_vehicle(db)
    clean = _copy(db, vehicle_id), _copy(db, other)
    db.execute("UPDATE core.vehicles SET ktype = 'X' WHERE vehicle_id = %s", (vehicle_id,))
    db.execute(
        "UPDATE core.vehicles SET field_sources = field_sources || %s WHERE vehicle_id = %s",
        (Jsonb({"match_state": "review:gone@2026-01-01"}), other),
    )
    db.commit()

    assert project_choices(db, None) == 2
    db.commit()

    assert (_copy(db, vehicle_id), _copy(db, other)) == clean
    assert project_choices(db, None) == 0


def _waits_for_a_lock(db: Connection) -> bool:
    row = db.execute("SELECT count(*) FROM pg_locks WHERE NOT granted").fetchone()
    db.commit()
    return bool(row and row[0])


def test_the_repair_keeps_a_source_key_another_writer_commits_meanwhile(db: Connection) -> None:
    """The repair runs without the row lock: it must rebuild `field_sources` from the row
    as it is when the update lands, not from the read that planned it."""

    vehicle_id = _vehicle(db)
    append_choice(db, _new(vehicle_id), None)  # a choice whose copy is not written yet
    db.commit()
    result: list[Any] = []

    def repair() -> None:
        with _connection(db) as connection:
            result.append(project_choices(connection, None))
            connection.commit()

    with _connection(db) as writer:
        writer.execute(
            "UPDATE core.vehicles SET field_sources = field_sources || %s WHERE vehicle_id = %s",
            (Jsonb({"colour": "ais:record-9@2026-09-30"}), vehicle_id),
        )
        thread = threading.Thread(target=repair)
        thread.start()
        deadline = time.monotonic() + 10
        while not _waits_for_a_lock(db) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert _waits_for_a_lock(db), "the repair should be waiting for the writer's row"
        writer.commit()
        thread.join(timeout=30)
    try:
        assert result == [1]
        row = db.execute(
            "SELECT ktype, match_state, field_sources ->> 'colour', field_sources ? 'ktype' "
            "FROM core.vehicles WHERE vehicle_id = %s", (vehicle_id,),
        ).fetchone()
        assert row == ("A", "manual", "ais:record-9@2026-09-30", True)
        assert project_choices(db, None) == 0
    finally:
        db.execute(
            "UPDATE core.vehicles SET field_sources = field_sources - 'colour' "
            "WHERE vehicle_id = %s AND field_sources ->> 'colour' = 'ais:record-9@2026-09-30'",
            (vehicle_id,),
        )
        db.commit()


def test_a_value_the_database_cannot_store_is_rejected_for_good(db: Connection) -> None:
    """Not "try again": the same request would fail the same way every time."""

    vehicle_id = _vehicle(db)
    repository = _own_connections(db)

    with pytest.raises(ChoiceRejectedError):
        repository.record(_new(vehicle_id, reason="bad\x00byte"))
    with pytest.raises(ChoiceRejectedError):
        repository.record(_new(vehicle_id, ktype="Z"))  # not among the stored candidates

    assert _count(db) == 0
    assert _copy(db, vehicle_id)[:2] == (None, None)


def test_a_writer_holding_an_older_state_does_not_undo_a_choice(db: Connection) -> None:
    """`save_vehicles` writes a state loaded earlier, without a lock."""

    vehicle_id = _vehicle(db)
    stale = load_vehicle(db, vehicle_id)
    assert stale is not None
    db.commit()
    chosen = _append(db, _new(vehicle_id))
    with_choice = _copy(db, vehicle_id)

    stale.values["colour"] = "RÖD"
    save_vehicles(db, [stale])
    db.commit()

    assert _copy(db, vehicle_id) == with_choice
    row = db.execute("SELECT colour FROM core.vehicles WHERE vehicle_id = %s",
                     (vehicle_id,)).fetchone()
    assert row == ("RÖD",)

    # The reverse: a state loaded while the choice stood, written after its withdrawal.
    holding_choice = load_vehicle(db, vehicle_id)
    assert holding_choice is not None and holding_choice.values["ktype"] == "A"
    _append(db, _new(vehicle_id, action="withdraw", supersedes_choice_id=chosen))
    save_vehicles(db, [holding_choice])
    db.commit()

    assert _copy(db, vehicle_id)[:4] == (None, None, None, None)
    # Writing an unchanged state changes no row.
    current = load_vehicle(db, vehicle_id)
    assert current is not None
    assert save_vehicles(db, [current]) == 0
    db.commit()


def test_the_match_state_filter_is_answered_from_its_partial_index(db: Connection) -> None:
    _append(db, _new(_vehicle(db)))
    with db.cursor() as cursor:
        cursor.execute("SET LOCAL plan_cache_mode = force_generic_plan")
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute(
            "EXPLAIN SELECT vehicle_id FROM core.vehicles "
            "WHERE match_state IN ('manual', 'manual_none') ORDER BY vehicle_id LIMIT 50"
        )
        plan = "\n".join(str(row[0]) for row in cursor.fetchall())
    db.rollback()

    assert "vehicles_match_state_idx" in plan


# ------------------------------------------------------------- the services, end to end


class _ScriptedEvaluator:
    """The matcher's outcome, set by the test; the car's inputs come from the database."""

    def __init__(self) -> None:
        self.terminal = "review_required"
        self.candidates: tuple[str, ...] = ("A", "B")
        self.conflicting: frozenset[str] = frozenset()

    def evaluate(self, record: MatchSourceRecord) -> MatchEvaluation:
        return MatchEvaluation(
            self.terminal,  # type: ignore[arg-type]
            ("match:scripted",),
            top_candidate_reference=self.candidates[0] if self.candidates else None,
            candidate_matches=tuple(
                {
                    "candidate_reference": reference,
                    "candidate_type": "TecDocKType",
                    "confidence": 0.9,
                    "evidence": {
                        "matched_fields": ["model"],
                        "missing_fields": [],
                        "conflicting_fields": ["power_kw"] if reference in self.conflicting else [],
                    },
                }
                for reference in self.candidates
            ),
            confidence=0.9,
        )

    def resolved_query(self, record: MatchSourceRecord) -> ResolvedMatchQuery:
        normalized: dict[str, Any] = record.payload["normalized"]  # type: ignore[assignment]
        power = normalized.get("power_kw")
        return ResolvedMatchQuery(
            key=("k",),
            scope_manufacturer=str(normalized.get("manufacturer") or ""),
            model_values=(str(normalized.get("model_family") or ""),),
            year=2015,
            fuels=frozenset({"diesel"}),
            engine_code=None,
            displacement_cc=1969,
            power_kw=int(power) if power is not None else None,
            drive_type=None,
            bodywork=None,
            recovery_reason=None,
            source_context=(),
            source_model_resolution=None,
        )


class _CountingChoices(KTypeChoiceRepository):
    """The real repository, counting the lookup's choice reads."""

    reads = 0

    def current(self, vehicle_id: str) -> Any:
        self.reads += 1
        return super().current(vehicle_id)


class _Api:
    def __init__(self, db: Connection) -> None:
        def factory() -> AbstractContextManager[Connection]:
            return nullcontext(db)

        self.evaluator = _ScriptedEvaluator()
        self.batch = "batch-1"
        self.catalog = {
            reference: VehicleCandidate(candidate_reference=reference, manufacturer="VOLVO",
                                        model="V70")
            for reference in ("A", "B", "C")
        }
        self.choices = _CountingChoices(factory)
        self.matching = VehicleMatchingService(
            VehicleMatchingRepository(factory), self._matcher, SummaryJobs(), choices=self.choices
        )
        self.service = KTypeChoiceService(self.choices, self.matching.lookup_vehicle, "abc1234")
        self.vehicles = VehicleService(VehicleRepository(factory))

    def _matcher(self) -> Matcher:
        return Matcher(
            self.batch, self.evaluator, self.catalog, "rules-test-1"  # type: ignore[arg-type]
        )

    def send(self, vehicle_id: str, action: str = "choose", **body: Any) -> KTypeChoiceRequest:
        """A request built from the lookup, the way the screen builds it."""

        lookup = self.matching.lookup_vehicle(vehicle_id)
        values: dict[str, Any] = {
            "operation_id": uuid4(),
            "action": action,
            "ktype": "A" if action == "choose" else None,
            "reviewer": "Ada",
            "supersedes_choice_id": lookup.choice.choice_id if lookup.choice else None,
            "evidence_fingerprint": lookup.evidence_fingerprint,
        }
        values.update(body)
        return KTypeChoiceRequest(**values)


@pytest.fixture
def api(db: Connection) -> _Api:
    return _Api(db)


def test_a_choice_is_stored_with_its_evidence_and_shown_by_the_lookup(
    api: _Api, db: Connection
) -> None:
    vehicle_id = _vehicle(db)
    before = api.matching.lookup_vehicle(vehicle_id)
    assert before.choice is None and (before.effective_ktype, before.effective_source) == (None, None)

    answer, created = api.service.record(
        vehicle_id, api.send(vehicle_id, ktype="B", reason=" seen on the car ")
    )

    assert created
    lookup = api.matching.lookup_vehicle(vehicle_id)
    assert answer == lookup
    choice = lookup.choice
    assert choice is not None
    assert (choice.status, choice.ktype, choice.reviewer) == ("chosen", "B", "Ada")
    assert choice.reason == "seen on the car"
    assert (choice.needs_review, choice.stale_reasons, choice.history_count) == (False, [], 1)
    assert (lookup.effective_ktype, lookup.effective_source) == ("B", "person")
    # The matcher's own outcome is still what it was.
    assert (lookup.terminal, lookup.top_ktype) == (before.terminal, before.top_ktype)

    (stored,) = chain(db, vehicle_id)
    assert stored.catalog_batch == "batch-1"
    assert stored.code_version == "abc1234"
    assert (stored.automatic_terminal, stored.automatic_ktype) == ("review_required", "A")
    assert stored.evidence_fingerprint == before.evidence_fingerprint
    evidence = stored.evidence
    assert evidence["inputs"]["power_kw"] == 133
    assert [item["ktype"] for item in evidence["candidates"]] == ["A", "B"]
    assert evidence["versions"]["code"] == "abc1234"
    assert evidence["versions"]["rule_set"] == "rules-test-1"
    text = Jsonb(evidence).obj.__repr__()
    assert vehicle_id not in text and "ABC123" not in text and "YV1BW84S1F1234567" not in text

    # The list and the record show it, from the copy.
    row = api.vehicles.search(
        [VehicleCondition(field="match_state", values=["manual", "manual_none"])], "",
        cursor=None, limit=10,
    ).items
    assert [(item.vehicle_id, item.ktype, item.match_state) for item in row] == [
        (vehicle_id, "B", "manual")
    ]
    assert "ktype" in row[0].review_fields
    field = next(item for item in api.vehicles.record(vehicle_id).fields if item.field == "ktype")
    assert field.source is not None
    assert (field.source.source, field.source.ref) == ("review", str(choice.choice_id))


def test_a_car_nobody_decided_costs_no_choice_read(api: _Api, db: Connection) -> None:
    """The live server is small: the car's own read says whether there is a choice."""

    vehicle_id, other = _vehicle(db), _other_vehicle(db)
    api.matching.lookup_vehicle(vehicle_id)
    api.matching.lookup("ABC123")
    assert api.choices.reads == 0

    api.service.record(vehicle_id, api.send(vehicle_id))
    reads = api.choices.reads
    assert api.matching.lookup_vehicle(vehicle_id).choice is not None
    assert api.matching.lookup("ABC123").choice is not None
    assert api.choices.reads == reads + 2
    # A withdrawn choice is still read: the next choice must supersede it.
    api.service.record(vehicle_id, api.send(vehicle_id, "withdraw"))
    reads = api.choices.reads
    assert api.matching.lookup_vehicle(vehicle_id).choice is not None
    assert api.matching.lookup_vehicle(other).choice is None
    assert api.choices.reads == reads + 1


def test_a_stale_choice_is_flagged_and_never_changed(api: _Api, db: Connection) -> None:
    vehicle_id = _vehicle(db)
    api.service.record(vehicle_id, api.send(vehicle_id))
    rows = _count(db)

    def flags() -> tuple[list[str], list[tuple[str, Any, Any]]]:
        lookup = api.matching.lookup_vehicle(vehicle_id)
        choice = lookup.choice
        assert choice is not None and (lookup.effective_ktype, choice.ktype) == ("A", "A")
        assert choice.needs_review == bool(choice.stale_reasons)
        return list(choice.stale_reasons), [
            (item.field, item.then, item.now) for item in choice.changed_inputs
        ]

    assert flags() == ([], [])

    db.execute("UPDATE core.vehicles SET power_kw = 110 WHERE vehicle_id = %s", (vehicle_id,))
    db.commit()
    assert flags() == (["evidence_changed"], [("power_kw", 133, 110)])
    db.execute("UPDATE core.vehicles SET power_kw = 133 WHERE vehicle_id = %s", (vehicle_id,))
    db.commit()

    api.batch = "batch-2"
    assert flags() == (["catalog_batch_changed"], [])
    api.batch = "batch-1"

    api.evaluator.candidates = ("B", "C")
    assert flags() == (["ktype_not_a_candidate"], [])
    del api.catalog["A"]
    assert flags() == (["ktype_not_in_catalog"], [])
    gone = api.matching.lookup_vehicle(vehicle_id).choice
    assert gone is not None and gone.chosen_candidate is not None
    assert gone.chosen_candidate["ktype"] == "A"

    api.evaluator.candidates = ("A", "B")
    assert flags() == ([], [])
    assert _count(db) == rows
    assert _copy(db, vehicle_id)[:2] == ("A", "manual")


def test_none_of_these_is_flagged_when_a_new_candidate_appears(api: _Api, db: Connection) -> None:
    vehicle_id = _vehicle(db)
    answer, _ = api.service.record(vehicle_id, api.send(vehicle_id, "none"))

    assert answer.choice is not None and answer.choice.status == "none"
    assert (answer.effective_ktype, answer.effective_source) == (None, "person")
    api.evaluator.candidates = ("A",)  # one dropping out does not invalidate "none"
    assert api.matching.lookup_vehicle(vehicle_id).choice.stale_reasons == []  # type: ignore[union-attr]
    api.evaluator.candidates = ("A", "C")
    assert api.matching.lookup_vehicle(vehicle_id).choice.stale_reasons == [  # type: ignore[union-attr]
        "new_candidates"
    ]
    assert _copy(db, vehicle_id)[:2] == (None, "manual_none")


def test_keep_change_and_withdraw_append_and_clear_the_flag(api: _Api, db: Connection) -> None:
    vehicle_id = _vehicle(db)
    api.service.record(vehicle_id, api.send(vehicle_id))
    api.batch = "batch-2"
    assert api.matching.lookup_vehicle(vehicle_id).choice.needs_review  # type: ignore[union-attr]

    kept, _ = api.service.record(vehicle_id, api.send(vehicle_id))
    assert kept.choice is not None
    assert (kept.choice.needs_review, kept.choice.history_count) == (False, 2)

    withdrawn, _ = api.service.record(vehicle_id, api.send(vehicle_id, "withdraw", reason="wrong"))
    assert withdrawn.choice is not None and withdrawn.choice.status == "withdrawn"
    assert (withdrawn.effective_ktype, withdrawn.effective_source) == (None, None)
    assert _copy(db, vehicle_id)[:2] == (None, None)

    # A choice after a withdrawal supersedes the withdrawal: one chain per car.
    again, _ = api.service.record(vehicle_id, api.send(vehicle_id, ktype="B"))
    assert again.choice is not None and again.choice.history_count == 4
    history = api.service.history(vehicle_id, with_evidence=True)
    assert [(entry.action, entry.ktype) for entry in history.entries] == [
        ("choose", "B"), ("withdraw", None), ("choose", "A"), ("choose", "A")
    ]
    assert history.current_choice_id == again.choice.choice_id
    assert history.entries[0].evidence is not None
    assert history.entries[0].evidence["catalog_batch"] == "batch-2"
    assert api.service.history(vehicle_id).entries[0].evidence is None
    assert api.service.history(_other_vehicle(db)).entries == []


def test_a_retry_is_answered_even_after_the_car_changed(api: _Api, db: Connection) -> None:
    vehicle_id = _vehicle(db)
    request = api.send(vehicle_id)
    api.service.record(vehicle_id, request)
    db.execute("UPDATE core.vehicles SET power_kw = 110 WHERE vehicle_id = %s", (vehicle_id,))
    db.commit()
    try:
        answer, created = api.service.record(vehicle_id, request)

        assert not created
        assert answer.choice is not None and answer.choice.choice_id == request.operation_id
        assert answer.choice.stale_reasons == ["evidence_changed"]
        assert _count(db) == 1
        # A new operation built on the old screen is refused instead.
        stale_screen = request.model_copy(
            update={"operation_id": uuid4(), "supersedes_choice_id": request.operation_id}
        )
        with pytest.raises(EvidenceChangedError):
            api.service.record(vehicle_id, stale_screen)
        assert _count(db) == 1
    finally:
        db.execute("UPDATE core.vehicles SET power_kw = 133 WHERE vehicle_id = %s", (vehicle_id,))
        db.commit()


def test_a_choice_writes_nothing_that_promotion_or_the_ledger_reads(
    api: _Api, db: Connection
) -> None:
    def counts() -> list[int]:
        found = []
        for table in ("core.match_routing_decisions", "core.match_decision_heads",
                      "core.enrichment_ledger"):
            row = db.execute(f"SELECT count(*) FROM {table}").fetchone()
            assert row is not None
            found.append(int(row[0]))
        db.commit()
        return found

    vehicle_id = _vehicle(db)
    before = counts()
    api.service.record(vehicle_id, api.send(vehicle_id))
    api.service.record(vehicle_id, api.send(vehicle_id, "none"))
    api.service.record(vehicle_id, api.send(vehicle_id, "withdraw"))

    assert counts() == before
    assert before[:2] == [0, 0]
    assert _copy(db, vehicle_id)[:4] == (None, None, None, None)
