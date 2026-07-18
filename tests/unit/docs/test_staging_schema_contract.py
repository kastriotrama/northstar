import re
from pathlib import Path

from ingestion.staging_migrations import STAGING_MIGRATION_STATEMENTS

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DOC = REPO_ROOT / "docs" / "staging-schema-design.md"


def documented_migration_names() -> set[str]:
    text = SCHEMA_DOC.read_text(encoding="utf-8")
    section = text.split("## 7. Migration runner", maxsplit=1)[1].split(
        "## 8.", maxsplit=1
    )[0]
    return set(re.findall(r"^\| `([a-z_]+)` \|", section, flags=re.MULTILINE))


def test_documented_names_match_migration_module_exactly() -> None:
    module_names = {statement.name for statement in STAGING_MIGRATION_STATEMENTS}
    assert documented_migration_names() == module_names


def test_documented_runner_command_exists() -> None:
    text = SCHEMA_DOC.read_text(encoding="utf-8")
    assert "northstar-ingest migrate-staging" in text


def test_documented_loader_entry_point_exists() -> None:
    text = SCHEMA_DOC.read_text(encoding="utf-8")
    assert "ingestion.staging_loaders import copy_raw_records" in text


def test_documented_contract_covers_subtask_criteria() -> None:
    text = SCHEMA_DOC.read_text(encoding="utf-8")

    # SCRUM-52: ownership/permission assumptions.
    assert "Ownership and permission assumptions" in text
    # SCRUM-53 / SCRUM-55: required row-count validation.
    assert "Row-count validation is required" in text
    assert "count_batch_rows" in text
    # SCRUM-54: source references preserved via the raw payload.
    assert "Source references live inside `raw_record`" in text
