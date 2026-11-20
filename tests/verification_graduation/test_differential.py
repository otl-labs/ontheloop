"""run_differential / run_verifier_differential / worktree classes (Decision 176 concern-split
decomposition of the former tests/test_verification_graduation.py monolith), moved verbatim.

Covers: the KERNEL differential (a real git worktree revert against origin/main on a synthetic
repo -- tautological entry rejected, genuine entry admitted), the VERIFIER differential
(synthetic new hermetic verifier -- tautological rejected, genuine admitted; non-hermetic
advisory-skipped), the rec-2655 importorskip-guarded skip predicate, and fail-loud-on-error
assertions.
"""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

from scripts import verification_graduation as vg
from scripts.verification_checks import CheckStatus

from .conftest import _commit_all, _git, _init_repo, _seed_graduation_deps

# ---------------------------------------------------------------------------
# TestKernelDifferential (c2) -- real git worktree revert on a synthetic repo
# ---------------------------------------------------------------------------


class TestKernelDifferential:
    def _build_repo(self, tmp_path: Path, base_has_sentinel: bool) -> Path:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "target.txt").write_text("sentinel\n" if base_has_sentinel else "nothing here\n", encoding="utf-8")
        (repo / "note.txt").write_text("base\n", encoding="utf-8")
        base_sha = _commit_all(repo, "base")
        _git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])
        (repo / "target.txt").write_text("sentinel\n", encoding="utf-8")
        (repo / "note.txt").write_text("head\n", encoding="utf-8")
        _commit_all(repo, "head")
        return repo

    def test_genuine_entry_is_admitted(self, tmp_path: Path) -> None:
        """origin/main lacks the sentinel (FAIL); HEAD/live has it (PASS) -> admitted."""
        repo = self._build_repo(tmp_path, base_has_sentinel=False)
        row = {
            "check_id": "genuine",
            "primitive_slot": "grep_count",
            "check_spec": {"path": "target.txt", "pattern": "sentinel", "operator": "eq", "count": 1},
        }
        outcome = vg.run_differential(row, repo_root=repo)
        assert outcome.admitted, outcome.reason

    def test_worktree_for_gives_same_verdicts(self, tmp_path: Path) -> None:
        """A caller-supplied base worktree yields the same verdicts as the default per-call worktree."""
        for base_has_sentinel, expect in ((False, True), (True, False)):
            repo = self._build_repo(tmp_path / str(base_has_sentinel), base_has_sentinel=base_has_sentinel)
            row = {
                "check_id": "x",
                "primitive_slot": "grep_count",
                "check_spec": {"path": "target.txt", "pattern": "sentinel", "operator": "eq", "count": 1},
            }
            default = vg.run_differential(row, repo_root=repo)
            seen: list[str] = []
            with vg.git_worktree("origin/main", repo_root=repo) as wt:

                def worktree_for(ref: str, wt: Path = wt) -> Path:
                    seen.append(ref)
                    return wt

                supplied = vg.run_differential(row, repo_root=repo, worktree_for=worktree_for)
            assert seen == ["origin/main"]
            assert supplied.admitted == default.admitted == expect

    def test_tautological_entry_is_rejected(self, tmp_path: Path) -> None:
        """origin/main ALSO has the sentinel -- passes on both trees -> rejected, not admitted."""
        repo = self._build_repo(tmp_path, base_has_sentinel=True)
        row = {
            "check_id": "tautological",
            "primitive_slot": "grep_count",
            "check_spec": {"path": "target.txt", "pattern": "sentinel", "operator": "eq", "count": 1},
        }
        outcome = vg.run_differential(row, repo_root=repo)
        assert not outcome.admitted
        assert "not admitted" in outcome.reason

    def test_check_failing_on_head_is_not_admitted(self, tmp_path: Path) -> None:
        repo = self._build_repo(tmp_path, base_has_sentinel=False)
        row = {
            "check_id": "wrong-pattern",
            "primitive_slot": "grep_count",
            "check_spec": {"path": "target.txt", "pattern": "does-not-exist-anywhere", "operator": "eq", "count": 1},
        }
        outcome = vg.run_differential(row, repo_root=repo)
        assert not outcome.admitted
        assert "does not pass on HEAD" in outcome.reason

    def test_worktree_revert_is_real_not_simulated(self, tmp_path: Path) -> None:
        """The revert_runner actually checks out origin/main via `git worktree add` (no CheckResult stubbing)."""
        repo = self._build_repo(tmp_path, base_has_sentinel=False)
        row = {
            "check_id": "real-worktree",
            "primitive_slot": "file_presence",
            "check_spec": {"path": "only-on-head.txt", "mode": "exists"},
        }
        (repo / "only-on-head.txt").write_text("x", encoding="utf-8")
        _commit_all(repo, "add head-only file")
        outcome = vg.run_differential(row, repo_root=repo)
        assert outcome.admitted, outcome.reason

    def test_materialize_error_during_differential_raises(self, tmp_path: Path) -> None:
        repo = self._build_repo(tmp_path, base_has_sentinel=False)
        row = {"check_id": "bad-slot", "primitive_slot": "not_a_real_slot", "check_spec": {}}
        with pytest.raises(vg.GraduationError):
            vg.run_differential(row, repo_root=repo)

    def test_worktree_add_failure_raises(self, tmp_path: Path) -> None:
        """A bogus ref (no origin/main configured) surfaces as GraduationError, not a silent skip."""
        repo = tmp_path / "not-a-repo"
        repo.mkdir()
        row = {
            "check_id": "no-repo",
            "primitive_slot": "file_presence",
            "check_spec": {"path": "x.txt", "mode": "absent"},
        }
        with pytest.raises(vg.GraduationError, match="git worktree add failed"):
            vg.run_differential(row, repo_root=repo)


