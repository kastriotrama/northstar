"""One correction for many cars: what the database guarantees, and the flow end to end.

Runs on a throwaway database built the way production builds vehicles, with the
real repositories and the real matcher on a small made-up catalog. Covers the
decisions migration and its definition-level verifier, database-enforced
immutability and chain rules (also when several rows arrive in one statement),
the checks that read a JSON key refusing a row that lacks it, the foreign key
from a car's correction row to the decision that wrote it, the scope queries,
the check, and applying, replaying, skipping and undoing a decision.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import Connection
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from api.app.features.vehicle_corrections.decision_repository import (
    DecisionRepository,
    VehiclesBusyError,
)
from api.app.features.vehicle_corrections.decision_service import (
    DecisionService,
    HarmsMoreThanItFixesError,
    NothingToApplyError,
    OperationReusedError,
)
from api.app.features.vehicle_corrections.preview import PreviewJobs
from api.app.features.vehicle_corrections.repository import (
    CorrectionRepository,
    EvidenceChangedError,
)
from api.app.features.vehicle_corrections.schemas import (
    CorrectionPreview,
    CorrectionRequest,
    DecisionRequest,
    DecisionWithdrawRequest,
    PreviewRequest,
    ScopesRequest,
)
from api.app.features.vehicle_corrections.service import CorrectionService, ReasonRequiredError
from api.app.features.vehicle_ktype_choices.repository import KTypeChoiceRepository
from api.app.features.vehicle_ktype_choices.schemas import KTypeChoiceRequest
from api.app.features.vehicle_ktype_choices.service import KTypeChoiceService
from api.app.features.vehicle_matching.repository import (
    VehicleMatchingRepository,
    matcher_input_hash,
)
from api.app.features.vehicle_matching.service import Matcher, SummaryJobs, VehicleMatchingService
from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator
from ingestion.vehicle_core_merge import VehicleState
from ingestion.vehicle_core_query import compile_vehicle_filter
from ingestion.vehicle_core_store import load_vehicles, save_vehicles
from ingestion.vehicle_core_ts import backfill_vehicle_core
from ingestion.vehicle_correction_decision_migrations import (
    COLUMNS,
    VEHICLE_CORRECTION_DECISION_MIGRATION_STATEMENTS,
    VehicleCorrectionDecisionSchemaContractError,
    run_vehicle_correction_decision_migrations,
    verify_vehicle_correction_decision_schema_contract,
)
from ingestion.vehicle_correction_decisions import (
    DecisionChangedError,
    NewDecisionEvent,
    append_event,
    decision,
    member_correction_id,
)
from ingestion.vehicle_fact_correction_migrations import (
    VehicleFactCorrectionSchemaContractError,
    run_vehicle_fact_correction_migrations,
    verify_vehicle_fact_correction_schema_contract,
)
from ingestion.vehicle_fact_corrections import NothingToWithdrawError, chains, group_rows
from ingestion.vehicle_ktype_choices import project_choices
from tests.integration.throwaway_database import throwaway_database
from tests.integration.vehicle_core_fixtures import (
    insert_ts_record,
    prepare_schema,
    project,
    volvo,
)

TABLE = "core.vehicle_correction_decisions"
CORRECTIONS = "core.vehicle_fact_corrections"
CHOICES = "core.vehicle_ktype_choices"

# Made-up cars. Four alike; one built later that resolves today and would lose
# its KType with the others' engine code; one with another power.
ALIKE = ("TST001", "TST002", "TST003", "TST004")
LATER = "TST099"
WEAKER = "TST098"

_PRISTINE: dict[str, VehicleState] = {}


def _ktype(reference: str, **overrides: Any) -> VehicleCandidate:
    values: dict[str, Any] = {
        "candidate_type": "TecDocKType", "year_from": 2007, "year_to": 2016,
        "fuels": frozenset({"diesel"}), "displacement_cc": 1969, "power_kw": 133,
        "bodyworks": frozenset({"estate"}),
    }
    values.update(overrides)
    return VehicleCandidate(reference, "VOLVO", "V70", **values)


# Two KTypes only an engine code tells apart, and a later one.
CATALOG = (
    _ktype("K1", engine_codes=frozenset({"D4204T14"})),
    _ktype("K2", engine_codes=frozenset({"D4204T23"})),
    _ktype("K4", engine_codes=frozenset({"D4204T14"}), year_from=2017, year_to=2020),
)


@pytest.fixture(scope="module")
def db() -> Iterator[Connection]:
    with throwaway_database("correction_decisions") as connection:
        prepare_schema(connection)
        for number, plate in enumerate(ALIKE, start=1):
            insert_ts_record(connection, volvo(vin=f"YV1BW84S1F12300{number:02d}", plate=plate))
        insert_ts_record(
            connection,
            volvo(vin="YV1BW84S1F1230099", plate=LATER, vehicle_year=2018,
                  registration_date="20180312", build_month="201802"),
        )
        insert_ts_record(connection, volvo(vin="YV1BW84S1F1230098", plate=WEAKER, kw="110"))
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
    """Each test starts with no decisions, no corrections and untouched cars."""

    yield
    db.rollback()
    for table in (CORRECTIONS, CHOICES, TABLE):
        db.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
        db.execute(f"DELETE FROM {table}")
        db.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")
    save_vehicles(db, copy.deepcopy(list(_PRISTINE.values())))
    project_choices(db, None)
    db.commit()
    verify_vehicle_correction_decision_schema_contract(db)
    verify_vehicle_fact_correction_schema_contract(db)
    db.commit()


def _vehicle(db: Connection, plate: str = "TST001") -> str:
    row = db.execute("SELECT vehicle_id FROM core.vehicles WHERE plate = %s", (plate,)).fetchone()
    assert row is not None
    db.commit()
    return str(row[0])


def _count(db: Connection, table: str = TABLE) -> int:
    row = db.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    db.commit()
    return int(row[0])


def _measured(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "affected": 4, "checked": 4, "complete": True, "gained": 4, "lost": 0, "moved": 0,
        "worse": 0,
    }
    values.update(overrides)
    return values


def _scope() -> dict[str, Any]:
    return {
        "kind": "like_this", "rung": 0, "anchor_value": None,
        "conditions": [{"field": "manufacturer", "operator": "equals", "values": ["Volvo"]}],
    }


def _event(**overrides: Any) -> NewDecisionEvent:
    event_id = overrides.pop("event_id", None) or uuid4()
    values: dict[str, Any] = {
        "event_id": event_id, "decision_id": event_id, "event": "apply",
        "supersedes_event_id": None, "field": "engine_code", "action": "set",
        "value": "D4204T23", "scope": _scope(),
        "scope_label": "All Volvo V70 cars with no engine code", "manufacturer": "Volvo",
        "model_family": "V70", "reviewer": "Ada", "reason": "seen on the engines",
        "catalog_batch": "batch-1", "code_version": "test", "measurement": _measured(),
    }
    values.update(overrides)
    return NewDecisionEvent(**values)


def _later(root: NewDecisionEvent, event: str = "withdraw", **overrides: Any) -> NewDecisionEvent:
    """An event on top of `root`: it carries none of what the root decided."""

    values: dict[str, Any] = {
        "event_id": uuid4(), "decision_id": root.decision_id, "event": event,
        "supersedes_event_id": root.event_id, "field": None, "action": None, "value": None,
        "scope": None, "scope_label": None, "manufacturer": None, "model_family": None,
    }
    values.update(overrides)
    return _event(**values)


_COLUMNS = tuple(column for column in COLUMNS if column != "created_at")


def _raw_values(new: NewDecisionEvent, position: int = 0, **overrides: Any) -> list[Any]:
    values: dict[str, Any] = {column: getattr(new, column, None) for column in _COLUMNS}
    values["chain_position"] = position
    values.update(overrides)
    values["scope"] = None if values["scope"] is None else Jsonb(values["scope"])
    values["measurement"] = Jsonb(values["measurement"])
    return [values[column] for column in _COLUMNS]


def _raw_insert_many(db: Connection, rows: list[list[Any]]) -> None:
    """Rows in ONE statement, past every Python check: only the database decides."""

    row = f"({', '.join(['%s'] * len(_COLUMNS))})"
    db.execute(
        f"INSERT INTO {TABLE} ({', '.join(_COLUMNS)}) VALUES {', '.join([row] * len(rows))}",
        [value for values in rows for value in values],
    )


def _refused(db: Connection, rows: list[list[Any]]) -> str | None:
    with pytest.raises(psycopg.errors.IntegrityError) as caught:
        _raw_insert_many(db, rows)
    db.rollback()
    return caught.value.diag.constraint_name


# ------------------------------------------------------------------ migration and drift


def test_the_migration_is_idempotent_and_its_contract_verifies(db: Connection) -> None:
    names = tuple(statement.name for statement in VEHICLE_CORRECTION_DECISION_MIGRATION_STATEMENTS)

    assert run_vehicle_correction_decision_migrations(db) == names
    assert run_vehicle_correction_decision_migrations(db) == names
    verify_vehicle_correction_decision_schema_contract(db)
    # The corrections table references it and verifies on top.
    run_vehicle_fact_correction_migrations(db)
    run_vehicle_fact_correction_migrations(db)


_LINK = "vehicle_correction_decisions_supersedes_previous_fkey"
_POSITION_KEY = "vehicle_correction_decisions_position_key"
_MEASURED_FIRST = "vehicle_correction_decisions_measured_before_applied"
_SHAPE = "vehicle_correction_decisions_measurement_shape"

_DRIFTS: dict[str, tuple[str, ...]] = {
    "a dropped check": (
        f"ALTER TABLE {TABLE} DROP CONSTRAINT vehicle_correction_decisions_reason_required",),
    "a disabled row trigger": (
        f"ALTER TABLE {TABLE} DISABLE TRIGGER vehicle_correction_decisions_append_only",),
    "a disabled truncate trigger": (
        f"ALTER TABLE {TABLE} DISABLE TRIGGER vehicle_correction_decisions_append_only_truncate",),
    "a dropped link foreign key": (f"ALTER TABLE {TABLE} DROP CONSTRAINT {_LINK}",),
    "a column made mandatory": (f"ALTER TABLE {TABLE} ALTER COLUMN reason SET NOT NULL",),
    "an extra column": (f"ALTER TABLE {TABLE} ADD COLUMN note TEXT",),
    "a dropped index": ("DROP INDEX core.vehicle_correction_decisions_make_model_idx",),
    # Same-named but weaker objects: caught by definition, not by name.
    "an applied-on-anything check that is not NULL-safe": (
        f"ALTER TABLE {TABLE} DROP CONSTRAINT {_MEASURED_FIRST}",
        (
            f"ALTER TABLE {TABLE} ADD CONSTRAINT {_MEASURED_FIRST} "
            "CHECK (event = 'propose' OR measurement ->> 'complete' = 'true')"
        ),
    ),
    "a measurement shape that is not NULL-safe": (
        f"ALTER TABLE {TABLE} DROP CONSTRAINT {_SHAPE}",
        (
            f"ALTER TABLE {TABLE} ADD CONSTRAINT {_SHAPE} CHECK ("
            "jsonb_typeof(measurement) = 'object' "
            "AND jsonb_typeof(measurement -> 'affected') = 'number' "
            "AND jsonb_typeof(measurement -> 'checked') = 'number' "
            "AND jsonb_typeof(measurement -> 'gained') = 'number' "
            "AND jsonb_typeof(measurement -> 'lost') = 'number' "
            "AND jsonb_typeof(measurement -> 'moved') = 'number' "
            "AND jsonb_typeof(measurement -> 'worse') = 'number')"
        ),
    ),
    "a link that does not carry the position": (
        f"ALTER TABLE {TABLE} DROP CONSTRAINT {_LINK}",
        (
            f"ALTER TABLE {TABLE} ADD CONSTRAINT {_LINK} "
            f"FOREIGN KEY (supersedes_event_id) REFERENCES {TABLE} (event_id)"
        ),
    ),
    "an unlogged table": (
        f"ALTER TABLE {CORRECTIONS} DROP CONSTRAINT vehicle_fact_corrections_group_fkey",
        f"ALTER TABLE {TABLE} SET UNLOGGED",
    ),
}


@pytest.mark.parametrize("drift", sorted(_DRIFTS))
def test_schema_drift_is_caught_by_definition(db: Connection, drift: str) -> None:
    for statement in _DRIFTS[drift]:
        db.execute(statement)

    with pytest.raises(VehicleCorrectionDecisionSchemaContractError):
        verify_vehicle_correction_decision_schema_contract(db)
    db.rollback()
    verify_vehicle_correction_decision_schema_contract(db)


@pytest.mark.parametrize(
    "drift",
    [
        f"ALTER TABLE {CORRECTIONS} DROP CONSTRAINT vehicle_fact_corrections_group_fkey",
        # The corrections table's own JSON check, weakened to one a missing key passes.
        (
            f"ALTER TABLE {CORRECTIONS} DROP CONSTRAINT vehicle_fact_corrections_evidence_shape; "
            f"ALTER TABLE {CORRECTIONS} ADD CONSTRAINT vehicle_fact_corrections_evidence_shape "
            "CHECK (jsonb_typeof(evidence) = 'object' AND evidence ? 'schema' "
            "AND jsonb_typeof(evidence -> 'automatic') = 'object')"
        ),
    ],
)
def test_the_corrections_table_must_name_decisions_and_check_its_json_null_safe(
    db: Connection, drift: str
) -> None:
    for statement in drift.split("; "):
        db.execute(statement)

    with pytest.raises(VehicleFactCorrectionSchemaContractError):
        verify_vehicle_fact_correction_schema_contract(db)
    db.rollback()
    verify_vehicle_fact_correction_schema_contract(db)


def test_the_database_refuses_to_change_or_remove_an_event(db: Connection) -> None:
    stored, created = append_event(db, _event())
    db.commit()
    assert created

    for statement in (
        f"UPDATE {TABLE} SET reason = 'other'",
        f"DELETE FROM {TABLE}",
        f"TRUNCATE {TABLE} CASCADE",
    ):
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            db.execute(statement)
        db.rollback()
    assert _count(db) == 1 and decision(db, stored.decision_id) is not None
    db.rollback()


# ---------------------------------------------------------------- chain rules


def test_an_event_is_idempotent_by_its_operation_id(db: Connection) -> None:
    event = _event()

    first, created = append_event(db, event)
    again, created_again = append_event(db, event)
    db.commit()

    assert (created, created_again, first) == (True, False, again)
    with pytest.raises(OperationReusedError):
        append_event(db, _event(event_id=event.event_id, reviewer="Bo"))
    db.rollback()
    assert _count(db) == 1


def test_the_chain_is_linear_and_a_decision_is_withdrawn_once(db: Connection) -> None:
    root = _event(event="propose", reason=None, measurement=_measured(complete=False))
    append_event(db, root)
    withdrawal = _later(root)
    stored, _ = append_event(db, withdrawal)
    db.commit()

    assert (stored.chain_position, stored.supersedes_event_id) == (1, root.event_id)
    record = decision(db, root.decision_id)
    assert record is not None and (record.status, record.member_count) == ("withdrawn", 0)
    with pytest.raises(NothingToWithdrawError):
        append_event(db, _later(root, supersedes_event_id=withdrawal.event_id))
    with pytest.raises(DecisionChangedError):
        append_event(db, _later(root))  # built on a head that is no longer the head
    db.rollback()
    assert _count(db) == 2


def test_one_statement_cannot_store_a_cycle_a_second_root_or_a_foreign_link(
    db: Connection,
) -> None:
    root, other = _event(), _event()
    first, second = _later(root), _later(root)

    # Two events that supersede each other.
    assert _refused(db, [
        _raw_values(first, 1, supersedes_event_id=second.event_id),
        _raw_values(second, 1, supersedes_event_id=first.event_id),
    ]) == _POSITION_KEY
    assert _refused(db, [
        _raw_values(root),
        _raw_values(first, 1, supersedes_event_id=second.event_id),
        _raw_values(second, 2, supersedes_event_id=first.event_id),
    ]) == _LINK
    # A chain that hangs on nothing, and a link into another decision.
    assert _refused(db, [_raw_values(first, 1)]) == _LINK
    assert _refused(db, [
        _raw_values(root), _raw_values(other),
        _raw_values(first, 1, supersedes_event_id=other.event_id),
    ]) == _LINK
    # A second root, a root that is not the decision, a later event that claims to be one.
    assert _refused(db, [_raw_values(root), _raw_values(root, event_id=uuid4())]) in {
        _POSITION_KEY, "vehicle_correction_decisions_root_is_the_decision"}
    assert _refused(db, [_raw_values(root, event_id=uuid4())]) == (
        "vehicle_correction_decisions_root_is_the_decision")
    assert _refused(db, [_raw_values(root), _raw_values(first, 1, supersedes_event_id=None)]) == (
        "vehicle_correction_decisions_root_supersedes_nothing")
    # In any row order the well-formed chain passes.
    _raw_insert_many(db, [_raw_values(first, 1), _raw_values(root)])
    db.commit()
    assert _count(db) == 2


_ROOT_RULE = "vehicle_correction_decisions_root_carries_decision"


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        # Nothing is applied on a partial check -- and a check that does not say is partial.
        ({"measurement": _measured(complete=False)}, _MEASURED_FIRST),
        ({"measurement": {k: v for k, v in _measured().items() if k != "complete"}},
         _MEASURED_FIRST),
        ({"measurement": _measured(complete="yes")}, _MEASURED_FIRST),
        # Every measured number must be there: a missing key fails, it does not pass as NULL.
        *(({"measurement": {k: v for k, v in _measured().items() if k != number}}, _SHAPE)
          for number in ("affected", "checked", "gained", "lost", "moved", "worse")),
        ({"measurement": _measured(worse="0")}, _SHAPE),
        ({"measurement": _measured(affected=None)}, _SHAPE),
        ({"scope": {"kind": "like_this", "conditions": []}},
         "vehicle_correction_decisions_scope_shape"),
        ({"scope": {"conditions": [], "anchor_value": None}},
         "vehicle_correction_decisions_scope_shape"),
        ({"scope": {"kind": "like_this", "conditions": {}, "anchor_value": None}},
         "vehicle_correction_decisions_scope_shape"),
        ({"reason": None}, "vehicle_correction_decisions_reason_required"),
        ({"reason": "x" * 1001}, "vehicle_correction_decisions_reason_required"),
        ({"reviewer": " "}, "vehicle_correction_decisions_reviewer_nonempty"),
        ({"reviewer": "x" * 121}, "vehicle_correction_decisions_reviewer_nonempty"),
        ({"event": "withdraw"}, "vehicle_correction_decisions_event_fits_position"),
        ({"event": "replace"}, "vehicle_correction_decisions_event_values"),
        ({"action": "withdraw", "value": None}, "vehicle_correction_decisions_action_values"),
        ({"value": None}, "vehicle_correction_decisions_value_matches_action"),
        ({"action": "ignore"}, "vehicle_correction_decisions_value_matches_action"),
        ({"field": None}, _ROOT_RULE),
        ({"scope": None}, _ROOT_RULE),
        ({"scope_label": None}, _ROOT_RULE),
        ({"manufacturer": None}, _ROOT_RULE),
        ({"manufacturer": " "}, "vehicle_correction_decisions_make_nonempty"),
        ({"scope_label": " "}, "vehicle_correction_decisions_label_nonempty"),
        ({"field": "Engine Code"}, "vehicle_correction_decisions_field_format"),
        ({"catalog_batch": ""}, "vehicle_correction_decisions_provenance_nonempty"),
    ],
)
def test_a_malformed_event_is_refused_by_a_named_constraint(
    db: Connection, overrides: dict[str, Any], constraint: str
) -> None:
    assert _refused(db, [_raw_values(_event(), **overrides)]) == constraint
    assert _count(db) == 0


def test_a_proposal_may_rest_on_a_partial_check_and_a_later_event_carries_no_decision(
    db: Connection,
) -> None:
    root = _event(event="propose", reason=None,
                  measurement={k: v for k, v in _measured().items() if k != "complete"})
    _raw_insert_many(db, [_raw_values(root)])
    db.commit()

    assert _refused(db, [_raw_values(_later(root), 1, field="engine_code")]) == _ROOT_RULE
    assert _refused(db, [_raw_values(_later(root, "propose"), 1)]) == (
        "vehicle_correction_decisions_event_fits_position")
    assert _count(db) == 1


# --------------------------------------------------------- the services, end to end


class _Api:
    def __init__(self, db: Connection, *, max_cars: int = 500) -> None:
        def factory() -> AbstractContextManager[Connection]:
            return nullcontext(db)

        self.db = db
        self.evaluator = TecDocDryRunEvaluator(CATALOG)
        self.matcher = Matcher(
            "batch-1", self.evaluator, {item.candidate_reference: item for item in CATALOG}
        )
        self.corrections = CorrectionRepository(factory)
        self.choices = KTypeChoiceRepository(factory)
        self.matching = VehicleMatchingService(
            VehicleMatchingRepository(factory), lambda: self.matcher, SummaryJobs(),
            choices=self.choices, corrections=self.corrections,
        )
        self.service = CorrectionService(
            self.corrections, self.matching.lookup_vehicle, "abc1234",
            what_if=self.matching.what_if,
        )
        self.choice_service = KTypeChoiceService(
            self.choices, self.matching.lookup_vehicle, "abc1234"
        )
        self.repository = DecisionRepository(factory)
        self.jobs = PreviewJobs()
        self.decisions = DecisionService(
            self.repository, self.matching.lookup_vehicle, self.matching.matcher, self.jobs,
            "abc1234", max_cars=max_cars, max_seconds=60, run_in_background=False,
        )

    def check(self, vehicle_id: str, kind: str = "same_data", **body: Any) -> CorrectionPreview:
        values: dict[str, Any] = {
            "field": "engine_code", "action": "set", "value": "D4204T23",
            "scope": {"kind": kind},
            "evidence_fingerprint": self.matching.lookup_vehicle(vehicle_id).evidence_fingerprint,
        }
        values.update(body)
        return self.decisions.start_preview(vehicle_id, PreviewRequest(**values))

    def decide(self, preview: CorrectionPreview, event: str = "apply", **body: Any) -> Any:
        values: dict[str, Any] = {
            "operation_id": uuid4(), "preview_id": preview.preview_id, "event": event,
            "reviewer": "Ada", "reason": "seen on the engines",
        }
        values.update(body)
        return self.decisions.decide(DecisionRequest(**values))

    def correct(self, vehicle_id: str, action: str = "set", field: str = "engine_code",
                **body: Any) -> Any:
        lookup = self.matching.lookup_vehicle(vehicle_id)
        head = next((item for item in lookup.corrections if item.field == field), None)
        values: dict[str, Any] = {
            "operation_id": uuid4(), "field": field, "action": action,
            "value": "D4204T14" if action == "set" else None, "reviewer": "Bo",
            "supersedes_correction_id": head.correction_id if head else None,
            "evidence_fingerprint": lookup.evidence_fingerprint,
        }
        values.update(body)
        return self.service.record(vehicle_id, CorrectionRequest(**values))[0]


@pytest.fixture
def api(db: Connection) -> _Api:
    return _Api(db)


def _engine(db: Connection, plate: str) -> tuple[Any, Any]:
    row = db.execute(
        "SELECT engine_code, field_sources ->> 'engine_code' FROM core.vehicles WHERE plate = %s",
        (plate,),
    ).fetchone()
    db.commit()
    assert row is not None
    return row[0], row[1]


def test_the_scopes_are_counted_and_is_empty_selects_cars_without_a_value(
    api: _Api, db: Connection
) -> None:
    anchor = _vehicle(db)

    options = api.decisions.scopes(
        anchor, ScopesRequest(field="engine_code", action="set", value="D4204T23")).scopes

    assert [(item.kind, item.rung, item.count, item.too_broad) for item in options] == [
        ("this_car", None, 1, False), ("same_data", None, 4, False), ("like_this", 0, 5, False)]
    assert options[1].label == "The 4 cars with exactly the same data"
    assert options[2].label == (
        "All Volvo V70 cars with no engine code, power 133 kW, displacement 1969 cc and "
        "fuel diesel")
    like = options[2]
    assert [(item.field, item.operator) for item in like.conditions][:2] == [
        ("manufacturer", "equals"), ("vehicle_scope", "equals")]
    assert ("engine_code", "is_empty", []) in [
        (item.field, item.operator, item.values) for item in like.conditions]
    db.rollback()

    # `is_empty` is NULL or the empty string, for text; NULL for a number.
    def count(*terms: Any) -> int:
        predicate = compile_vehicle_filter(list(terms))
        row = db.execute(
            f"SELECT count(*) FROM core.vehicles AS v WHERE {predicate.sql}", predicate.parameters
        ).fetchone()
        db.rollback()
        assert row is not None
        return int(row[0])

    assert count(("engine_code", "is_empty", ())) == 6
    assert count(("manufacturer", "is_empty", ())) == 0
    assert count(("production_year", "is_empty", ())) == 0
    assert count(("manufacturer", "equals", ("Volvo",)), ("fuel_secondary", "is_empty", ())) == 6
    db.execute("ALTER TABLE core.vehicles DISABLE TRIGGER USER")
    db.execute("UPDATE core.vehicles SET engine_code = '' WHERE plate = 'TST001'")
    db.execute("UPDATE core.vehicles SET engine_code = 'X' WHERE plate = 'TST002'")
    predicate = compile_vehicle_filter([("engine_code", "is_empty", ())])
    row = db.execute(
        f"SELECT count(*) FROM core.vehicles AS v WHERE {predicate.sql}", predicate.parameters
    ).fetchone()
    db.rollback()
    assert row == (5,)
    # A scope that takes too long to count is offered without a count, never run to the end.
    slow = DecisionRepository(lambda: nullcontext(db), count_timeout="1ms")
    db.execute("SELECT 1")
    db.rollback()
    assert slow.count_scope([("manufacturer", "equals", ("Volvo",))]) in {None, 6}


def test_a_check_evaluates_every_car_of_the_scope_and_writes_nothing(
    api: _Api, db: Connection
) -> None:
    anchor = _vehicle(db)
    remembered = api.evaluator.cache_size

    preview = api.check(anchor, "like_this")

    assert (preview.status, preview.error) == ("done", None)
    assert (preview.affected, preview.checked, preview.complete) == (5, 5, True)
    counts = preview.counts.model_dump()
    assert (counts["gained"], counts["lost"]) == (4, 1)
    assert sum(counts[name] for name in (
        "gained", "lost", "moved", "worse", "same", "still_unresolved", "no_effect",
        "already_corrected", "not_like_this")) == 5
    # The corrected field is the engine code: the engine proxy checks nothing.
    assert preview.engine_check.model_dump() == {"agree": 0, "differ": 0, "unchecked": 4}
    assert (preview.would_write, preview.can_apply, preview.blocked_by) == (4, True, [])
    lost = api.decisions.preview_cars(preview.preview_id, "lost", offset=0, limit=10).cars
    assert [(item.plate, item.before.ktype if item.before else None,
             item.after.terminal if item.after else None) for item in lost] == [
        (LATER, "K4", "hard_conflict")]
    # The anchor's lookup is the only evaluation the matcher remembered ...
    assert api.evaluator.cache_size == remembered + 1
    # ... and nothing was written anywhere.
    assert (_count(db), _count(db, CORRECTIONS)) == (0, 0)
    assert _engine(db, "TST002") == (None, None)

    # The cars alike are evaluated once: they hand the matcher the same.
    same = api.check(anchor)
    assert (same.affected, same.counts.gained, same.complete) == (4, 4, True)
    assert same.scope.label == "The 4 cars with exactly the same data"


def test_apply_writes_exactly_the_checked_cars_and_their_copies(api: _Api, db: Connection) -> None:
    anchor = _vehicle(db)
    preview = api.check(anchor, "like_this")
    operation = uuid4()

    result, created = api.decide(preview, operation_id=operation)

    assert created
    assert (result.decision_id, result.status, result.written) == (operation, "applied", 4)
    assert result.skipped.model_dump() == {"changed_since_check": 0, "corrected_meanwhile": 0}
    assert result.written_by_outcome == {"gained": 4}
    db.rollback()
    members = group_rows(db, operation)
    ids = {plate: _vehicle(db, plate) for plate in (*ALIKE, LATER, WEAKER)}
    # The lost car is left out, the car outside the scope was never a candidate.
    assert sorted(row.vehicle_id for row in members) == sorted(ids[plate] for plate in ALIKE)
    for row in members:
        assert row.correction_id == member_correction_id(operation, row.vehicle_id, "engine_code")
        assert (row.field, row.action, row.value, row.chain_position) == (
            "engine_code", "set", "D4204T23", 0)
        assert (row.reviewer, row.reason, row.group_id) == ("Ada", "seen on the engines", operation)
        assert (row.previous_value, row.previous_source) == (None, "registry")
        assert (row.automatic_terminal, row.catalog_batch, row.code_version) == (
            "review_required", "batch-1", "abc1234")
        assert row.vehicle_id not in repr(row.evidence)
    # The vehicle copies, exactly as one car's correction writes them.
    for plate in ALIKE:
        value, source = _engine(db, plate)
        assert value == "D4204T23"
        assert source == f"correction:{member_correction_id(operation, ids[plate], 'engine_code')}"
    assert _engine(db, LATER) == _engine(db, WEAKER) == (None, None)

    record = decision(db, operation)
    db.rollback()
    assert record is not None and (record.status, record.member_count) == ("applied", 4)
    measured = record.root.measurement
    assert (measured["complete"], measured["written"], measured["gained"], measured["lost"]) == (
        True, 4, 4, 1)
    assert (record.root.manufacturer, record.root.model_family) == ("Volvo", "V70")

    # Each car is matched on it at once, and its lookup names the decision.
    for plate in ALIKE:
        lookup = api.matching.lookup_vehicle(ids[plate])
        assert (lookup.terminal, lookup.top_ktype, lookup.copy_drift) == ("resolved", "K2", [])
        (state,) = lookup.corrections
        assert state.decision is not None
        assert state.decision.model_dump() == {
            "decision_id": operation, "member_count": 4, "reviewer": "Ada",
            "scope_label": preview.scope.label,
        }
    assert api.matching.lookup_vehicle(ids[LATER]).corrections == []
    listed = api.decisions.decisions(10).decisions
    assert [(item.decision_id, item.status, item.member_count) for item in listed] == [
        (operation, "applied", 4)]
    assert api.decisions.decision(operation) == listed[0]
    db.rollback()


def test_a_replay_writes_nothing_twice_and_other_content_is_refused(
    api: _Api, db: Connection
) -> None:
    preview = api.check(_vehicle(db))
    operation = uuid4()
    first, created = api.decide(preview, operation_id=operation)

    replay, created_again = api.decide(preview, operation_id=operation)

    assert (created, created_again, replay) == (True, False, first)
    assert (_count(db), _count(db, CORRECTIONS)) == (1, 4)
    with pytest.raises(OperationReusedError):
        api.decide(preview, operation_id=operation, reviewer="Bo")
    # The same cars again under another operation: every one carries a correction now.
    with pytest.raises(NothingToApplyError):
        api.decide(preview)
    db.rollback()
    assert (_count(db), _count(db, CORRECTIONS)) == (1, 4)


def test_a_car_that_changed_or_was_corrected_since_the_check_is_skipped(
    api: _Api, db: Connection
) -> None:
    anchor = _vehicle(db)
    preview = api.check(anchor)
    # After the check: a person corrects one car's engine code, and another car's
    # data changes under everyone (a re-import fills its drive type).
    corrected = _vehicle(db, "TST002")
    api.correct(corrected)
    changed = _vehicle(db, "TST003")
    api.correct(changed, field="power_kw", value="140")
    db.rollback()

    result, _ = api.decide(preview)

    assert result.written == 2
    assert result.skipped.model_dump() == {"changed_since_check": 1, "corrected_meanwhile": 1}
    db.rollback()
    assert sorted(row.vehicle_id for row in group_rows(db, result.decision_id)) == sorted(
        [anchor, _vehicle(db, "TST004")])
    # The person's own word stands.
    assert _engine(db, "TST002")[0] == "D4204T14"
    assert chains(db, changed).keys() == {"power_kw"}
    db.rollback()


def test_harmed_cars_are_written_only_when_included_and_a_reason_is_required(
    api: _Api, db: Connection
) -> None:
    preview = api.check(_vehicle(db), "like_this")

    with pytest.raises(ReasonRequiredError):
        api.decide(preview, reason=None)
    assert _count(db) == 0
    result, _ = api.decide(preview, include_changed=True)

    assert (result.written, result.written_by_outcome) == (5, {"gained": 4, "lost": 1})
    db.rollback()
    later = _vehicle(db, LATER)
    assert later in {row.vehicle_id for row in group_rows(db, result.decision_id)}
    (row,) = chains(db, later)["engine_code"]
    assert (row.automatic_terminal, row.automatic_ktype) == ("resolved", "K4")
    assert api.matching.lookup_vehicle(later).terminal == "hard_conflict"
    db.rollback()


def test_a_check_that_harms_as_many_as_it_fixes_is_only_a_proposal(
    api: _Api, db: Connection
) -> None:
    # Three of the cars alike get their engine code from a person first: their
    # vehicles carry it, so the scope "no engine code" no longer holds them.
    for plate in ALIKE[1:]:
        api.correct(_vehicle(db, plate), value="D4204T23")
    db.rollback()
    preview = api.check(_vehicle(db), "like_this")
    assert (preview.affected, preview.counts.gained, preview.counts.lost) == (2, 1, 1)
    assert preview.blocked_by == ["harms_more_than_it_fixes"]

    with pytest.raises(HarmsMoreThanItFixesError):
        api.decide(preview, include_changed=True)
    result, created = api.decide(preview, "propose", reason=None)

    assert created and (result.status, result.written) == ("proposed", 0)
    assert (_count(db), _count(db, CORRECTIONS)) == (1, 3)
    record = decision(db, result.decision_id)
    db.rollback()
    assert record is not None and (record.status, record.member_count) == ("proposed", 0)


def test_a_capped_check_cannot_be_applied_and_the_database_would_refuse_it_too(
    db: Connection,
) -> None:
    api = _Api(db, max_cars=2)
    preview = api.check(_vehicle(db))

    assert (preview.affected, preview.checked, preview.complete, preview.stopped_by) == (
        4, 2, False, "cap")
    assert preview.blocked_by == ["not_all_cars_checked"]
    result, _ = api.decide(preview, "propose")
    assert result.status == "proposed"
    db.rollback()
    # Past the service: an application resting on that measurement is refused.
    record = decision(db, result.decision_id)
    assert record is not None
    with pytest.raises(psycopg.errors.CheckViolation) as refused:
        append_event(db, _event(measurement=record.root.measurement))
    db.rollback()
    assert refused.value.diag.constraint_name == _MEASURED_FIRST


@contextmanager
def _connection(db: Connection) -> Iterator[Connection]:
    with psycopg.connect(make_conninfo(db.info.dsn, password=db.info.password)) as connection:
        yield connection


def test_a_locked_car_answers_busy_and_nothing_is_written(api: _Api, db: Connection) -> None:
    preview = api.check(_vehicle(db))
    busy = DecisionService(
        DecisionRepository(lambda: _connection(db), lock_timeout="200ms"),
        api.matching.lookup_vehicle, api.matching.matcher, api.jobs, "abc1234",
    )
    request = DecisionRequest(operation_id=uuid4(), preview_id=preview.preview_id,
                              event="apply", reviewer="Ada", reason="seen")
    db.execute("SELECT 1 FROM core.vehicles WHERE plate = 'TST003' FOR NO KEY UPDATE")

    with pytest.raises(VehiclesBusyError):
        busy.decide(request)
    db.rollback()

    assert (_count(db), _count(db, CORRECTIONS)) == (0, 0)
    # The same operation id goes through once the car is free.
    result, created = busy.decide(request)
    assert created and result.written == 4


def test_withdraw_restores_every_member_and_leaves_what_a_person_changed(
    api: _Api, db: Connection
) -> None:
    preview = api.check(_vehicle(db))
    applied, _ = api.decide(preview)
    db.rollback()
    # A person replaces one car's value afterwards: that car is theirs now.
    theirs = _vehicle(db, "TST004")
    api.correct(theirs, value="D4204T14", confirm_change=True)
    db.rollback()
    operation = uuid4()
    request = DecisionWithdrawRequest(operation_id=operation, reviewer="Bo", reason="wrong group")

    with pytest.raises(ReasonRequiredError):
        api.decisions.withdraw(applied.decision_id, DecisionWithdrawRequest(
            operation_id=uuid4(), reviewer="Bo", reason=None))
    result, created = api.decisions.withdraw(applied.decision_id, request)

    assert created
    assert (result.status, result.withdrawn, result.left_changed, result.member_count) == (
        "withdrawn", 3, 1, 4)
    assert result.skipped == {"changed_by_person": 1}
    db.rollback()
    for plate in ALIKE[:3]:
        vehicle_id = _vehicle(db, plate)
        head, original = chains(db, vehicle_id)["engine_code"]
        assert (head.action, head.group_id, head.supersedes_correction_id) == (
            "withdraw", operation, original.correction_id)
        assert (head.reviewer, head.reason, head.previous_value) == ("Bo", "wrong group", "D4204T23")
        # The copy is retracted and the car is matched as before the decision.
        assert _engine(db, plate) == (None, None)
        lookup = api.matching.lookup_vehicle(vehicle_id)
        assert (lookup.terminal, lookup.copy_drift) == ("review_required", [])
        assert lookup.corrections[0].status == "withdrawn"
        assert lookup.corrections[0].decision is not None
        assert lookup.corrections[0].decision.reviewer == "Bo"
    assert _engine(db, "TST004")[0] == "D4204T14"
    assert chains(db, theirs)["engine_code"][0].group_id is None

    # A replay answers the same; a second withdrawal has nothing to withdraw.
    again, created_again = api.decisions.withdraw(applied.decision_id, request)
    assert (created_again, again) == (False, result)
    with pytest.raises(OperationReusedError):
        api.decisions.withdraw(applied.decision_id, request.model_copy(update={"reviewer": "Cy"}))
    with pytest.raises(NothingToWithdrawError):
        api.decisions.withdraw(applied.decision_id, DecisionWithdrawRequest(
            operation_id=uuid4(), reviewer="Bo", reason="again"))
    db.rollback()
    assert api.decisions.decision(applied.decision_id).status == "withdrawn"
    assert (_count(db), _count(db, CORRECTIONS)) == (2, 8)


def test_one_car_of_a_decision_is_undone_by_the_one_car_withdraw(api: _Api, db: Connection) -> None:
    applied, _ = api.decide(api.check(_vehicle(db)))
    db.rollback()
    one = _vehicle(db, "TST002")

    answer = api.correct(one, "withdraw")

    assert (answer.terminal, answer.corrections[0].status) == ("review_required", "withdrawn")
    assert _engine(db, "TST002") == (None, None)
    result, _ = api.decisions.withdraw(applied.decision_id, DecisionWithdrawRequest(
        operation_id=uuid4(), reviewer="Bo", reason="the rest too"))
    assert (result.withdrawn, result.left_changed) == (3, 1)
    db.rollback()


def test_a_ktype_choice_on_a_member_is_kept_and_flagged_afterwards(
    api: _Api, db: Connection
) -> None:
    chosen_car = _vehicle(db, "TST003")
    shown = api.matching.lookup_vehicle(chosen_car)
    api.choice_service.record(chosen_car, KTypeChoiceRequest(
        operation_id=uuid4(), action="choose", ktype="K1", reviewer="Ada",
        evidence_fingerprint=shown.evidence_fingerprint))
    db.rollback()

    preview = api.check(_vehicle(db))
    assert (preview.counts.with_choice, preview.counts.choice_would_disagree) == (1, 1)
    api.decide(preview)
    db.rollback()

    after = api.matching.lookup_vehicle(chosen_car)
    assert after.choice is not None
    assert (after.choice.ktype, after.choice.needs_review, after.choice.stale_reasons) == (
        "K1", True, ["evidence_changed"])
    # The person's choice stands; the matcher says K2.
    assert (after.effective_ktype, after.effective_source, after.top_ktype) == ("K1", "person", "K2")
    db.rollback()


# ------------------------------------------------------- one car, under its lock


def test_a_one_car_write_is_refused_when_the_car_changed_before_its_lock(
    api: _Api, db: Connection
) -> None:
    vehicle_id = _vehicle(db)
    lookup = api.matching.lookup_vehicle(vehicle_id)
    request = CorrectionRequest(
        operation_id=uuid4(), field="engine_code", action="set", value="D4204T23",
        reviewer="Ada", evidence_fingerprint=lookup.evidence_fingerprint)
    db.rollback()

    class _Racing(CorrectionRepository):
        """Another writer changes the car between the service's lookup and the lock."""

        def record(self, new: Any, *, checked_on: str | None = None) -> Any:
            assert checked_on == lookup.matcher_input_hash
            db.execute("ALTER TABLE core.vehicles DISABLE TRIGGER USER")
            db.execute("UPDATE core.vehicles SET power_kw = 140 WHERE vehicle_id = %s",
                       (new.vehicle_id,))
            db.execute("ALTER TABLE core.vehicles ENABLE TRIGGER USER")
            db.commit()
            return super().record(new, checked_on=checked_on)

    racing = CorrectionService(
        _Racing(lambda: nullcontext(db)), api.matching.lookup_vehicle, "abc1234",
        what_if=api.matching.what_if)

    with pytest.raises(EvidenceChangedError):
        racing.record(vehicle_id, request)
    db.rollback()
    assert _count(db, CORRECTIONS) == 0
    # On the car as it is now, the same correction goes through -- no matcher ran for the check.
    fresh = api.matching.lookup_vehicle(vehicle_id)
    assert fresh.matcher_input_hash != lookup.matcher_input_hash
    car = VehicleMatchingRepository(lambda: nullcontext(db)).vehicle_car_records([vehicle_id])[0]
    assert matcher_input_hash(car.record) == fresh.matcher_input_hash
    _, created = api.service.record(vehicle_id, request.model_copy(update={
        "operation_id": uuid4(), "evidence_fingerprint": fresh.evidence_fingerprint}))
    assert created
    db.rollback()


