"""Fill and refresh `core.vehicle_match_results`: the only place the matcher runs in bulk.

`result_for` reduces one evaluation to the stored row -- the same reading the
Matching summary makes of an evaluation (`bucket_for`, `separating_fields`,
`missing_on_car`), so the stored statistics and a live lookup of the same car
agree.

`MatchResultRefresher` matches cars page by page and commits each page, so a
run that stops is continued by running it again: the cars it finished are
current and are not selected a second time. A car whose `input_hash` is what
its row was computed from is not matched again unless a rebuild is asked for.
"""

from __future__ import annotations

import multiprocessing
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, Self
from uuid import UUID, uuid4

from psycopg import Connection

from api.app.features.vehicle_matching.repository import (
    CarRecord,
    matcher_input_hash,
    read_vehicle_car_records,
)
from api.app.features.vehicle_matching.service import (
    Matcher,
    _evidence,
    _is_compatible,
    bucket_for,
    missing_on_car,
    separating_fields,
)
from ingestion.tecdoc.match_run_adapters import MatchEvaluation, ResolvedMatchQuery
from ingestion.vehicle_match_results import (
    STATE_NONE,
    STATE_NOT_MATCHABLE,
    STATE_ONE_UNCONFIRMED,
    STATE_RESOLVED,
    STATE_SEVERAL,
    MatchResult,
    RunPins,
    add_progress,
    count_to_match,
    database_now,
    finish_run,
    mark_checked,
    start_run,
    stored_hashes,
    upsert_results,
    vehicle_ids_to_match,
)

_BUCKET_STATES = {
    "one": STATE_ONE_UNCONFIRMED,
    "several": STATE_SEVERAL,
    "none": STATE_NONE,
    "not_matchable": STATE_NOT_MATCHABLE,
}


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


def state_of(evaluation: MatchEvaluation) -> str:
    """`resolved` when the matcher accepted a KType; otherwise by its compatible candidates."""

    if evaluation.terminal == "resolved" and evaluation.top_candidate_reference:
        return STATE_RESOLVED
    return _BUCKET_STATES[bucket_for(evaluation)]


@dataclass(frozen=True)
class Outcome:
    """One evaluation reduced to what is stored, before the run's own columns are known."""

    state: str
    terminal: str
    ktype: str | None
    best_candidate_ktype: str | None
    confidence: float | None
    candidate_ktypes: tuple[str, ...]
    candidate_confidences: tuple[float, ...]
    separating_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]
    conflicting_fields: tuple[str, ...]
    reason_codes: tuple[str, ...]


def _confidence(value: object) -> float | None:
    if not isinstance(value, int | float):
        return None
    return min(1.0, max(0.0, float(value)))


def outcome_for(
    evaluation: MatchEvaluation, query: ResolvedMatchQuery | None, matcher: Matcher
) -> Outcome:
    state = state_of(evaluation)
    compatible = [c for c in evaluation.candidate_matches if _is_compatible(c)]
    separating = separating_fields(evaluation, matcher.catalog) if len(compatible) > 1 else []
    best = evaluation.top_candidate_reference
    conflicting: list[str] = []
    if evaluation.candidate_matches:
        top = evaluation.candidate_matches[0]
        best = best or str(top["candidate_reference"])
        if not compatible:
            conflicting = [str(name) for name in _evidence(top).get("conflicting_fields") or []]
    return Outcome(
        state=state,
        terminal=evaluation.terminal,
        ktype=evaluation.top_candidate_reference if state == STATE_RESOLVED else None,
        best_candidate_ktype=best,
        confidence=_confidence(evaluation.confidence),
        candidate_ktypes=tuple(str(c["candidate_reference"]) for c in compatible),
        candidate_confidences=tuple(_confidence(c.get("confidence")) or 0.0 for c in compatible),
        separating_fields=tuple(separating),
        missing_fields=tuple(missing_on_car(separating, query)),
        conflicting_fields=tuple(conflicting),
        reason_codes=tuple(evaluation.reason_codes),
    )


