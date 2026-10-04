"""Re-reading the tyre sizes of a stopped record changes its tyre part and nothing else."""

from __future__ import annotations

from typing import Any

from ingestion.normalization_rules import PIPELINE_VERSION, normalize_ts_record
from ingestion.tyre_reparse import reparse_label, reread

# A notation the parser reads today; the stored result below predates that.
_READABLE = "225/45ZR17 91W"
_TYPO = "22545R17X"


def _stopped(**other: Any) -> tuple[dict[str, Any], list[str]]:
    """A stored result as an older parser left it: the tyre texts parked as candidates."""

    payload = {
        "normalized": {"manufacturer": "Volvo", "model_family": "V70", "power_kw": 133,
                       **other.get("normalized", {})},
        "candidates": {"tyre_front": _READABLE, "tyre_rear": _READABLE,
                       **other.get("candidates", {})},
        "confidence": 0.55,
        "pipeline_version": "normalization-pipeline-v10",
        "decision_trace": [{"step": "kept"}],
    }
    return payload, ["tyre_size_unrecognized", *other.get("reasons", [])]


def test_todays_parser_reads_the_notation_this_test_relies_on() -> None:
    outcome = normalize_ts_record({"brand": "VOLVO", "tyre_front": _READABLE, "tyre_rear": _TYPO})
    assert isinstance(outcome.normalized.get("tyre_front"), dict)
    assert "tyre_size_unrecognized" in outcome.review_reasons  # the typo still stops


def test_a_readable_tyre_lifts_the_stop_and_leaves_every_other_value_alone() -> None:
    payload, reasons = _stopped()
    result = reread(payload, reasons, {"tyre_front": _READABLE, "tyre_rear": _READABLE})

    assert result is not None
    assert (result.status, result.confidence, result.review_reasons) == ("resolved", 0.95, ())
    normalized = result.payload["normalized"]
    assert {key: normalized[key] for key in ("manufacturer", "model_family", "power_kw")} == {
        "manufacturer": "Volvo", "model_family": "V70", "power_kw": 133}
    assert normalized["tyre_front"]["section_width_mm"] == 225
    assert normalized["tyre_staggered"] is False and normalized["rim_diameter_in"] == 17.0
    assert result.payload["candidates"] == {}
    assert result.payload["decision_trace"] == [{"step": "kept"}]  # carried over untouched
    # The stored result is not modified in place.
    assert payload["candidates"]["tyre_front"] == _READABLE and "tyre_front" not in payload["normalized"]


def test_a_tyre_that_still_does_not_read_leaves_the_record_as_it_is() -> None:
    payload, reasons = _stopped()
    assert reread(payload, reasons, {"tyre_front": _READABLE, "tyre_rear": _TYPO}) is None


def test_other_open_points_keep_the_record_where_they_put_it() -> None:
    payload, reasons = _stopped(reasons=["manufacturer_missing"])
    stopped = reread(payload, reasons, {"tyre_front": _READABLE})
    assert stopped is not None
    assert (stopped.status, stopped.review_reasons) == ("review_required", ("manufacturer_missing",))

    payload, reasons = _stopped(candidates={"type_approval": "odd*format"})
    provisional = reread(payload, reasons, {"tyre_front": _READABLE})
    assert provisional is not None
    assert (provisional.status, provisional.confidence) == ("provisional", 0.8)
    assert provisional.payload["candidates"] == {"type_approval": "odd*format"}


def test_the_label_names_the_step_and_the_parser_that_ran() -> None:
    label = reparse_label("normalization-pipeline-v10")
    assert label == f"normalization-pipeline-v10+tyres-{PIPELINE_VERSION.rsplit('-', 1)[-1]}"
