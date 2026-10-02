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


def test_match_parser_accepts_complete_context_policy_pin() -> None:
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
            "rules-v1",
            "--candidate-catalog-version",
            "tecdoc-v1",
            "--expected-ktype-count",
            "72570",
            "--policy-version",
            "routing-v1",
            "--code-revision",
            "abc123",
            "--context-policy",
            "reviewed-policy.json",
            "--context-policy-version",
            "context-v2",
            "--context-policy-sha256",
            "deadbeef",
        ]
    )

    assert str(args.context_policy) == "reviewed-policy.json"
    assert args.context_policy_version == "context-v2"
    assert args.context_policy_sha256 == "deadbeef"


def test_match_command_rejects_partial_context_policy_pin(tmp_path) -> None:
    manifest = tmp_path / "policy.json"
    manifest.write_text("{}")

    exit_code = main(
        [
            "match-ts-tecdoc",
            "--operation-id",
            "00000000-0000-4000-8000-000000000001",
            "--source-version",
            "ts-2026-08",
            "--source-batch-prefix",
            "passenger-part-",
            "--expected-source-rows",
            "1",
            "--normalization-rule-version",
            "rules-v1",
            "--candidate-catalog-version",
            "tecdoc-v1",
            "--expected-ktype-count",
            "1",
            "--policy-version",
            "routing-v1",
            "--code-revision",
            "abc123",
            "--context-policy",
            str(manifest),
        ]
    )

    assert exit_code == 2


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


def test_deploy_time_migration_applies_only_the_vehicle_facts_schema(
    monkeypatch: pytest.MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    """Runs on every deploy, so it must be the schema and nothing heavier."""

    from contextlib import contextmanager

    from ingestion import cli

    calls: list[str] = []

    class _Postgres:
        @contextmanager
        def connect(self):  # type: ignore[no-untyped-def]
            yield "connection"

    class _Datastores:
        postgres = _Postgres()

    monkeypatch.setattr(cli.DatastoreClients, "from_settings", lambda _settings: _Datastores())
    monkeypatch.setattr(
        cli,
        "run_vehicle_facts_migrations",
        lambda connection: calls.append(connection) or ("add_vehicle_facts_vehicle_scope_column",),
    )
    monkeypatch.setattr(cli, "refresh_vehicle_facts", lambda *a, **k: pytest.fail("no refresh"))
    monkeypatch.setattr(cli, "backfill_canonical_columns", lambda *a, **k: pytest.fail("no backfill"))

    assert main(["migrate-vehicle-facts"]) == 0
    assert calls == ["connection"]
    assert "add_vehicle_facts_vehicle_scope_column" in capsys.readouterr().out


def test_deploy_time_migration_fails_the_deploy_when_the_schema_cannot_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deploy.sh runs under `set -e`: a non-zero exit must stop the rollout
    before an API that needs the new columns goes live without them."""

    from ingestion import cli

    def _boom(_settings: object) -> object:
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(cli.DatastoreClients, "from_settings", _boom)

    assert main(["migrate-vehicle-facts"]) == 1


def test_canonical_backfill_parser_is_resumable() -> None:
    args = build_parser().parse_args(
        ["backfill-canonical-vehicle-facts", "--since", "4242", "--max-pages", "3"]
    )

    assert args.since == 4242
    assert args.max_pages == 3


def test_scope_backfill_parser_is_resumable() -> None:
    args = build_parser().parse_args(["backfill-vehicle-scope", "--since", "955", "--max-pages", "2"])

    assert args.since == 955
    assert args.max_pages == 2


def test_vehicle_core_commands_are_registered(capsys: CaptureFixture[str]) -> None:
    main(["list-commands"])
    output = capsys.readouterr().out

    for command in (
        "migrate-vehicle-core",
        "backfill-vehicle-core",
        "import-ais-vin-export",
        "learn-vehicle-rules",
        "apply-vehicle-rules",
    ):
        assert command in output


def test_vehicle_core_backfill_parser_is_resumable() -> None:
    args = build_parser().parse_args(["backfill-vehicle-core", "--since", "77", "--max-pages", "4"])

    assert args.since == 77
    assert args.max_pages == 4


def test_ais_import_requires_a_file() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["import-ais-vin-export"])


def test_learning_rules_is_a_dry_run_unless_activated() -> None:
    args = build_parser().parse_args(["learn-vehicle-rules", "--family", "ENG-VV"])

    assert args.activate is False
    assert args.family == ["ENG-VV"]


def test_vehicle_core_migration_fails_the_deploy_when_the_schema_cannot_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ingestion import cli

    def _boom(_settings: object) -> object:
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(cli.DatastoreClients, "from_settings", _boom)

    assert main(["migrate-vehicle-core"]) == 1


def test_a_missing_ais_export_fails_without_touching_the_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from ingestion import cli

    monkeypatch.setattr(
        cli.DatastoreClients, "from_settings", lambda _s: pytest.fail("no database")
    )

    assert main(["import-ais-vin-export", "--file", str(tmp_path / "missing.xml")]) == 1
