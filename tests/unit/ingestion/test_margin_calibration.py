import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ingestion.margin_calibration import (
    ACCEPT,
    REJECT,
    LabelledPair,
    band_weights,
    choose_threshold,
    effective_sample_size,
    sweep_thresholds,
    wilson_lower_bound,
)
from scripts import fit_margin_threshold
from scripts.sample_margin_calibration_set import PLATE_LETTERS, _seek_keys, select_stratified


def _pair(margin: float, verdict: str, *, band: str = "0.10-0.15", weight: float = 1.0):
    return LabelledPair(margin=margin, band=band, verdict=verdict, weight=weight)


def test_labelled_pair_rejects_unusable_labels() -> None:
    with pytest.raises(ValueError):
        LabelledPair(margin=0.1, band="b", verdict="unsure", weight=1.0)
    with pytest.raises(ValueError):
        LabelledPair(margin=0.1, band="b", verdict=ACCEPT, weight=0.0)


def test_wilson_lower_bound_stays_below_the_point_estimate() -> None:
    # A perfect run of 5 must not be reported as certainty.
    assert wilson_lower_bound(5, 5, 1.96) < 1.0
    # More evidence at the same rate tightens the bound upward.
    assert wilson_lower_bound(50, 50, 1.96) > wilson_lower_bound(5, 5, 1.96)
    assert wilson_lower_bound(0, 0, 1.96) == 0.0


def test_effective_sample_size_penalises_uneven_weighting() -> None:
    assert effective_sample_size((1.0, 1.0, 1.0, 1.0)) == pytest.approx(4.0)
    # One dominant weight carries most of the mass, so information drops.
    assert effective_sample_size((97.0, 1.0, 1.0, 1.0)) < 1.5


def test_band_weights_recover_population_scale() -> None:
    weights = band_weights(
        band_population={"low": 1000, "high": 50},
        labelled_per_band={"low": 25, "high": 25},
    )
    assert weights == {"low": 40.0, "high": 2.0}


def test_band_weights_skip_bands_without_population_or_labels() -> None:
    weights = band_weights(
        band_population={"a": 10, "b": 0},
        labelled_per_band={"a": 0, "b": 5, "c": 5},
    )
    assert weights == {}


def test_weighting_corrects_stratified_sampling_bias() -> None:
    # Equal sampling hides that the wide-margin band dominates the population.
    narrow = [_pair(0.02, REJECT, band="narrow", weight=40.0) for _ in range(10)]
    wide = [_pair(0.50, ACCEPT, band="wide", weight=2.0) for _ in range(10)]
    entries = sweep_thresholds(
        tuple(narrow + wide), minimum_effective_sample=0.0
    )
    at_zero = next(entry for entry in entries if entry.threshold == 0.02)
    # Unweighted this would read 0.5; weighted it reflects the real mix.
    assert at_zero.weighted_precision == pytest.approx(20 / 420)


def test_sweep_reports_precision_and_recall_at_each_threshold() -> None:
    pairs = (
        _pair(0.05, REJECT),
        _pair(0.10, REJECT),
        _pair(0.20, ACCEPT),
        _pair(0.30, ACCEPT),
    )
    entries = sweep_thresholds(pairs, minimum_effective_sample=0.0)
    by_threshold = {entry.threshold: entry for entry in entries}
    assert by_threshold[0.05].weighted_precision == pytest.approx(0.5)
    assert by_threshold[0.20].weighted_precision == pytest.approx(1.0)
    # Raising the gate trades recall away.
    assert by_threshold[0.20].weighted_recall == pytest.approx(1.0)
    assert by_threshold[0.30].weighted_recall == pytest.approx(0.5)


def test_choose_threshold_picks_the_smallest_qualifying_gate() -> None:
    pairs = tuple(
        [_pair(0.05, REJECT) for _ in range(40)]
        + [_pair(0.30, ACCEPT) for _ in range(60)]
    )
    entries = sweep_thresholds(pairs, minimum_effective_sample=10.0)
    chosen = choose_threshold(entries, target_precision=0.90)
    assert chosen is not None
    assert chosen.threshold == pytest.approx(0.30)


def test_thin_support_is_undecidable_rather_than_a_confident_answer() -> None:
    # Three clean accepts look perfect but cannot justify a policy change.
    pairs = tuple(_pair(0.40, ACCEPT) for _ in range(3))
    entries = sweep_thresholds(pairs, minimum_effective_sample=30.0)
    assert all(not entry.decidable for entry in entries)
    assert choose_threshold(entries, target_precision=0.95) is None


def test_unreachable_target_returns_no_threshold() -> None:
    pairs = tuple(
        [_pair(0.10, REJECT) for _ in range(50)]
        + [_pair(0.50, REJECT) for _ in range(50)]
    )
    entries = sweep_thresholds(pairs, minimum_effective_sample=10.0)
    assert choose_threshold(entries, target_precision=0.95) is None


def test_sweep_rejects_unsupported_confidence_and_empty_input() -> None:
    assert sweep_thresholds(()) == ()
    with pytest.raises(ValueError):
        sweep_thresholds((_pair(0.1, ACCEPT),), confidence=0.80)
    with pytest.raises(ValueError):
        choose_threshold((), target_precision=1.0)


