import re
from pathlib import Path

from northstar.node_ids import NodeIdPrefix, is_valid_node_id

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DOC = REPO_ROOT / "docs" / "graph-schema-design.md"


def schema_text() -> str:
    return SCHEMA_DOC.read_text(encoding="utf-8")


def test_documented_prefixes_match_the_shared_utility() -> None:
    text = schema_text()
    prefix_rows = re.findall(
        r"^\| `(?:Manufacturer|ModelFamily|Platform|Engine|Transmission|BodyType|FuelType|VehicleVariant|Alias)` "
        r"\| `([A-Z]{3})` \| `[A-Z]{3}-<ULID>` \|$",
        text,
        flags=re.MULTILINE,
    )

    assert prefix_rows == [prefix.value for prefix in NodeIdPrefix]


def test_documented_production_example_is_valid() -> None:
    text = schema_text()
    examples = re.findall(r"`([A-Z]{3}-[0-9A-HJKMNP-TV-Z]{26})`", text)

    assert examples
    assert all(is_valid_node_id(example) for example in examples)


def contains_phrase(text: str, phrase: str) -> bool:
    """Match a documented phrase regardless of markdown line wrapping."""

    pattern = r"\s+".join(re.escape(word) for word in phrase.split())
    return re.search(pattern, text) is not None


def test_documentation_keeps_external_codes_out_of_node_ids() -> None:
    text = schema_text()
    guide_text = (REPO_ROOT / "docs" / "STAKEHOLDER_DECISION_GUIDE.md").read_text(
        encoding="utf-8"
    )

    assert contains_phrase(text, "`ABC123` and `13902` never become node IDs")
    assert contains_phrase(text, "look up and reconcile existing identity before minting")
    assert contains_phrase(
        guide_text, "ID generation provides uniqueness; lookup and reconciliation"
    )
