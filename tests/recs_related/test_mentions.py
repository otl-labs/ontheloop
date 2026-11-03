"""Hermetic: the text-mention channel over a tmp tree (writes only under tmp_path)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.recs_related.envelope import CHANNEL_OK
from scripts.recs_related.mentions import mentions_of

pytestmark = pytest.mark.unit


def _write(root: Path, rel: str, text: str = "") -> str:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return rel


def _tracked(root: Path) -> frozenset[str]:
    return frozenset(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    for rel in (
        "src/common/reader.py",
        "src/common/pkg/__init__.py",
        "scripts/tool.py",
        "tests/test_tool.py",
        "docs/contracts/ports.yaml",
        "docs/contracts/other.yaml",
        "infra/main.tf",
        "infra/unique_name.sh",
    ):
        _write(tmp_path, rel, "x = 1\n")
    return tmp_path


class TestMentions:
    def test_repo_paths_in_yaml_markdown_and_python_resolve(self, tree: Path):
        _write(tree, "docs/a.yaml", "owner: docs/contracts/ports.yaml\nwhere: `infra/main.tf`.\n")
        _write(tree, "docs/b.md", "See scripts/tool.py: then tests/test_tool.py, and src/common/reader.py.\n")
        _write(tree, "scripts/caller.py", 'PATH = "docs/contracts/other.yaml"\n')
        tracked = _tracked(tree)
        assert mentions_of("docs/a.yaml", tree, tracked).paths == {"docs/contracts/ports.yaml", "infra/main.tf"}
        assert mentions_of("docs/b.md", tree, tracked).paths == {
            "scripts/tool.py",
            "tests/test_tool.py",
            "src/common/reader.py",
        }
        assert mentions_of("scripts/caller.py", tree, tracked).paths == {"docs/contracts/other.yaml"}

    def test_dotted_modules_and_module_attributes_resolve(self, tree: Path):
        _write(
            tree, "tests/test_x.py", 'import src.common.reader\nmock.patch("scripts.tool.run")\nfrom src.common import pkg\n'
        )
        got = mentions_of("tests/test_x.py", tree, _tracked(tree))
        assert got.paths == {"src/common/reader.py", "scripts/tool.py"}

    def test_dotted_package_resolves_to_its_init(self, tree: Path):
        _write(tree, "docs/c.md", "uses src.common.pkg.helper today\n")
        assert mentions_of("docs/c.md", tree, _tracked(tree)).paths == {"src/common/pkg/__init__.py"}

    def test_quoted_unique_basename_resolves_but_an_ambiguous_one_does_not(self, tree: Path):
        _write(tree, "scripts/loader.py", 'A = "unique_name.sh"\nB = "ports.yaml"\nC = \'main.tf\'\n')
        _write(tree, "infra/ports.yaml", "x: 1\n")
        got = mentions_of("scripts/loader.py", tree, _tracked(tree))
        assert got.paths == {"infra/unique_name.sh", "infra/main.tf"}

    def test_quoted_path_prefix_must_be_a_tracked_path(self, tree: Path):
        _write(tree, "scripts/loader.py", 'A = "nowhere/ports.yaml"\nB = "docs/contracts/ports.yaml"\n')
        assert mentions_of("scripts/loader.py", tree, _tracked(tree)).paths == {"docs/contracts/ports.yaml"}

    def test_untracked_paths_self_mentions_and_bare_substring_lookalikes_drop(self, tree: Path):
        _write(
            tree,
            "docs/d.md",
            "docs/d.md itself; a/new/untracked.py; myscripts/tool.py.bak; prefix-docs/contracts/ports.yaml; "
            "scripts.toolbox; xscripts.tool\n",
        )
        got = mentions_of("docs/d.md", tree, _tracked(tree))
        assert got.paths == frozenset()
        assert got.status == CHANNEL_OK

    def test_untracked_query_file_reports_unavailable(self, tree: Path):
        got = mentions_of("docs/not_yet.md", tree, _tracked(tree))
        assert got.paths == frozenset() and got.status.startswith("unavailable: ") and "untracked" in got.status

    def test_unreadable_tracked_file_reports_unavailable(self, tree: Path):
        tracked = _tracked(tree) | {"docs/ghost.md"}
        got = mentions_of("docs/ghost.md", tree, tracked)
        assert got.status.startswith("unavailable: ") and "unreadable" in got.status

    def test_binary_text_is_decoded_leniently(self, tree: Path):
        (tree / "docs").mkdir(exist_ok=True)
        (tree / "docs/blob.bin").write_bytes(b"\xff\xfe scripts/tool.py \x00")
        got = mentions_of("docs/blob.bin", tree, _tracked(tree))
        assert got.paths == {"scripts/tool.py"}
