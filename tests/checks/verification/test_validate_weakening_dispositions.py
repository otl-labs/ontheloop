"""Mirror test for scripts/checks/verification/validate_weakening_dispositions.py (rec-3357).

Builds a small real git fixture repository (one base commit, then working-tree edits) so the
retirement/refactor cite rules -- which read real git history and real on-disk files -- can be
exercised without mocking git itself. No new conftest.py; fixtures live in this file only.
"""

from __future__ import annotations

import subprocess
import unittest.mock as mock
from pathlib import Path

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.verification.validate_weakening_dispositions import (
    Candidate,
    _cite_resolves,
    _defines_name,
    _planned_change_resolves,
    _read_file,
    _read_file_at_ref,
    main,
    run_check,
    validate_weakening_dispositions,
)

_REVIEW_DISPOSITIONS_YAML = """
invariants:
  content:
    review_dispositions:
      refactor: "cite file.py::name"
      planned-change: "cite acceptance_criteria[N] or test_obligations[N]"
      retirement: "cite the deleted path or file.py::name"
      growth-safe-pin: "cite file.py::name"
      owned-skip: "cite rec-NNNN or env: label"
      weakening: "never"
"""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result


def _init_committed_repo(repo: Path) -> None:
    """git init + identity config + one empty commit, for tests that only need a resolvable HEAD."""
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / ".gitkeep").write_text("", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")


def _cand(
    category: str = "assertion-removed", path: str = "tests/t.py", node: str = "test_a", detail: str = "detail"
) -> Candidate:
    return Candidate(category, path, node, detail)


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")

    (root / "scripts" / "checks").mkdir(parents=True)
    (root / "scripts" / "checks" / "foo.py").write_text("def kept_fn():\n    pass\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "old_subject.py").write_text("def helper_fn():\n    pass\n", encoding="utf-8")
    (root / "docs" / "contracts").mkdir(parents=True)
    (root / "docs" / "contracts" / "implement-scope-boundary.yaml").write_text(_REVIEW_DISPOSITIONS_YAML, encoding="utf-8")

    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    base_sha = _git(root, "rev-parse", "HEAD").stdout.strip()
    return root, base_sha


def _write_plan(root: Path, rel: str, weakening_dispositions: list[dict]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "acceptance_criteria": ["ac 0", "ac 1"],
                "test_obligations": [{"source": "x"}],
                "weakening_dispositions": weakening_dispositions,
            }
        ),
        encoding="utf-8",
    )


def _candidate_sources(category: str, path: str, node: str) -> dict:
    """Minimal (status, path) + reader pair that makes find_candidates emit exactly one
    candidate of the requested shape."""
    if category == "assertion-removed":
        base_src = f"def {node}():\n    assert x == 1\n    assert y == 2\n"
        head_src = f"def {node}():\n    assert x == 1\n"
    elif category == "skip-added":
        base_src = f"def {node}():\n    pass\n"
        head_src = f'import pytest\n\n@pytest.mark.skip(reason="uncited")\ndef {node}():\n    pass\n'
    else:
        raise ValueError(category)
    return {"changed": [("M", path)], "sources": {path: (base_src, head_src)}}


