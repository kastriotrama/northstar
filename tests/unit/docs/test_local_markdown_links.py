import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_DIR = REPO_ROOT / "docs"

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)\s]*)?\)")
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:")


def iter_local_links(docs_dir: Path = DOCS_DIR) -> list[tuple[Path, str]]:
    links: list[tuple[Path, str]] = []
    for doc in sorted(docs_dir.rglob("*.md")):
        for target in MARKDOWN_LINK.findall(doc.read_text(encoding="utf-8")):
            if not target.startswith(EXTERNAL_PREFIXES):
                links.append((doc, target))
    return links


def find_broken_local_links(
    docs_dir: Path = DOCS_DIR, repo_root: Path = REPO_ROOT
) -> list[str]:
    broken: list[str] = []
    for doc, target in iter_local_links(docs_dir):
        resolved = (doc.parent / target).resolve()
        label = f"{doc.relative_to(repo_root)} -> {target}"
        try:
            resolved.relative_to(repo_root)
        except ValueError:
            broken.append(f"{label} (outside repository)")
            continue
        if not resolved.is_file():
            broken.append(label)
    return broken


def test_local_markdown_links_resolve() -> None:
    broken = find_broken_local_links()

    assert not broken, "Broken local markdown links:\n" + "\n".join(broken)


def test_nested_markdown_links_are_checked(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    docs_dir = repo_root / "docs"
    nested_dir = docs_dir / "decisions"
    nested_dir.mkdir(parents=True)
    (nested_dir / "README.md").write_text("[missing](missing.md)", encoding="utf-8")

    assert find_broken_local_links(docs_dir, repo_root) == [
        "docs/decisions/README.md -> missing.md"
    ]


def test_links_cannot_escape_repository(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    docs_dir = repo_root / "docs"
    docs_dir.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (docs_dir / "README.md").write_text("[outside](../../outside.md)", encoding="utf-8")

    assert find_broken_local_links(docs_dir, repo_root) == [
        "docs/README.md -> ../../outside.md (outside repository)"
    ]
