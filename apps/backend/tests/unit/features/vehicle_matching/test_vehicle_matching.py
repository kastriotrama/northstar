"""Matching diagnostics: how an evaluation is read back, and the job around it.

The matcher itself is the audit's and has its own tests; these cover what this
feature adds -- the one/several/none reading, the gap it names, the rule overlay,
a person's corrections laid over the car and the job lifecycle -- with a scripted
evaluator standing in for the catalog.
"""

import copy
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.app.features.vehicle_matching.repository import (
    EVIDENCE_FALLBACK,
    MATCHER_FIELDS,
    CarRecord,
    Hypothetical,
    _vehicle_car_record,
    lay_hypothetical,
    matcher_input_hash,
    overlay_corrections,
    overlay_resolutions,
    overlay_vehicle,
    surrogate_record_id,
)
from api.app.features.vehicle_matching.router import get_service
from api.app.features.vehicle_matching.service import (
    JobCapacityError,
    Matcher,
    SummaryJobNotFoundError,
    SummaryJobs,
    VehicleMatchingService,
    VehicleNotFoundError,
    bucket_for,
    missing_on_car,
    outcome_of,
    separating_fields,
    verdict_of,
)
from api.app.features.vehicles.schemas import VehicleCondition, VehicleFilter
from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.match_run_service import MatchSourceRecord
from ingestion.tecdoc.match_run_adapters import (
    MatchEvaluation,
    ResolvedMatchQuery,
    TecDocDryRunEvaluator,
)
from ingestion.vehicle_fact_corrections import CorrectionHead


def _match(reference: str, *, conflicts: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "candidate_reference": reference,
        "candidate_type": "TecDocKType",
        "confidence": 0.9,
        "evidence": {
            "manufacturer": "VOLVO",
            "model": "XC60",
            "matched_fields": ["model"],
            "missing_fields": [],
            "conflicting_fields": list(conflicts),
        },
    }


def _evaluation(*candidates: dict[str, Any], scored: bool = True) -> MatchEvaluation:
    reasons = ("match:automatic_candidate_threshold_met",) if scored else ("policy:x",)
    return MatchEvaluation(
        "resolved" if scored else "policy_excluded",
        reasons,
        candidate_matches=tuple(candidates),
    )


def _ktype(reference: str, *, engines: frozenset[str], power: int = 140) -> VehicleCandidate:
    return VehicleCandidate(
        candidate_reference=reference,
        manufacturer="VOLVO",
        model="XC60",
        year_from=2017,
        year_to=2022,
        fuels=frozenset({"diesel"}),
        engine_codes=engines,
        power_kw=power,
    )


def _query(**overrides: Any) -> ResolvedMatchQuery:
    values: dict[str, Any] = {
        "key": ("k",),
        "scope_manufacturer": "VOLVO",
        "model_values": ("XC60",),
        "year": 2018,
        "fuels": frozenset({"diesel"}),
        "engine_code": None,
        "displacement_cc": 1969,
        "power_kw": 140,
        "drive_type": "awd",
        "bodywork": "suv",
        "recovery_reason": None,
        "source_context": (),
        "source_model_resolution": None,
    }
    values.update(overrides)
    return ResolvedMatchQuery(**values)


# ------------------------------------------------------------------------- the reading


def test_a_car_stopped_before_scoring_is_not_matchable() -> None:
    assert bucket_for(_evaluation(scored=False)) == "not_matchable"


def test_buckets_count_only_candidates_that_conflict_on_nothing() -> None:
    """A KType the car contradicts on power is not a match, however it scored."""

    assert bucket_for(_evaluation()) == "none"
    assert bucket_for(_evaluation(_match("A"), _match("B", conflicts=("power_kw",)))) == "one"
    assert bucket_for(_evaluation(_match("A"), _match("B"))) == "several"
    assert bucket_for(_evaluation(_match("A", conflicts=("bodywork",)))) == "none"


def test_separating_fields_name_what_differs_among_compatible_candidates() -> None:
    catalog = {
        "A": _ktype("A", engines=frozenset({"D4204T14"})),
        "B": _ktype("B", engines=frozenset({"D4204T23"})),
        # Differs on power too, but conflicts with the car, so it must not count.
        "C": _ktype("C", engines=frozenset({"B4204T"}), power=187),
    }
    evaluation = _evaluation(_match("A"), _match("B"), _match("C", conflicts=("power_kw",)))

    assert separating_fields(evaluation, catalog) == ["engine_code"]


def test_the_gap_is_a_separating_field_the_car_has_no_value_for() -> None:
    assert missing_on_car(["engine_code", "power_kw"], _query()) == ["engine_code"]
    assert missing_on_car(["engine_code"], _query(engine_code="D4204T14")) == []
    assert missing_on_car(["engine_code"], None) == []


# ------------------------------------------------------------------------- the overlay


def test_a_rules_value_outranks_the_derivation_it_corrects() -> None:
    """Same precedence as effective_value: a corrected car is matched as corrected."""

    merged, applied = overlay_resolutions(
        {"bodywork_form": "estate", "power_kw": 140}, {"bodywork_form": "suv", "engine_code": ""}
    )

    assert merged["bodywork_form"] == "suv"
    assert merged["power_kw"] == 140
    assert "engine_code" not in merged
    assert applied == ("bodywork_form",)


def test_a_vehicles_merged_values_replace_the_origin_derivation() -> None:
    """An engine code AIS supplied and a reviewer's correction both reach the matcher."""

    merged, overlaid = overlay_vehicle(
        {"bodywork_form": "estate", "power_kw": 140, "engine_code": None, "record_route": "x"},
        {"bodywork_form": "suv", "power_kw": 140, "engine_code": "D4204T14", "drive_type": None},
        {"bodywork_form": "review:rule-1", "engine_code": "ais:x@2026-09-19"},
        "transportstyrelsen",
    )

    assert merged["bodywork_form"] == "suv"
    assert merged["engine_code"] == "D4204T14"
    assert merged["record_route"] == "x"
    # The same value is not an overlay, and an empty vehicle field blanks nothing.
    assert overlaid == {"bodywork_form": "review", "engine_code": "ais"}
    assert "drive_type" not in merged


def test_a_vehicle_value_from_its_origin_record_is_labelled_with_the_origin() -> None:
    merged, overlaid = overlay_vehicle({}, {"manufacturer": "VOLVO"}, {}, "ais")

    assert merged == {"manufacturer": "VOLVO"}
    assert overlaid == {"manufacturer": "ais"}


def test_a_vehicle_without_a_ts_record_gets_a_stable_positive_record_id() -> None:
    first = surrogate_record_id("NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7G")

    assert first >= 1
    assert first == surrogate_record_id("NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7G")
    assert first != surrogate_record_id("NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7H")


# -------------------------------------------------------------- a person's corrections


def _set(value: str) -> CorrectionHead:
    return CorrectionHead("set", value, uuid4())


def _ignore() -> CorrectionHead:
    return CorrectionHead("ignore", None, uuid4())


def _withdrawn() -> CorrectionHead:
    return CorrectionHead("withdraw", None, uuid4())


def test_a_persons_corrections_are_the_last_layer_over_the_cars_values() -> None:
    heads = {"engine_code": _set("DFGA"), "power_kw": _set("110"), "model_family": _ignore()}

    merged, overlaid, applied = overlay_corrections(
        {"engine_code": "DPCA", "power_kw": 140, "model_family": "Golf", "bodywork_form": "estate"},
        {"engine_code": "review", "model_family": "rule", "bodywork_form": "ais"},
        heads,
    )

    # A set value replaces what was there, typed as the matcher reads it; an
    # ignored field is gone, so the matcher has no value for it.
    assert merged == {"engine_code": "DFGA", "power_kw": 110, "bodywork_form": "estate"}
    assert overlaid == {
        "engine_code": "correction", "power_kw": "correction", "model_family": "correction",
        "bodywork_form": "ais",
    }
    assert applied == heads


