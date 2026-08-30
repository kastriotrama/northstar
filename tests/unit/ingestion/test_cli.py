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
    assert "migrate-match-runs" in output
    assert "match-ts-tecdoc" in output
    assert "import-normalization-bundle" in output
    assert "export-rule-delta" in output
    assert "import-remote-passenger" in output


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


def test_parser_registers_version_pinned_match_audit() -> None:
    args = build_parser().parse_args(
        [
            "match-ts-tecdoc",
            "--operation-id",
            "00000000-0000-4000-8000-000000000001",
            "--source-version",
            "ts-2026-08",
            "--source-batch-prefix",
            "passenger-part-",
            "--expected-source-rows",
            "6515471",
            "--normalization-rule-version",
            "ts-review-20260817T073842135705Z",
            "--candidate-catalog-version",
            "tecdoc-0326",
            "--candidate-source",
            "postgres",
            "--expected-ktype-count",
            "55808",
            "--policy-version",
            "confidence-routing-v1",
            "--code-revision",
            "abc123",
            "--source-mode",
            "raw",
            "--max-batches",
            "2",
        ]
    )

    assert args.command == "match-ts-tecdoc"
    assert args.expected_source_rows == 6_515_471
    assert args.expected_ktype_count == 55_808
    assert args.candidate_source == "postgres"
    assert args.source_mode == "raw"
    assert args.max_batches == 2


def test_canonical_promotion_parser_supports_postgres_only_catalog_rebuild() -> None:
    args = build_parser().parse_args(
        [
            "promote-tecdoc-canonical",
            "--batch-id",
            "tecdoc-0326-catalog-v6",
            "--source-path",
            "/licensed/source",
            "--reference-path",
            "/licensed/reference",
            "--source-checksum",
            "abc123",
            "--candidate-catalog-only",
        ]
    )

    assert args.candidate_catalog_only is True


def test_commands_fail_safely_or_run_with_batch_id() -> None:
    # TecDoc is now a real job and refuses to run without version/license evidence.
    assert main(["tecdoc", "--batch-id", "tecdoc-batch-1"]) == 2
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

    args = parser.parse_args(["import-normalization-bundle", "--file", "snapshot.xlsx"])
    assert args.command == "import-normalization-bundle"
    assert str(args.file) == "snapshot.xlsx"


def test_rule_delta_export_defaults_to_latest_target() -> None:
    parser = build_parser()

    args = parser.parse_args(
        [
            "export-rule-delta",
            "--baseline-version",
            "rules-v1",
            "--output",
            "latest-rules.sql",
        ]
    )

    assert args.command == "export-rule-delta"
    assert args.baseline_version == "rules-v1"
    assert args.target_version is None
    assert str(args.output) == "latest-rules.sql"


def test_remote_passenger_import_uses_shared_contract_defaults() -> None:
    args = build_parser().parse_args(["import-remote-passenger", "--retain-raw"])

    assert args.prefix == "normalization-vdai-passenger-full-v323-20260817"
    assert args.batch_size == 25_000
    assert args.expected_source_count == 6_515_471
    assert args.retain_raw is True
    assert args.recover_stale_part is False
