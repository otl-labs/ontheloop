"""Relocated TestCommonPrimitives, TestMockInterceptionPreservation, and
TestNoLocalRootRecomputation from the retired tests/test_checks_registry.py monolith
(Decision 169, amends Decision 104).

_common.py's own mapped coverage-checker home (the _CONCERN_SPLIT_TEST_PACKAGES registration in
scripts/test_coverage_checker.py) is this package -- its 100% per-file coverage is asserted HERE.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

import scripts.checks._common as _common
import scripts.checks.registry as registry

ROOT = Path(__file__).parent.parent.parent.parent


class TestCommonPrimitives:
    """Direct coverage of scripts/checks/_common.py's primitives."""

    def test_run_delegates_to_subprocess_run(self) -> None:
        result = _common.run(["true"])
        assert result.returncode == 0

    def test_invoke_step_appends_on_nonzero(self, capsys: pytest.CaptureFixture) -> None:
        failed: list[str] = []
        with patch("scripts.checks._common.run", return_value=MagicMock(returncode=1)):
            _common.invoke_step("dummy-step", ["true"], failed)
        assert failed == ["dummy-step"]
        assert "dummy-step" in capsys.readouterr().out

    def test_invoke_step_no_append_on_zero(self) -> None:
        failed: list[str] = []
        with patch("scripts.checks._common.run", return_value=MagicMock(returncode=0)):
            _common.invoke_step("dummy-step", ["true"], failed)
        assert failed == []

    def test_get_changed_files_origin_main_success_branch(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 0
            result.stdout = "a.py\n"
            return result

        with patch("scripts.checks._common.run", side_effect=mock_run), patch("scripts.checks._common.ROOT", tmp_path):
            files = _common.get_changed_files()
        assert files == ["a.py"]

    def test_get_changed_files_head_fallback_branch(self, tmp_path: Path) -> None:
        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            result = MagicMock()
            if "origin/main" in cmd:
                result.returncode = 1
                result.stdout = ""
            else:
                result.returncode = 0
                result.stdout = ""
            return result

        with patch("scripts.checks._common.run", side_effect=mock_run), patch("scripts.checks._common.ROOT", tmp_path):
            files = _common.get_changed_files()
        assert files == []

    def test_get_status_aware_diff_full_pass(self, tmp_path: Path) -> None:
        """Exercises every line of get_status_aware_diff(): a successful merge-base, a mixed
        M/A/D/malformed diff, and untracked existing/nonexistent paths."""
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "new_thing.py").write_text("x = 2\n", encoding="utf-8")

        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 0
            if cmd[:2] == ["git", "merge-base"]:
                result.stdout = "deadbeef\n"
            elif cmd[:2] == ["git", "diff"]:
                result.stdout = "M\ta.py\n\nD\tscripts/gone.py\nno-tab-here\nM\tnot_on_disk.py\nM\t   \nR\told_renamed.py\n"
            elif cmd[:2] == ["git", "ls-files"]:
                result.stdout = "new_thing.py\nghost.py\n"
            else:
                result.stdout = ""
            return result

        with patch("scripts.checks._common.run", side_effect=mock_run), patch("scripts.checks._common.ROOT", tmp_path):
            entries = _common.get_status_aware_diff()

        assert set(entries) == {("M", "a.py"), ("D", "scripts/gone.py"), ("??", "new_thing.py")}

    def test_get_changed_files_root_param_scopes_cwd_and_existence_filter(self, tmp_path: Path) -> None:
        """rec-3166: passing root= must scope every subprocess cwd AND the existence filter to
        the injected root, never to ROOT (patched to a different, decoy directory here)."""
        decoy = tmp_path / "decoy"
        decoy.mkdir()
        injected = tmp_path / "injected"
        injected.mkdir()
        (injected / "a.py").write_text("x = 1\n", encoding="utf-8")

        seen_cwds: list[Path] = []

        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            seen_cwds.append(kwargs.get("cwd"))
            result = MagicMock()
            result.returncode = 0
            result.stdout = "a.py\n"
            return result

        with patch("scripts.checks._common.run", side_effect=mock_run), patch("scripts.checks._common.ROOT", decoy):
            files = _common.get_changed_files(root=injected)
        assert files == ["a.py"]
        assert all(cwd == injected for cwd in seen_cwds)

    def test_get_status_aware_diff_root_param_scopes_cwd_and_existence_filters(self, tmp_path: Path) -> None:
        decoy = tmp_path / "decoy"
        decoy.mkdir()
        injected = tmp_path / "injected"
        injected.mkdir()
        (injected / "new_thing.py").write_text("x = 2\n", encoding="utf-8")

        seen_cwds: list[Path] = []

        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            seen_cwds.append(kwargs.get("cwd"))
            result = MagicMock()
            result.returncode = 0
            if cmd[:2] == ["git", "merge-base"]:
                result.stdout = "deadbeef\n"
            elif cmd[:2] == ["git", "diff"]:
                result.stdout = ""
            elif cmd[:2] == ["git", "ls-files"]:
                result.stdout = "new_thing.py\n"
            else:
                result.stdout = ""
            return result

        with patch("scripts.checks._common.run", side_effect=mock_run), patch("scripts.checks._common.ROOT", decoy):
            entries = _common.get_status_aware_diff(root=injected)
        assert entries == [("??", "new_thing.py")]
        assert all(cwd == injected for cwd in seen_cwds)

    def test_get_changed_files_nonexistent_root_raises(self) -> None:
        """Decision 55 fail-loud: a nonexistent injected root must never silently degrade to
        reading the real repository (the pre-fix rec-3166 behaviour)."""
        with pytest.raises(FileNotFoundError):
            _common.get_changed_files(root=Path("/nonexistent-root-rec-3166"))

    def test_get_status_aware_diff_nonexistent_root_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            _common.get_status_aware_diff(root=Path("/nonexistent-root-rec-3166"))

    def test_push_context_base_nonexistent_root_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        with pytest.raises(FileNotFoundError):
            _common.push_context_base(root=Path("/nonexistent-root-rec-3166"))

    def test_get_status_aware_diff_merge_base_failure_fallback(self, tmp_path: Path) -> None:
        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 0 if cmd[:2] != ["git", "merge-base"] else 1
            result.stdout = ""
            return result

        with patch("scripts.checks._common.run", side_effect=mock_run), patch("scripts.checks._common.ROOT", tmp_path):
            entries = _common.get_status_aware_diff()
        assert entries == []

    def test_plan_paths_from_changed_filters_and_sorts(self) -> None:
        result = _common.plan_paths_from_changed(
            ["docs/plans/PLAN-b.yaml", "scripts/foo.py", "docs/plans/PLAN-a.yaml", "docs/plans/nested/PLAN-c.yaml"]
        )
        assert result == ["docs/plans/PLAN-a.yaml", "docs/plans/PLAN-b.yaml"]

    def test_load_plan_loads_via_roadmap_plan_document(self, tmp_path: Path) -> None:
        slug = "common-helper-fixture"
        plans_dir = tmp_path / "docs" / "plans"
        plans_dir.mkdir(parents=True)
        plan_dict = {
            "schema_version": 2,
            "slug": slug,
            "intent": "Fixture plan for _common.load_plan direct coverage.",
            "plan_type": "IMPLEMENTATION",
            "verification_tier": "V2",
            "plan_path": f"docs/plans/PLAN-{slug}.yaml",
            "phase": "Test fixture",
            "scope": [{"file": "scripts/dummy.py", "action": "Modify", "purpose": "fixture"}],
            "acceptance_criteria": ["dummy criterion"],
            "verification_plan": [
                {
                    "step": 1,
                    "phase": "pre-deploy",
                    "hermetic": True,
                    "action": "dummy",
                    "command": "true",
                    "expected": "n/a",
                    "fix_if": "n/a",
                }
            ],
            "execution_steps": ["dummy step"],
        }
        (plans_dir / f"PLAN-{slug}.yaml").write_text(yaml.dump(plan_dict), encoding="utf-8")

        doc = _common.load_plan(f"docs/plans/PLAN-{slug}.yaml", tmp_path)
        assert doc.slug == slug
        assert str(tmp_path) not in sys.path  # injected then cleaned up

    def _git_repo_with_base_commit(self, repo: Path) -> None:
        def _git(args: list[str]) -> None:
            result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
            assert result.returncode == 0, f"git {args} failed: {result.stderr}"

        _git(["init", "-q"])
        _git(["config", "user.email", "test@example.com"])
        _git(["config", "user.name", "Test"])
        (repo / "base.txt").write_text("base\n", encoding="utf-8")
        _git(["add", "-A"])
        _git(["commit", "-q", "-m", "base"])
        base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True, encoding="utf-8"
        ).stdout.strip()
        _git(["update-ref", "refs/remotes/origin/main", base_sha])

    def test_origin_main_reachable_true_with_ref(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git_repo_with_base_commit(repo)

        assert _common.origin_main_reachable(repo) is True

    def test_origin_main_reachable_false_without_repo(self, tmp_path: Path) -> None:
        assert _common.origin_main_reachable(tmp_path) is False


class TestStatusDiffBase:
    """scripts.checks._common.status_diff_base -- Decision 159's sole base-resolution path for
    get_status_aware_diff (and any future status-aware-diff caller)."""

    def test_push_base_wins(self, tmp_path: Path) -> None:
        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 0
            result.stdout = "pushbase\n" if cmd[:2] == ["git", "rev-parse"] else ""
            return result

        with (
            patch("scripts.checks._common.run", side_effect=mock_run),
            patch("scripts.checks._common.ROOT", tmp_path),
            patch.dict("os.environ", {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_BEFORE": "pushbase"}),
        ):
            assert _common.status_diff_base() == "pushbase"

    def test_merge_base_fallback(self, tmp_path: Path) -> None:
        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            result = MagicMock()
            if cmd[:2] == ["git", "merge-base"]:
                result.returncode = 0
                result.stdout = "mergebasesha\n"
            else:
                result.returncode = 1
                result.stdout = ""
            return result

        with patch("scripts.checks._common.run", side_effect=mock_run), patch("scripts.checks._common.ROOT", tmp_path):
            assert _common.status_diff_base() == "mergebasesha"

    def test_none_when_neither_resolves(self, tmp_path: Path) -> None:
        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 1
            result.stdout = ""
            return result

        with patch("scripts.checks._common.run", side_effect=mock_run), patch("scripts.checks._common.ROOT", tmp_path):
            assert _common.status_diff_base() is None


class TestImplementationDeclaredResolver:
    """scripts.checks._common.resolve_declared_plans -- content-keyed plan resolution
    (Decision 148 sole home, PLAN-plan-resolution-content-keyed)."""

    @staticmethod
    def _git(repo: Path, args: list[str]) -> subprocess.CompletedProcess:
        result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
        assert result.returncode == 0, f"git {args} failed: {result.stderr}"
        return result

    def _init_repo(self, repo: Path) -> None:
        repo.mkdir(parents=True, exist_ok=True)
        self._git(repo, ["init", "-q"])
        self._git(repo, ["config", "user.email", "test@example.com"])
        self._git(repo, ["config", "user.name", "Test"])

    def _commit_all(self, repo: Path, message: str) -> str:
        self._git(repo, ["add", "-A"])
        self._git(repo, ["commit", "-q", "-m", message])
        return self._git(repo, ["rev-parse", "HEAD"]).stdout.strip()

    def _write_plan(self, repo: Path, slug: str, declared: bool | None) -> str:
        rel = f"docs/plans/PLAN-{slug}.yaml"
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        content: dict = {"schema_version": 1, "slug": slug}
        if declared is not None:
            content["implementation_declared"] = declared
        path.write_text(yaml.dump(content), encoding="utf-8")
        return rel

    def test_flip_resolves(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        self._init_repo(repo)
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        base_sha = self._commit_all(repo, "base")
        self._git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])

        rel = self._write_plan(repo, "flip", declared=True)
        self._commit_all(repo, "declare implementation")

        assert _common.resolve_declared_plans([rel], repo, "origin/main") == [rel]

    def test_already_declared_at_base_does_not_reresolve(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        self._init_repo(repo)
        rel = self._write_plan(repo, "steady", declared=True)
        base_sha = self._commit_all(repo, "base, already declared")
        self._git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])

        (repo / "unrelated.txt").write_text("x\n", encoding="utf-8")
        self._commit_all(repo, "unrelated change")

        assert _common.resolve_declared_plans([rel], repo, "origin/main") == []

    def test_no_declaration_resolves_nothing(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        self._init_repo(repo)
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        base_sha = self._commit_all(repo, "base")
        self._git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])

        rel = self._write_plan(repo, "undeclared", declared=False)
        self._commit_all(repo, "plan added, not declared")

        assert _common.resolve_declared_plans([rel], repo, "origin/main") == []

    def test_absent_at_base_resolves_as_newly_declared(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        self._init_repo(repo)
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        base_sha = self._commit_all(repo, "base")
        self._git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])

        rel = self._write_plan(repo, "net-new", declared=True)
        self._commit_all(repo, "net-new declared plan")

        assert _common.resolve_declared_plans([rel], repo, "origin/main") == [rel]

    def test_malformed_yaml_at_base_is_not_declared(self, tmp_path: Path) -> None:
        """A baseline read that succeeds (git show returncode 0) but is malformed YAML is a
        legitimate 'not declared at this ref', never raised -- unlike a malformed WORKING-TREE
        read, which does raise."""
        repo = tmp_path / "repo"
        self._init_repo(repo)
        rel = "docs/plans/PLAN-base-malformed.yaml"
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not: [valid, yaml, shape", encoding="utf-8")
        base_sha = self._commit_all(repo, "base with malformed plan")
        self._git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])

        path.write_text(yaml.dump({"schema_version": 1, "slug": "base-malformed", "implementation_declared": True}))
        self._commit_all(repo, "fix and declare")

        assert _common.resolve_declared_plans([rel], repo, "origin/main") == [rel]

    def test_deleted_in_diff_candidate_is_skipped_not_resolved(self, tmp_path: Path) -> None:
        assert _common.resolve_declared_plans(["docs/plans/PLAN-gone.yaml"], tmp_path, "origin/main") == []

    def test_unreadable_working_tree_plan_raises(self, tmp_path: Path) -> None:
        rel = "docs/plans/PLAN-unreadable.yaml"
        (tmp_path / rel).mkdir(parents=True)  # a directory where a file is expected
        with pytest.raises(OSError):
            _common.resolve_declared_plans([rel], tmp_path, "origin/main")

    def test_malformed_working_tree_yaml_raises(self, tmp_path: Path) -> None:
        rel = "docs/plans/PLAN-malformed.yaml"
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not: [valid, yaml, shape", encoding="utf-8")
        with pytest.raises(yaml.YAMLError):
            _common.resolve_declared_plans([rel], tmp_path, "origin/main")

    def test_schema_invalid_plan_does_not_raise(self, tmp_path: Path) -> None:
        """A working-tree plan that's valid YAML but would fail PlanDocument schema validation
        (missing required fields) is not this resolver's concern -- it only reads the raw
        implementation_declared key, never PlanDocument."""
        rel = "docs/plans/PLAN-schema-invalid.yaml"
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump({"implementation_declared": True}), encoding="utf-8")
        assert _common.resolve_declared_plans([rel], tmp_path, "origin/main") == [rel]

    def test_base_is_injected_not_derived_from_push_context_base(self, tmp_path: Path) -> None:
        """The resolver must never call push_context_base() itself -- callers default `base` at
        their own call site so an injected `root` reads its baseline from the FIXTURE repo, not
        the real one (GITHUB_EVENT_NAME=push would otherwise make push_context_base() resolve
        against this session's real repo state)."""
        repo = tmp_path / "repo"
        self._init_repo(repo)
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        base_sha = self._commit_all(repo, "base")
        self._git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])
        rel = self._write_plan(repo, "push-aware", declared=True)
        self._commit_all(repo, "declare")

        def _explode(*args, **kwargs):
            raise AssertionError("resolve_declared_plans must not call push_context_base() internally")

        with (
            patch("scripts.checks._common.push_context_base", side_effect=_explode),
            patch.dict("os.environ", {"GITHUB_EVENT_NAME": "push"}),
        ):
            result = _common.resolve_declared_plans([rel], repo, "origin/main")
        assert result == [rel]


