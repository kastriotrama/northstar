import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DOC = REPO_ROOT / "docs" / "graph-schema-design.md"

EXPECTED_RELATIONSHIPS = {
    "BUILT_ON",
    "HAS_BODY",
    "MADE_BY",
    "PLATFORM_OF",
    "REFERS_TO",
    "USES_ENGINE",
    "USES_TRANSMISSION",
    "VARIANT_OF",
}
EXPECTED_QUERIES = {
    "conflict_lookup",
    "gap_detection",
    "k_type_resolve",
    "plate_resolve",
    "sibling_amortization",
    "structured_form_search",
}


def schema_text() -> str:
    return SCHEMA_DOC.read_text(encoding="utf-8")


def relationship_names(text: str) -> set[str]:
    catalog = text.split("### 5.2 Relationship catalog", maxsplit=1)[1].split(
        "### 5.3 Cardinality and write rules", maxsplit=1
    )[0]
    return set(re.findall(r"^\| `([A-Z][A-Z_]*)` \|", catalog, flags=re.MULTILINE))


def query_names(text: str) -> set[str]:
    starts = set(re.findall(r"<!-- query:([a-z_]+):start -->", text))
    ends = set(re.findall(r"<!-- query:([a-z_]+):end -->", text))
    assert starts == ends
    return starts


def test_relationship_catalog_is_complete_and_exact() -> None:
    assert relationship_names(schema_text()) == EXPECTED_RELATIONSHIPS


def test_all_six_executable_query_blocks_are_present() -> None:
    assert query_names(schema_text()) == EXPECTED_QUERIES


def test_removed_relationship_names_do_not_reappear() -> None:
    text = schema_text()
    for removed_name in ("BELONGS_TO", "HAS_TRANSMISSION", "MEMBER_OF"):
        assert removed_name not in text