# ---------------------------------------------------------------------------
# rec-2655: importorskip-guarded skip predicate (narrow co-occurrence, fail-closed)
# ---------------------------------------------------------------------------


def _write_guarded_test_file(repo: Path, dep: str) -> str:
    rel = "tests/test_guarded_fixture.py"
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"import pytest\n\nfoo = pytest.importorskip({dep!r})\n\n\ndef test_x() -> None:\n    assert True\n",
        encoding="utf-8",
    )
    return rel


def test_importorskip_node_is_skipped_not_failed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    rel = _write_guarded_test_file(repo, "totally_fake_module_xyz")
    row = {
        "check_id": "guarded",
        "primitive_slot": "test_selector",
        "check_spec": {"node_id": f"{rel}::test_x"},
    }
    fail_result = vg.CheckResult(
        status=CheckStatus.FAIL,
        message="pytest node failed",
        actual="collected 0 items / 1 error\nERROR: found no collectors for the given node_id\n",
    )
    with (
        mock.patch.object(vg, "materialize_check_in_tree", return_value=mock.Mock(run=lambda: fail_result)),
        mock.patch.object(vg, "_excluded_heavy_import_names", return_value={"totally_fake_module_xyz"}),
    ):
        outcome = vg.run_differential(row, repo_root=repo)
    assert outcome.admitted is False
    assert outcome.skipped is True
    assert "importorskip-guarded" in outcome.reason


def test_genuine_failure_is_not_skipped(tmp_path: Path) -> None:
    """A node that really fails (no importorskip guard, no 'found no collectors' shape)
    stays the existing hard-fail path -- the skip predicate must not mask it (Decision 55)."""
    repo = tmp_path / "repo"
    rel = _write_guarded_test_file(repo, "totally_fake_module_xyz")
    row = {
        "check_id": "genuine-fail",
        "primitive_slot": "test_selector",
        "check_spec": {"node_id": f"{rel}::test_x"},
    }
    fail_result = vg.CheckResult(
        status=CheckStatus.FAIL,
        message="pytest node failed",
        actual="FAILED tests/test_guarded_fixture.py::test_x - AssertionError\n",
    )
    with (
        mock.patch.object(vg, "materialize_check_in_tree", return_value=mock.Mock(run=lambda: fail_result)),
        mock.patch.object(vg, "_excluded_heavy_import_names", return_value={"totally_fake_module_xyz"}),
    ):
        outcome = vg.run_differential(row, repo_root=repo)
    assert outcome.admitted is False
    assert outcome.skipped is False
    assert "does not pass on HEAD" in outcome.reason