def test_a_copy_no_standing_correction_is_behind_is_skipped_and_reported(
    api: _Api, db: Connection
) -> None:
    vehicle_id = _vehicle(db)
    api.correct(vehicle_id, value="D4204T23")
    db.rollback()
    stale = load_vehicles(db, [vehicle_id])[vehicle_id]
    db.rollback()
    assert api.matching.lookup_vehicle(vehicle_id).copy_drift == []
    api.correct(vehicle_id, "withdraw")
    db.rollback()

    # A writer that read the car before the withdrawal saves its older state.
    save_vehicles(db, [stale])
    db.commit()

    assert _engine(db, "TST001")[0] == "D4204T23"
    lookup = api.matching.lookup_vehicle(vehicle_id)
    # The table is the truth: the matcher is not handed the stale copy.
    assert lookup.copy_drift == ["engine_code"]
    assert lookup.inputs is not None and lookup.inputs.engine_code is None
    assert lookup.terminal == "review_required"
    db.rollback()


# ------------------------------------------------------------------- the HTTP contract


def test_the_http_contract_end_to_end(api: _Api, db: Connection) -> None:
    from fastapi.testclient import TestClient

    from api.app.features.vehicle_corrections.router import (
        get_correction_service,
        get_decision_service,
    )
    from api.app.features.vehicle_matching.router import get_service
    from api.app.main import create_app

    app = create_app()
    app.dependency_overrides[get_service] = lambda: api.matching
    app.dependency_overrides[get_correction_service] = lambda: api.service
    app.dependency_overrides[get_decision_service] = lambda: api.decisions
    client = TestClient(app)
    anchor = _vehicle(db)
    base = "/v1/vehicle-corrections"

    def code(response: Any) -> tuple[int, str]:
        return response.status_code, response.json()["detail"]["code"]

    shown = client.get("/v1/vehicles/matching/lookup", params={"vehicle_id": anchor}).json()
    correction = {"field": "engine_code", "action": "set", "value": "D4204T23"}
    scopes = client.post(f"/v1/vehicles/{anchor}/corrections/scopes", json=correction).json()
    assert [(item["kind"], item["count"]) for item in scopes["scopes"]] == [
        ("this_car", 1), ("same_data", 4), ("like_this", 5)]

    # The option as the scopes call gave it goes back with the check.
    started = client.post(f"/v1/vehicles/{anchor}/corrections/preview", json={
        **correction, "scope": {**scopes["scopes"][2], "narrow": []},
        "evidence_fingerprint": shown["evidence_fingerprint"]})
    assert started.status_code == 202
    preview = client.get(f"{base}/previews/{started.json()['preview_id']}").json()
    assert (preview["status"], preview["complete"], preview["would_write"]) == ("done", True, 4)
    assert (preview["counts"]["gained"], preview["counts"]["lost"]) == (4, 1)
    cars = client.get(f"{base}/previews/{preview['preview_id']}/cars",
                      params={"outcome": "lost", "limit": 10}).json()
    assert [(item["plate"], item["after"]["terminal"]) for item in cars["cars"]] == [
        (LATER, "hard_conflict")]

    assert code(client.post(f"/v1/vehicles/{anchor}/corrections/preview", json={
        **correction, "scope": {"kind": "same_data"}, "evidence_fingerprint": "f" * 64,
    })) == (409, "evidence_changed")
    assert code(client.post(f"/v1/vehicles/{anchor}/corrections/preview", json={
        **correction, "field": "production_year", "value": "2014",
        "scope": {"kind": "like_this"}})) == (422, "scope_not_offered")

    body = {"operation_id": str(uuid4()), "preview_id": preview["preview_id"], "event": "apply",
            "include_changed": False, "reviewer": "Ada", "reason": None}
    assert code(client.post(f"{base}/decisions", json=body)) == (422, "reason_required")
    body["reason"] = "seen on the engines"
    applied = client.post(f"{base}/decisions", json=body)
    assert applied.status_code == 201
    assert (applied.json()["status"], applied.json()["written"]) == ("applied", 4)
    assert client.post(f"{base}/decisions", json=body).status_code == 200
    assert code(client.post(f"{base}/decisions", json={**body, "reviewer": "Bo"})) == (
        409, "operation_id_reused")
    assert code(client.post(f"{base}/decisions", json={
        **body, "operation_id": str(uuid4()), "preview_id": "gone"})) == (404, "preview_not_found")

    after = client.get("/v1/vehicles/matching/lookup", params={"vehicle_id": anchor}).json()
    assert (after["terminal"], after["top_ktype"]) == ("resolved", "K2")
    assert after["corrections"][0]["decision"] == {
        "decision_id": body["operation_id"], "scope_label": preview["scope"]["label"],
        "member_count": 4, "reviewer": "Ada"}
    listed = client.get(f"{base}/decisions").json()["decisions"]
    assert [(item["decision_id"], item["status"]) for item in listed] == [
        (body["operation_id"], "applied")]

    undo = {"operation_id": str(uuid4()), "reviewer": "Bo", "reason": "wrong group"}
    url = f"{base}/decisions/{body['operation_id']}/withdraw"
    assert code(client.post(url, json={**undo, "reason": None})) == (422, "reason_required")
    undone = client.post(url, json=undo)
    assert undone.status_code == 201
    assert (undone.json()["withdrawn"], undone.json()["left_changed"]) == (4, 0)
    assert client.post(url, json=undo).status_code == 200
    assert code(client.post(url, json={**undo, "operation_id": str(uuid4())})) == (
        422, "nothing_to_withdraw")
    assert code(client.post(f"{base}/decisions/{uuid4()}/withdraw", json=undo)) == (
        409, "operation_id_reused")
    assert code(client.post(f"{base}/decisions/{uuid4()}/withdraw", json={
        **undo, "operation_id": str(uuid4())})) == (404, "decision_not_found")
    assert client.get(f"{base}/decisions/{body['operation_id']}").json()["status"] == "withdrawn"
    restored = client.get("/v1/vehicles/matching/lookup", params={"vehicle_id": anchor}).json()
    assert restored["evidence_fingerprint"] == shown["evidence_fingerprint"]
    db.rollback()

