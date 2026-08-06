import pytest
from pytest import CaptureFixture

from ingestion.cli import build_parser, main


def test_list_commands_prints_stub_jobs(capsys: CaptureFixture[str]) -> None:
    exit_code = main(["list-commands"])

    output = capsys.readouterr().out

    assert exit_code == 0
    assert "healthcheck" in output
    assert "load" in output
    assert "normalize" in output
    assert "graph-write" in output
    assert "index" in output
    assert "tecdoc" in output
    assert "TecDoc" in output
    assert "transportstyrelsen" in output
    assert "Transportstyrelsen" in output
    assert "migrate-review-queue" in output
    assert "migrate-job-bookkeeping" in output
    assert "migrate-confidence-routing" in output
    assert "import-normalization-bundle" in output


def test_parser_registers_stub_job_commands() -> None:
    parser = build_parser()

    parsed_tecdoc = parser.parse_args(["tecdoc", "--batch-id", "tecdoc-batch-1"])
    parsed_transportstyrelsen = parser.parse_args(
        ["transportstyrelsen", "--batch-id", "transportstyrelsen-batch-1"],
    )
    parsed_healthcheck = parser.parse_args(["healthcheck", "--batch-id", "healthcheck-1"])

    assert parsed_tecdoc.command == "tecdoc"
    assert parsed_tecdoc.batch_id == "tecdoc-batch-1"
    assert parsed_transportstyrelsen.command == "transportstyrelsen"
    assert parsed_transportstyrelsen.batch_id == "transportstyrelsen-batch-1"
    assert parsed_healthcheck.command == "healthcheck"
    assert parsed_healthcheck.batch_id == "healthcheck-1"


def test_stub_job_command_runs_with_batch_id() -> None:
    assert main(["tecdoc", "--batch-id", "tecdoc-batch-1"]) == 0
    assert main(["healthcheck", "--batch-id", "healthcheck-1"]) == 0


def test_normalize_requires_an_explicit_source_batch() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["normalize"])

    args = parser.parse_args(["normalize", "--batch-id", "ts-pilot"])
    assert args.batch_id == "ts-pilot"


def test_bundle_import_requires_an_explicit_excel_file() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["import-normalization-bundle"])

    args = parser.parse_args(
        ["import-normalization-bundle", "--file", "snapshot.xlsx"]
    )
    assert args.command == "import-normalization-bundle"
    assert str(args.file) == "snapshot.xlsx"