def test_differential_outcome_skipped_defaults_false() -> None:
    assert vg.DifferentialOutcome(admitted=True, reason="x").skipped is False


class TestSkipPredicateEdgeCases:
    """Direct coverage for the _module_level_importorskip_dep / _differential_skip_reason
    branches not exercised by the two module-level tests above."""

    def test_importorskip_dep_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert vg._module_level_importorskip_dep(tmp_path / "does-not-exist.py") is None

    def test_importorskip_dep_no_guard_returns_none(self, tmp_path: Path) -> None:
        f = tmp_path / "plain.py"
        f.write_text("def test_x():\n    assert True\n", encoding="utf-8")
        assert vg._module_level_importorskip_dep(f) is None

    def test_skip_reason_empty_node_id_returns_none(self) -> None:
        row = {"primitive_slot": "test_selector", "check_spec": {}}
        live = vg.CheckResult(status=CheckStatus.FAIL, message="x", actual="")
        assert vg._differential_skip_reason(row, live, Path(".")) is None

    def test_skip_reason_no_guard_in_file_returns_none(self, tmp_path: Path) -> None:
        rel = "tests/test_unguarded.py"
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_x():\n    assert True\n", encoding="utf-8")
        row = {"primitive_slot": "test_selector", "check_spec": {"node_id": f"{rel}::test_x"}}
        live = vg.CheckResult(
            status=CheckStatus.FAIL,
            message="x",
            actual="collected 0 items / 1 error\nERROR: found no collectors for the given node_id\n",
        )
        assert vg._differential_skip_reason(row, live, tmp_path) is None

    def test_skip_reason_guarded_dep_not_excluded_returns_none(self, tmp_path: Path) -> None:
        """The importorskip guard names a real, always-importable module (os) -- present, so
        _excluded_and_absent's absence half fails and the co-occurrence does not hold."""
        rel = "tests/test_guarded_but_present.py"
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("import pytest\n\nfoo = pytest.importorskip('os')\n", encoding="utf-8")
        row = {"primitive_slot": "test_selector", "check_spec": {"node_id": f"{rel}::test_x"}}
        live = vg.CheckResult(
            status=CheckStatus.FAIL,
            message="x",
            actual="collected 0 items / 1 error\nERROR: found no collectors for the given node_id\n",
        )
        assert vg._differential_skip_reason(row, live, tmp_path) is None


# ---------------------------------------------------------------------------
# TestVerifierDifferential (c3) -- synthetic new verifier files
# ---------------------------------------------------------------------------

_HERMETIC_GENUINE_VERIFIER = """\
from scripts.verifiers.harness import Verifier, VerifierResult, VerifierStatus


class NewVerifier(Verifier):
    covers = ["covered.txt"]

    async def verify(self) -> VerifierResult:
        from pathlib import Path
        text = Path("covered.txt").read_text(encoding="utf-8")
        status = VerifierStatus.PASS if "expected-marker" in text else VerifierStatus.FAIL
        return VerifierResult(name=self.name, status=status)
"""

_HERMETIC_TAUTOLOGICAL_VERIFIER = """\
from scripts.verifiers.harness import Verifier, VerifierResult, VerifierStatus


class NewVerifier(Verifier):
    covers = ["covered.txt"]

    async def verify(self) -> VerifierResult:
        return VerifierResult(name=self.name, status=VerifierStatus.PASS)
"""

