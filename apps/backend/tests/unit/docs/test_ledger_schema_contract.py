import re
from pathlib import Path

from ingestion.ledger_migrations import LEDGER_MIGRATION_STATEMENTS

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DOC = REPO_ROOT / "docs" / "ledger-schema-design.md"


def documented_migration_names() -> set[str]:
    text = SCHEMA_DOC.read_text(encoding="utf-8")
    section = text.split("## 7. Migration runner", maxsplit=1)[1].split(
        "## 8.", maxsplit=1
    )[0]
    return set(re.findall(r"^\| `([a-z_]+)` \|", section, flags=re.MULTILINE))


def test_documented_names_match_migration_module_exactly() -> None:
    module_names = {statement.name for statement in LEDGER_MIGRATION_STATEMENTS}
    assert documented_migration_names() == module_names


def test_documented_runner_command_exists() -> None:
    text = SCHEMA_DOC.read_text(encoding="utf-8")
    assert "northstar-ingest migrate-ledger" in text


def test_documented_contract_covers_story_criteria() -> None:
    text = SCHEMA_DOC.read_text(encoding="utf-8")

    # SCRUM-56: columns and index strategy, JSONB/array justified.
    for column in (
        "source",
        "event_id",
        "cost_eur",
        "attributes_added",
        "target_node_id",
        "nodes_benefited",
        "confidence",
        "created_at",
    ):
        assert f"`{column}`" in text, column
    assert "Array, not JSONB" in text
    assert "JSONB, not columns" in text
    # SCRUM-58: append-only policy, correction pattern, provenance rule.
    assert "append-only" in text
    assert "correction pattern" in text
    assert "corrects_ledger_id" in text
    assert "Correction chains are linear and node-local" in text
    assert "one event cannot be recorded twice" in text
    assert "Every graph write records a ledger entry" in text
    # SCRUM-59: examples with source and confidence for both sources.
    assert 'source="tecdoc"' in text
    assert 'source="transportstyrelsen"' in text
    assert "fetch_entries_for_node" in text
    # SCRUM-13/68 alignment: evidence lives here, not in parallel edges.
    assert "never as parallel edges" in text or "never into parallel" in text
