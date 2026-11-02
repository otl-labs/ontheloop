"""Tests for scripts/roadmap/wording_check_pending.py: plan-created evaluators, their attribution and the stand-in."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.checks.contracts.validate_contract_drift import validate_contract_drift
from scripts.roadmap import wording_check_pending as wp

_CONTRACT = "docs/contracts/zz-probe.yaml"
_CHECK = "validate_zz_probe"
_MODULE_REL = f"scripts/checks/roadmap/{_CHECK}.py"
_CONTRACT_TEXT = f"""contract:
  id: zz-probe
  class: D
  contract_version: 1
  status: ratified
  ratified_via: "PLAN-x"
  description: probe
  subject: zz-probe
  evaluator:
    check: {_CHECK}
amendment_log:
  - {{date: "2026-11-02", semantic_break: false, change_class: field_add, summary: probe}}
"""
_DRIFT_MARKER = "GATE-FAILURE v1 source=gate check=validate_contract_drift"
_PYTEST_MARKER = "GATE-FAILURE v1 source=gate check=pytest_diff"


def _plan(*rows: tuple[str, str]) -> Any:
    return SimpleNamespace(scope=[SimpleNamespace(file=f, action=a) for f, a in rows])


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    _write(tmp_path, _CONTRACT, _CONTRACT_TEXT)
    return tmp_path


class TestPendingPairs:
    def test_a_created_evaluator_module_is_pending(self, tree: Path) -> None:
        pairs = wp.pending_pairs(tree, {_CONTRACT}, _plan((_MODULE_REL, "Create")))
        assert pairs == [("zz-probe.yaml", _CHECK, _MODULE_REL)]

    def test_a_modify_row_an_absent_row_and_no_plan_are_not_pending(self, tree: Path) -> None:
        assert wp.pending_pairs(tree, {_CONTRACT}, _plan((_MODULE_REL, "Modify"))) == []
        assert wp.pending_pairs(tree, {_CONTRACT}, _plan(("scripts/other.py", "Create"))) == []
        assert wp.pending_pairs(tree, {_CONTRACT}, None) == []

    def test_a_row_outside_scripts_checks_does_not_count(self, tree: Path) -> None:
        assert wp.pending_pairs(tree, {_CONTRACT}, _plan((f"scripts/roadmap/{_CHECK}.py", "Create"))) == []

    def test_an_existing_module_is_not_pending(self, tree: Path) -> None:
        _write(tree, _MODULE_REL, "")
        assert wp.pending_pairs(tree, {_CONTRACT}, _plan((_MODULE_REL, "Create"))) == []

    @pytest.mark.parametrize(
        "text",
        ["contract:\n  class: C\n  evaluator: {check: validate_zz_probe}\n", "[1, 2]\n", "contract: 1\n", ": : :\n"],
        ids=["not-class-d", "not-a-mapping", "contract-not-mapping", "malformed"],
    )
    def test_a_non_d_or_malformed_contract_is_skipped(self, tree: Path, text: str) -> None:
        _write(tree, _CONTRACT, text)
        assert wp.pending_pairs(tree, {_CONTRACT}, _plan((_MODULE_REL, "Create"))) == []

    def test_a_contract_without_a_string_check_is_skipped(self, tree: Path) -> None:
        _write(tree, _CONTRACT, "contract:\n  class: D\n  evaluator: {agent_surface: x}\n")
        assert wp.pending_pairs(tree, {_CONTRACT}, _plan((_MODULE_REL, "Create"))) == []

    def test_a_missing_or_nested_sidecar_file_is_skipped(self, tree: Path) -> None:
        plan = _plan((_MODULE_REL, "Create"))
        assert wp.pending_pairs(tree, {"docs/contracts/missing.yaml"}, plan) == []
        _write(tree, "docs/contracts/sub/zz-probe.yaml", _CONTRACT_TEXT)
        assert wp.pending_pairs(tree, {"docs/contracts/sub/zz-probe.yaml", "docs/other.yaml"}, plan) == []


class Recorder:
    def __init__(self, rc: int = 0, out: str = "", raises: BaseException | None = None) -> None:
        self.rc, self.out, self.raises = rc, out, raises
        self.calls: list[tuple[list[str], Path, int]] = []

    def __call__(self, cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
        self.calls.append((cmd, cwd, timeout))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(cmd, self.rc, self.out, "")


_PAIRS = [("zz-probe.yaml", _CHECK, _MODULE_REL)]
_PENDING = ["--pending", f"zz-probe.yaml:{_CHECK}", "--"]


class TestAttribute:
    def test_a_drift_only_red_runs_the_sub_run_with_no_test_files(self, tree: Path) -> None:
        runner = Recorder()
        assert wp.attribute(tree, f"x\n{_DRIFT_MARKER}\n", _PAIRS, runner, 600) is None
        cmd, cwd, timeout = runner.calls[0]
        assert cmd == ["bin/venv-python", "-m", "scripts.roadmap.wording_check_pending", *_PENDING]
        assert (cwd, timeout) == (tree, 600)

    def test_drift_plus_tests_passes_the_failing_files_that_exist(self, tree: Path) -> None:
        _write(tree, "tests/test_a.py", "")
        summary = "FAILED tests/test_a.py::test_x - boom\nERROR tests/gone.py::t\nFAILED src/x.py::t\n"
        out = f"{_DRIFT_MARKER}\n{_PYTEST_MARKER}\n{summary}"
        runner = Recorder()
        assert wp.attribute(tree, out, _PAIRS, runner, 600) is None
        assert runner.calls[0][0][-1] == "tests/test_a.py" and runner.calls[0][0][-2] == "--"

    def test_a_third_gate_marker_is_a_reason(self, tree: Path) -> None:
        out = f"{_DRIFT_MARKER}\nGATE-FAILURE v1 source=gate check=ruff\n"
        runner = Recorder()
        reason = wp.attribute(tree, out, _PAIRS, runner, 600)
        assert reason is not None and "ruff" in reason and runner.calls == []

    def test_no_gate_marker_is_a_reason(self, tree: Path) -> None:
        reason = wp.attribute(tree, "FAIL: something\n", _PAIRS, Recorder(), 600)
        assert reason is not None and "does not include validate_contract_drift" in reason

    def test_a_pytest_red_without_drift_is_a_reason(self, tree: Path) -> None:
        assert wp.attribute(tree, f"{_PYTEST_MARKER}\n", _PAIRS, Recorder(), 600) is not None

    def test_status_and_precommit_markers_are_ignored(self, tree: Path) -> None:
        out = f"{_DRIFT_MARKER}\nSTATUS v1 source=pytest_diff result=failed\n- hook id: ruff\n- exit code: 1\n"
        assert wp.attribute(tree, out, _PAIRS, Recorder(), 600) is None

    def test_pytest_diff_with_no_nameable_file_is_a_reason(self, tree: Path) -> None:
        runner = Recorder()
        reason = wp.attribute(tree, f"{_DRIFT_MARKER}\n{_PYTEST_MARKER}\nno summary\n", _PAIRS, runner, 600)
        assert reason is not None and "no failing test file" in reason and runner.calls == []

    def test_a_red_sub_run_echoes_its_failing_lines(self, tree: Path) -> None:
        reason = wp.attribute(tree, f"{_DRIFT_MARKER}\n", _PAIRS, Recorder(rc=1, out="drift left over\n"), 600)
        assert reason is not None and "still red" in reason and "drift left over" in reason

    def test_a_sub_run_that_times_out_or_cannot_start_is_a_reason(self, tree: Path) -> None:
        out = f"{_DRIFT_MARKER}\n"
        assert "timed out after 600 s" in str(
            wp.attribute(tree, out, _PAIRS, Recorder(raises=subprocess.TimeoutExpired("x", 1)), 600)
        )
        assert "could not start" in str(wp.attribute(tree, out, _PAIRS, Recorder(raises=FileNotFoundError("v")), 600))


def _drift_labels(contracts: Path) -> list[str]:
    failed: list[str] = []
    validate_contract_drift(failed, contracts_dir=contracts, router_path=contracts / "none.yaml")
    return failed


class TestStandIn:
    @pytest.fixture
    def contracts(self, tmp_path: Path) -> Path:
        _write(tmp_path, "zz-probe.yaml", _CONTRACT_TEXT)
        return tmp_path

    def test_the_real_drift_check_reports_the_label_without_the_stand_in_and_not_with_it(
        self, contracts: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert any("evaluator does not resolve" in label for label in _drift_labels(contracts))
        with wp.stand_in([("zz-probe.yaml", _CHECK)]):
            assert _drift_labels(contracts) == []
        capsys.readouterr()

    def test_only_the_listed_pair_is_stood_in(self, contracts: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with wp.stand_in([("other.yaml", _CHECK), ("zz-probe.yaml", "validate_other")]):
            assert _drift_labels(contracts)
        capsys.readouterr()

    def test_other_calls_delegate_unchanged_and_the_original_is_restored(self) -> None:
        from scripts.checks.contracts import _population
        from scripts.contracts_schema import EvaluatorSpec

        original = _population.resolve_evaluator
        root = Path(__file__).resolve().parents[1]
        with wp.stand_in([("zz-probe.yaml", _CHECK)]):
            ok, _ = _population.resolve_evaluator("file-router.yaml", EvaluatorSpec(check="validate_placement"), root=root)
            assert ok
            ok, detail = _population.resolve_evaluator("zz-probe.yaml", EvaluatorSpec(check=_CHECK), root=root)
            assert ok and "stood in" in detail
        assert _population.resolve_evaluator is original

    def test_the_original_is_restored_on_an_exception(self) -> None:
        from scripts.checks.contracts import _population

        original = _population.resolve_evaluator
        with pytest.raises(RuntimeError), wp.stand_in([]):
            raise RuntimeError("boom")
        assert _population.resolve_evaluator is original


class TestCli:
    def _contracts(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _write(tmp_path, "zz-probe.yaml", _CONTRACT_TEXT)
        import scripts.checks.contracts.validate_contract_drift as mod

        real = mod.validate_contract_drift
        monkeypatch.setattr(
            mod, "validate_contract_drift", lambda failed: real(failed, contracts_dir=tmp_path, router_path=tmp_path / "n")
        )

    def test_zero_test_files_never_call_the_test_runner(self, monkeypatch, tmp_path, capsys) -> None:
        self._contracts(monkeypatch, tmp_path)
        calls: list[list[str]] = []
        rc = wp.main(["--pending", f"zz-probe.yaml:{_CHECK}", "--"], test_runner=lambda a: calls.append(a) or 0)
        capsys.readouterr()
        assert rc == 0 and calls == []

    def test_a_drift_label_left_over_exits_one_and_prints_it(self, monkeypatch, tmp_path, capsys) -> None:
        self._contracts(monkeypatch, tmp_path)
        assert wp.main(["--"], test_runner=lambda a: 0) == 1
        assert "evaluator does not resolve" in capsys.readouterr().out

    def test_test_files_run_with_the_pinned_flags_and_decide_the_exit(self, monkeypatch, tmp_path, capsys) -> None:
        self._contracts(monkeypatch, tmp_path)
        seen: list[list[str]] = []
        pending = ["--pending", f"zz-probe.yaml:{_CHECK}", "--", "tests/test_a.py"]
        assert wp.main(pending, test_runner=lambda a: seen.append(a) or 0) == 0
        assert wp.main(pending, test_runner=lambda a: 5) == 1
        capsys.readouterr()
        assert seen[0][0] == "tests/test_a.py" and "--randomly-seed=20260710" in seen[0] and "-n" not in seen[0]
        assert "--timeout" in seen[0] and "--timeout-method=thread" in seen[0]

    @pytest.mark.parametrize("bad", ["nocolon", ":check", "name:"])
    def test_a_malformed_pending_argument_exits_two(self, bad: str, capsys) -> None:
        assert wp.main(["--pending", bad, "--"]) == 2
        assert "malformed" in capsys.readouterr().err

    def test_the_default_test_runner_is_pytest(self, monkeypatch) -> None:
        import pytest as pytest_mod

        monkeypatch.setattr(pytest_mod, "main", lambda args: 0)
        assert wp._run_pytest(["x"]) == 0


class TestIsolation:
    def test_nothing_in_checks_validate_or_workflows_names_the_stand_in(self) -> None:
        root = Path(__file__).resolve().parents[1]
        files = [
            *(root / "scripts/checks").rglob("*.py"),
            root / "scripts/validate.py",
            *(root / ".github/workflows").rglob("*"),
        ]
        named = [
            str(f.relative_to(root)) for f in files if f.is_file() and "wording_check_pending" in f.read_text(encoding="utf-8")
        ]
        assert named == []