_NON_HERMETIC_VERIFIER = """\
from scripts.verifiers.harness import Hermeticity, Verifier, VerifierResult, VerifierStatus


class NewVerifier(Verifier):
    covers = ["covered.txt"]
    hermeticity = Hermeticity.NON_HERMETIC_BY_CONSTRUCTION

    async def verify(self) -> VerifierResult:
        return VerifierResult(name=self.name, status=VerifierStatus.PASS)
"""

_HERMETIC_ALWAYS_FAILS_VERIFIER = """\
from scripts.verifiers.harness import Verifier, VerifierResult, VerifierStatus


class NewVerifier(Verifier):
    covers = ["covered.txt"]

    async def verify(self) -> VerifierResult:
        return VerifierResult(name=self.name, status=VerifierStatus.FAIL)
"""


class TestVerifierDifferential:
    def _build_repo_with_verifier(self, tmp_path: Path, verifier_source: str) -> Path:
        """A synthetic repo with a real scripts/verifiers/harness.py copy (imported at run time)
        plus a brand-new scripts/verifiers/new_verifier.py and its covered file, at HEAD.
        origin/main is set to the base commit, which lacks the new verifier entirely (this
        models the "verifier does not exist on origin/main" c3 scenario).
        """
        real_root = vg.ROOT
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "scripts").mkdir()
        (repo / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "scripts" / "verifiers").mkdir()
        (repo / "scripts" / "verifiers" / "__init__.py").write_text("", encoding="utf-8")
        harness_src = (real_root / "scripts" / "verifiers" / "harness.py").read_text(encoding="utf-8")
        (repo / "scripts" / "verifiers" / "harness.py").write_text(harness_src, encoding="utf-8")
        _seed_graduation_deps(repo)
        (repo / "covered.txt").write_text("not yet marked\n", encoding="utf-8")
        base_sha = _commit_all(repo, "base (no new verifier, no marker)")
        _git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])
        (repo / "scripts" / "verifiers" / "new_verifier.py").write_text(verifier_source, encoding="utf-8")
        (repo / "covered.txt").write_text("expected-marker\n", encoding="utf-8")
        _commit_all(repo, "head: add new verifier + its covered change")
        return repo

    def test_genuine_hermetic_verifier_is_admitted(self, tmp_path: Path) -> None:
        repo = self._build_repo_with_verifier(tmp_path, _HERMETIC_GENUINE_VERIFIER)
        outcome = vg.run_verifier_differential(
            "scripts/verifiers/new_verifier.py", "NewVerifier", ["covered.txt"], repo_root=repo
        )
        assert outcome.admitted and not outcome.skipped, outcome.reason

    def test_tautological_hermetic_verifier_is_rejected(self, tmp_path: Path) -> None:
        repo = self._build_repo_with_verifier(tmp_path, _HERMETIC_TAUTOLOGICAL_VERIFIER)
        outcome = vg.run_verifier_differential(
            "scripts/verifiers/new_verifier.py", "NewVerifier", ["covered.txt"], repo_root=repo
        )
        assert not outcome.admitted and not outcome.skipped
        assert "passes even with its covered change reverted" in outcome.reason

    def test_non_hermetic_verifier_is_advisory_skipped(self, tmp_path: Path) -> None:
        repo = self._build_repo_with_verifier(tmp_path, _NON_HERMETIC_VERIFIER)
        outcome = vg.run_verifier_differential(
            "scripts/verifiers/new_verifier.py", "NewVerifier", ["covered.txt"], repo_root=repo
        )
        assert outcome.skipped
        assert not outcome.admitted
        assert "NON_HERMETIC_BY_CONSTRUCTION" in outcome.reason

    def test_missing_verifier_file_raises(self, tmp_path: Path) -> None:
        repo = self._build_repo_with_verifier(tmp_path, _HERMETIC_GENUINE_VERIFIER)
        with pytest.raises(vg.GraduationError, match="cannot parse verifier file"):
            vg.run_verifier_differential("scripts/verifiers/does_not_exist.py", "NewVerifier", ["covered.txt"], repo_root=repo)

    def test_missing_class_raises(self, tmp_path: Path) -> None:
        repo = self._build_repo_with_verifier(tmp_path, _HERMETIC_GENUINE_VERIFIER)
        with pytest.raises(vg.GraduationError, match="not found"):
            vg.run_verifier_differential("scripts/verifiers/new_verifier.py", "NoSuchClass", ["covered.txt"], repo_root=repo)

    def test_verifier_failing_at_head_is_not_admitted(self, tmp_path: Path) -> None:
        """A hermetic verifier that never PASSes at HEAD/live is rejected before any worktree op."""
        repo = self._build_repo_with_verifier(tmp_path, _HERMETIC_ALWAYS_FAILS_VERIFIER)
        outcome = vg.run_verifier_differential(
            "scripts/verifiers/new_verifier.py", "NewVerifier", ["covered.txt"], repo_root=repo
        )
        assert not outcome.admitted and not outcome.skipped
        assert "expected PASS" in outcome.reason

    def test_checkout_failure_in_verifier_differential_raises(self, tmp_path: Path) -> None:
        """A covered_changed path that doesn't resolve on origin/main fails the revert checkout."""
        repo = self._build_repo_with_verifier(tmp_path, _HERMETIC_GENUINE_VERIFIER)
        with pytest.raises(vg.GraduationError, match="could not revert covered files"):
            vg.run_verifier_differential(
                "scripts/verifiers/new_verifier.py",
                "NewVerifier",
                ["does-not-exist-anywhere.txt"],
                repo_root=repo,
            )