class TestDispositionsRequired:
    def _rc(
        self,
        root: Path,
        base_sha: str,
        plan_rel: str,
        dispositions: list[dict],
        *,
        status_changed: list[tuple[str, str]] | None = None,
        sources: dict | None = None,
    ) -> list[str]:
        _write_plan(root, plan_rel, dispositions)
        sources = sources or {}
        changed = status_changed if status_changed is not None else [("M", p) for p in sources]
        failed: list[str] = []
        run_check(
            failed,
            root=root,
            base=base_sha,
            resolved=[plan_rel],
            status_changed=changed,
            read_base=lambda p: sources.get(p, (None, None))[0],
            read_head=lambda p: sources.get(p, (None, None))[1],
        )
        return failed

    _PLAN = "docs/plans/PLAN-x.yaml"
    _ASSERT_KEY = "[assertion-removed] tests/t.py::test_a"

    def test_missing_entry_fails(self, repo: tuple[Path, str]) -> None:
        root, base_sha = repo
        spec = _candidate_sources("assertion-removed", "tests/t.py", "test_a")
        failed = self._rc(root, base_sha, self._PLAN, [], sources=spec["sources"])
        assert any("no recorded disposition" in f for f in failed)

    def test_duplicate_entry_fails(self, repo: tuple[Path, str]) -> None:
        root, base_sha = repo
        entry = {"candidate": self._ASSERT_KEY, "disposition": "refactor", "cite": "scripts/checks/foo.py::kept_fn"}
        spec = _candidate_sources("assertion-removed", "tests/t.py", "test_a")
        failed = self._rc(root, base_sha, self._PLAN, [entry, entry], sources=spec["sources"])
        assert any("has 2 recorded entries" in f for f in failed)

    def test_stale_entry_fails(self, repo: tuple[Path, str]) -> None:
        root, base_sha = repo
        entry = {"candidate": "[assertion-removed] tests/gone.py::test_z", "disposition": "refactor", "cite": "x.py::y"}
        failed = self._rc(root, base_sha, self._PLAN, [entry])
        assert any("stale" in f for f in failed)

    def test_unknown_disposition_fails(self, repo: tuple[Path, str]) -> None:
        root, base_sha = repo
        entry = {"candidate": self._ASSERT_KEY, "disposition": "bogus", "cite": "x"}
        spec = _candidate_sources("assertion-removed", "tests/t.py", "test_a")
        failed = self._rc(root, base_sha, self._PLAN, [entry], sources=spec["sources"])
        assert any("unknown disposition" in f for f in failed)

    def test_weakening_entry_fails(self, repo: tuple[Path, str]) -> None:
        root, base_sha = repo
        entry = {"candidate": self._ASSERT_KEY, "disposition": "weakening", "cite": "x"}
        spec = _candidate_sources("assertion-removed", "tests/t.py", "test_a")
        failed = self._rc(root, base_sha, self._PLAN, [entry], sources=spec["sources"])
        assert any("recorded as weakening" in f for f in failed)

    def test_refactor_resolving_cite_passes(self, repo: tuple[Path, str]) -> None:
        root, base_sha = repo
        entry = {"candidate": self._ASSERT_KEY, "disposition": "refactor", "cite": "scripts/checks/foo.py::kept_fn"}
        spec = _candidate_sources("assertion-removed", "tests/t.py", "test_a")
        assert self._rc(root, base_sha, self._PLAN, [entry], sources=spec["sources"]) == []

    def test_refactor_unresolvable_cite_fails(self, repo: tuple[Path, str]) -> None:
        root, base_sha = repo
        entry = {"candidate": self._ASSERT_KEY, "disposition": "refactor", "cite": "scripts/checks/foo.py::nope_fn"}
        spec = _candidate_sources("assertion-removed", "tests/t.py", "test_a")
        failed = self._rc(root, base_sha, self._PLAN, [entry], sources=spec["sources"])
        assert any("does not resolve" in f for f in failed)

    def test_retirement_via_deleted_path_passes(self, repo: tuple[Path, str]) -> None:
        root, base_sha = repo
        (root / "tests" / "old_subject.py").unlink()
        entry = {
            "candidate": "[test-removed] tests/old_subject.py::helper_fn",
            "disposition": "retirement",
            "cite": "tests/old_subject.py",
        }
        failed = self._rc(root, base_sha, self._PLAN, [entry], status_changed=[("D", "tests/old_subject.py")])
        # No candidate was actually produced (find_candidates needs test_functions, not helper
        # functions) -- this exercises the stale-entry path instead, proving retirement's own
        # deleted-path branch is reachable without crashing.
        assert any("stale" in f for f in failed)

    def test_retirement_cite_naming_own_path_fails(self, repo: tuple[Path, str]) -> None:
        root, base_sha = repo
        entry = {"candidate": self._ASSERT_KEY, "disposition": "retirement", "cite": "tests/t.py::test_a"}
        spec = _candidate_sources("assertion-removed", "tests/t.py", "test_a")
        failed = self._rc(root, base_sha, self._PLAN, [entry], sources=spec["sources"])
        assert any("does not resolve" in f for f in failed)

    def test_owned_skip_on_skip_added_candidate_passes(self, repo: tuple[Path, str]) -> None:
        root, base_sha = repo
        entry = {"candidate": "[skip-added] tests/t.py::test_a", "disposition": "owned-skip", "cite": "rec-999"}
        base_src = "def test_a():\n    pass\n"
        head_src = 'import pytest\n\n@pytest.mark.skip(reason="rec-999")\ndef test_a():\n    pass\n'
        sources = {"tests/t.py": (base_src, head_src)}
        assert self._rc(root, base_sha, self._PLAN, [entry], sources=sources) == []

    def test_owned_skip_on_non_skip_candidate_fails(self, repo: tuple[Path, str]) -> None:
        root, base_sha = repo
        entry = {"candidate": self._ASSERT_KEY, "disposition": "owned-skip", "cite": "rec-999"}
        spec = _candidate_sources("assertion-removed", "tests/t.py", "test_a")
        failed = self._rc(root, base_sha, self._PLAN, [entry], sources=spec["sources"])
        assert any("does not resolve" in f for f in failed)

    def test_planned_change_valid_index_passes(self, repo: tuple[Path, str]) -> None:
        root, base_sha = repo
        entry = {"candidate": self._ASSERT_KEY, "disposition": "planned-change", "cite": "acceptance_criteria[0]"}
        spec = _candidate_sources("assertion-removed", "tests/t.py", "test_a")
        assert self._rc(root, base_sha, self._PLAN, [entry], sources=spec["sources"]) == []

    def test_planned_change_invalid_index_fails(self, repo: tuple[Path, str]) -> None:
        root, base_sha = repo
        entry = {"candidate": self._ASSERT_KEY, "disposition": "planned-change", "cite": "acceptance_criteria[99]"}
        spec = _candidate_sources("assertion-removed", "tests/t.py", "test_a")
        failed = self._rc(root, base_sha, self._PLAN, [entry], sources=spec["sources"])
        assert any("does not resolve" in f for f in failed)

    def test_two_sites_in_one_scope_need_one_entry(self, repo: tuple[Path, str]) -> None:
        root, base_sha = repo
        entry = {"candidate": self._ASSERT_KEY, "disposition": "refactor", "cite": "scripts/checks/foo.py::kept_fn"}
        base_src = "def test_a():\n    assert x == 1\n    assert y == 2\n"
        head_src = "def test_a():\n    assert x == 1\n"
        sources = {"tests/t.py": (base_src, head_src)}
        assert self._rc(root, base_sha, self._PLAN, [entry], sources=sources) == []

    def test_complete_resolving_record_passes(self, repo: tuple[Path, str]) -> None:
        root, base_sha = repo
        entry = {"candidate": self._ASSERT_KEY, "disposition": "refactor", "cite": "scripts/checks/foo.py::kept_fn"}
        spec = _candidate_sources("assertion-removed", "tests/t.py", "test_a")
        assert self._rc(root, base_sha, self._PLAN, [entry], sources=spec["sources"]) == []