def result_for(
    vehicle_id: str,
    outcome: Outcome,
    *,
    input_hash: str,
    pins: RunPins,
    run_id: UUID,
    evaluated_at: datetime,
) -> MatchResult:
    return MatchResult(
        vehicle_id=vehicle_id,
        state=outcome.state,
        terminal=outcome.terminal,
        ktype=outcome.ktype,
        best_candidate_ktype=outcome.best_candidate_ktype,
        confidence=outcome.confidence,
        candidate_count=len(outcome.candidate_ktypes),
        candidate_ktypes=list(outcome.candidate_ktypes),
        candidate_confidences=list(outcome.candidate_confidences),
        separating_fields=list(outcome.separating_fields),
        missing_fields=list(outcome.missing_fields),
        conflicting_fields=list(outcome.conflicting_fields),
        reason_codes=list(outcome.reason_codes),
        catalog_batch=pins.catalog_batch,
        matcher_version=pins.matcher_version,
        input_hash=input_hash,
        run_id=run_id,
        evaluated_at=evaluated_at,
    )


def evaluate_one(matcher: Matcher, car: CarRecord) -> Outcome:
    evaluation, query = matcher.evaluate(car.record)
    return outcome_for(evaluation, query, matcher)


# The matcher is built once in the parent and inherited by forked workers, so
# the catalog is loaded once, not per worker (as the match impact report does).
_WORKER_MATCHER: Matcher | None = None


def _evaluate_in_worker(car: CarRecord) -> Outcome:
    assert _WORKER_MATCHER is not None
    return evaluate_one(_WORKER_MATCHER, car)


class Evaluators:
    """Evaluates pages of cars, on forked workers when asked for more than one.

    The pool lives as long as the run, so each worker's memo of evaluations
    carries over from page to page. Workers fork: POSIX only.
    """

    def __init__(self, matcher: Matcher, *, workers: int = 1) -> None:
        self._matcher = matcher
        self._workers = workers
        self._pool: Any = None

    def __enter__(self) -> Self:
        if self._workers > 1:
            global _WORKER_MATCHER
            _WORKER_MATCHER = self._matcher
            self._pool = multiprocessing.get_context("fork").Pool(self._workers)
        return self

    def __exit__(self, *_: object) -> None:
        global _WORKER_MATCHER
        if self._pool is not None:
            self._pool.terminate()
            self._pool.join()
            self._pool = None
        _WORKER_MATCHER = None

    def evaluate(self, cars: Sequence[CarRecord]) -> list[Outcome]:
        if self._pool is None or len(cars) < 2:
            return [evaluate_one(self._matcher, car) for car in cars]
        return list(self._pool.map(_evaluate_in_worker, cars, chunksize=32))


@dataclass
class RefreshCounts:
    run_id: UUID
    target: int
    #: Cars the matcher ran for; their rows were written.
    evaluated: int = 0
    #: Cars read again and found to be, to the matcher, what their row was computed from.
    unchanged: int = 0