# ---------------------------------------------------------------------------
# Injected revert_runner + fail-loud-on-error
# ---------------------------------------------------------------------------


class TestInjectedRevertRunnerAndFailLoud:
    def test_make_worktree_revert_runner_real_checkout(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "f.txt").write_text("base\n", encoding="utf-8")
        base_sha = _commit_all(repo, "base")
        _git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])
        (repo / "f.txt").write_text("head\n", encoding="utf-8")
        _commit_all(repo, "head")

        row = {
            "check_id": "revert-runner-check",
            "primitive_slot": "grep_count",
            "check_spec": {"path": "f.txt", "pattern": "head", "operator": "eq", "count": 1},
        }
        runner = vg.make_worktree_revert_runner(row, ref="origin/main", repo_root=repo)
        result = runner(vg.materialize_check_in_tree(row, repo))
        assert result.status == CheckStatus.FAIL

    def test_git_worktree_cleans_up_on_success(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "f.txt").write_text("x\n", encoding="utf-8")
        _commit_all(repo, "base")
        with vg.git_worktree("HEAD", repo_root=repo) as wt:
            assert wt.exists()
            captured = wt
        assert not captured.exists()

    def test_git_worktree_raises_on_bad_ref(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "f.txt").write_text("x\n", encoding="utf-8")
        _commit_all(repo, "base")
        with pytest.raises(vg.GraduationError, match="git worktree add failed"):
            with vg.git_worktree("refs/heads/does-not-exist", repo_root=repo):
                pass  # pragma: no cover

    def test_git_worktree_falls_back_to_prune_on_remove_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "f.txt").write_text("x\n", encoding="utf-8")
        _commit_all(repo, "base")

        from scripts.checks.verification import _graduation_git

        real_run_git = _graduation_git.run_git
        calls: list[list[str]] = []

        def fake_run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
            calls.append(args)
            if args[:2] == ["worktree", "remove"]:
                return subprocess.CompletedProcess(args=["git", *args], returncode=1, stdout="", stderr="simulated failure")
            return real_run_git(args, cwd)

        monkeypatch.setattr(_graduation_git, "run_git", fake_run_git)
        with vg.git_worktree("HEAD", repo_root=repo):
            pass
        assert any(a[:2] == ["worktree", "prune"] for a in calls), calls

    def test_verifier_differential_non_deterministic_status_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "scripts").mkdir()
        (repo / "scripts" / "verifiers").mkdir(parents=True)
        (repo / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "scripts" / "verifiers" / "__init__.py").write_text("", encoding="utf-8")
        harness_src = (vg.ROOT / "scripts" / "verifiers" / "harness.py").read_text(encoding="utf-8")
        (repo / "scripts" / "verifiers" / "harness.py").write_text(harness_src, encoding="utf-8")
        _seed_graduation_deps(repo)
        (repo / "covered.txt").write_text("x\n", encoding="utf-8")
        base_sha = _commit_all(repo, "base")
        _git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])
        (repo / "scripts" / "verifiers" / "new_verifier.py").write_text(_HERMETIC_GENUINE_VERIFIER, encoding="utf-8")
        _commit_all(repo, "head")

        statuses = iter(["PASS", "WARN"])
        monkeypatch.setattr(vg, "_run_verifier_subprocess", lambda *a, **k: next(statuses))
        with pytest.raises(vg.GraduationError, match="non-deterministic"):
            vg.run_verifier_differential("scripts/verifiers/new_verifier.py", "NewVerifier", ["covered.txt"], repo_root=repo)