def test_a_car_nobody_corrected_and_a_withdrawn_correction_change_nothing() -> None:
    """A withdrawn head is no correction in force: the car's own data applies again."""

    values = {"engine_code": "DPCA", "production_year": 2015, "production_month": 2,
              "production_date": "2015-02", "fuel_match_tokens": ["diesel"]}
    sources = {"engine_code": "review", "production_month": "transportstyrelsen"}
    withdrawn = {name: _withdrawn() for name in (
        "engine_code", "production_year", "production_month", "fuel", "electrification_type")}

    for heads in ({}, withdrawn):
        merged, overlaid, applied = overlay_corrections(values, sources, heads)

        assert (merged, overlaid, applied) == (values, sources, {})
        assert merged is not values and overlaid is not sources


def test_a_correction_this_version_cannot_apply_is_left_alone() -> None:
    odd = {
        "colour": _set("red"),             # not a correctable field in this version
        "power_kw": _set("many"),          # a value that types to nothing
        "model_family": _set(" - "),
        "fuel": _set("steam"),
    }

    merged, overlaid, applied = overlay_corrections({"power_kw": 140}, {}, odd)

    assert (merged, overlaid, applied) == ({"power_kw": 140}, {}, {})


_DATED = {
    "production_year": 2015, "production_month": 2, "production_date": "2015-02",
    "production_date_precision": "month", "power_kw": 140,
}


def test_a_corrected_year_or_month_silences_the_normalizations_own_date() -> None:
    """The matcher falls back to that date for the build month, and it carries both."""

    sources = {"production_month": "transportstyrelsen"}

    merged, overlaid, _ = overlay_corrections(_DATED, sources, {"production_year": _set("2014")})
    assert merged == {"production_year": 2014, "production_month": 2, "power_kw": 140}
    assert overlaid == {"production_year": "correction", "production_month": "transportstyrelsen"}

    merged, overlaid, _ = overlay_corrections(_DATED, sources, {"production_year": _ignore()})
    assert merged == {"production_month": 2, "power_kw": 140}

    merged, overlaid, _ = overlay_corrections(_DATED, sources, {"production_month": _set("11")})
    assert merged == {"production_year": 2015, "production_month": 11, "power_kw": 140}
    assert overlaid == {"production_month": "correction"}

    merged, overlaid, _ = overlay_corrections(_DATED, sources, {"production_month": _ignore()})
    assert merged == {"production_year": 2015, "power_kw": 140}
    assert overlaid == {"production_month": "correction"}

    # Another field's correction leaves the date alone.
    merged, overlaid, _ = overlay_corrections(_DATED, sources, {"power_kw": _set("110")})
    assert merged == {**_DATED, "power_kw": 110}
    assert overlaid == {**sources, "power_kw": "correction"}


def test_a_corrected_fuel_replaces_the_carriers_and_the_tokens_together() -> None:
    car = {"energy_sources": ["petrol"], "fuel_match_tokens": ["diesel"], "power_kw": 140}
    sources = {"fuel_match_tokens": "ais"}

    merged, overlaid, _ = overlay_corrections(car, sources, {"fuel": _set("petrol,electricity")})
    assert merged == {
        "energy_sources": ["petrol", "electricity"],
        "fuel_match_tokens": ["petrol", "electricity", "hybrid_petrol"], "power_kw": 140,
    }
    assert overlaid == {"fuel": "correction"}

    merged, overlaid, _ = overlay_corrections(car, sources, {"fuel": _ignore()})
    assert merged == {"power_kw": 140}
    assert overlaid == {"fuel": "correction"}


def test_the_vehicles_hybrid_type_is_laid_over_only_where_it_says_something_new() -> None:
    """A car AIS added has its hybrid type on the vehicle alone, from a rule.

    A registry car's own record already states the type the vehicle carries, so
    laying the same value over changes nothing the matcher is handed.
    """

    assert "electrification_type" in MATCHER_FIELDS
    derived = {"electrification_type": "plug_in_hybrid"}
    merged, overlaid = overlay_vehicle(
        dict(derived), {"electrification_type": "plug_in_hybrid"}, {}, "transportstyrelsen"
    )
    assert (merged, overlaid) == (derived, {})
    merged, overlaid = overlay_vehicle(
        {}, {"electrification_type": "hybrid"}, {"electrification_type": "rule:ELT-GC-1"}, "ais"
    )
    assert (merged, overlaid) == (
        {"electrification_type": "hybrid"}, {"electrification_type": "rule"})


    merged, overlaid, _ = overlay_corrections(derived, {}, {"electrification_type": _set("hybrid")})
    assert (merged, overlaid) == (
        {"electrification_type": "hybrid"}, {"electrification_type": "correction"})
    merged, overlaid, _ = overlay_corrections(derived, {}, {"electrification_type": _ignore()})
    assert (merged, overlaid) == ({}, {"electrification_type": "correction"})


def _vehicle_row(
    vehicle: dict[str, Any],
    *,
    sources: dict[str, str] | None = None,
    normalized: dict[str, Any] | None = None,
    raw: dict[str, Any] | None = None,
    status: str = "resolved",
    review_reasons: list[str] | None = None,
) -> tuple[Any, ...]:
    """A row as `vehicle_car_records` reads it, for a made-up TS-origin vehicle."""

    payload = {"normalized": normalized or {}, "candidates": {}}
    return (
        "NOR-01ARZ3NDEKTSV4RRFFQ69G5FAV", "ABC123", "YV1BW84S1F1234567", 7,
        "transportstyrelsen", status, sources or {},
        *(vehicle.get(name) for name in MATCHER_FIELDS),
        status, payload, review_reasons or [], raw or {"brand": "VOLVO", "model": "V70"},
        *(None for _ in EVIDENCE_FALLBACK),
        # The last column: whether a person has recorded a KType choice for the car.
        False,
    )


def test_a_corrected_field_is_neither_inferred_nor_rule_filled() -> None:
    vehicle = {"manufacturer": "Volvo", "model_family": "V70", "engine_code": "DPCA",
               "bodywork_form": "suv", "power_kw": 133}
    sources = {"model_family": "rule:MOD-1", "engine_code": "review:rule-7",
               "bodywork_form": "review:rule-8"}
    derived = {"manufacturer": "Volvo", "power_kw": 133, "bodywork_form": "estate"}
    row = _vehicle_row(vehicle, sources=sources, normalized=derived)

    plain = _vehicle_car_record(row)
    corrected = _vehicle_car_record(
        row, {"model_family": _set("XC70"), "engine_code": _ignore(), "power_kw": _set("120")}
    )

    assert plain.record.payload["inferred_fields"] == ["model_family"]
    assert plain.rule_filled == ("bodywork_form", "engine_code", "model_family")
    assert (plain.corrections, plain.has_corrections, plain.stop_reasons) == ({}, False, ())
    normalized: dict[str, Any] = corrected.record.payload["normalized"]  # type: ignore[assignment]
    assert (normalized["model_family"], normalized["power_kw"]) == ("XC70", 120)
    assert "engine_code" not in normalized
    assert corrected.overlaid == {
        "model_family": "correction", "engine_code": "correction", "power_kw": "correction",
        "bodywork_form": "review",
    }
    assert corrected.record.payload["inferred_fields"] == []
    assert corrected.rule_filled == ("bodywork_form",)
    assert set(corrected.corrections) == {"model_family", "engine_code", "power_kw"}
    assert corrected.has_corrections
    # The identity beside the car is the corrected one too.
    assert (corrected.manufacturer, corrected.model_family) == ("Volvo", "XC70")
    assert corrected.source_record_id == plain.source_record_id == 7


