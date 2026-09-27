"""The match impact report: how one run is counted and two runs are compared."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from api.app.features.vehicle_matching.impact import (
    CarOutcome,
    ImpactReport,
    build_report,
    compare,
    engine_agreement,
    engine_forms,
    evaluate_cars,
    render,
)
from api.app.features.vehicle_matching.repository import CarRecord
from api.app.features.vehicle_matching.service import Matcher
from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.match_run_service import MatchSourceRecord
from ingestion.tecdoc.match_run_adapters import MatchEvaluation
from scripts.match_impact_report import load_reference, report_cars


def _ktype(reference: str, *engines: str) -> VehicleCandidate:
    return VehicleCandidate(
        candidate_reference=reference, manufacturer="VOLVO", model="XC60",
        year_from=2017, year_to=2022, fuels=frozenset({"diesel"}),
        engine_codes=frozenset(engines), power_kw=140,
    )


CATALOG = {"K1": _ktype("K1", "D4204T14"), "K2": _ktype("K2", "BHZ (DV6FC)"), "K3": _ktype("K3"),
           "K4": _ktype("K4", "M 177.980")}


def _outcome(vid: str, terminal: str = "resolved", ktype: str | None = "K1",
             engine: str | None = "D4204T14", maker: str = "VOLVO") -> CarOutcome:
    return CarOutcome(vid, maker, terminal, "one", ktype, ("match:x", "model_evidence:primary"), engine)


def test_engine_forms_split_tecdoc_bracket_parts() -> None:
    assert engine_forms("bhz (dv6fc)") == {"BHZ(DV6FC)", "BHZ", "DV6FC"}
    assert engine_forms("D 4204 T14") == engine_forms("D4204T14") == {"D4204T14"}
    assert engine_forms("K4M-856") == engine_forms("K4M 856")
    assert engine_forms("  ") == set()


@pytest.mark.parametrize(
    ("engine", "ktype", "expected"),
    [
        ("D4204T14", "K1", True),
        ("B5254T", "K1", False),
        ("M177.980", "K4", True),
        ("BHZ", "K2", True),
        ("DV6FC", "K2", True),
        (None, "K1", None),
        ("D4204T14", "K3", None),
        ("D4204T14", "missing", None),
    ],
)
def test_engine_agreement(engine: str | None, ktype: str, expected: bool | None) -> None:
    assert engine_agreement(engine, CATALOG.get(ktype)) is expected


def test_a_run_counts_terminals_engine_agreement_and_reference() -> None:
    outcomes = [
        _outcome("a"),
        _outcome("b", engine="B5254T"),
        _outcome("c", ktype="K3"),
        _outcome("d", terminal="provisional", maker="KIA"),
    ]
    extra = [_outcome("z", ktype="K2")]
    report = build_report(
        outcomes, CATALOG, label="base", catalog_batch="b1", seed="s", population="p",
        reference={"a": "K1", "b": "K2", "d": "K1", "z": "K2"}, reference_outcomes=extra,
    )

    assert report.evaluated == 4
    assert report.terminals == {"resolved": 3, "provisional": 1}
    assert report.share("resolved") == 0.75
    assert report.engine == {"resolved": 3, "agrees": 1, "differs": 1, "unchecked": 1}
    # Reference cars outside the sample are scored but never counted as sample cars.
    assert report.reference == {"cars": 4, "correct": 2, "wrong": 1, "not_resolved": 1}
    assert report.manufacturers["KIA"] == {"cars": 1, "provisional": 1}
    assert "z" not in report.cars
    assert report.top_reasons == {"match:x": 1}


def test_two_runs_compare_car_by_car_and_survive_json() -> None:
    before = build_report(
        [_outcome("a"), _outcome("b", terminal="provisional"), _outcome("c")],
        CATALOG, label="base", catalog_batch="b1", seed="s", population="p",
    )
    after = build_report(
        [_outcome("a", ktype="K2"), _outcome("b"), _outcome("c", terminal="unmatched")],
        CATALOG, label="after", catalog_batch="b1", seed="s", population="p",
    )
    restored = ImpactReport.from_json(json.loads(json.dumps(before.to_json())))

    change = compare(restored, after)
    assert (change.gained, change.lost, change.moved, change.shared) == (1, 1, 1, 3)
    text = render(after, restored)
    assert "+1 resolved, -1 lost, 1 moved" in text
    assert "(+0.0 pts)" in text


def test_reports_on_different_catalogs_are_not_compared() -> None:
    one = build_report([], CATALOG, label="a", catalog_batch="b1", seed="s", population="p")
    two = build_report([], CATALOG, label="b", catalog_batch="b2", seed="s", population="p")
    with pytest.raises(ValueError, match="catalog"):
        compare(one, two)
    with pytest.raises(ValueError, match="version"):
        ImpactReport.from_json({**one.to_json(), "version": 99})


class _Evaluator:
    def evaluate(self, record: MatchSourceRecord) -> MatchEvaluation:
        terminal: Any = "resolved" if record.source_record_id % 2 else "unmatched"
        return MatchEvaluation(terminal, ("match:x",), top_candidate_reference="K1")

    def resolved_query(self, record: MatchSourceRecord) -> None:
        return None


def _car(rid: int) -> CarRecord:
    record = MatchSourceRecord(rid, {"normalized": {"engine_code": "D4204T14"}})
    return CarRecord(rid, None, None, "VOLVO", "XC60", record, (), vehicle_id=f"NOR-{rid}")


@pytest.mark.parametrize("workers", [1, 2])
def test_cars_are_evaluated_in_order_with_or_without_workers(workers: int) -> None:
    matcher = Matcher("b1", _Evaluator(), CATALOG)  # type: ignore[arg-type]
    outcomes = evaluate_cars(matcher, [_car(rid) for rid in range(1, 6)], workers=workers)
    assert [o.vehicle_id for o in outcomes] == [f"NOR-{rid}" for rid in range(1, 6)]
    assert [o.terminal for o in outcomes][:2] == ["resolved", "unmatched"]
    assert outcomes[0].engine_code == "D4204T14"


def test_reference_csv_needs_both_columns(tmp_path: Path) -> None:
    good = tmp_path / "ref.csv"
    good.write_text("vehicle_id,ktype_reference\nNOR-1, K1 \n")
    assert load_reference(good) == {"NOR-1": "K1"}
    bad = tmp_path / "bad.csv"
    bad.write_text("vehicle_id\nNOR-1\n")
    with pytest.raises(ValueError, match="ktype_reference"):
        load_reference(bad)


def test_an_earlier_reports_cars_can_be_evaluated_again(tmp_path: Path) -> None:
    report = build_report(
        [_outcome("b"), _outcome("a", terminal="provisional")],
        CATALOG, label="base", catalog_batch="b1", seed="s", population="passenger, registered",
    )
    path = tmp_path / "base.json"
    path.write_text(json.dumps(report.to_json()))

    ids, population = report_cars(path)

    assert ids == ["a", "b"]
    assert population == "cars of base.json (passenger, registered)"
