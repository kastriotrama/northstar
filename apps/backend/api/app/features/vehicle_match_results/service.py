"""Matching statistics and car lists, read from stored results.

Read-only, and never the matcher: a request here costs a few grouped queries
whatever the number of cars. Keeping the stored results current is the
refresher's job (`refresh.py`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from api.app.features.vehicle_match_results.repository import (
    OVERVIEW_STATES,
    STATE_CHOSEN,
    MatchResultRepository,
    narrowing_predicate,
)
from api.app.features.vehicle_match_results.schemas import (
    FieldCount,
    MatchResultCar,
    MatchResultCarPage,
    MatchResultCarsRequest,
    MatchResultCounts,
    MatchResultOverview,
    MatchRunInfo,
    ReasonCount,
    StateCount,
    ValueCount,
)
from api.app.features.vehicle_matching.service import CANDIDATE_LIMIT
from api.app.features.vehicles.schemas import VehicleFilter
from api.app.features.vehicles.service import terms
from ingestion.vehicle_match_results import StoredRun


def _fields(counts: Sequence[tuple[str, int]]) -> list[FieldCount]:
    return [FieldCount(field=name, cars=cars) for name, cars in counts]


def _values(counts: Sequence[tuple[str, int]]) -> list[ValueCount]:
    return [ValueCount(value=name, cars=cars) for name, cars in counts]


def _run(run: StoredRun | None) -> MatchRunInfo | None:
    if run is None:
        return None
    return MatchRunInfo(
        run_id=str(run.run_id),
        mode=run.mode,
        status=run.status,
        catalog_batch=run.catalog_batch,
        matcher_version=run.matcher_version,
        target=run.target,
        evaluated=run.evaluated,
        unchanged=run.unchanged,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _several_counts(counts: Sequence[tuple[str, int]]) -> dict[str, int]:
    """Candidate counts as the Matching summary labels them: the cap reads "5+"."""

    labelled: dict[str, int] = {}
    for value, cars in sorted(counts, key=lambda item: int(item[0])):
        label = f"{CANDIDATE_LIMIT}+" if int(value) >= CANDIDATE_LIMIT else value
        labelled[label] = labelled.get(label, 0) + cars
    return labelled


def _car(row: dict[str, Any]) -> MatchResultCar:
    chosen = row["state"] == STATE_CHOSEN
    return MatchResultCar(
        vehicle_id=row["vehicle_id"],
        plate=row["plate"],
        vin=row["vin"],
        manufacturer=row["manufacturer"],
        model_family=row["model_family"],
        production_year=row["production_year"],
        state=row["state"],
        automatic_state=row["automatic_state"],
        terminal=row["terminal"],
        ktype=row["vehicle_ktype"] if chosen else row["automatic_ktype"],
        automatic_ktype=row["automatic_ktype"],
        best_candidate_ktype=row["best_candidate_ktype"],
        confidence=row["confidence"],
        candidate_ktypes=list(row["candidate_ktypes"] or []),
        candidate_confidences=list(row["candidate_confidences"] or []),
        separating_fields=list(row["separating_fields"] or []),
        missing_fields=list(row["missing_fields"] or []),
        conflicting_fields=list(row["conflicting_fields"] or []),
        reason_codes=list(row["reason_codes"] or []),
        evaluated_at=row["evaluated_at"],
        changed_since_matched=bool(row["changed_since_matched"]),
    )


class MatchResultService:
    def __init__(self, repository: MatchResultRepository) -> None:
        self._repository = repository

    def counts(self, vehicle_filter: VehicleFilter) -> MatchResultCounts:
        counts = self._repository.counts(terms(vehicle_filter.conditions), vehicle_filter.text)
        by_state = dict(counts["states"])
        return MatchResultCounts(
            total=sum(by_state.values()),
            states=[
                StateCount(state=state, cars=by_state.get(state, 0))  # type: ignore[arg-type]
                for state in OVERVIEW_STATES
            ],
            changed_since_matched=counts["changed_since_matched"],
        )

    def overview(self, vehicle_filter: VehicleFilter) -> MatchResultOverview:
        counts = self._repository.overview(terms(vehicle_filter.conditions), vehicle_filter.text)
        by_state = dict(counts["states"])
        return MatchResultOverview(
            total=sum(by_state.values()),
            # Every state is listed, a zero included, so a screen has a fixed set of tiles.
            states=[
                StateCount(state=state, cars=by_state.get(state, 0))  # type: ignore[arg-type]
                for state in OVERVIEW_STATES
            ],
            terminals=_values(counts["terminals"]),
            several_candidate_counts=_several_counts(counts["several_candidate_counts"]),
            candidate_limit=CANDIDATE_LIMIT,
            several_separating_fields=_fields(counts["several_separating_fields"]),
            several_missing_fields=_fields(counts["several_missing_fields"]),
            none_conflicting_fields=_fields(counts["none_conflicting_fields"]),
            none_without_candidates=counts["none_without_candidates"],
            not_matchable_reasons=[
                ReasonCount(reason=reason, cars=cars)
                for reason, cars in counts["not_matchable_reasons"]
            ],
            changed_since_matched=counts["changed_since_matched"],
            catalog_batches=_values(counts["catalog_batches"]),
            matcher_versions=_values(counts["matcher_versions"]),
            latest_run=_run(counts["latest_run"]),
        )

    def cars(self, request: MatchResultCarsRequest) -> MatchResultCarPage:
        total, rows = self._repository.cars(
            terms(request.conditions),
            request.text,
            state=request.state,
            narrowing=narrowing_predicate(
                missing_field=request.missing_field,
                separating_field=request.separating_field,
                conflicting_field=request.conflicting_field,
                reason=request.reason,
                ktype=request.ktype,
                candidate_count=request.candidate_count,
            ),
            after=request.after,
            limit=request.limit,
        )
        cars = [_car(row) for row in rows]
        return MatchResultCarPage(
            state=request.state,
            total=total,
            cars=cars,
            next_after=cars[-1].vehicle_id if len(cars) == request.limit else None,
        )