def test_the_vehicles_copy_of_a_correction_is_used_only_while_that_correction_stands() -> None:
    """`core.vehicles` carries a copy of a `set` under the source `correction:<id>`. The table
    is the truth: the copy is handed to the matcher only when the field's head is that `set`."""

    # The merge itself labels such a value a correction, whoever wrote it.
    merged, overlaid = overlay_vehicle(
        {"engine_code": "DPCA", "model_family": "V70"},
        {"engine_code": "DFGA", "model_family": "XC70"},
        {"engine_code": "correction:0b9c5e9e", "model_family": "correction:5d0f5c0a"},
        "transportstyrelsen",
    )
    assert (merged["engine_code"], merged["model_family"]) == ("DFGA", "XC70")
    assert overlaid == {"engine_code": "correction", "model_family": "correction"}

    engine, model = _set("DFGA"), _set("XC70")
    derived = {"manufacturer": "Volvo", "model_family": "V70", "engine_code": "DPCA"}
    row = _vehicle_row(
        {"manufacturer": "Volvo", "model_family": "XC70", "engine_code": "DFGA"},
        sources={
            "model_family": f"correction:{model.correction_id}",
            "engine_code": f"correction:{engine.correction_id}",
        },
        normalized=derived,
    )

    # One direction: the corrections stand, the copies are theirs and are used.
    standing = _vehicle_car_record(row, {"engine_code": engine, "model_family": model})
    values: dict[str, Any] = standing.record.payload["normalized"]  # type: ignore[assignment]
    assert (values["engine_code"], values["model_family"]) == ("DFGA", "XC70")
    assert standing.overlaid == {"engine_code": "correction", "model_family": "correction"}
    assert (standing.rule_filled, standing.record.payload["inferred_fields"]) == ((), [])
    assert standing.copy_drift == ()

    # The other: nobody's correction is behind a copy. It is skipped, the
    # derivation stands, and the car says which fields drifted.
    for heads in (
        None,
        {},
        {"engine_code": _withdrawn(), "model_family": _withdrawn()},
        {"engine_code": _set("DFGA"), "model_family": _set("XC70")},  # other ids
    ):
        stale = _vehicle_car_record(row, heads)
        if heads and all(head.action == "set" for head in heads.values()):
            continue_values: dict[str, Any] = stale.record.payload["normalized"]  # type: ignore[assignment]
            # Another standing correction still decides the value: the table, not the copy.
            assert (continue_values["engine_code"], continue_values["model_family"]) == (
                "DFGA", "XC70")
        else:
            left: dict[str, Any] = stale.record.payload["normalized"]  # type: ignore[assignment]
            assert (left["engine_code"], left["model_family"]) == ("DPCA", "V70")
            assert stale.overlaid == {}
        assert stale.copy_drift == ("model_family", "engine_code")

    # An ignore on the field is no `set`: a copy left behind is stale under it too.
    ignored = _vehicle_car_record(row, {"engine_code": _ignore(), "model_family": model})
    assert "engine_code" not in ignored.record.payload["normalized"]  # type: ignore[operator]
    assert ignored.copy_drift == ("engine_code",)


def test_a_stale_fuel_copy_on_any_of_its_columns_is_skipped() -> None:
    head = _set("diesel")
    derived = {"manufacturer": "Volvo", "energy_sources": ["petrol"],
               "fuel_match_tokens": ["petrol"]}
    vehicle = {"manufacturer": "Volvo", "fuel": "diesel", "fuel_secondary": "electricity"}

    def car(sources: dict[str, str], heads: dict[str, CorrectionHead] | None) -> CarRecord:
        return _vehicle_car_record(_vehicle_row(vehicle, sources=sources, normalized=derived), heads)

    ours = f"correction:{head.correction_id}"
    assert car({"fuel": ours, "fuel_secondary": ours}, {"fuel": head}).copy_drift == ()
    # Only the second fuel's source names a correction nobody stands behind.
    drifted = car({"fuel": "transportstyrelsen", "fuel_secondary": f"correction:{uuid4()}"}, None)
    assert drifted.copy_drift == ("fuel",)
    assert drifted.record.payload["normalized"]["fuel_match_tokens"] == ["petrol"]  # type: ignore[index]
    # A value from any other source is no correction's copy.
    assert car({"fuel": "ais:x@2026-09-19"}, None).copy_drift == ()


def test_a_hypothetical_correction_is_one_more_layer_and_writes_nothing() -> None:
    derived = {"manufacturer": "Volvo", "power_kw": 133}
    vehicle = {"manufacturer": "Volvo", "model_family": "V70", "power_kw": 133,
               "engine_code": "DPCA", "drive_type": "fwd"}
    sources = {"engine_code": "review:rule-7", "model_family": "rule:MOD-1"}
    standing = {"power_kw": _set("120")}
    car = _vehicle_car_record(_vehicle_row(vehicle, sources=sources, normalized=derived), standing)
    before = copy.deepcopy(car)

    def laid(field: str, action: str, value: str | None = None) -> CarRecord:
        return lay_hypothetical(car, Hypothetical(field, action, value))  # type: ignore[arg-type]

    engine = laid("engine_code", "set", "DFGA")
    values: dict[str, Any] = engine.record.payload["normalized"]  # type: ignore[assignment]
    # After the standing corrections, like a stored one: the value, its source,
    # and no longer a rule's fill.
    assert (values["engine_code"], values["power_kw"]) == ("DFGA", 120)
    assert engine.overlaid["engine_code"] == "correction"
    assert (car.rule_filled, engine.rule_filled) == (
        ("engine_code", "model_family"), ("model_family",))
    # The stored corrections the record lists stay the stored ones.
    assert engine.corrections == car.corrections == standing
    assert engine.record.source_record_id == car.record.source_record_id

    ignored = laid("drive_type", "ignore")
    assert "drive_type" not in ignored.record.payload["normalized"]  # type: ignore[operator]
    family = laid("model_family", "set", "XC70")
    assert (family.model_family, family.record.payload["inferred_fields"]) == ("XC70", [])
    # It replaces a standing correction of the same field in the evaluation only.
    assert laid("power_kw", "set", "140").record.payload["normalized"]["power_kw"] == 140  # type: ignore[index]

    # A pure layer: the car that was read is untouched.
    assert car == before
    # The hash names exactly what the matcher is handed, whichever record carries it.
    assert matcher_input_hash(car.record) == matcher_input_hash(before.record)
    assert matcher_input_hash(engine.record) != matcher_input_hash(car.record)
    # Setting the value the car already has hands the matcher the same: no effect.
    assert matcher_input_hash(laid("engine_code", "set", "DPCA").record) == matcher_input_hash(
        car.record)
    assert matcher_input_hash(MatchSourceRecord(1, {"a": 1})) == matcher_input_hash(
        MatchSourceRecord(2, {"a": 1}))
    assert len(matcher_input_hash(car.record)) == 64


def test_what_if_runs_the_real_matcher_on_the_layered_car_and_remembers_nothing() -> None:
    catalog = (
        VehicleCandidate("K1", "Volvo", "V70", engine_codes=frozenset({"D4204T14"})),
        VehicleCandidate("K2", "Volvo", "V70", engine_codes=frozenset({"D4204T23"})),
    )
    evaluator = TecDocDryRunEvaluator(catalog)
    row = _vehicle_row(
        {"manufacturer": "Volvo", "model_family": "V70"},
        normalized={"manufacturer": "Volvo", "model_family": "V70"},
    )

    class _OneCar:
        def vehicle_car_records(self, ids: list[str]) -> list[CarRecord]:
            return [] if ids == ["V404"] else [_vehicle_car_record(row)]

    matcher = Matcher("batch-1", evaluator, {item.candidate_reference: item for item in catalog})
    service = VehicleMatchingService(_OneCar(), lambda: matcher, SummaryJobs())  # type: ignore[arg-type]

    lookup = service.lookup_vehicle("v1")
    remembered = evaluator.cache_size
    outcome = service.what_if("v1", Hypothetical("engine_code", "set", "D4204T23"))

    assert lookup.terminal != "resolved"
    assert lookup.matcher_input_hash == matcher_input_hash(_vehicle_car_record(row).record)
    assert outcome == ("resolved", "K2")
    assert evaluator.cache_size == remembered
    # The same evaluation as a stored correction gets.
    stored = _vehicle_car_record(row, {"engine_code": _set("D4204T23")})
    assert outcome_of(evaluator.evaluate(stored.record)) == outcome
    assert service.matcher() is matcher
    assert matcher.key(stored.record) == evaluator.evaluation_key(stored.record)
    with pytest.raises(VehicleNotFoundError):
        service.what_if("V404", Hypothetical("engine_code", "ignore"))


