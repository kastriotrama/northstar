from ingestion.cli import build_parser, main
from pytest import CaptureFixture


def test_list_commands_prints_stub_jobs(capsys: CaptureFixture[str]) -> None:
    exit_code = main(["list-commands"])

    output = capsys.readouterr().out

    assert exit_code == 0
    assert "tecdoc" in output
    assert "transportstyrelsen" in output


def test_parser_registers_stub_job_commands() -> None:
    parser = build_parser()

    parsed_tecdoc = parser.parse_args(["tecdoc"])
    parsed_transportstyrelsen = parser.parse_args(["transportstyrelsen"])

    assert parsed_tecdoc.command == "tecdoc"
    assert parsed_transportstyrelsen.command == "transportstyrelsen"
