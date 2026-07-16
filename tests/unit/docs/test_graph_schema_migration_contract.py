import re
from pathlib import Path

from ingestion.graph_migrations import GRAPH_MIGRATION_STATEMENTS

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DOC = REPO_ROOT / "docs" / "graph-schema-design.md"


def documented_migration_names() -> set[str]:
    text = SCHEMA_DOC.read_text(encoding="utf-8")
    section = text.split("## 8. Constraints and indexes", maxsplit=1)[1].split(
        "## 9.", maxsplit=1
    )[0]
    return set(re.findall(r"^\| `([a-z_]+)` \|", section, flags=re.MULTILINE))


def test_documented_names_match_migration_module_exactly() -> None:
    module_names = {statement.name for statement in GRAPH_MIGRATION_STATEMENTS}
    assert documented_migration_names() == module_names


def test_documented_runner_command_exists() -> None:
    text = SCHEMA_DOC.read_text(encoding="utf-8")
    assert "northstar-ingest migrate-graph" in text
