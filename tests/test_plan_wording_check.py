"""Tests for scripts/roadmap/plan_wording_check.py: the rehearsal of a plan's approved wording through the fast tier."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from scripts.roadmap import plan_wording_check as pwc
from scripts.roadmap.plan_gate_rounds import GateRound

_BUDGET = "config/prose_budgets.yaml"
_STRUCT = "config/structural_size_budgets.yaml"
_INDEX = "docs/decisions-index.json"
_SKILL = ".claude/skills/demo/SKILL.md"
_BUDGET_TEXT = (
    "# header comment\nS4:\n  .claude/skills/demo/SKILL.md: 100  # raise-approved: dec-75 kept note\n  other.md: 7\n"
    "S1:\n  root_ambient_load_set: 50\n"
)
_STRUCT_TEXT = "a: 1\nb: 2\n"
_BASELINE = ".secrets.baseline"
_CONTRACT = "docs/contracts/zz-probe.yaml"
_CHECK = "validate_zz_probe"
_MODULE = f"scripts/checks/roadmap/{_CHECK}.py"
_GATE = "GATE-FAILURE v1 source=gate check="
_CONTRACT_BODY = f"contract:\n  id: zz\n  class: D\n  evaluator:\n    check: {_CHECK}\n"


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.email", "t@example.com")
    _git(main, "config", "user.name", "t")
    _git(main, "config", "commit.gpgsign", "false")
    _write(main, ".gitignore", "logs/debug/\n")
    _write(main, _SKILL, "one two three\n")
    _write(main, "docs/DECISIONS.md", "decision text\n")
    _write(main, _BUDGET, _BUDGET_TEXT)
    _write(main, _STRUCT, _STRUCT_TEXT)
    _write(main, _INDEX, "{}\n")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "base")
    _git(main, "update-ref", "refs/remotes/origin/main", "HEAD")
    return main


@pytest.fixture
def tree(repo: Path, tmp_path: Path) -> Path:
    scratch = tmp_path / "scratch"
    _git(repo, "worktree", "add", "-q", "--detach", str(scratch), "HEAD")
    return scratch


def _sidecar(tmp_path: Path, blocks: list[dict[str, str]] | None = None) -> Path:
    blocks = blocks or [{"id": "b1", "file": _SKILL, "before": "two\n", "after": "TWO\n"}]
    path = tmp_path / "sidecar.yaml"
    path.write_text(yaml.safe_dump({"schema_version": 1, "slug": "demo", "blocks": blocks}), encoding="utf-8")
    return path


class FakeRunner:
    """Stands in for the tree's bin/venv-python: measure, decisions_index and validate --pre."""

    def __init__(
        self,
        *,
        pre_rc: int = 0,
        pre_out: str = "All checks passed.\n",
        manifest: dict[str, Any] | None | str = "complete",
        surfaces: dict[str, Any] | str | None = None,
        index_rc: int = 0,
        raises: BaseException | None = None,
        sub_rc: int = 0,
        sub_out: str = "",
        hook_steps: list[tuple[int, str | None]] | None = None,
    ) -> None:
        self.sub_rc, self.sub_out, self.hook_steps = sub_rc, sub_out, list(hook_steps or [])
        self.timeouts: list[int] = []
        self.calls: list[tuple[list[str], Path]] = []
        self.pre_rc, self.pre_out, self.manifest, self.index_rc, self.raises = pre_rc, pre_out, manifest, index_rc, raises
        self.surfaces = surfaces if surfaces is not None else {}

    def __call__(self, cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
        self.calls.append((cmd, cwd))
        self.timeouts.append(timeout)
        if "scripts.roadmap.wording_check_pending" in cmd:
            return subprocess.CompletedProcess(cmd, self.sub_rc, self.sub_out, "")
        if "pre_commit" in cmd:
            rc, rewrite = self.hook_steps.pop(0)
            if rewrite is not None:
                _write(cwd, _BASELINE, rewrite)
            return subprocess.CompletedProcess(cmd, rc, "hook said no\n" if rc else "", "")
        if self.raises is not None and "scripts.validate" in cmd:
            raise self.raises
        if cmd[1] == "-c":
            out = self.surfaces if isinstance(self.surfaces, str) else json.dumps(self.surfaces)
            return subprocess.CompletedProcess(cmd, 0, out, "")
        if "scripts.decisions_index" in cmd:
            _write(cwd, _INDEX, '{"rewritten": true}\n')
            return subprocess.CompletedProcess(cmd, self.index_rc, "index failed" if self.index_rc else "", "")
        if self.manifest == "complete":
            manifest: Any = {"sha": _git(cwd, "rev-parse", "HEAD"), "capped": False, "deferred": []}
        else:
            manifest = self.manifest
        if manifest is not None:
            _write(cwd, "logs/debug/selection-manifest.json", json.dumps(manifest))
        return subprocess.CompletedProcess(cmd, self.pre_rc, self.pre_out, "")

    @property
    def commands(self) -> list[list[str]]:
        return [call[0] for call in self.calls]


def _place(tree: Path) -> None:
    _write(tree, _SKILL, "one TWO three\n")


def _surfaces(size: int = 90) -> dict[str, Any]:
    return {
        "S4": {"prose_bytes": size, "files": [{"path": _SKILL, "prose_bytes": size}]},
        "S1": {"prose_bytes": 40, "files": [{"path": "AGENTS.md", "prose_bytes": 40}]},
    }


def _run(tmp_path: Path, tree: Path, runner: FakeRunner, **kwargs: Any) -> pwc.Result:
    return pwc.run("demo", tree, _sidecar(tmp_path, kwargs.pop("blocks", None)), runner)


class TestRefusals:
    def test_head_not_on_main(self, tmp_path: Path, repo: Path, tree: Path) -> None:
        _write(tree, "extra.txt", "x")
        _git(tree, "add", "-A")
        _git(
            tree,
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            "x",
        )
        _place(tree)
        result = _run(tmp_path, tree, FakeRunner())
        assert result.verdict == "FAIL" and "not an ancestor of origin/main" in result.reason
        assert result.digest and result.selection == ""

    def test_edit_outside_the_sidecar(self, tmp_path: Path, tree: Path) -> None:
        _place(tree)
        _write(tree, "stray.txt", "x")
        result = _run(tmp_path, tree, FakeRunner())
        assert result.verdict == "FAIL" and "outside the sidecar" in result.reason and "stray.txt" in result.reason

    def test_a_fifth_companion_is_not_allowed(self, tmp_path: Path, tree: Path) -> None:
        _place(tree)
        _write(tree, "docs/DECISIONS.md", "changed\n")
        assert "docs/DECISIONS.md" in _run(tmp_path, tree, FakeRunner()).reason

    def test_verify_mismatch(self, tmp_path: Path, tree: Path) -> None:
        _write(tree, _SKILL, "one other three\n")
        result = _run(tmp_path, tree, FakeRunner())
        assert result.verdict == "FAIL" and "does not verify" in result.reason

    def test_unreadable_sidecar(self, tmp_path: Path, tree: Path) -> None:
        result = pwc.run("demo", tree, tmp_path / "missing.yaml", FakeRunner())
        assert result.verdict == "FAIL" and "cannot be read" in result.reason

    def test_a_sidecar_that_does_not_parse(self, tmp_path: Path, tree: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("blocks: []\n", encoding="utf-8")
        assert "cannot be read" in pwc.run("demo", tree, bad, FakeRunner()).reason

    def test_a_git_error(self, tmp_path: Path) -> None:
        not_a_repo = tmp_path / "plain"
        not_a_repo.mkdir()
        result = pwc.run("demo", not_a_repo, _sidecar(tmp_path), FakeRunner())
        assert result.verdict == "FAIL" and "git rev-parse HEAD failed" in result.reason

    def test_runner_timeout(self, tmp_path: Path, tree: Path) -> None:
        _place(tree)
        result = _run(tmp_path, tree, FakeRunner(raises=subprocess.TimeoutExpired("x", 1)))
        assert result.verdict == "FAIL" and "timed out after 900 s" in result.reason

    def test_runner_cannot_start(self, tmp_path: Path, tree: Path) -> None:
        _place(tree)
        result = _run(tmp_path, tree, FakeRunner(raises=FileNotFoundError("no venv")))
        assert result.verdict == "FAIL" and "could not start" in result.reason

    def test_decisions_index_failure(self, tmp_path: Path, tree: Path) -> None:
        _write(tree, "docs/DECISIONS.md", "decision text plus\n")
        blocks = [{"id": "d1", "file": "docs/DECISIONS.md", "before": "text\n", "after": "text plus\n"}]
        result = _run(tmp_path, tree, FakeRunner(index_rc=1), blocks=blocks)
        assert result.verdict == "FAIL" and "decisions_index --write failed" in result.reason

    def test_prose_measurement_that_is_not_json(self, tmp_path: Path, tree: Path) -> None:
        _place(tree)
        result = _run(tmp_path, tree, FakeRunner(surfaces="not json"))
        assert result.verdict == "FAIL" and "did not return JSON" in result.reason

    def test_a_refusal_runs_no_fast_tier(self, tmp_path: Path, tree: Path) -> None:
        runner = FakeRunner()
        _write(tree, _SKILL, "one other three\n")
        _run(tmp_path, tree, runner)
        assert runner.calls == []


class TestFastTier:
    def test_pass_with_complete_selection(self, tmp_path: Path, tree: Path) -> None:
        _place(tree)
        runner = FakeRunner(surfaces=_surfaces())
        result = _run(tmp_path, tree, runner)
        assert result.verdict == "PASS" and result.selection == "complete" and len(result.digest) == 64
        assert result.base == _git(tree, "rev-parse", "HEAD")
        assert runner.commands[-1] == ["bin/venv-python", "-m", "scripts.validate", "--pre"]
        assert all(cwd == tree for _, cwd in runner.calls)

    def test_red_fast_tier_echoes_the_failing_lines(self, tmp_path: Path, tree: Path) -> None:
        _place(tree)
        noise = "".join(f"line {n}\n" for n in range(40))
        runner = FakeRunner(surfaces=_surfaces(), pre_rc=1, pre_out=noise + "  FAIL: cap test overflowed\nFailed checks:\n")
        result = _run(tmp_path, tree, runner)
        assert result.verdict == "FAIL" and "fast tier" in result.reason and result.selection == "complete"
        assert result.echo.splitlines() == ["  FAIL: cap test overflowed", "Failed checks:"]

    def test_red_fast_tier_without_failure_markers_echoes_the_tail(self, tmp_path: Path, tree: Path) -> None:
        _place(tree)
        out = "".join(f"line {n}\n" for n in range(30))
        result = _run(tmp_path, tree, FakeRunner(surfaces=_surfaces(), pre_rc=2, pre_out=out))
        assert result.echo.splitlines() == [f"line {n}" for n in range(10, 30)]

    @pytest.mark.parametrize(
        "manifest",
        [
            None,
            {"sha": "0" * 40, "capped": False, "deferred": []},
            {"sha": "SELF", "capped": False, "deferred": [], "fallback": True},
            {"sha": "SELF", "capped": True, "deferred": []},
            {"sha": "SELF", "capped": False, "deferred": ["tests/test_x.py"]},
            ["not", "a", "dict"],
        ],
        ids=["absent", "other-sha", "fallback", "capped", "deferred", "not-a-dict"],
    )
    def test_selection_is_degraded(self, tmp_path: Path, tree: Path, manifest: Any) -> None:
        _place(tree)
        if isinstance(manifest, dict) and manifest.get("sha") == "SELF":
            manifest = {**manifest, "sha": _git(tree, "rev-parse", "HEAD")}
        result = _run(tmp_path, tree, FakeRunner(surfaces=_surfaces(), manifest=manifest))
        assert result.verdict == "PASS" and result.selection == "degraded"

    def test_an_unparseable_manifest_is_degraded(self, tmp_path: Path, tree: Path) -> None:
        _place(tree)
        result = _run(tmp_path, tree, FakeRunner(surfaces=_surfaces(), manifest="{broken"))
        assert result.selection == "degraded"

    def test_a_stale_manifest_is_deleted_before_the_run(self, tmp_path: Path, tree: Path) -> None:
        _place(tree)
        _write(tree, "logs/debug/selection-manifest.json", json.dumps({"sha": _git(tree, "rev-parse", "HEAD")}))
        result = _run(tmp_path, tree, FakeRunner(surfaces=_surfaces(), manifest=None))
        assert result.selection == "degraded"
        assert not (tree / "logs/debug/selection-manifest.json").exists()


class TestVenvLink:
    def test_the_checkout_venv_is_linked_when_absent(self, tmp_path: Path, tree: Path, monkeypatch) -> None:
        fake_root = tmp_path / "checkout"
        (fake_root / ".venv").mkdir(parents=True)
        monkeypatch.setattr(pwc, "ROOT", fake_root)
        _place(tree)
        _run(tmp_path, tree, FakeRunner(surfaces=_surfaces()))
        assert (tree / ".venv").is_symlink() and (tree / ".venv").resolve() == (fake_root / ".venv").resolve()

    def test_an_existing_venv_is_left_alone(self, tmp_path: Path, tree: Path, monkeypatch) -> None:
        fake_root = tmp_path / "checkout"
        (fake_root / ".venv").mkdir(parents=True)
        monkeypatch.setattr(pwc, "ROOT", fake_root)
        (tree / ".venv").mkdir()
        _place(tree)
        _run(tmp_path, tree, FakeRunner(surfaces=_surfaces()))
        assert not (tree / ".venv").is_symlink()

    def test_no_link_when_the_checkout_has_no_venv(self, tmp_path: Path, tree: Path, monkeypatch) -> None:
        monkeypatch.setattr(pwc, "ROOT", tmp_path / "empty")
        _place(tree)
        _run(tmp_path, tree, FakeRunner(surfaces=_surfaces()))
        assert not (tree / ".venv").exists()

    def test_the_venv_link_is_not_an_edit(self, tmp_path: Path, tree: Path, monkeypatch) -> None:
        fake_root = tmp_path / "checkout"
        (fake_root / ".venv").mkdir(parents=True)
        monkeypatch.setattr(pwc, "ROOT", fake_root)
        _place(tree)
        (tree / ".venv").symlink_to(fake_root / ".venv", target_is_directory=True)
        assert _run(tmp_path, tree, FakeRunner(surfaces=_surfaces())).verdict == "PASS"


class TestCompanions:
    def test_a_shrunk_sidecar_file_lowers_its_budget_entry(self, tmp_path: Path, tree: Path) -> None:
        _place(tree)
        _run(tmp_path, tree, FakeRunner(surfaces=_surfaces(90)))
        text = (tree / _BUDGET).read_text(encoding="utf-8")
        assert ".claude/skills/demo/SKILL.md: 90  # raise-approved: dec-75 kept note" in text
        assert "# header comment" in text and "other.md: 7" in text

    def test_a_grown_sidecar_file_is_left_unraised(self, tmp_path: Path, tree: Path) -> None:
        _place(tree)
        _run(tmp_path, tree, FakeRunner(surfaces=_surfaces(250)))
        assert ".claude/skills/demo/SKILL.md: 100 " in (tree / _BUDGET).read_text(encoding="utf-8")

    def test_the_root_load_set_entry_is_lowered_for_a_root_member(self, tmp_path: Path, tree: Path) -> None:
        pwc._lower_prose_budgets(tree, {"AGENTS.md"}, FakeRunner(surfaces=_surfaces()))
        assert "root_ambient_load_set: 40" in (tree / _BUDGET).read_text(encoding="utf-8")

    def test_an_unmarked_budget_change_is_reset(self, tmp_path: Path, tree: Path) -> None:
        _place(tree)
        _write(tree, _BUDGET, _BUDGET_TEXT.replace("other.md: 7", "other.md: 70"))
        _write(tree, _STRUCT, "a: 1\nb: 3\nc: 4\n")
        _run(tmp_path, tree, FakeRunner(surfaces=_surfaces()))
        assert "other.md: 7\n" in (tree / _BUDGET).read_text(encoding="utf-8")
        assert (tree / _STRUCT).read_text(encoding="utf-8") == _STRUCT_TEXT

    def test_a_marked_raise_is_kept_in_either_budget_file(self, tmp_path: Path, tree: Path) -> None:
        _place(tree)
        _write(tree, _BUDGET, _BUDGET_TEXT.replace("other.md: 7", "other.md: 70  # raise-approved: dec-9 why"))
        _write(tree, _STRUCT, "a: 1\nb: 2\nc: 4  # raise-approved: dec-9 new\n")
        _run(tmp_path, tree, FakeRunner(surfaces=_surfaces()))
        assert "other.md: 70  # raise-approved: dec-9 why" in (tree / _BUDGET).read_text(encoding="utf-8")
        assert "c: 4  # raise-approved: dec-9 new" in (tree / _STRUCT).read_text(encoding="utf-8")

    def test_an_unmarked_deletion_is_restored_and_a_marked_one_pair_is_kept(self, tmp_path: Path) -> None:
        base = "a: 1\nb: 2\nc: 3\n"
        assert pwc._keep_marked_lines(base, "a: 1\nc: 3\n") == base
        assert pwc._keep_marked_lines(base, "a: 1\nb: 2\nc: 3\nd: 4\n") == base
        marked = "a: 1\nb: 9  # raise-approved: dec-1 r\nc: 3\n"
        assert pwc._keep_marked_lines(base, marked) == marked

    def test_a_missing_budget_file_is_skipped(self, tmp_path: Path, tree: Path) -> None:
        _git(tree, "rm", "-q", "-f", _BUDGET)
        _git(tree, "rm", "-q", "-f", _STRUCT)
        _place(tree)
        runner = FakeRunner()
        assert _run(tmp_path, tree, runner).verdict == "PASS"
        assert not any(cmd[1] == "-c" for cmd in runner.commands)

    def test_the_decisions_index_is_rewritten_only_when_decisions_md_is_a_sidecar_file(
        self, tmp_path: Path, tree: Path
    ) -> None:
        _place(tree)
        _write(tree, _INDEX, "tampered\n")
        runner = FakeRunner(surfaces=_surfaces())
        _run(tmp_path, tree, runner)
        assert (tree / _INDEX).read_text(encoding="utf-8") == "{}\n"
        assert not any("scripts.decisions_index" in cmd for cmd in runner.commands)

    def test_the_decisions_index_is_regenerated_for_a_decisions_edit(self, tmp_path: Path, tree: Path) -> None:
        _write(tree, "docs/DECISIONS.md", "decision text plus\n")
        blocks = [{"id": "d1", "file": "docs/DECISIONS.md", "before": "text\n", "after": "text plus\n"}]
        runner = FakeRunner(surfaces={})
        assert _run(tmp_path, tree, runner, blocks=blocks).verdict == "PASS"
        assert ["bin/venv-python", "-m", "scripts.decisions_index", "--write"] in runner.commands
        assert (tree / _INDEX).read_text(encoding="utf-8") == '{"rewritten": true}\n'


class TestCli:
    def test_pass_prints_the_row_and_exits_zero(self, tmp_path: Path, tree: Path, monkeypatch, capsys) -> None:
        _place(tree)
        sidecar = _sidecar(tmp_path)
        monkeypatch.setattr(pwc, "default_runner", FakeRunner(surfaces=_surfaces()))
        monkeypatch.setattr(pwc, "ROOT", tmp_path)
        code = pwc.main(["--slug", "demo", "--tree", str(tree), "--sidecar", str(sidecar)])
        out = capsys.readouterr().out
        assert code == 0
        assert out.startswith("WORDING-CHECK verdict=PASS digest=")
        assert "selection=complete" in out
        assert "- {gate: wording-check, round: 1, verdict: PASS, digest: " in out

    def test_a_red_fast_tier_prints_the_reason_the_echo_and_exits_one(
        self, tmp_path: Path, tree: Path, monkeypatch, capsys
    ) -> None:
        _place(tree)
        monkeypatch.setattr(pwc, "default_runner", FakeRunner(surfaces=_surfaces(), pre_rc=1, pre_out="FAIL: boom\n"))
        monkeypatch.setattr(pwc, "ROOT", tmp_path)
        code = pwc.main(["--slug", "demo", "--tree", str(tree), "--sidecar", str(_sidecar(tmp_path))])
        out = capsys.readouterr().out
        assert code == 1
        assert (
            "verdict=FAIL" in out and "reason: the fast tier" in out and "FAIL: boom" in out and "gate: wording-check" in out
        )

    def test_a_refusal_prints_its_reason_and_no_row(self, tmp_path: Path, tree: Path, monkeypatch, capsys) -> None:
        _write(tree, "stray.txt", "x")
        monkeypatch.setattr(pwc, "ROOT", tmp_path)
        code = pwc.main(["--slug", "demo", "--tree", str(tree), "--sidecar", str(_sidecar(tmp_path))])
        out = capsys.readouterr().out
        assert code == 1
        assert out.startswith("WORDING-CHECK verdict=FAIL reason:") and "gate: wording-check" not in out

    def test_the_default_sidecar_is_the_checkout_copy(self, tmp_path: Path, tree: Path, monkeypatch) -> None:
        approved = tmp_path / "docs" / "plans" / "approved"
        approved.mkdir(parents=True)
        (approved / "demo.yaml").write_text(_sidecar(tmp_path).read_text(encoding="utf-8"), encoding="utf-8")
        monkeypatch.setattr(pwc, "ROOT", tmp_path)
        _place(tree)
        assert pwc.run("demo", tree, None, FakeRunner(surfaces=_surfaces())).verdict == "PASS"


class TestDefaultRunner:
    def test_runs_a_command_in_the_given_directory(self, tmp_path: Path) -> None:
        done = pwc.default_runner(["pwd"], tmp_path, 30)
        assert done.returncode == 0 and Path(done.stdout.strip()).resolve() == tmp_path.resolve()


def _plan(*rows: tuple[str, str], rounds: int = 0) -> Any:
    scope = [SimpleNamespace(file=f, action=a) for f, a in rows]
    return SimpleNamespace(scope=scope, gate_rounds=[SimpleNamespace(gate="wording-check")] * rounds)


_CONTRACT_BLOCKS = [{"id": "c1", "file": _CONTRACT, "before": "", "after": _CONTRACT_BODY}]


def _contract_run(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner, action: str = "Create"
) -> pwc.Result:
    monkeypatch.setattr(pwc, "_load_plan", lambda path: _plan((_MODULE, action)))
    _write(tree, _CONTRACT, _CONTRACT_BODY)
    return _run(tmp_path, tree, runner, blocks=_CONTRACT_BLOCKS)


class TestPlanCreatedEvaluator:
    def test_a_drift_only_red_is_an_attributed_pass(self, tmp_path: Path, tree: Path, monkeypatch) -> None:
        runner = FakeRunner(pre_rc=1, pre_out=f"{_GATE}validate_contract_drift\n")
        result = _contract_run(tmp_path, tree, monkeypatch, runner)
        assert result.verdict == "PASS" and result.pairs == (("zz-probe.yaml", _CHECK, _MODULE),)
        sub = [cmd for cmd in runner.commands if "scripts.roadmap.wording_check_pending" in cmd][0]
        assert sub == [
            "bin/venv-python",
            "-m",
            "scripts.roadmap.wording_check_pending",
            "--pending",
            f"zz-probe.yaml:{_CHECK}",
            "--",
        ]
        assert runner.timeouts[-1] == pwc.ATTRIBUTION_TIMEOUT_S

    def test_the_attributed_pass_prints_the_count_the_line_and_the_row_field(
        self, tmp_path: Path, tree: Path, monkeypatch
    ) -> None:
        runner = FakeRunner(pre_rc=1, pre_out=f"{_GATE}validate_contract_drift\n")
        text = pwc._render("demo", _contract_run(tmp_path, tree, monkeypatch, runner))
        lines = text.splitlines()
        assert lines[0].startswith("WORDING-CHECK verdict=PASS ") and lines[0].endswith(" pending_evaluators=1")
        assert "fast tier red only on 1 plan-created evaluator(s); clean with them stood in" in lines
        assert f"pending: docs/contracts/zz-probe.yaml evaluator {_CHECK} (the plan creates {_MODULE})" in lines
        assert "pending_evaluators: 1" in lines[-1] and "round: 1," in lines[-1]

    def test_a_reason_from_attribute_is_a_fail(self, tmp_path: Path, tree: Path, monkeypatch) -> None:
        runner = FakeRunner(pre_rc=1, pre_out=f"{_GATE}validate_contract_drift\n", sub_rc=1, sub_out="still red\n")
        result = _contract_run(tmp_path, tree, monkeypatch, runner)
        assert result.verdict == "FAIL" and "fast tier" in result.reason and "still red" in result.reason

    def test_another_red_check_stays_a_fail(self, tmp_path: Path, tree: Path, monkeypatch) -> None:
        out = f"{_GATE}validate_contract_drift\n{_GATE}ruff\n"
        result = _contract_run(tmp_path, tree, monkeypatch, FakeRunner(pre_rc=1, pre_out=out))
        assert result.verdict == "FAIL" and "ruff" in result.reason

    def test_a_modify_row_has_no_pending_pair_and_a_red_stays_a_fail_as_today(
        self, tmp_path: Path, tree: Path, monkeypatch
    ) -> None:
        runner = FakeRunner(pre_rc=1, pre_out=f"{_GATE}validate_contract_drift\n")
        result = _contract_run(tmp_path, tree, monkeypatch, runner, "Modify")
        assert result.verdict == "FAIL" and result.reason == "the fast tier (scripts.validate --pre) failed"
        assert not any("scripts.roadmap.wording_check_pending" in cmd for cmd in runner.commands)

    def test_a_green_fast_tier_records_no_pending_count(self, tmp_path: Path, tree: Path, monkeypatch) -> None:
        result = _contract_run(tmp_path, tree, monkeypatch, FakeRunner())
        assert result.verdict == "PASS" and result.pairs == ()
        assert "pending_evaluators" not in pwc._render("demo", result)


@pytest.fixture
def baseline_tree(repo: Path, tmp_path: Path) -> Path:
    _write(repo, _BASELINE, '{"results": {}, "generated_at": "t0"}\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "baseline")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    scratch = tmp_path / "scratch_baseline"
    _git(repo, "worktree", "add", "-q", "--detach", str(scratch), "HEAD")
    return scratch


class TestBaselineCompanion:
    def test_the_baseline_is_admitted_by_the_scope_rule(self, tmp_path: Path, tree: Path) -> None:
        _place(tree)
        _write(tree, _BASELINE, "{}\n")
        assert _run(tmp_path, tree, FakeRunner()).verdict == "PASS"

    def test_a_settle_reason_is_a_fail_and_runs_no_fast_tier(self, tmp_path: Path, baseline_tree: Path) -> None:
        _place(baseline_tree)
        runner = FakeRunner(hook_steps=[(1, None)])
        result = _run(tmp_path, baseline_tree, runner)
        assert result.verdict == "FAIL" and "hook rejects the placed words" in result.reason
        assert not any("scripts.validate" in cmd for cmd in runner.commands)
        assert runner.timeouts[-1] == pwc.MEASURE_TIMEOUT_S

    def test_a_clean_hook_run_reaches_the_fast_tier(self, tmp_path: Path, baseline_tree: Path) -> None:
        _place(baseline_tree)
        runner = FakeRunner(hook_steps=[(0, None)])
        assert _run(tmp_path, baseline_tree, runner).verdict == "PASS"
        assert runner.commands[-1] == ["bin/venv-python", "-m", "scripts.validate", "--pre"]


class TestRender:
    def test_printed_row_commits_past_detect_secrets(self) -> None:
        result = pwc.Result("PASS", digest="a" * 64, base="b" * 40, selection="complete")
        row = pwc._render("demo", result).splitlines()[-1]
        assert row.endswith("  # pragma: allowlist secret")
        parsed = yaml.safe_load(row.removeprefix("  - ").split("  # pragma")[0].replace("{", "{").strip())
        assert GateRound.model_validate(parsed).verdict == "PASS"

    def test_an_attributed_row_parses_with_its_count(self) -> None:
        result = pwc.Result("PASS", digest="a" * 64, base="b" * 40, selection="complete", pairs=(("x.yaml", "c", "m.py"),))
        row = pwc._render("demo", result).splitlines()[-1]
        parsed = yaml.safe_load(row.split("  # pragma")[0].removeprefix("  - "))
        assert GateRound.model_validate(parsed).pending_evaluators == 1


class TestHardening:
    def test_measurement_has_its_own_shorter_timeout(self, tmp_path: Path, tree: Path) -> None:
        _place(tree)
        runner = FakeRunner(surfaces=_surfaces())
        _run(tmp_path, tree, runner)
        measured = [t for (cmd, _), t in zip(runner.calls, runner.timeouts, strict=True) if cmd[1] == "-c"]
        assert measured == [pwc.MEASURE_TIMEOUT_S] == [120]
        assert runner.timeouts[-1] == pwc.PRE_TIMEOUT_S

    def test_attribution_has_its_own_timeout(self, tmp_path: Path, tree: Path, monkeypatch) -> None:
        runner = FakeRunner(pre_rc=1, pre_out=f"{_GATE}validate_contract_drift\n")
        _contract_run(tmp_path, tree, monkeypatch, runner)
        assert pwc.ATTRIBUTION_TIMEOUT_S == 600 and runner.timeouts[-1] == 600

    def test_round_comes_from_the_parsed_plan(self, tmp_path: Path, tree: Path, monkeypatch) -> None:
        monkeypatch.setattr(pwc, "_load_plan", lambda path: _plan(rounds=2))
        _place(tree)
        assert _run(tmp_path, tree, FakeRunner(surfaces=_surfaces())).round == 3

    def test_an_unloadable_plan_means_round_one_and_one_stderr_note(self, tmp_path: Path, tree: Path, capsys) -> None:
        _place(tree)
        result = pwc.run("demo", tree, _sidecar(tmp_path), FakeRunner(surfaces=_surfaces()), plan_path=tmp_path / "none.yaml")
        err = capsys.readouterr().err
        assert result.round == 1 and result.verdict == "PASS" and err.count("note: plan") == 1 and "\n" == err[-1]

    def test_echo_keeps_gate_markers_and_drops_noise(self) -> None:
        out = f"Error: unrelated noise\n  FAIL: boom\nFailed checks:\n  - ruff\n{_GATE}ruff\nValueError seen\nMore\n"
        assert pwc._failing_lines(out).splitlines() == ["  FAIL: boom", "Failed checks:", "  - ruff", f"{_GATE}ruff"]
