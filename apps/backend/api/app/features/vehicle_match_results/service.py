"""Matching statistics and car lists, read from stored results.

Read-only, and never the matcher: a request here costs a few grouped queries
whatever the number of cars. Keeping the stored results current is the
refresher's job (`refresh.py`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from pydantic import ValidationError

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
    ReviewerRuleChange,
    ReviewerRuleChanges,
    StateCount,
    ValueCount,
)
from api.app.features.vehicle_match_results.summaries import (
    SUMMARY_COUNTS,
    SUMMARY_OVERVIEW,
    StoredSummaries,
)
from api.app.features.vehicle_matching.service import CANDIDATE_LIMIT
from api.app.features.vehicles.schemas import VehicleFilter
from api.app.features.vehicles.service import terms
from ingestion.vehicle_match_results import StoredRun

#: The strip or the breakdown: both are answered from a kept count the same way.
_Counted = TypeVar("_Counted", MatchResultCounts, MatchResultOverview)

_OPERATOR_WORDS = {
    "equals": "=",
    "not_equals": "is not",
    "contains": "contains",
    "starts_with": "starts with",
    "gte": "is at least",
    "lte": "is at most",
    "is_empty": "is empty",
}


def conditions_in_words(conditions: object) -> str:
    """A rule's stored conditions as one line: `brand contains KIA and is_4wd = 0`."""

    if not isinstance(conditions, list):
        return ""
    parts: list[str] = []
    for condition in conditions:
        if not isinstance(condition, dict):
            continue
        values = condition.get("values")
        if not values and condition.get("value") not in (None, ""):
            values = [condition["value"]]
        operator = _OPERATOR_WORDS.get(str(condition.get("operator") or "equals"), "=")
        said = " or ".join(str(value) for value in values or [])
        parts.append(" ".join(part for part in (str(condition.get("field")), operator, said) if part))
    return " and ".join(parts)


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
    def __init__(
        self, repository: MatchResultRepository, summaries: StoredSummaries | None = None
    ) -> None:
        self._repository = repository
        #: Without it every look counts the cars (tests, a database without the table).
        self._summaries = summaries

    def counts(self, vehicle_filter: VehicleFilter) -> MatchResultCounts:
        """The strip above the list, from the kept count when there is one."""

        return self._kept(SUMMARY_COUNTS, vehicle_filter, MatchResultCounts, self.count_now)

    def overview(self, vehicle_filter: VehicleFilter) -> MatchResultOverview:
        """The breakdown, from the kept count when there is one."""

        return self._kept(SUMMARY_OVERVIEW, vehicle_filter, MatchResultOverview, self.overview_now)

    def _kept(
        self,
        kind: str,
        vehicle_filter: VehicleFilter,
        model: type[_Counted],
        count_now: Callable[[VehicleFilter], _Counted],
    ) -> _Counted:
        if self._summaries is None:
            return count_now(vehicle_filter)

        def count() -> dict[str, Any]:
            return count_now(vehicle_filter).model_dump(mode="json")

        kept = self._summaries.get(kind, vehicle_filter, count)
        try:
            counted = model.model_validate(kept.payload)
        except ValidationError:
            # Kept by code that counted something else: count again rather than fail.
            kept = self._summaries.recount(kind, vehicle_filter, count)
            counted = model.model_validate(kept.payload)
        return counted.model_copy(
            update={"counted_at": kept.counted_at, "updating": kept.updating}
        )

    def recount(self, vehicle_filter: VehicleFilter) -> tuple[MatchResultCounts, MatchResultOverview]:
        """Count the filter's cars now and keep both counts: after a load or a long run."""

        if self._summaries is None:
            return self.count_now(vehicle_filter), self.overview_now(vehicle_filter)
        counts = self._summaries.recount(
            SUMMARY_COUNTS, vehicle_filter,
            lambda: self.count_now(vehicle_filter).model_dump(mode="json"),
        )
        overview = self._summaries.recount(
            SUMMARY_OVERVIEW, vehicle_filter,
            lambda: self.overview_now(vehicle_filter).model_dump(mode="json"),
        )
        return (
            MatchResultCounts.model_validate(counts.payload),
            MatchResultOverview.model_validate(overview.payload),
        )

    def count_now(self, vehicle_filter: VehicleFilter) -> MatchResultCounts:
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

    def overview_now(self, vehicle_filter: VehicleFilter) -> MatchResultOverview:
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
            resolved_only_fit=counts["resolved_only_fit"],
            not_matchable_reasons=[
                ReasonCount(reason=reason, cars=cars)
                for reason, cars in counts["not_matchable_reasons"]
            ],
            changed_since_matched=counts["changed_since_matched"],
            catalog_batches=_values(counts["catalog_batches"]),
            matcher_versions=_values(counts["matcher_versions"]),
            latest_run=_run(counts["latest_run"]),
        )

    def reviewer_rules(self, limit: int) -> ReviewerRuleChanges:
        return ReviewerRuleChanges(
            rules=[
                ReviewerRuleChange(
                    rule_id=row["rule_id"],
                    status=row["status"],
                    author=row["author"],
                    applied_by=row["applied_by"],
                    retired_by=row["retired_by"],
                    created_at=row["created_at"],
                    applied_at=row["applied_at"],
                    retired_at=row["retired_at"],
                    conditions=conditions_in_words(row["conditions"]),
                    target_field=row["target_field"],
                    target_value=row["target_value"],
                    override=bool(row["override"]),
                    note=row["note"],
                    records_written=int(row["records_written"] or 0),
                    vehicles=int(row["vehicles"] or 0),
                    out_of_date=int(row["out_of_date"] or 0),
                )
                for row in self._repository.reviewer_rules(limit)
            ]
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
