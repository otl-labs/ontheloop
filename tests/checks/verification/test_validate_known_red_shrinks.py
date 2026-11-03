"""Mirror of scripts/checks/verification/validate_known_red_shrinks.py (the known-red list only shrinks)."""

import subprocess
from pathlib import Path

import yaml

from scripts.checks import registry
from scripts.checks.verification.validate_known_red_shrinks import KNOWN_RED_REL, ids_from_text, validate_known_red_shrinks


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _write(root: Path, ids: list[str]) -> None:
    path = root / KNOWN_RED_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [{"check_id": i, "owner_rec": "rec-1", "entered_at": "2026-11-03"} for i in ids]
    path.write_text(yaml.safe_dump({"schema_version": 1, "entries": entries}), encoding="utf-8")


def _commit(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", "commit", "-qm", message)


def _repo(tmp_path: Path, base_ids: list[str] | None) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    if base_ids is None:
        (tmp_path / "README").write_text("x", encoding="utf-8")
    else:
        _write(tmp_path, base_ids)
    _commit(tmp_path, "base")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(tmp_path, "checkout", "-q", "-b", "work")
    return tmp_path


def _decl() -> registry._Declaration:
    declaration = registry._CURRENT_DECLARATION
    assert declaration is not None
    return declaration


def _run(root: Path) -> list[str]:
    failed: list[str] = []
    validate_known_red_shrinks(failed, root=root)
    return failed


class TestShrink:
    def test_added_id_fails(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, ["a", "b"])
        _write(root, ["a", "b", "c"])
        _commit(root, "grow")
        failed = _run(root)
        assert failed
        assert "c" in failed[0]

    def test_removed_id_passes(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, ["a", "b"])
        _write(root, ["a"])
        _commit(root, "shrink")
        failed = _run(root)
        assert failed == []
        assert _decl().kind == "examined"

    def test_no_base_list_skips(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, None)
        _write(root, ["a"])
        _commit(root, "introduce")
        failed = _run(root)
        assert failed == []
        assert _decl().kind == "skipped"

    def test_merge_base_comparison(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, ["a", "b"])
        _git(root, "checkout", "-q", "main")
        _write(root, ["a"])
        _commit(root, "main removes b")
        _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(root, "checkout", "-q", "work")
        failed = _run(root)
        assert failed == []
        assert _decl().kind == "examined"
        _write(root, ["a", "b", "z"])
        _commit(root, "grow")
        failed = _run(root)
        assert failed
        assert "z" in failed[0]


class TestShrinkEdges:
    def test_ids_from_text_tolerates_malformed(self) -> None:
        assert ids_from_text("{{{") == []
        assert ids_from_text("entries: 3") == []
        assert ids_from_text("entries: [{check_id: a}, 5, {check_id: 7}]") == ["a"]

    def test_no_base_ref_skips(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-q", "-b", "main")
        (tmp_path / "f").write_text("x", encoding="utf-8")
        _commit(tmp_path, "only")
        failed = _run(tmp_path)
        assert failed == []
        assert _decl().kind == "skipped"

    def test_origin_main_fallback_and_deleted_list(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, ["a"])
        _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
        (root / KNOWN_RED_REL).unlink()
        _commit(root, "delete list")
        failed = _run(root)
        assert failed == []
        assert _decl().count == 0

    def test_unrelated_history_falls_back_to_origin_main(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, ["a"])
        _git(root, "checkout", "-q", "--orphan", "other")
        _write(root, ["a", "extra"])
        _commit(root, "orphan")
        failed = _run(root)
        assert failed
        assert "extra" in failed[0]
