from ingestion.cli import build_parser, main
from pytest import CaptureFixture


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