def test_a_car_whose_corrections_were_all_withdrawn_still_says_it_has_some() -> None:
    """The lookup reads the details then: the next correction must supersede the withdrawal."""

    car = _vehicle_car_record(_vehicle_row({"manufacturer": "Volvo"}), {"power_kw": _withdrawn()})

    assert (car.corrections, car.has_corrections) == ({}, True)


def _real_query(evaluator: TecDocDryRunEvaluator, row: tuple[Any, ...],
                heads: dict[str, CorrectionHead]) -> ResolvedMatchQuery:
    resolved = evaluator.resolved_query(_vehicle_car_record(row, heads).record)
    assert resolved is not None
    return resolved


def test_the_real_matcher_is_handed_the_corrected_values() -> None:
    """Not a scripted evaluator: what the correction layer hands over is what is keyed on."""

    evaluator = TecDocDryRunEvaluator((VehicleCandidate("1", "Volvo", "V70"),))
    derived = {"manufacturer": "Volvo", "model_family": "V70", "power_kw": 133,
               "production_year": 2015, "production_date": "2015-02",
               "production_date_precision": "month", "energy_sources": ["petrol", "electricity"],
               "fuel_match_tokens": ["petrol", "electricity", "hybrid_petrol"],
               "electrification_type": "plug_in_hybrid"}
    vehicle = {**derived, "production_month": 2, "engine_code": "DPCA"}
    row = _vehicle_row(vehicle, sources={"engine_code": "ais:x@2026-09-19"}, normalized=derived)

    def query(heads: dict[str, CorrectionHead]) -> ResolvedMatchQuery:
        return _real_query(evaluator, row, heads)

    plain = query({})
    assert (plain.year, plain.build_month, plain.power_kw, plain.engine_code) == (
        2015, 201502, 133, "DPCA")
    assert plain.fuels == {"petrol", "electricity", "hybrid_petrol"}
    assert plain.electrification == "plug_in_hybrid"

    corrected = query({"power_kw": _set("120"), "engine_code": _ignore()})
    assert (corrected.power_kw, corrected.engine_code, corrected.build_month) == (120, None, 201502)

    # The build month is the year and the month the matcher is handed, corrected or not.
    assert query({"production_year": _set("2014")}).build_month == 201402
    assert query({"production_month": _set("11")}).build_month == 201511
    both = query({"production_year": _set("2014"), "production_month": _set("11")})
    assert (both.year, both.build_month) == (2014, 201411)
    # An ignored month or year leaves no build month: the normalization's own
    # date (2015-02 here) does not speak in their place.
    no_month = query({"production_month": _ignore()})
    assert (no_month.year, no_month.build_month) == (2015, None)
    no_year = query({"production_year": _ignore()})
    assert (no_year.year, no_year.build_month) == (None, None)

    diesel = query({"fuel": _set("diesel")})
    assert diesel.fuels == {"diesel"}
    hybrid = query({"fuel": _set("diesel,electricity")})
    assert hybrid.fuels == {"diesel", "electricity", "hybrid_diesel"}
    assert query({"fuel": _ignore()}).fuels == frozenset()

    assert query({"electrification_type": _set("hybrid")}).electrification == "hybrid"
    assert query({"electrification_type": _ignore()}).electrification is None

    keys = {plain.key, corrected.key, no_month.key, no_year.key, diesel.key, hybrid.key}
    assert len(keys) == 6  # every correction is a different evaluation


def test_a_model_family_correction_is_the_cars_model() -> None:
    """It fills a model the car lacks, replaces a rule's guess, and stands over a registry
    model text that names another catalog model: a person looked at this car."""

    evaluator = TecDocDryRunEvaluator(
        (VehicleCandidate("1", "Volvo", "V70"), VehicleCandidate("2", "Volvo", "XC70"))
    )
    derived = {"manufacturer": "Volvo"}

    def models(raw: dict[str, Any], vehicle: dict[str, Any], sources: dict[str, str],
               heads: dict[str, CorrectionHead]) -> tuple[str, ...] | None:
        row = _vehicle_row({**derived, **vehicle}, sources=sources, normalized=derived, raw=raw)
        resolved = evaluator.resolved_query(_vehicle_car_record(row, heads).record)
        return None if resolved is None else resolved.model_values

    nameless = {"brand": "VOLVO"}
    assert models(nameless, {}, {}, {}) is None  # no model evidence: the car is not scored
    assert models(nameless, {}, {}, {"model_family": _set("XC70")}) == ("XC70",)
    # A learned rule's model is a guess the car's text may overrule; a person's is the car's.
    guessed = ({"model_family": "V70"}, {"model_family": "rule:MOD-1"})
    assert models(nameless, *guessed, {}) == ("V70",)
    assert models(nameless, *guessed, {"model_family": _set("XC70")}) == ("XC70",)
    assert models(nameless, *guessed, {"model_family": _ignore()}) is None
    # The registry names a model the catalog knows. Uncorrected, the matcher reads that text ...
    named = {"brand": "VOLVO", "model": "V70"}
    assert models(named, {}, {}, {}) == ("V70", "V70")
    # ... a person's model stands over it, and is not weighed against the brand text either.
    assert models(named, {}, {}, {"model_family": _set("XC70")}) == ("XC70",)
    both = {"brand": "VOLVO V70", "model": "V70"}
    assert models(both, {}, {}, {"model_family": _set("XC70")}) == ("XC70",)
    # Marking the model as wrong asserts nothing: the registry text is read again.
    assert models(named, {"model_family": "V70"}, {}, {"model_family": _ignore()}) == ("V70", "V70")
    # An undone correction leaves no trace at the matcher.
    assert models(named, {}, {}, {"model_family": _withdrawn()}) == ("V70", "V70")


def test_only_a_corrected_car_tells_the_matcher_what_a_person_asserted() -> None:
    evaluator = TecDocDryRunEvaluator(
        (VehicleCandidate("1", "Volvo", "V70"), VehicleCandidate("2", "Volvo", "XC70"))
    )
    derived = {"manufacturer": "Volvo", "model_family": "V70", "power_kw": 120}
    row = _vehicle_row(dict(derived), normalized=derived, raw={"brand": "VOLVO", "model": "V70"})

    plain = _vehicle_car_record(row)
    assert "asserted_fields" not in plain.record.payload
    assert evaluator.resolved_query(plain.record).recovery_reason == "model_recovered_from_model"

    corrected = _vehicle_car_record(row, {"model_family": _set("XC70"), "power_kw": _set("125")})
    # Only the model is named: any other corrected value is weighed like the one it replaced.
    assert corrected.record.payload["asserted_fields"] == ["model_family"]
    assert evaluator.resolved_query(corrected.record).recovery_reason == "model_asserted_by_person"
    assert "asserted_fields" not in _vehicle_car_record(row, {"power_kw": _set("125")}).record.payload
    # A model a person only marked as wrong is not something they asserted.
    ignored = _vehicle_car_record(row, {"model_family": _ignore()})
    assert "asserted_fields" not in ignored.record.payload

    # A correction not saved yet is weighed the same way, so a check shows the true effect.
    what_if = lay_hypothetical(plain, Hypothetical("model_family", "set", "XC70"))
    assert what_if.record.payload["asserted_fields"] == ["model_family"]
    assert evaluator.resolved_query(what_if.record).model_values == ("XC70",)
    assert "asserted_fields" not in plain.record.payload  # the car read earlier is untouched


