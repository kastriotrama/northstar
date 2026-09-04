import json
from pathlib import Path
from typing import Any

import pytest

from ingestion.golden_corpus import (
    DEFAULT_CORPUS_PATH,
    GOLDEN_CORPUS_VERSION,
    GoldenCorpusError,
    approve_corpus,
    main,
    verify_corpus,
)


def _load_document(path: Path = DEFAULT_CORPUS_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_document(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_committed_corpus_has_required_size_coverage_and_approved_outputs() -> None:
    report = verify_corpus()
    document = _load_document()
    tags = {tag for case in document["cases"] for tag in case["tags"]}

    assert report.corpus_version == GOLDEN_CORPUS_VERSION
    assert report.case_count == 223
    assert report.normalization_count == 183
    assert report.reconciliation_count == 40
    assert {"common", "rare", "ambiguous", "review", "resolved", "provisional"} <= tags


def test_unapproved_change_reports_case_and_readable_unified_diff(tmp_path: Path) -> None:
    document = _load_document()
    changed_case = document["cases"][0]
    second_changed_case = document["cases"][1]
    changed_case["expected"]["status"] = "resolved"
    second_changed_case["expected"]["status"] = "resolved"
    corpus_path = tmp_path / "changed.json"
    _write_document(corpus_path, document)

    with pytest.raises(GoldenCorpusError) as captured:
        verify_corpus(corpus_path)

    message = str(captured.value)
    assert "2 unapproved golden regression(s)" in message
    assert changed_case["id"] in message
    assert second_changed_case["id"] in message
    assert f"--- approved/{changed_case['id']}" in message
    assert f"+++ actual/{changed_case['id']}" in message
    assert '-  "status": "resolved"' in message
    assert '+  "status": "provisional"' in message


def test_explicit_approval_replaces_expected_results_and_reverifies(tmp_path: Path) -> None:
    document = _load_document()
    document["cases"][0]["expected"] = {"kind": "normalization"}
    corpus_path = tmp_path / "approval.json"
    _write_document(corpus_path, document)

    report = approve_corpus(corpus_path)

    assert report.case_count == 223
    assert verify_corpus(corpus_path) == report


def test_sensitive_source_fields_are_rejected_before_execution(tmp_path: Path) -> None:
    document = _load_document()
    document["cases"][0]["input"]["raw_record"]["vin"] = "NOT-STORED"
    corpus_path = tmp_path / "sensitive.json"
    _write_document(corpus_path, document)

    with pytest.raises(GoldenCorpusError, match="sensitive field 'vin'"):
        verify_corpus(corpus_path)


def test_duplicate_case_ids_are_rejected(tmp_path: Path) -> None:
    document = _load_document()
    document["cases"][1]["id"] = document["cases"][0]["id"]
    corpus_path = tmp_path / "duplicate.json"
    _write_document(corpus_path, document)

    with pytest.raises(GoldenCorpusError, match="case IDs must be unique"):
        verify_corpus(corpus_path)


def test_cli_prints_case_summary(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(("verify", str(DEFAULT_CORPUS_PATH))) == 0
    output = capsys.readouterr().out

    assert "223 cases passed" in output
    assert "183 normalization" in output
    assert "40 reconciliation" in output
