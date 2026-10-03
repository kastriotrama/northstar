"""How one evaluation becomes a stored row, and how the read side labels and narrows."""

from __future__ import annotations

from typing import Any

import pytest

from api.app.features.vehicle_match_results.refresh import outcome_for, state_of
from api.app.features.vehicle_match_results.repository import (
    UnknownStateError,
    narrowing_predicate,
    state_predicate,
)
from api.app.features.vehicle_match_results.service import _several_counts
from api.app.features.vehicle_matching.service import CANDIDATE_LIMIT, Matcher
from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.tecdoc.match_run_adapters import MatchEvaluation, ResolvedMatchQuery


def _ktype(reference: str, power: int, drive: str | None = None) -> VehicleCandidate:
    return VehicleCandidate(
        candidate_reference=reference, manufacturer="VOLVO", model="V70",
        year_from=2013, year_to=2016, power_kw=power, drive_type=drive,
    )


CATALOG = {"A": _ktype("A", 133, "fwd"), "B": _ktype("B", 133, "awd"), "C": _ktype("C", 120)}
MATCHER = Matcher("batch-1", None, CATALOG)  # type: ignore[arg-type]
QUERY = ResolvedMatchQuery(
    key=("k",), scope_manufacturer="VOLVO", model_values=("V70",), year=2015,
    fuels=frozenset({"diesel"}), engine_code=None, displacement_cc=1969, power_kw=133,
    drive_type=None, bodywork=None, recovery_reason=None, source_context=(),
    source_model_resolution=None,
)


def _candidate(reference: str, *conflicting: str, confidence: float = 0.9) -> dict[str, Any]:
    return {
        "candidate_reference": reference,
        "candidate_type": "TecDocKType",
        "confidence": confidence,
        "evidence": {"conflicting_fields": list(conflicting)},
    }


def _evaluation(
    terminal: str, *candidates: dict[str, Any], reasons: tuple[str, ...] = ("match:scored",)
) -> MatchEvaluation:
    return MatchEvaluation(
        terminal,  # type: ignore[arg-type]
        reasons,
        top_candidate_reference=str(candidates[0]["candidate_reference"]) if candidates else None,
        candidate_matches=candidates,
        confidence=float(candidates[0]["confidence"]) if candidates else None,
    )


def test_an_accepted_ktype_is_resolved_even_when_others_were_compatible() -> None:
    evaluation = _evaluation("resolved", _candidate("A"), _candidate("B", confidence=0.6))
    outcome = outcome_for(evaluation, QUERY, MATCHER)
    assert outcome.state == "resolved"
    assert outcome.ktype == "A"
    assert outcome.candidate_ktypes == ("A", "B")
    assert outcome.candidate_confidences == (0.9, 0.6)


def test_several_names_the_fields_that_differ_and_the_ones_the_car_lacks() -> None:
    outcome = outcome_for(
        _evaluation("review_required", _candidate("A"), _candidate("B")), QUERY, MATCHER
    )
    assert outcome.state == "several"
    assert outcome.ktype is None
    assert outcome.best_candidate_ktype == "A"
    assert outcome.separating_fields == ("drive_type",)
    assert outcome.missing_fields == ("drive_type",)
    assert outcome.conflicting_fields == ()


def test_one_compatible_ktype_that_was_not_accepted_is_its_own_state() -> None:
    outcome = outcome_for(
        _evaluation("provisional", _candidate("A"), _candidate("C", "power_kw")), QUERY, MATCHER
    )
    assert outcome.state == "one_unconfirmed"
    assert outcome.ktype is None
    assert outcome.candidate_ktypes == ("A",)
    assert outcome.separating_fields == ()


def test_none_keeps_what_the_best_candidate_conflicts_on() -> None:
    outcome = outcome_for(
        _evaluation("hard_conflict", _candidate("C", "power_kw", "year")), QUERY, MATCHER
    )
    assert outcome.state == "none"
    assert outcome.best_candidate_ktype == "C"
    assert outcome.conflicting_fields == ("power_kw", "year")
    assert outcome.candidate_ktypes == ()


def test_none_without_any_candidate_has_no_best_candidate() -> None:
    outcome = outcome_for(_evaluation("review_required"), QUERY, MATCHER)
    assert outcome.state == "none"
    assert outcome.best_candidate_ktype is None


def test_a_car_stopped_before_matching_is_not_matchable() -> None:
    evaluation = _evaluation("normalization_review", reasons=("normalization_review_required",))
    assert state_of(evaluation) == "not_matchable"
    outcome = outcome_for(evaluation, None, MATCHER)
    assert outcome.reason_codes == ("normalization_review_required",)
    assert outcome.confidence is None


def test_a_confidence_outside_the_range_is_clamped_not_refused() -> None:
    outcome = outcome_for(
        _evaluation("review_required", _candidate("A", confidence=1.0000001), _candidate("B")),
        QUERY, MATCHER,
    )
    assert outcome.confidence == 1.0
    assert outcome.candidate_confidences[0] == 1.0


def test_candidate_counts_at_the_matchers_cap_read_as_or_more() -> None:
    counts = [(str(CANDIDATE_LIMIT), 7), ("2", 10), ("3", 4)]
    assert _several_counts(counts) == {"2": 10, "3": 4, f"{CANDIDATE_LIMIT}+": 7}


def test_a_state_list_is_narrowed_only_by_what_is_asked() -> None:
    assert narrowing_predicate().sql == "true"
    narrowed = narrowing_predicate(missing_field="drive_type", ktype="123", candidate_count=2)
    assert "m.missing_fields @> ARRAY[%s]::text[]" in narrowed.sql
    assert "m.candidate_ktypes @> ARRAY[%s]::text[]" in narrowed.sql
    assert narrowed.parameters == ["drive_type", "123", "123", "123", 2]


def test_a_persons_choice_is_not_counted_under_the_matchers_state() -> None:
    assert "v.match_state IS NULL" in state_predicate("several")
    assert state_predicate("chosen") == "v.match_state = 'manual'"
    with pytest.raises(UnknownStateError):
        state_predicate("resolved; DROP TABLE core.vehicles")