class TestMockInterceptionPreservation:
    """Patching each _common primitive intercepts through a check's body, dispatched via
    registry.resolve() (Decision 169, amends Decision 104's `_validate.<name>` dispatch)."""

    def test_patching_common_root_intercepts_moved_check(self, tmp_path: Path) -> None:
        """validate_subprocess_encoding scans _common.ROOT; patching _common.ROOT redirects it."""
        (tmp_path / "src").mkdir()
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            registry.resolve("validate_subprocess_encoding")(failed)
        assert failed == []

    def test_patching_common_get_changed_files_intercepts_moved_check(self) -> None:
        """validate_environment_taxonomy calls _common.get_changed_files(); patching it redirects."""
        with patch("scripts.checks._common.get_changed_files", return_value=[]):
            failed: list[str] = []
            registry.resolve("validate_environment_taxonomy")(failed)
        assert failed == []

    def test_patching_common_run_intercepts_check_source_registry(self, tmp_path: Path) -> None:
        """check_source_registry uses _common.ROOT for its file scan; verifies the _common.run
        primitive is resolvable as a patch target (used by other moved checks, e.g. validate_requirements)."""
        assert callable(_common.run)
        with patch("scripts.checks._common.ROOT", tmp_path):
            (tmp_path / "config" / "agent" / "data_quality").mkdir(parents=True)
            failed: list[str] = []
            registry.resolve("check_source_registry")(failed)
        assert failed == ["Source registry CI guard"]

    def test_patching_common_python_is_resolvable(self) -> None:
        """PYTHON is exported from _common and importable as a patch target."""
        with patch("scripts.checks._common.PYTHON", "/usr/bin/env-python-test"):
            assert _common.PYTHON == "/usr/bin/env-python-test"

    def test_patching_common_invoke_step_is_resolvable(self) -> None:
        calls: list[str] = []
        with patch("scripts.checks._common.invoke_step", side_effect=lambda name, cmd, failed: calls.append(name)):
            _common.invoke_step("dummy", ["true"], [])
        assert calls == ["dummy"]

    def test_check_to_check_pair_interception(self) -> None:
        """validate_ci_workflow_guards calls _ensure_root_on_path (co-located helper); patching
        a moved check-to-helper pair still intercepts through the moved body."""
        with patch(
            "scripts.checks.ci_guards.validate_ci_workflow_guards._ensure_root_on_path",
            return_value=False,
        ) as mock_ensure:
            failed: list[str] = []
            registry.resolve("validate_ci_workflow_guards")(failed)
        mock_ensure.assert_called_once()