def _pages(ids: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for start in range(0, len(ids), size):
        yield ids[start : start + size]


class MatchResultRefresher:
    def __init__(
        self,
        connection_factory: ConnectionFactory,
        matcher: Callable[[], Matcher],
        matcher_version: str,
        *,
        page_size: int = 1000,
    ) -> None:
        self._connection_factory = connection_factory
        self._matcher = matcher
        self._matcher_version = matcher_version.strip() or "unknown"
        self._page_size = page_size

    def pins(self) -> RunPins:
        matcher = self._matcher()
        return RunPins(matcher.batch_id, self._matcher_version, matcher.rule_set_version)

    def refresh_vehicles(
        self,
        vehicle_ids: Sequence[str],
        *,
        force: bool = False,
        mode: str = "vehicles",
        workers: int = 1,
        progress: Callable[[RefreshCounts], None] | None = None,
    ) -> RefreshCounts:
        """Bring these cars' rows up to date; `force` matches them even when unchanged."""

        ids = list(dict.fromkeys(vehicle_ids))
        return self._run(mode, len(ids), lambda _: _pages(ids, self._page_size), force=force,
                         workers=workers, progress=progress)

    def refresh_scope(
        self,
        *,
        rebuild: bool = False,
        scope: str | None = "passenger",
        registered_only: bool = True,
        workers: int = 1,
        limit: int | None = None,
        progress: Callable[[RefreshCounts], None] | None = None,
    ) -> RefreshCounts:
        """Match every new or stale car of the scope; `rebuild` matches every car.

        A rebuild that stops is continued by a plain run only for the cars it
        had not reached if the catalog batch changed; otherwise run it again.
        """

        catalog_batch = self._matcher().batch_id
        with self._connection_factory() as connection:
            target = count_to_match(
                connection, catalog_batch=catalog_batch, only_stale=not rebuild,
                scope=scope, registered_only=registered_only,
            )
        if limit is not None:
            target = min(target, limit)

        def pages(connection_factory: ConnectionFactory) -> Iterator[Sequence[str]]:
            after: str | None = None
            left = limit
            while left is None or left > 0:
                size = self._page_size if left is None else min(self._page_size, left)
                with connection_factory() as connection:
                    ids = vehicle_ids_to_match(
                        connection, catalog_batch=catalog_batch, only_stale=not rebuild,
                        after=after, limit=size, scope=scope, registered_only=registered_only,
                    )
                if not ids:
                    return
                yield ids
                after = ids[-1]
                if left is not None:
                    left -= len(ids)

        return self._run("all" if rebuild else "stale", target, pages, force=rebuild,
                         workers=workers, progress=progress)

    def _run(
        self,
        mode: str,
        target: int,
        pages: Callable[[ConnectionFactory], Iterator[Sequence[str]]],
        *,
        force: bool,
        workers: int,
        progress: Callable[[RefreshCounts], None] | None,
    ) -> RefreshCounts:
        matcher = self._matcher()
        pins = self.pins()
        counts = RefreshCounts(run_id=uuid4(), target=target)
        with self._connection_factory() as connection:
            start_run(connection, counts.run_id, mode, pins, target=target)
            connection.commit()
        try:
            with Evaluators(matcher, workers=workers) as evaluators:
                for ids in pages(self._connection_factory):
                    evaluated, unchanged = self._page(ids, pins, counts.run_id, evaluators, force)
                    counts.evaluated += evaluated
                    counts.unchanged += unchanged
                    if progress is not None:
                        progress(counts)
        except BaseException as error:
            with self._connection_factory() as connection:
                finish_run(connection, counts.run_id, "failed", error=type(error).__name__)
                connection.commit()
            raise
        with self._connection_factory() as connection:
            finish_run(connection, counts.run_id, "completed")
            connection.commit()
        return counts

    def _page(
        self,
        ids: Sequence[str],
        pins: RunPins,
        run_id: UUID,
        evaluators: Evaluators,
        force: bool,
    ) -> tuple[int, int]:
        """Read, match and store one page in one transaction."""

        with self._connection_factory() as connection:
            checked_at = database_now(connection)
            cars = read_vehicle_car_records(connection, ids)
            hashes = {str(car.vehicle_id): matcher_input_hash(car.record) for car in cars}
            stored = {} if force else stored_hashes(connection, list(hashes))
            current = (pins.catalog_batch,)
            unchanged = [
                vehicle_id
                for vehicle_id, digest in hashes.items()
                if stored.get(vehicle_id) == (digest, *current)
            ]
            skip = set(unchanged)
            todo = [car for car in cars if str(car.vehicle_id) not in skip]
            outcomes = evaluators.evaluate(todo)
            results = [
                result_for(
                    str(car.vehicle_id), outcome, input_hash=hashes[str(car.vehicle_id)],
                    pins=pins, run_id=run_id, evaluated_at=checked_at,
                )
                for car, outcome in zip(todo, outcomes, strict=True)
            ]
            upsert_results(connection, results)
            mark_checked(connection, unchanged, checked_at)
            add_progress(connection, run_id, evaluated=len(results), unchanged=len(unchanged))
            connection.commit()
        return len(results), len(unchanged)
