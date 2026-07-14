import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_DIR = REPO_ROOT / "docs"

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)\s]*)?\)")
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:")


def iter_local_links() -> list[tuple[Path, str]]:
    links: list[tuple[Path, str]] = []
    for doc in sorted(DOCS_DIR.glob("*.md")):
        for target in MARKDOWN_LINK.findall(doc.read_text(encoding="utf-8")):
            if not target.startswith(EXTERNAL_PREFIXES):
                links.append((doc, target))
    return links


def test_local_markdown_links_resolve() -> None:
    broken: list[str] = []
    for doc, target in iter_local_links():
        resolved = (doc.parent / target).resolve()
        if not resolved.exists():
            broken.append(f"{doc.relative_to(REPO_ROOT)} -> {target}")

    assert not broken, "Broken local markdown links:\n" + "\n".join(broken)