class TestNoLocalRootRecomputation:
    """Constraint: no scripts/checks module may recompute ROOT locally."""

    def test_no_checks_module_recomputes_root(self) -> None:
        violations: list[str] = []
        checks_dir = ROOT / "scripts" / "checks"
        for py_file in sorted(checks_dir.rglob("*.py")):
            if py_file == checks_dir / "_common.py":
                continue  # the sole source of ROOT
            text = py_file.read_text(encoding="utf-8")
            if "Path(__file__).parent.parent.parent" in text or "Path(__file__).resolve().parent.parent" in text:
                violations.append(str(py_file.relative_to(ROOT)))
        assert violations == [], f"Modules recomputing ROOT locally: {violations}"

    def test_zero_residual_bare_root_patch_sites_in_tests(self) -> None:
        """Grep-count closure: no test still patches validate.run/ROOT/get_changed_files
        expecting it to intercept a moved check body (Decision 104/169 namespace migration)."""
        import re

        residual: list[str] = []
        for py_file in sorted((ROOT / "tests" / "checks").rglob("*.py")) + sorted((ROOT / "tests" / "validate").rglob("*.py")):
            text = py_file.read_text(encoding="utf-8")
            residual.extend(re.findall(r'patch\("validate\.(run|ROOT|get_changed_files)"', text))
        assert residual == [], f"Residual validate.{{run,ROOT,get_changed_files}} patch sites: {residual}"