def test_a_month_without_a_year_gives_the_matcher_no_build_month() -> None:
    evaluator = TecDocDryRunEvaluator((VehicleCandidate("1", "Volvo", "V70"),))
    derived = {"manufacturer": "Volvo", "model_family": "V70", "production_date": "2015-02",
               "production_date_precision": "month"}
    row = _vehicle_row(dict(derived), normalized=derived)

    # Uncorrected, the matcher reads the month off the normalization's date ...
    assert _real_query(evaluator, row, {}).build_month == 201502
    # ... and a corrected month replaces that date rather than sitting beside it.
    set_month = _real_query(evaluator, row, {"production_month": _set("11")})
    assert (set_month.year, set_month.build_month) == (None, None)


# ------------------------------------------------------- a car released from the stop


def test_a_stopped_car_is_handed_over_as_it_is_until_a_person_releases_it() -> None:
    evaluator = TecDocDryRunEvaluator((VehicleCandidate("1", "Volvo", "V70"),))
    derived = {"manufacturer": "Volvo", "model_family": "V70"}
    row = _vehicle_row(dict(derived), normalized=derived, status="review_required",
                       review_reasons=["tyre_size_unrecognized"])

    stopped = _vehicle_car_record(row)
    assert stopped.stop_reasons == ("tyre_size_unrecognized",)
    evaluation = evaluator.evaluate(stopped.record)
    assert (evaluation.terminal, evaluation.reason_codes) == (
        "normalization_review", ("normalization:tyre_size_unrecognized",))

    released = _vehicle_car_record(row, {"normalization_stop": _ignore()})
    # The matcher runs, with every guard on; nothing else about the record changed.
    assert released.record.payload["normalization_status"] == "resolved"
    assert released.record.payload["review_reasons"] == []
    assert released.record.payload["normalized"] == stopped.record.payload["normalized"]
    assert evaluator.evaluate(released.record).terminal == "resolved"
    # The reasons stay known, and the release counts as a correction in force.
    assert released.stop_reasons == ("tyre_size_unrecognized",)
    assert set(released.corrections) == {"normalization_stop"} and released.has_corrections
    assert released.overlaid == stopped.overlaid

    again = _vehicle_car_record(row, {"normalization_stop": _withdrawn()})
    assert again.record.payload["normalization_status"] == "review_required"
    assert again.record.payload["review_reasons"] == ["tyre_size_unrecognized"]
    assert (again.corrections, again.has_corrections) == ({}, True)


def test_a_stop_without_reasons_is_named_in_the_matchers_own_word() -> None:
    row = _vehicle_row({"manufacturer": "Volvo"}, status="review_required")

    assert _vehicle_car_record(row).stop_reasons == ("normalization_review_required",)


@pytest.mark.parametrize("status", ["failed", "resolved", "provisional"])
def test_a_release_only_lifts_the_normalization_stop(status: str) -> None:
    """A failed normalization or a policy route is another outcome: it stays what it is."""

    derived = {"manufacturer": "Volvo", "model_family": "V70",
               "record_route": "exclude_from_passenger_car_dataset"}
    row = _vehicle_row(dict(derived), normalized=derived, status=status)

    car = _vehicle_car_record(row, {"normalization_stop": _ignore()})

    assert car.record.payload["normalization_status"] == status
    assert (car.stop_reasons, car.corrections) == ((), {})
    evaluator = TecDocDryRunEvaluator((VehicleCandidate("1", "Volvo", "V70"),))
    assert evaluator.evaluate(car.record).terminal == (
        "failed" if status == "failed" else "policy_excluded")


# --------------------------------------------------------------------------- the service


class _Evaluator:
    """Stands in for TecDocDryRunEvaluator: one scripted outcome per car."""

    def __init__(self, outcomes: dict[int, MatchEvaluation]) -> None:
        self._outcomes = outcomes

    def evaluate(self, record: MatchSourceRecord) -> MatchEvaluation:
        return self._outcomes[record.source_record_id]

    def resolved_query(self, record: MatchSourceRecord) -> ResolvedMatchQuery:
        return _query()


#: Vehicles the scripted repository reports as corrected by a person, and the
#: reasons it reports a vehicle's record as stopped for.
_CORRECTED: set[str] = set()
_STOPPED: dict[str, tuple[str, ...]] = {}


def _car(rid: int, *, vehicle: bool = True) -> CarRecord:
    return CarRecord(
        source_record_id=rid,
        plate=f"P{rid}",
        vin=None,
        manufacturer="Volvo",
        model_family="XC60",
        record=MatchSourceRecord(rid, {}),
        rule_filled=(),
        vehicle_id=f"V{rid}" if vehicle else None,
        overlaid={"engine_code": "ais"} if vehicle else {},
        has_corrections=vehicle and f"V{rid}" in _CORRECTED,
        stop_reasons=_STOPPED.get(f"V{rid}", ()) if vehicle else (),
        # Vehicle V2 is the car nobody decided; every other vehicle has a chain.
        has_choices=vehicle and rid != 2,
    )


@pytest.fixture(autouse=True)
def _nobody_corrected() -> Any:
    yield
    _CORRECTED.clear()
    _STOPPED.clear()


class _Repository:
    """Vehicles are `V<n>`; each is evaluated as record `n`."""

    def __init__(self, ids: list[int]) -> None:
        self._ids = [f"V{rid}" for rid in ids]

    def vehicles_for_identifier(self, identifier: str, *, limit: int = 10) -> list[str]:
        return ["V1", "V9"] if identifier == "ABC123" else []

    def vehicle_population(self, terms: Any, text: str, *, limit: int) -> tuple[int, list[str]]:
        return len(self._ids), self._ids[:limit]

    def vehicle_car_records(self, ids: list[str]) -> list[CarRecord]:
        return [_car(int(vehicle_id[1:])) for vehicle_id in ids if vehicle_id != "V404"]

    def car_records(self, ids: list[int]) -> list[CarRecord]:
        return [_car(rid, vehicle=False) for rid in ids]


def _service(outcomes: dict[int, MatchEvaluation], jobs: SummaryJobs | None = None) -> VehicleMatchingService:
    catalog = {
        "A": _ktype("A", engines=frozenset({"D4204T14"})),
        "B": _ktype("B", engines=frozenset({"D4204T23"})),
    }
    matcher = Matcher("batch-1", _Evaluator(outcomes), catalog)  # type: ignore[arg-type]
    return VehicleMatchingService(
        _Repository(sorted(outcomes)), lambda: matcher, jobs or SummaryJobs()  # type: ignore[arg-type]
    )


def test_lookup_normalizes_the_identifier_and_explains_the_gap() -> None:
    service = _service({1: _evaluation(_match("A"), _match("B")), 9: _evaluation()})

    result = service.lookup(" abc 123 ")

    assert result.vehicle_id == "V1"
    assert result.source_record_id == 1
    assert result.overlaid_fields == {"engine_code": "ais"}
    assert result.bucket == "several"
    assert result.catalog_batch == "batch-1"
    assert [candidate.ktype for candidate in result.candidates] == ["A", "B"]
    assert result.candidates[0].engine_codes == ["D4204T14"]
    assert result.missing_separating_fields == ["engine_code"]
    assert result.other_vehicle_ids == ["V9"]


def test_lookup_by_vehicle_matches_that_vehicle() -> None:
    service = _service({1: _evaluation(_match("A")), 9: _evaluation()})

    result = service.lookup_vehicle(" v9 ")

    assert result.vehicle_id == "V9"
    assert result.bucket == "none"
    assert result.other_vehicle_ids == []
    with pytest.raises(VehicleNotFoundError):
        service.lookup_vehicle("V404")


def test_lookup_by_record_explains_that_exact_record() -> None:
    service = _service({1: _evaluation(_match("A")), 9: _evaluation()})

    result = service.lookup_record(9)

    assert result.source_record_id == 9
    assert result.vehicle_id is None
    assert result.bucket == "none"
    assert result.other_vehicle_ids == []