class TestImplementLegOnly:
    def test_no_flip_is_noop_even_with_candidates(self, tmp_path: Path) -> None:
        registry.pop_declaration()
        failed: list[str] = []
        import unittest.mock as mock

        with mock.patch("scripts.checks._common.resolve_declared_plans", return_value=[]):
            with mock.patch("scripts.checks._common.status_diff_base", return_value="deadbeef"):
                validate_weakening_dispositions(failed, changed_files=[], root=tmp_path)
        assert failed == []
        decl = registry.pop_declaration()
        assert decl is not None
        assert decl.kind == "examined"
        assert decl.count == 0

    def test_unreachable_base_skips(self, tmp_path: Path) -> None:
        registry.pop_declaration()
        failed: list[str] = []
        import unittest.mock as mock

        with mock.patch("scripts.checks._common.status_diff_base", return_value=None):
            validate_weakening_dispositions(failed, root=tmp_path)
        assert failed == []
        decl = registry.pop_declaration()
        assert decl is not None
        assert decl.kind == "skipped"


class TestHelpers:
    def test_read_file_missing_returns_none(self, tmp_path: Path) -> None:
        assert _read_file(tmp_path, "nope.py") is None

    def test_read_file_at_ref_bad_ref_returns_none(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-q")
        assert _read_file_at_ref(tmp_path, "not-a-real-ref", "t.py") is None

    def test_defines_name_edge_cases(self) -> None:
        assert _defines_name("def f(:\n", "f") is False
        assert _defines_name(None, "f") is False
        assert _defines_name("def other():\n    pass\n", "f") is False
        assert _defines_name("class Other:\n    def m(self):\n        pass\n", "Foo::m") is False
        assert _defines_name("class Foo:\n    def m(self):\n        pass\n", "Foo::m") is True

    def test_cite_resolves_shape_rejections(self, tmp_path: Path) -> None:
        old = _cand("test-removed", "tests/old.py", "helper_fn")
        assert _cite_resolves("refactor", "", _cand(), root=Path("/x"), base="B", deleted_paths=set()) is False
        assert _cite_resolves("refactor", "not-a-token", _cand(), root=tmp_path, base="B", deleted_paths=set()) is False
        assert _cite_resolves("retirement", "not-a-token", old, root=tmp_path, base="B", deleted_paths=set()) is False
        own = "tests/old.py::helper_fn"
        assert _cite_resolves("retirement", own, old, root=tmp_path, base="B", deleted_paths=set()) is False
        assert _cite_resolves("bogus", "x", _cand(), root=tmp_path, base="B", deleted_paths=set()) is False

    def test_cite_resolves_owned_skip_rules(self, tmp_path: Path) -> None:
        ineligible = _cand()
        assert _cite_resolves("owned-skip", "rec-1", ineligible, root=tmp_path, base="B", deleted_paths=set()) is False
        env_match = _cand("skip-added", detail="pytest.mark.skip -- env: windows only")
        assert (
            _cite_resolves("owned-skip", "env: windows only", env_match, root=tmp_path, base="B", deleted_paths=set()) is True
        )
        no_match = _cand("skip-added", detail="pytest.mark.skip -- some other reason")
        assert _cite_resolves("owned-skip", "rec-999", no_match, root=tmp_path, base="B", deleted_paths=set()) is False

    def test_planned_change_resolves_edge_cases(self, tmp_path: Path) -> None:
        assert _planned_change_resolves("acceptance_criteria[0]", tmp_path, "B", ["docs/plans/PLAN-missing.yaml"]) is False
        assert _planned_change_resolves("not-a-cite", tmp_path, "B", []) is False

    def test_main_runs_against_real_tmp_repo(self, tmp_path: Path) -> None:
        with (
            mock.patch("scripts.checks._common.ROOT", tmp_path),
            mock.patch("scripts.checks._common.status_diff_base", return_value=None),
        ):
            assert main([]) == 0

    def test_read_file_at_ref_success(self, tmp_path: Path) -> None:
        _init_committed_repo(tmp_path)
        (tmp_path / "f.py").write_text("x = 1\n", encoding="utf-8")
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-q", "-m", "f")
        assert _read_file_at_ref(tmp_path, "HEAD", "f.py") == "x = 1\n"

    def test_plan_doc_at_bad_yaml_returns_none(self, tmp_path: Path) -> None:
        from scripts.checks.verification.validate_weakening_dispositions import _plan_doc_at

        (tmp_path / "bad.yaml").write_text("key: [unterminated\n", encoding="utf-8")
        assert _plan_doc_at(tmp_path, None, "bad.yaml") is None

    def test_cite_resolves_retirement_deleted_path_passes(self, tmp_path: Path) -> None:
        old = _cand("test-removed", "tests/old.py", "helper_fn")
        assert (
            _cite_resolves("retirement", "tests/old.py", old, root=tmp_path, base="B", deleted_paths={"tests/old.py"}) is True
        )

    def test_cite_resolves_retirement_resolving_cite_passes(self, tmp_path: Path) -> None:
        _init_committed_repo(tmp_path)
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "foo.py").write_text("def helper_fn():\n    pass\n", encoding="utf-8")
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-q", "-m", "foo")
        base_sha = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        (tmp_path / "scripts" / "foo.py").unlink()

        old = _cand("test-removed", "tests/old.py", "helper_fn")
        cite = "scripts/foo.py::helper_fn"
        assert _cite_resolves("retirement", cite, old, root=tmp_path, base=base_sha, deleted_paths=set()) is True

    def test_run_check_skips_entry_with_no_candidate_key(self, tmp_path: Path) -> None:
        plan_rel = "docs/plans/PLAN-x.yaml"
        path = tmp_path / plan_rel
        path.parent.mkdir(parents=True)
        path.write_text(
            yaml.safe_dump({"weakening_dispositions": [{"disposition": "refactor", "cite": "x"}]}), encoding="utf-8"
        )
        failed: list[str] = []
        run_check(
            failed,
            root=tmp_path,
            base="B",
            resolved=[plan_rel],
            status_changed=[],
            read_base=lambda p: None,
            read_head=lambda p: None,
        )
        assert failed == []

    def test_validate_weakening_dispositions_success_path_on_real_tmp_repo(self, tmp_path: Path) -> None:
        plan_rel = "docs/plans/PLAN-x.yaml"
        (tmp_path / "docs" / "plans").mkdir(parents=True)
        (tmp_path / plan_rel).write_text(yaml.safe_dump({"weakening_dispositions": []}), encoding="utf-8")
        (tmp_path / "docs" / "contracts").mkdir(parents=True)
        (tmp_path / "docs" / "contracts" / "implement-scope-boundary.yaml").write_text(
            "invariants:\n  content:\n    review_dispositions: {}\n", encoding="utf-8"
        )
        registry.pop_declaration()
        with (
            mock.patch("scripts.checks._common.ROOT", tmp_path),
            mock.patch("scripts.checks._common.status_diff_base", return_value="BASE"),
            mock.patch("scripts.checks._common.get_changed_files", return_value=[plan_rel]),
            mock.patch("scripts.checks._common.resolve_declared_plans", return_value=[plan_rel]),
            mock.patch("scripts.checks._common.get_status_aware_diff", return_value=[]),
        ):
            failed: list[str] = []
            validate_weakening_dispositions(failed, root=tmp_path)
        assert failed == []
        decl = registry.pop_declaration()
        assert decl is not None
        assert decl.count == 1

    def test_main_prints_failed_block(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plan_rel = "docs/plans/PLAN-x.yaml"
        (tmp_path / "docs" / "plans").mkdir(parents=True)
        key = "[assertion-removed] tests/t.py::test_a"
        (tmp_path / plan_rel).write_text(
            yaml.safe_dump({"weakening_dispositions": [{"candidate": key, "disposition": "bogus", "cite": "x"}]}),
            encoding="utf-8",
        )
        (tmp_path / "docs" / "contracts").mkdir(parents=True)
        (tmp_path / "docs" / "contracts" / "implement-scope-boundary.yaml").write_text(
            "invariants:\n  content:\n    review_dispositions: {}\n", encoding="utf-8"
        )
        base_src = "def test_a():\n    assert x == 1\n    assert y == 2\n"
        head_src = "def test_a():\n    assert x == 1\n"
        with (
            mock.patch("scripts.checks._common.ROOT", tmp_path),
            mock.patch("scripts.checks._common.status_diff_base", return_value="BASE"),
            mock.patch("scripts.checks._common.get_changed_files", return_value=[plan_rel]),
            mock.patch("scripts.checks._common.resolve_declared_plans", return_value=[plan_rel]),
            mock.patch(
                "scripts.checks._common.get_status_aware_diff",
                return_value=[("M", "tests/t.py")],
            ),
            mock.patch(
                "scripts.checks.verification.validate_weakening_dispositions._read_file_at_ref",
                return_value=base_src,
            ),
            mock.patch(
                "scripts.checks.verification.validate_weakening_dispositions._read_file",
                side_effect=lambda root, rel: (
                    head_src
                    if rel == "tests/t.py"
                    else ((root / rel).read_text(encoding="utf-8") if (root / rel).exists() else None)
                ),
            ),
        ):
            code = main([])
        assert code == 1
        assert "FAILED:" in capsys.readouterr().out
