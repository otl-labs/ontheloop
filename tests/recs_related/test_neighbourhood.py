"""Hermetic: the structural channel over a tmp repo tree (no git, no network, writes only under tmp_path)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.recs_related import neighbourhood as nb
from scripts.recs_related.envelope import CHANNEL_OK

pytestmark = pytest.mark.unit


def _write(root: Path, rel: str, text: str = "") -> str:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return rel


def _tracked(root: Path) -> frozenset[str]:
    return frozenset(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("scripts.test_coverage_checker.ROOT", tmp_path)
    _write(tmp_path, "src/__init__.py")
    _write(tmp_path, "src/pkg/__init__.py")
    _write(tmp_path, "src/pkg/b.py", "VALUE = 1\n")
    _write(tmp_path, "src/pkg/a.py", "from src.pkg.b import VALUE\n")
    _write(tmp_path, "src/pkg/c.py", "from src.pkg.a import VALUE\n")
    _write(tmp_path, "src/pkg/notes.md", "notes\n")
    _write(tmp_path, "tests/__init__.py")
    _write(tmp_path, "tests/test_a.py", "def test_x():\n    assert 'src.pkg.a.VALUE'\n")
    return tmp_path


class TestNeighbourhood:
    def test_module_of(self):
        assert nb.module_of("src/pkg/a.py") == "src.pkg.a"
        assert nb.module_of("src/pkg/__init__.py") == "src.pkg"
        assert nb.module_of("docs/x.yaml") is None
        assert nb.module_of("other/x.py") is None
        assert nb.module_of("scripts/x.py") == "scripts.x"

    def test_path_of_prefers_module_file_then_package_init(self):
        tracked = frozenset({"src/pkg/a.py", "src/pkg/__init__.py"})
        assert nb.path_of("src.pkg.a", tracked) == "src/pkg/a.py"
        assert nb.path_of("src.pkg", tracked) == "src/pkg/__init__.py"
        assert nb.path_of("src.missing", tracked) is None

    def test_upstream_and_downstream_one_hop(self, repo: Path):
        got = nb.neighbours_of("src/pkg/a.py", repo, _tracked(repo))
        assert got.imports == CHANNEL_OK
        assert nb.RELATION_UPSTREAM in got.relations["src/pkg/b.py"]
        assert nb.RELATION_DOWNSTREAM in got.relations["src/pkg/c.py"]
        assert "src/pkg/a.py" not in got.relations

    def test_two_hops_are_not_reached(self, repo: Path):
        got = nb.neighbours_of("src/pkg/b.py", repo, _tracked(repo))
        assert nb.RELATION_DOWNSTREAM in got.relations["src/pkg/a.py"]
        assert nb.RELATION_DOWNSTREAM not in got.relations["src/pkg/c.py"]

    def test_patch_string_edge_in_either_direction(self, repo: Path):
        incoming = nb.neighbours_of("src/pkg/a.py", repo, _tracked(repo))
        assert nb.RELATION_PATCH_STRING in incoming.relations["tests/test_a.py"]
        outgoing = nb.neighbours_of("tests/test_a.py", repo, _tracked(repo))
        assert nb.RELATION_PATCH_STRING in outgoing.relations["src/pkg/a.py"]

    def test_package_siblings_include_non_python_files(self, repo: Path):
        got = nb.neighbours_of("src/pkg/a.py", repo, _tracked(repo))
        assert nb.RELATION_SIBLING in got.relations["src/pkg/notes.md"]
        assert nb.RELATION_SIBLING in got.relations["src/pkg/c.py"]
        assert "tests/test_a.py" not in {p for p, r in got.relations.items() if nb.RELATION_SIBLING in r}

    def test_tests_mirror_through_map_source_to_test(self, repo: Path):
        _write(repo, "src/common/x.py", "X = 1\n")
        _write(repo, "tests/test_x.py", "def test_x():\n    pass\n")
        got = nb.neighbours_of("src/common/x.py", repo, _tracked(repo))
        assert nb.RELATION_TESTS_MIRROR in got.relations["tests/test_x.py"]

    def test_tests_mirror_through_the_stem_rule_when_the_checker_maps_nothing(self, repo: Path):
        _write(repo, "scripts/newpkg/widget.py", "W = 1\n")
        _write(repo, "tests/newpkg/test_widget.py", "def test_w():\n    pass\n")
        assert nb.map_source_to_test(repo / "scripts/newpkg/widget.py") is None
        got = nb.neighbours_of("scripts/newpkg/widget.py", repo, _tracked(repo))
        assert nb.RELATION_TESTS_MIRROR in got.relations["tests/newpkg/test_widget.py"]

    def test_stem_rule_skips_package_init_and_tests_themselves(self, repo: Path, monkeypatch: pytest.MonkeyPatch):
        _write(repo, "tests/test___init__.py")
        monkeypatch.setattr(nb, "map_source_to_test", lambda _p: None)
        assert not any(
            nb.RELATION_TESTS_MIRROR in r
            for r in nb.neighbours_of("src/pkg/__init__.py", repo, _tracked(repo)).relations.values()
        )
        got = nb.neighbours_of("tests/test_a.py", repo, _tracked(repo))
        assert not any(nb.RELATION_TESTS_MIRROR in r for r in got.relations.values())

    def test_directory_target_expands_to_its_test_modules(self, repo: Path, monkeypatch: pytest.MonkeyPatch):
        _write(repo, "src/big.py", "B = 1\n")
        _write(repo, "tests/big/test_one.py")
        _write(repo, "tests/big/helper.py")
        monkeypatch.setattr(nb, "map_source_to_test", lambda _p: repo / "tests" / "big")
        got = nb.neighbours_of("src/big.py", repo, _tracked(repo))
        assert nb.RELATION_TESTS_MIRROR in got.relations["tests/big/test_one.py"]
        assert "tests/big/helper.py" not in got.relations

    def test_mapping_outside_the_repo_root_is_ignored(self, repo: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(nb, "map_source_to_test", lambda _p: Path("/elsewhere/tests/test_a.py"))
        got = nb.neighbours_of("src/pkg/a.py", repo, _tracked(repo))
        assert not any(nb.RELATION_TESTS_MIRROR in r for p, r in got.relations.items() if p != "tests/test_a.py")

    def test_untracked_file_yields_siblings_but_no_import_neighbours(self, repo: Path):
        got = nb.neighbours_of("src/pkg/not_yet.py", repo, _tracked(repo))
        assert got.imports.startswith("unavailable: ")
        assert "untracked" in got.imports
        assert nb.RELATION_SIBLING in got.relations["src/pkg/a.py"]
        assert not any(r & {nb.RELATION_UPSTREAM, nb.RELATION_DOWNSTREAM} for r in got.relations.values())

    def test_non_python_file_has_no_import_channel_to_report(self, repo: Path):
        got = nb.neighbours_of("src/pkg/notes.md", repo, _tracked(repo))
        assert got.imports == CHANNEL_OK
        assert nb.RELATION_SIBLING in got.relations["src/pkg/a.py"]

    def test_tracked_path_missing_from_the_graph_is_reported_unavailable(self, repo: Path):
        tracked = _tracked(repo) | {"src/pkg/ghost.py"}
        got = nb.neighbours_of("src/pkg/ghost.py", repo, tracked)
        assert "not a node of the import graph" in got.imports

    def test_graph_build_failure_is_unavailable_not_raised(self, repo: Path, monkeypatch: pytest.MonkeyPatch):
        def boom(_root):
            raise OSError("disk gone")

        monkeypatch.setattr(nb.dg, "build_graph", boom)
        got = nb.neighbours_of("src/pkg/a.py", repo, _tracked(repo))
        assert got.imports.startswith("unavailable: import graph could not be built")
        assert nb.RELATION_SIBLING in got.relations["src/pkg/b.py"]