class _Choices:
    """Stands in for the choice repository: one head per vehicle, reads counted."""

    def __init__(self, heads: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.heads = heads or {}
        self.error = error
        self.reads: list[str] = []

    def current(self, vehicle_id: str) -> Any:
        self.reads.append(vehicle_id)
        if self.error:
            raise self.error
        return self.heads.get(vehicle_id)


def _with_choices(service: VehicleMatchingService, choices: _Choices) -> VehicleMatchingService:
    service._choices = choices
    return service


def _head(shown: Any, **overrides: Any) -> Any:
    from datetime import UTC, datetime
    from uuid import uuid4

    from api.app.features.vehicle_ktype_choices import evidence
    from ingestion.vehicle_ktype_choices import StoredChoice

    values: dict[str, Any] = {
        "choice_id": uuid4(), "vehicle_id": shown.vehicle_id, "chain_position": 0,
        "action": "choose", "ktype": "B",
        "supersedes_choice_id": None, "reviewer": "Ada", "reason": None,
        "catalog_batch": shown.catalog_batch, "automatic_terminal": shown.terminal,
        "automatic_ktype": shown.top_ktype, "code_version": "v",
        "evidence_fingerprint": shown.evidence_fingerprint,
        "evidence": evidence.snapshot(shown, "v"), "created_at": datetime(2026, 10, 2, tzinfo=UTC),
    }
    values.update(overrides)
    return StoredChoice(**values)


def test_a_lookup_without_a_choice_reader_keeps_its_defaults() -> None:
    service = _service({1: _evaluation(_match("A"), _match("B"))})

    result = service.lookup_vehicle("V1")

    assert len(result.evidence_fingerprint) == 64
    assert result.evidence_fingerprint == service.lookup_vehicle("V1").evidence_fingerprint
    assert result.choice is None
    # The scripted evaluation is "resolved" with no top candidate: nothing effective.
    assert (result.effective_ktype, result.effective_source) == (None, None)
    assert result.inputs is not None
    assert (result.inputs.build_month, result.inputs.electrification) == (None, None)


def test_a_resolved_match_is_the_effective_ktype_until_a_person_chooses() -> None:
    resolved = MatchEvaluation(
        "resolved", ("match:x",), top_candidate_reference="A",
        candidate_matches=(_match("A"), _match("B")),
    )
    choices = _Choices()
    service = _with_choices(_service({1: resolved}), choices)

    shown = service.lookup_vehicle("V1")
    assert (shown.effective_ktype, shown.effective_source, shown.choice) == ("A", "matcher", None)

    choices.heads["V1"] = (_head(shown), 2)
    chosen = service.lookup_vehicle("V1")

    assert chosen.choice is not None
    assert (chosen.choice.status, chosen.choice.ktype, chosen.choice.history_count) == (
        "chosen", "B", 2)
    assert chosen.choice.needs_review is False
    assert (chosen.effective_ktype, chosen.effective_source) == ("B", "person")
    # The matcher's own result is untouched.
    assert (chosen.terminal, chosen.top_ktype) == ("resolved", "A")
    assert chosen.evidence_fingerprint == shown.evidence_fingerprint


def test_a_lookup_flags_a_choice_that_no_longer_fits_and_asks_the_catalog() -> None:
    choices = _Choices()
    service = _with_choices(_service({1: _evaluation(_match("A"), _match("B"))}), choices)
    shown = service.lookup_vehicle("V1")
    choices.heads["V1"] = (_head(shown, ktype="B", catalog_batch="older-batch"), 1)

    stale = service.lookup_vehicle("V1").choice
    assert stale is not None and stale.stale_reasons == ["catalog_batch_changed"]

    # "A" and "B" are the scripted catalog; a KType outside it is "not in the catalog".
    gone_evidence = dict(shown.model_dump(mode="json"), candidates=[{"ktype": "Z"}])
    choices.heads["V1"] = (_head(shown, ktype="Z", evidence=gone_evidence), 1)
    gone = service.lookup_vehicle("V1").choice
    assert gone is not None and "ktype_not_in_catalog" in gone.stale_reasons


def test_a_vehicle_lookup_reads_the_choice_once_and_a_record_lookup_never() -> None:
    choices = _Choices()
    service = _with_choices(
        _service({1: _evaluation(_match("A")), 2: _evaluation(_match("A")), 9: _evaluation()}),
        choices,
    )

    service.lookup_vehicle("V1")
    service.lookup("ABC123")
    assert choices.reads == ["V1", "V1"]
    # The car's own read says nobody decided V2: no second connection for it.
    assert service.lookup_vehicle("V2").choice is None
    assert choices.reads == ["V1", "V1"]

    by_record = service.lookup_record(9)
    assert by_record.choice is None and len(by_record.evidence_fingerprint) == 64
    job = service.start_summary([], "", 10, run_in_background=False)
    assert job.evaluated == 3
    assert choices.reads == ["V1", "V1"]


def test_a_failing_choice_read_fails_the_lookup() -> None:
    """A choice is never silently hidden: the router answers 503."""

    import psycopg

    service = _with_choices(
        _service({1: _evaluation(_match("A"))}), _Choices(error=psycopg.OperationalError("down"))
    )

    with pytest.raises(psycopg.OperationalError):
        service.lookup_vehicle("V1")


class _Corrections:
    """Stands in for the correction repository: the heads per vehicle, reads counted."""

    def __init__(self, heads: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.heads = heads or {}
        self.error = error
        self.reads: list[str] = []

    def current(self, vehicle_id: str) -> Any:
        self.reads.append(vehicle_id)
        if self.error:
            raise self.error
        return self.heads.get(vehicle_id, {})


def _with_corrections(
    service: VehicleMatchingService, corrections: _Corrections
) -> VehicleMatchingService:
    service._corrections = corrections
    return service


def _correction_head(vehicle_id: str, field: str, action: str, value: str | None,
                     position: int = 0) -> Any:
    from datetime import UTC, datetime

    from ingestion.vehicle_fact_corrections import StoredCorrection

    return StoredCorrection(
        correction_id=uuid4(), vehicle_id=vehicle_id, field=field, chain_position=position,
        action=action,  # type: ignore[arg-type]
        value=value, supersedes_correction_id=None, group_id=None, reviewer="Ada", reason=None,
        previous_value="DPCA", previous_source="review", catalog_batch="batch-1",
        automatic_terminal="review_required", automatic_ktype="A", code_version="v",
        evidence_fingerprint="a" * 64, evidence={"schema": "x", "automatic": {}},
        created_at=datetime(2026, 10, 2, tzinfo=UTC),
    )


_CORRECTABLE = [
    "manufacturer", "model_family", "engine_code", "power_kw", "displacement_cc", "drive_type",
    "bodywork_form", "production_year", "production_month", "fuel", "electrification_type",
]


def test_a_lookup_without_a_correction_reader_keeps_its_defaults() -> None:
    result = _service({1: _evaluation(_match("A"), _match("B"))}).lookup_vehicle("V1")

    assert (result.corrections, result.correctable_fields, result.stop_reasons) == ([], [], [])


def test_a_vehicle_lookup_describes_its_corrections_and_what_can_be_corrected() -> None:
    corrections = _Corrections()
    service = _with_corrections(
        _service({1: _evaluation(_match("A"), _match("B"))}), corrections
    )

    plain = service.lookup_vehicle("V1")

    assert plain.corrections == []
    assert [item.field for item in plain.correctable_fields] == _CORRECTABLE
    engine = plain.correctable_fields[2]
    # The scripted car hands the matcher no values; AIS is where its engine code would be from.
    assert (engine.current_value, engine.current_source) == (None, "ais")
    assert engine.suggestions == ["D4204T14", "D4204T23"]
    assert plain.correctable_fields[3].suggestions == ["140"]
    assert plain.correctable_fields[9].type == "list"

    withdrawn = _correction_head("V1", "power_kw", "withdraw", None, 1)
    ignored = _correction_head("V1", "engine_code", "ignore", None)
    corrections.heads["V1"] = {"power_kw": (withdrawn, 2), "engine_code": (ignored, 1)}
    _CORRECTED.add("V1")
    described = service.lookup_vehicle("V1")

    assert [(item.field, item.status, item.history_count) for item in described.corrections] == [
        ("engine_code", "ignored", 1), ("power_kw", "withdrawn", 2),
    ]
    assert described.corrections[0].correction_id == ignored.correction_id
    # Describing changes nothing the matcher concluded.
    assert described.evidence_fingerprint == plain.evidence_fingerprint
    assert (described.terminal, described.bucket) == (plain.terminal, plain.bucket)


def test_a_lookup_names_the_decision_behind_a_row_many_cars_share() -> None:
    from dataclasses import replace

    from ingestion.vehicle_correction_decisions import DecisionRef

    group, decision_id = uuid4(), uuid4()
    asked: list[list[Any]] = []

    class _Decided(_Corrections):
        def decisions(self, group_ids: Any) -> Any:
            asked.append(list(group_ids))
            return {group: DecisionRef(decision_id, "All Volvo XC60 cars with no drive type", 59, "Bo")}

    corrections = _Decided()
    service = _with_corrections(_service({1: _evaluation(_match("A"))}), corrections)
    own = _correction_head("V1", "power_kw", "set", "140")
    shared = replace(_correction_head("V1", "drive_type", "set", "awd"), group_id=group)
    corrections.heads["V1"] = {"power_kw": (own, 1), "drive_type": (shared, 1)}
    _CORRECTED.add("V1")

    described = service.lookup_vehicle("V1")

    by_field = {item.field: item for item in described.corrections}
    assert by_field["power_kw"].decision is None
    decision = by_field["drive_type"].decision
    assert decision is not None
    assert (decision.decision_id, decision.scope_label, decision.member_count, decision.reviewer) == (
        decision_id, "All Volvo XC60 cars with no drive type", 59, "Bo")
    assert asked == [[group]]
    # A car whose rows are all its own costs no decision read.
    corrections.heads["V1"] = {"power_kw": (own, 1)}
    service.lookup_vehicle("V1")
    assert asked == [[group]]
    assert described.copy_drift == []


def test_the_build_month_shown_comes_from_what_the_matcher_keyed_on() -> None:
    class _Dated(_Evaluator):
        def resolved_query(self, record: MatchSourceRecord) -> ResolvedMatchQuery:
            return _query(build_month=201809, electrification="plug_in_hybrid")

    service = _with_corrections(_service({1: _evaluation(_match("A"))}), _Corrections())
    matcher = service._matcher()
    dated = Matcher(matcher.batch_id, _Dated({1: _evaluation(_match("A"))}), matcher.catalog)  # type: ignore[arg-type]
    service._matcher = lambda: dated

    fields_ = {item.field: item for item in service.lookup_vehicle("V1").correctable_fields}

    assert fields_["production_month"].current_value == "9"
    assert fields_["production_month"].evidence_keys == ["year_month"]
    assert fields_["electrification_type"].evidence_keys == ["electrification", "match_guard:plug_in"]


def test_the_corrections_details_are_read_only_for_a_car_that_has_some() -> None:
    """Any other car costs no second connection; a record lookup and a summary never read."""

    corrections = _Corrections()
    service = _with_corrections(
        _service({1: _evaluation(_match("A")), 2: _evaluation(_match("A")), 9: _evaluation()}),
        corrections,
    )

    plain = service.lookup_vehicle("V1")
    assert corrections.reads == [] and plain.corrections == []
    assert [item.field for item in plain.correctable_fields] == _CORRECTABLE

    _CORRECTED.update({"V1", "V2"})
    service.lookup_vehicle("V1")
    service.lookup("ABC123")
    assert corrections.reads == ["V1", "V1"]

    by_record = service.lookup_record(9)
    assert (by_record.corrections, by_record.correctable_fields) == ([], [])
    job = service.start_summary([], "", 10, run_in_background=False)
    assert job.evaluated == 3
    assert corrections.reads == ["V1", "V1"]


def test_a_failing_correction_read_fails_the_lookup() -> None:
    """A correction is never silently hidden: the router answers 503."""

    import psycopg

    service = _with_corrections(
        _service({1: _evaluation(_match("A"))}),
        _Corrections(error=psycopg.errors.UndefinedTable("no such table")),
    )
    _CORRECTED.add("V1")

    with pytest.raises(psycopg.Error):
        service.lookup_vehicle("V1")


def test_a_lookup_shows_why_a_cars_record_is_stopped_also_once_released() -> None:
    service = _service({1: _evaluation(_match("A")), 9: _evaluation()})
    _STOPPED["V1"] = ("tyre_size_unrecognized",)

    assert service.lookup_vehicle("V1").stop_reasons == ["tyre_size_unrecognized"]
    assert service.lookup_vehicle("V9").stop_reasons == []
    assert service.lookup_record(1).stop_reasons == []


def test_lookup_of_an_unknown_plate_says_so() -> None:
    with pytest.raises(VehicleNotFoundError):
        _service({}).lookup("NOPE")


def test_summary_counts_buckets_and_names_the_gaps() -> None:
    outcomes = {
        1: _evaluation(_match("A")),
        2: _evaluation(_match("A"), _match("B")),
        3: _evaluation(_match("A", conflicts=("bodywork",))),
        4: _evaluation(),
        5: _evaluation(scored=False),
    }

    job = _service(outcomes).start_summary([], "", 10, run_in_background=False)

    assert job.status == "done"
    assert job.evaluated == 5
    summary = job.summary
    assert summary.buckets == {"one": 1, "several": 1, "none": 2, "not_matchable": 1}
    assert [(item.field, item.cars) for item in summary.none_conflicting_fields] == [("bodywork", 1)]
    assert summary.none_without_candidates == 1
    assert [(item.field, item.cars) for item in summary.several_missing_separating_fields] == [
        ("engine_code", 1)
    ]
    assert summary.not_matchable_reasons[0].reason == "policy:x"
    assert summary.examples["several"][0].plate == "P2"
    assert summary.examples["several"][0].vehicle_id == "V2"


def test_the_verdict_is_the_routing_gates_own_explanation() -> None:
    gate = {"signal": "routing_gate", "value": "review_required",
            "explanation": "The top candidates are too close to separate safely."}
    routed = MatchEvaluation(
        "review_required", ("route:candidate_margin_below_gate",),
        decision_trace=({"signal": "text_similarity", "explanation": "Stage 2"}, gate),
    )

    assert verdict_of(routed) == "The top candidates are too close to separate safely."
    assert verdict_of(_evaluation(scored=False)) is None


def test_summary_lists_every_car_of_a_bucket_with_what_stopped_it() -> None:
    outcomes = {
        1: _evaluation(_match("A")),
        2: _evaluation(_match("A"), _match("B")),
        3: _evaluation(_match("A", conflicts=("bodywork",))),
        4: _evaluation(),
        5: _evaluation(scored=False),
        6: _evaluation(_match("A"), _match("B")),
    }
    service = _service(outcomes)
    job = service.start_summary([], "", 10, run_in_background=False)

    several = service.summary_cars(job.job_id, "several", offset=0, limit=50)
    assert several.total == 2
    assert [car.plate for car in several.cars] == ["P2", "P6"]
    first = several.cars[0]
    assert (first.vehicle_id, first.candidates, first.terminal) == ("V2", 2, "resolved")
    assert "engine_code" in first.separating_fields
    assert first.missing_fields == ["engine_code"]

    none = service.summary_cars(job.job_id, "none", offset=0, limit=50)
    assert [(car.plate, car.conflicting_fields) for car in none.cars] == [
        ("P3", ["bodywork"]), ("P4", []),
    ]
    stopped = service.summary_cars(job.job_id, "not_matchable", offset=0, limit=50).cars[0]
    assert (stopped.reason_codes, stopped.verdict) == (["policy:x"], None)

    page = service.summary_cars(job.job_id, "several", offset=1, limit=1)
    assert (page.total, page.offset, [car.plate for car in page.cars]) == (2, 1, ["P6"])


def test_summary_reports_sampling_when_the_limit_cuts_the_population() -> None:
    outcomes = {rid: _evaluation(_match("A")) for rid in range(1, 6)}

    summary = _service(outcomes).start_summary([], "", 2, run_in_background=False).summary

    assert summary.population == 5
    assert summary.evaluated == 2
    assert summary.sampled is True


def test_a_cancelled_summary_keeps_what_it_counted() -> None:
    jobs = SummaryJobs()
    service = _service({1: _evaluation(_match("A"))}, jobs)
    job = jobs.create(population=1, target=1)
    job.cancel.set()

    service._run(job, ["V1"])

    assert service.summary_job(job.job_id).status == "cancelled"


def test_a_failing_summary_reports_why_instead_of_dying_silently() -> None:
    jobs = SummaryJobs()
    service = _service({}, jobs)  # no scripted outcome: evaluate raises KeyError
    job = jobs.create(population=1, target=1)

    service._run(job, ["V42"])

    snapshot = service.summary_job(job.job_id)
    assert snapshot.status == "failed"
    assert snapshot.error is not None and snapshot.error.startswith("KeyError")


def test_running_summaries_are_capped() -> None:
    jobs = SummaryJobs(max_running=1)
    jobs.create(population=1, target=1)

    with pytest.raises(JobCapacityError):
        jobs.create(population=1, target=1)


def test_jobs_are_listed_newest_first_with_the_filter_they_ran_on() -> None:
    """A job outlives its screen; the list is how a reopened view finds it again."""

    jobs = SummaryJobs()
    service = _service({1: _evaluation(_match("A"))}, jobs)
    volvo = VehicleFilter(conditions=[VehicleCondition(field="manufacturer", values=["Volvo"])])
    finished = service.start_summary([], "", 1, vehicle_filter=volvo, run_in_background=False)
    running = jobs.create(population=1, target=1)

    listed = service.summary_jobs()

    assert [(job.job_id, job.status) for job in listed] == [
        (running.job_id, "running"),
        (finished.job_id, "done"),
    ]
    assert listed[1].filter == volvo
    assert listed[0].filter is None


def test_an_unknown_job_is_reported_not_invented() -> None:
    with pytest.raises(SummaryJobNotFoundError):
        SummaryJobs().get("nope")


# ------------------------------------------------------------------------------ HTTP


def test_http_maps_the_services_errors(client: TestClient) -> None:
    jobs = SummaryJobs()  # one per process in the app, as `_jobs()` caches it
    client.app.dependency_overrides[get_service] = lambda: _service({1: _evaluation()}, jobs)  # type: ignore[attr-defined]
    try:
        assert client.get("/v1/vehicles/matching/lookup", params={"q": "NOPE"}).status_code == 404
        assert client.get("/v1/vehicles/matching/lookup").status_code == 422
        both = {"q": "ABC123", "source_record_id": 1}
        assert client.get("/v1/vehicles/matching/lookup", params=both).status_code == 422
        by_record = client.get("/v1/vehicles/matching/lookup", params={"source_record_id": 1})
        assert by_record.status_code == 200
        by_vehicle = client.get("/v1/vehicles/matching/lookup", params={"vehicle_id": "V1"})
        assert by_vehicle.json()["vehicle_id"] == "V1"
        unknown = client.get("/v1/vehicles/matching/lookup", params={"vehicle_id": "V404"})
        assert unknown.status_code == 404
        assert {"evidence_fingerprint", "choice", "effective_ktype", "effective_source"} <= set(
            by_vehicle.json()
        )
        assert (by_vehicle.json()["corrections"], by_vehicle.json()["correctable_fields"]) == (
            [], []
        )
        assert by_vehicle.json()["stop_reasons"] == []
        layered = {"conditions": [{"field": "fuel", "operator": "gte", "values": ["1", "2"]}]}
        assert client.post("/v1/vehicles/matching/summary", json=layered).status_code == 422
        assert client.get("/v1/vehicles/matching/summary/nope").status_code == 404
        started = client.post("/v1/vehicles/matching/summary", json={"conditions": [], "limit": 1})
        assert started.status_code == 202
        assert started.json()["target"] == 1
        assert started.json()["filter"] == {"conditions": [], "text": ""}
        listed = client.get("/v1/vehicles/matching/summary")
        assert listed.status_code == 200
        assert [job["job_id"] for job in listed.json()] == [started.json()["job_id"]]
        too_many = client.post("/v1/vehicles/matching/summary", json={"conditions": [], "limit": 50_000})
        assert too_many.status_code == 422
        cars_url = f"/v1/vehicles/matching/summary/{started.json()['job_id']}/cars"
        cars = client.get(cars_url, params={"bucket": "none"})
        assert cars.status_code == 200
        assert cars.json()["total"] == 1
        assert cars.json()["cars"][0]["vehicle_id"] == "V1"
        assert client.get(cars_url, params={"bucket": "maybe"}).status_code == 422
        assert client.get(cars_url).status_code == 422
        assert client.get("/v1/vehicles/matching/summary/nope/cars",
                          params={"bucket": "one"}).status_code == 404
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def test_matching_routes_are_not_read_as_a_vehicle_id(client: TestClient) -> None:
    """/v1/vehicles/{source_record_id} must not swallow /v1/vehicles/matching/..."""

    client.app.dependency_overrides[get_service] = lambda: _service({})  # type: ignore[attr-defined]
    try:
        response = client.get("/v1/vehicles/matching/lookup", params={"q": "NOPE"})
        assert response.json()["detail"].startswith("No vehicle with plate or VIN")
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def test_a_lookup_whose_database_read_fails_answers_503(client: TestClient) -> None:
    """E.g. a database without the corrections table: loud, never a car matched uncorrected."""

    import psycopg

    class _Broken(_Repository):
        def vehicle_car_records(self, ids: list[str]) -> list[CarRecord]:
            raise psycopg.errors.UndefinedTable("relation core.vehicle_fact_corrections")

    service = _service({1: _evaluation()})
    service._repository = _Broken([1])  # type: ignore[assignment]
    client.app.dependency_overrides[get_service] = lambda: service  # type: ignore[attr-defined]
    try:
        response = client.get("/v1/vehicles/matching/lookup", params={"vehicle_id": "V1"})
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]

    assert response.status_code == 503
    assert "relation" not in response.text


def test_a_car_without_a_ts_record_offers_its_registry_text_as_evidence() -> None:
    def row(raw: dict[str, Any] | None, decided: bool = False) -> tuple[Any, ...]:
        matcher_values = [None] * len(MATCHER_FIELDS)
        matcher_values[MATCHER_FIELDS.index("manufacturer")] = "Volvo"
        registry = {"registry_brand_text": "VOLVO XC40 RECHARGE", "registry_model_text": "XC40",
                    "variant_code": "XK", "version_code": None, "registry_type_code": "X"}
        return (
            "NOR-01ARZ3NDEKTSV4RRFFQ69G5FAV", "ABC123", "YV1XZ", None, "ais", "resolved", {},
            *matcher_values, None, None, None, raw,
            *(registry[name] for name in EVIDENCE_FALLBACK.values()),
            decided,
        )

    ais_only = _vehicle_car_record(row(None)).record.payload["source_evidence"]
    with_ts = _vehicle_car_record(row({"brand": "VOLVO", "model": ""})).record.payload["source_evidence"]

    assert (ais_only["brand"], ais_only["model"], ais_only["variant"]) == (
        "VOLVO XC40 RECHARGE", "XC40", "XK"
    )
    assert ais_only["version"] is None
    # A TS record's own text wins; an empty TS field still falls back.
    assert (with_ts["brand"], with_ts["model"]) == ("VOLVO", "XC40")
    # The row's last column says whether a person has decided this car.
    assert _vehicle_car_record(row(None)).has_choices is False
    assert _vehicle_car_record(row(None, decided=True)).has_choices is True
