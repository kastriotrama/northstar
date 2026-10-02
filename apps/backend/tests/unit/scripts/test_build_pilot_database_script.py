"""The pilot database script: arguments and the refusals that need no database."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from api.app.features.vehicle_matching.repository import SAMPLE_SEED
from ingestion.pilot_database import (
    MANIFEST_KEY,
    Check,
    PilotBuildError,
    PilotOptions,
    PilotOutcome,
    PilotPlan,
)
from scripts import build_pilot_database as script

REQUIRED = ["--target-database", "northstar_pilot", "--catalog-batch", "batch-1"]


def test_the_defaults_are_a_dry_run_of_the_500k_registered_passenger_slice() -> None:
    args = script.parse_arguments(REQUIRED)

    assert (args.size, args.seed, args.scope) == (500_000, SAMPLE_SEED, "passenger")
    assert args.seed == "northstar-match-impact-v1"
    assert args.manifest is None and args.verify is None
    assert script.default_manifest_path("northstar_pilot") == Path(
        "northstar_pilot.manifest.json"
    )
    assert args.registered_only is True
    assert args.commit is False and args.replace is False
    assert args.maintenance_database == "postgres"


def test_registered_only_can_be_switched_off() -> None:
    assert script.parse_arguments([*REQUIRED, "--no-registered-only"]).registered_only is False


def test_target_and_catalog_batch_are_required() -> None:
    with pytest.raises(SystemExit):
        script.parse_arguments(["--target-database", "northstar_pilot"])
    with pytest.raises(SystemExit):
        script.parse_arguments(["--catalog-batch", "batch-1"])


def test_replace_without_commit_is_refused_before_any_connection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unreachable(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the build must not start")

    monkeypatch.setattr(script, "build_pilot_database", unreachable)

    assert script.main([*REQUIRED, "--replace"]) == 2
    assert "--replace only makes sense with --commit" in capsys.readouterr().out


def test_arguments_reach_the_build_and_a_refusal_exits_with_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    def refuse(source_url: str, options: PilotOptions, **kwargs: Any) -> None:
        seen.update(kwargs, options=options, source_url=source_url)
        raise PilotBuildError("database 'northstar_pilot' exists")

    monkeypatch.setattr(script, "build_pilot_database", refuse)
    monkeypatch.setattr(script, "get_ingestion_settings",
                        lambda: type("Settings", (), {"database_url": "postgresql://source"})())

    status = script.main([*REQUIRED, "--size", "1000", "--seed", "s", "--commit", "--replace",
                          "--chunk-size", "50"])

    assert status == 2
    assert capsys.readouterr().out.strip() == "error: database 'northstar_pilot' exists"
    assert seen["options"] == PilotOptions("northstar_pilot", "batch-1", size=1000, seed="s")
    assert (seen["commit"], seen["replace"], seen["chunk_size"]) == (True, True, 50)
    assert seen["source_url"] == "postgresql://source"
    assert seen["manifest_path"] == Path("northstar_pilot.manifest.json")


def _outcome(*checks: Check, built: bool = True, problems: tuple[str, ...] = ()) -> PilotOutcome:
    plan = PilotPlan(
        options=PilotOptions("northstar_pilot", "batch-1"), source_database="app",
        target_exists=False, target_marker=None, eligible_vehicles=10, slice_vehicles=5,
        slice_records=5, catalog_status="completed", tables=(), problems=problems, warnings=(),
    )
    return PilotOutcome(plan, built=built, checks=checks)


def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(script, "get_ingestion_settings",
                        lambda: type("Settings", (), {"database_url": "postgresql://source"})())


def test_a_failed_check_exits_with_1_and_a_verified_build_with_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _settings(monkeypatch)
    passed = Check("slice size", True, "5 vehicles, asked for 5")
    failed = Check("core.vehicles (class B)", False, "4 rows, the source has 5")

    monkeypatch.setattr(script, "build_pilot_database", lambda *_a, **_k: _outcome(passed, failed))
    assert script.main([*REQUIRED, "--commit"]) == 1
    assert "1 FAILED -- do not ship this database" in capsys.readouterr().out

    monkeypatch.setattr(script, "build_pilot_database", lambda *_a, **_k: _outcome(passed))
    assert script.main([*REQUIRED, "--commit"]) == 0


def test_a_dry_run_with_a_problem_exits_with_1_and_passes_no_manifest_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _settings(monkeypatch)
    seen: dict[str, Any] = {}

    def planned(_url: str, _options: PilotOptions, **kwargs: Any) -> PilotOutcome:
        seen.update(kwargs)
        return _outcome(built=False, problems=tuple(seen.get("problems", ())))

    monkeypatch.setattr(script, "build_pilot_database", planned)
    assert script.main(REQUIRED) == 0
    assert seen["manifest_path"] is None and seen["commit"] is False

    seen["problems"] = ("database 'northstar_pilot' exists and is not a pilot build",)
    assert script.main(REQUIRED) == 1
    assert "Dry run" in capsys.readouterr().out


def test_a_manifest_folder_that_does_not_exist_is_refused_before_the_build(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def unreachable(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the build must not start")

    monkeypatch.setattr(script, "build_pilot_database", unreachable)

    status = script.main([*REQUIRED, "--commit", "--manifest", str(tmp_path / "gone" / "m.json")])

    assert status == 2
    assert "does not exist" in capsys.readouterr().out


def _manifest_file(tmp_path: Path) -> Path:
    path = tmp_path / "pilot.manifest.json"
    path.write_text(json.dumps({
        MANIFEST_KEY: 1, "seed": "s", "size": 5, "catalog_batch": "batch-1",
        "source_snapshot_at": "2026-10-02T08:00:00+00:00",
        "tables": {"core.vehicles": {"class": "B", "rows": 5, "checksum": "1:2",
                                     "columns": ["vehicle_id"]}},
    }))
    return path


def test_verify_needs_only_a_database_and_a_manifest(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _settings(monkeypatch)
    seen: dict[str, Any] = {}

    def verified(url: str, database: str, manifest: dict[str, Any]) -> tuple[Check, ...]:
        seen.update(url=url, database=database, manifest=manifest)
        return (Check("core.vehicles (class B)", True, "5 rows, content equals the manifest"),)

    monkeypatch.setattr(script, "verify_pilot_database", verified)
    monkeypatch.setattr(script, "build_pilot_database",
                        lambda *_a, **_k: pytest.fail("--verify must not build"))

    assert script.main(["--verify", "live_db", "--manifest", str(_manifest_file(tmp_path))]) == 0
    assert (seen["url"], seen["database"]) == ("postgresql://source", "live_db")
    assert seen["manifest"]["tables"]["core.vehicles"]["rows"] == 5
    assert "1 of 1 checks passed" in capsys.readouterr().out


def test_verify_exits_with_1_on_a_mismatch_and_names_the_table(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _settings(monkeypatch)
    monkeypatch.setattr(script, "verify_pilot_database", lambda *_a: (
        Check("core.vehicles (class B)", False, "4 rows, the manifest has 5"),
    ))

    assert script.main(["--verify", "live_db", "--manifest", str(_manifest_file(tmp_path))]) == 1
    printed = capsys.readouterr().out
    assert "FAIL  core.vehicles (class B): 4 rows, the manifest has 5" in printed
    assert "1 FAILED" in printed


def test_verify_refuses_a_missing_or_broken_manifest_and_build_options(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _settings(monkeypatch)
    monkeypatch.setattr(script, "verify_pilot_database",
                        lambda *_a: pytest.fail("nothing may be read"))
    broken = tmp_path / "broken.json"
    broken.write_text("{}")

    assert script.main(["--verify", "live_db"]) == 2
    assert "--verify needs --manifest" in capsys.readouterr().out
    assert script.main(["--verify", "live_db", "--manifest", str(tmp_path / "none.json")]) == 2
    assert "cannot read the manifest" in capsys.readouterr().out
    assert script.main(["--verify", "live_db", "--manifest", str(broken)]) == 2
    assert "not a pilot manifest" in capsys.readouterr().out
    assert script.main(["--verify", "live_db", "--manifest", str(broken), "--commit"]) == 2
    assert "--verify builds nothing" in capsys.readouterr().out