def test_high_margin_sampling_filter_keeps_only_target_boundary() -> None:
    rng = __import__("random").Random(7)
    items = [
        SimpleNamespace(band="0.25-0.30", separation_margin=0.29),
        SimpleNamespace(band="0.25-0.30", separation_margin=0.30),
        SimpleNamespace(band="0.30-0.40", separation_margin=0.30),
        SimpleNamespace(band="0.30-0.40", separation_margin=0.35),
        SimpleNamespace(band="0.40-1.00", separation_margin=0.60),
    ]

    selected = select_stratified(  # type: ignore[arg-type]
        items, per_band=25, rng=rng, min_margin=0.30, max_margin=1.0
    )

    assert [item.separation_margin for item in selected] == [0.3, 0.35, 0.6]


def test_seek_keys_sample_the_whole_prefix_space_reproducibly() -> None:
    keys = _seek_keys(100, random.Random(20260824))
    assert keys == _seek_keys(100, random.Random(20260824))
    assert len(keys) == len(set(keys)) == 100
    assert keys == tuple(sorted(keys))
    # The previous sort-then-truncate implementation excluded the upper half.
    midpoint = PLATE_LETTERS[len(PLATE_LETTERS) // 2]
    assert 30 <= sum(key[0] >= midpoint for key in keys) <= 70


def test_seek_keys_exhaust_the_prefix_space_without_duplicate_seeks() -> None:
    capacity = len(PLATE_LETTERS) ** 3
    assert len(_seek_keys(capacity + 1, random.Random(7))) == capacity
    with pytest.raises(ValueError, match="positive"):
        _seek_keys(0, random.Random(7))


def test_fitter_rejects_weights_from_another_batch_before_database_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    weights = tmp_path / "weights.json"
    weights.write_text(json.dumps({"batch_label": "another-batch", "per_band_population": {}}))
    monkeypatch.setattr(sys, "argv", [
        "fit-margin", "--batch-label", "expected-batch", "--band-weights", str(weights)
    ])
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    connect = MagicMock(side_effect=AssertionError("must validate before connecting"))
    monkeypatch.setattr(fit_margin_threshold.psycopg, "connect", connect)
    with pytest.raises(ValueError, match="batch"):
        fit_margin_threshold.main()
    connect.assert_not_called()


@pytest.mark.parametrize("changed_pin", ["source_version", "candidate_catalog_version", "seed"])
def test_fitter_rejects_verdicts_from_different_pinned_inputs(changed_pin: str) -> None:
    pins = {
        "source_version": "source-v1",
        "normalization_rule_version": "rules-v1",
        "candidate_catalog_version": "catalog-v1",
        "seed": 7,
    }
    mismatched = {**pins, changed_pin: "different"}
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        ("match_margin_calibration", json.dumps({
            "pins": mismatched, "separation_margin": 0.4, "band": "0.40-1.00"
        }), "resolved", {"verdict": "accept"})
    ]
    with pytest.raises(ValueError, match="pins"):
        fit_margin_threshold.load_verdicts(
            connection, batch_label="batch", expected_pins=pins
        )


def test_fitter_preserves_valid_labels_and_counts_pending_reviews() -> None:
    pins = {"source_version": "source-v1", "seed": 7}
    detail = json.dumps({"pins": pins, "separation_margin": 0.4, "band": "0.40-1.00"})
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        ("match_margin_calibration", detail, "resolved", {"verdict": "accept"}),
        ("match_margin_calibration", detail, "resolved", {"verdict": "unsure"}),
        ("match_margin_calibration", detail, "pending", None),
    ]
    verdicts, pending = fit_margin_threshold.load_verdicts(
        connection, batch_label="batch", expected_pins=pins
    )
    assert verdicts == [(0.4, "0.40-1.00", "accept"), (0.4, "0.40-1.00", "unsure")]
    assert pending == 1


def test_fitter_rejects_non_calibration_rows() -> None:
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        ("manufacturer_missing", "{}", "resolved", {"verdict": "accept"})
    ]
    with pytest.raises(ValueError, match="non-calibration"):
        fit_margin_threshold.load_verdicts(connection, batch_label="batch", expected_pins={})


@pytest.mark.parametrize("invalid", ["pins", "seed"])
def test_fitter_requires_reproducible_manifest_before_database_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    payload = {
        "batch_label": "batch", "seed": 7,
        "pins": {"source_version": "source", "normalization_rule_version": "rules",
                 "candidate_catalog_version": "catalog"},
    }
    del payload[invalid]
    weights = tmp_path / "weights.json"
    weights.write_text(json.dumps(payload))
    monkeypatch.setattr(sys, "argv", [
        "fit-margin", "--batch-label", "batch", "--band-weights", str(weights)
    ])
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    connect = MagicMock(side_effect=AssertionError("must validate before connecting"))
    monkeypatch.setattr(fit_margin_threshold.psycopg, "connect", connect)
    with pytest.raises(ValueError, match=invalid):
        fit_margin_threshold.main()
    connect.assert_not_called()