# ---------------------------------------------------------------------------
# _run_verifier_subprocess error paths (fail-loud on a crashed/garbled subprocess)
# ---------------------------------------------------------------------------


class TestRunVerifierSubprocessErrors:
    def test_crash_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=2, stdout="", stderr="boom")
        )
        with pytest.raises(vg.GraduationError, match="crashed"):
            vg._run_verifier_subprocess("mod", "Cls", Path("."))

    def test_no_output_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        )
        with pytest.raises(vg.GraduationError, match="produced no output"):
            vg._run_verifier_subprocess("mod", "Cls", Path("."))

    def test_bad_json_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout="not json", stderr="")
        )
        with pytest.raises(vg.GraduationError, match="could not parse"):
            vg._run_verifier_subprocess("mod", "Cls", Path("."))

    def test_missing_status_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout='{"foo": "bar"}', stderr=""),
        )
        with pytest.raises(vg.GraduationError, match="missing 'status'"):
            vg._run_verifier_subprocess("mod", "Cls", Path("."))


# ---------------------------------------------------------------------------
# Subprocess entry point (`python -m scripts.verification_graduation --run-verifier ...`)
# ---------------------------------------------------------------------------


class TestSubprocessEntryPoint:
    def _register_fake_verifier(self, monkeypatch: pytest.MonkeyPatch, module_name: str) -> None:
        from scripts.verifiers.harness import Verifier, VerifierResult, VerifierStatus

        class _FakeVerifier(Verifier):
            async def verify(self) -> VerifierResult:
                return VerifierResult(name=self.name, status=VerifierStatus.PASS)

        fake_module = types.ModuleType(module_name)
        fake_module.FakeVerifier = _FakeVerifier
        monkeypatch.setitem(sys.modules, module_name, fake_module)

    def test_run_verifier_entry_prints_json(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        self._register_fake_verifier(monkeypatch, "fake_verifier_module_for_entry_test")
        vg._run_verifier_entry("fake_verifier_module_for_entry_test", "FakeVerifier")
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["status"] == "PASS"

    def test_main_dispatches_to_run_verifier_entry(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        self._register_fake_verifier(monkeypatch, "fake_verifier_module_for_main_test")
        monkeypatch.setattr(sys, "argv", ["prog", "--run-verifier", "fake_verifier_module_for_main_test", "FakeVerifier"])
        runpy.run_module("scripts.verification_graduation", run_name="__main__")
        payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert payload["status"] == "PASS"

    def test_main_prints_usage_on_bad_args(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["prog"])
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("scripts.verification_graduation", run_name="__main__")
        assert exc_info.value.code == 2
