"""Unit coverage for scripts/checks/verification/_vp_replay_rule_v.py (rec-5547)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.checks.deps import validate_hermetic_fast_tier_collectability as sibling
from scripts.checks.verification import _vp_replay_rule_v as rv

SCOPE = {"tests/test_new.py", "tests/pkg/test_a.py", "scripts/mod.py", "tests/data/fixture.json"}


def scan(command: str, root: Path, scope: set[str] | None = None) -> rv.ScanResult:
    return rv.step_rule_v_scan(command, SCOPE if scope is None else scope, root)


def refused(command: str, root: Path, scope: set[str] | None = None) -> bool:
    return bool(scan(command, root, scope).findings)


def excluded(command: str, root: Path) -> int:
    res = scan(command, root)
    assert not res.findings, command
    return res.excluded


def _plan(steps: list[dict], scope: list[tuple[str, str]] | None = None) -> dict:
    return {
        "schema_version": 2,
        "slug": "unit",
        "intent": "Unit fixture.",
        "plan_type": "IMPLEMENTATION",
        "verification_tier": "V2",
        "plan_path": "docs/plans/PLAN-unit.yaml",
        "phase": "fixture",
        "scope": [{"file": f, "action": a, "purpose": "x"} for f, a in (scope or [("tests/test_new.py", "Create")])],
        "acceptance_criteria": ["x"],
        "verification_plan": steps,
        "execution_steps": ["x"],
    }


def _step(n: int, command: str, *, hermetic: bool = True, phase: str = "pre-deploy") -> dict:
    return {
        "step": n,
        "phase": phase,
        "hermetic": hermetic,
        "action": "a",
        "command": command,
        "expected": "e",
        "fix_if": "f",
    }


def _write(root: Path, steps: list[dict], scope: list[tuple[str, str]] | None = None) -> str:
    rel = "docs/plans/PLAN-unit.yaml"
    (root / "docs" / "plans").mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(yaml.dump(_plan(steps, scope)), encoding="utf-8")
    return rel


def test_hermetic_whole_file_in_scope_step_is_refused(tmp_path: Path) -> None:
    assert refused("bin/venv-python -m pytest tests/test_new.py -q", tmp_path)
    assert refused("pytest ./tests/test_new.py", tmp_path)
    assert refused("pytest tests/pkg/", tmp_path)
    assert refused("pytest scripts/mod.py --doctest-modules", tmp_path)
    assert refused("pytest tests/test_new.py 2>&1 | tail -3", tmp_path)


def test_ruff_phase_is_refused(tmp_path: Path) -> None:
    assert scan("bin/venv-python -m ruff check scripts/", tmp_path).findings == [("ruff", [])]
    assert refused("ruff format --check .", tmp_path)
    plan_rel = _write(tmp_path, [_step(1, "ruff check scripts/")])
    failed: list[str] = []
    rv.rule_v_pre_pass([plan_rel], tmp_path, {plan_rel}, set(), failed)
    assert len(failed) == 1 and "hermetic: false or drop the step" in failed[0]


def test_ruff_phase_with_substituted_args_is_refused(tmp_path: Path) -> None:
    assert refused("ruff check $(git ls-files '*.py')", tmp_path)


def test_prefixed_invocations_are_detected(tmp_path: Path) -> None:
    for command in (
        "FOO=1 pytest tests/test_new.py",
        "env -u HOME A=b pytest tests/test_new.py",
        "bin/venv-python -I -X dev -W error -m pytest tests/test_new.py",
        "timeout 60 pytest tests/test_new.py",
        "timeout -k 5 60 bin/venv-python -m pytest tests/test_new.py",
        "! pytest tests/test_new.py",
    ):
        assert refused(command, tmp_path), command


def test_newline_and_heredoc_commands_split(tmp_path: Path) -> None:
    assert not refused("bin/venv-python -m pytest tests/other.py -q\ngit diff tests/test_new.py", tmp_path)
    assert refused("git status\nbin/venv-python -m pytest tests/test_new.py", tmp_path)
    body = "cat <<EOF\npytest tests/test_new.py\nit's an apostrophe\nEOF\ntrue"
    assert scan(body, tmp_path) == rv.ScanResult()
    assert scan("cat <<-'EOF'\n\tpytest tests/test_new.py\n\tEOF", tmp_path).findings == []


@pytest.mark.parametrize(
    "command",
    [
        "pytest $(echo tests/test_new.py)",
        "pytest `echo tests/test_new.py`",
        "(cd tests && pytest test_new.py)",
        "env -C tests pytest test_new.py",
        "echo tests/test_new.py | xargs pytest",
        "pytest --pyargs tests.test_new",
        "pytest @args.txt",
        "PYTEST_ADDOPTS=-x pytest tests/test_new.py::t",
        "python -c \"import pytest; pytest.main(['tests/test_new.py'])\"",
        "sh -c 'pytest tests/test_new.py'",
        "bash -c 'x; bin/venv-python -m pytest tests/test_new.py'",
        "{ cd tests; pytest test_new.py; }",
        "cd tests && pytest test_new.py",
    ],
)
def test_not_plain_forms_are_counted_excluded(command: str, tmp_path: Path) -> None:
    assert excluded(command, tmp_path) >= 1


def test_unterminated_heredoc_is_excluded(tmp_path: Path) -> None:
    res = scan("cat <<EOF\npytest tests/test_new.py", tmp_path)
    assert res.excluded == 1 and not res.findings


def test_no_target_and_dot_target_are_the_root(tmp_path: Path) -> None:
    assert refused("pytest -q", tmp_path)
    assert refused("pytest .", tmp_path)
    assert not refused("pytest -q", tmp_path, {"scripts/mod.py"})


def test_glob_matches_a_create_row_absent_on_disk(tmp_path: Path) -> None:
    assert refused("pytest tests/test_n*.py", tmp_path)


def test_pytest_dash_c_value_is_not_code(tmp_path: Path) -> None:
    assert refused("pytest -c pytest.ini tests/test_new.py", tmp_path)


def test_here_string_is_not_a_heredoc(tmp_path: Path) -> None:
    assert rv._cut_heredocs("cat <<< x\npytest tests/test_new.py") == ("cat <<< x\npytest tests/test_new.py", False)


def test_directory_with_only_non_test_in_scope_members_passes(tmp_path: Path) -> None:
    assert not refused("pytest tests/data/", tmp_path)


def test_glob_dotdot_and_absolute_targets_resolve(tmp_path: Path) -> None:
    (tmp_path / "tests" / "pkg").mkdir(parents=True)
    (tmp_path / "tests" / "pkg" / "test_a.py").write_text("", encoding="utf-8")
    assert refused("pytest tests/**/test_a.py", tmp_path)
    assert refused("pytest tests/pkg/../test_new.py", tmp_path)
    assert refused(f"pytest {tmp_path}/tests/test_new.py", tmp_path)
    assert not refused("pytest ../outside/tests/test_new.py", tmp_path)
    assert not refused("pytest /elsewhere/tests/test_new.py", tmp_path)


def test_selectors_out_of_scope_and_non_hermetic_steps_pass(tmp_path: Path) -> None:
    assert not refused("pytest tests/test_new.py::test_a tests/pkg/test_a.py::TestX", tmp_path)
    assert not refused("pytest tests/unrelated.py", tmp_path)
    plan_rel = _write(
        tmp_path,
        [
            _step(1, "pytest tests/test_new.py", hermetic=False),
            _step(2, "pytest tests/test_new.py", phase="post-deploy"),
            _step(3, "pytest tests/test_new.py::t"),
        ],
    )
    failed: list[str] = []
    rv.rule_v_pre_pass([plan_rel], tmp_path, {plan_rel}, set(), failed)
    assert failed == []


def test_k_filter_does_not_exempt_a_whole_target(tmp_path: Path) -> None:
    assert refused("pytest tests/test_new.py -k some_name", tmp_path)


@pytest.mark.parametrize(
    "option",
    ["--ignore", "--deselect", "-k", "--cov", "--cov-report", "--cov-config", "--junitxml", "--basetemp"],
)
def test_option_values_are_never_targets(option: str, tmp_path: Path) -> None:
    assert not refused(f"pytest tests/unrelated.py {option} tests/test_new.py", tmp_path)
    assert not refused(f"pytest tests/unrelated.py {option}=tests/test_new.py", tmp_path)


def test_value_options_cover_the_sibling_set() -> None:
    assert sibling._VALUE_OPTIONS <= rv.VALUE_OPTIONS


def test_resolved_plan_prints_advisory_and_never_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan_rel = _write(tmp_path, [_step(1, "pytest tests/test_new.py")])
    failed: list[str] = []
    rv.rule_v_pre_pass([plan_rel], tmp_path, set(), {plan_rel}, failed)
    assert failed == []
    assert "ADVISORY: 1 resolved plan(s)" in capsys.readouterr().out


def test_modified_plan_is_excluded_and_announced(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan_rel = _write(tmp_path, [_step(1, "pytest tests/test_new.py")])
    failed: list[str] = []
    rv.rule_v_pre_pass([plan_rel], tmp_path, set(), set(), failed)
    assert failed == []
    assert "EXCLUDED: 1 diff-present plan(s) neither added nor resolved" in capsys.readouterr().out


def test_several_directory_targets_warn_and_never_fail(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    plan_rel = _write(tmp_path, [_step(1, "pytest a b")])
    failed: list[str] = []
    rv.rule_v_pre_pass([plan_rel], tmp_path, {plan_rel}, set(), failed)
    assert failed == []
    assert "WARN:" in capsys.readouterr().out


def test_unparseable_segment_fails_open_and_is_announced(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan_rel = _write(tmp_path, [_step(1, "pytest 'tests/test_new.py")])
    failed: list[str] = []
    rv.rule_v_pre_pass([plan_rel], tmp_path, {plan_rel}, set(), failed)
    assert failed == []
    assert "EXCLUDED: 1 not-plain" in capsys.readouterr().out


def test_import_error_reddens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(rel: str, root: Path):
        raise ImportError("nope")

    monkeypatch.setattr(rv._common, "load_plan", boom)
    failed: list[str] = []
    rv.rule_v_pre_pass(["docs/plans/PLAN-x.yaml"], tmp_path, {"docs/plans/PLAN-x.yaml"}, set(), failed)
    assert len(failed) == 1 and failed[0].startswith("vp-hermetic-rule-v ")


def test_load_error_skips(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    failed: list[str] = []
    rv.rule_v_pre_pass(["docs/plans/PLAN-missing.yaml"], tmp_path, {"docs/plans/PLAN-missing.yaml"}, set(), failed)
    assert failed == []
    assert "SKIP:" in capsys.readouterr().out


def test_quoting_escapes_continuations_and_separators(tmp_path: Path) -> None:
    assert refused('pytest "a\\"b" tests/test_new.py', tmp_path)
    assert refused("pytest tests/te\\st_new.py", tmp_path)
    assert refused("pytest \\\n tests/test_new.py", tmp_path)
    assert refused("true && pytest tests/test_new.py", tmp_path)
    assert refused("false || pytest tests/test_new.py", tmp_path)
    assert refused("pytest tests/test_new.py &", tmp_path)
    assert refused("pytest tests/test_new.py > out.txt", tmp_path)
    assert refused("pytest tests/test_new.py >out.txt 2>&1", tmp_path)
    assert refused("pytest tests/test_new.py &> out.txt", tmp_path)


def test_unrecognised_prefixes_and_options_are_excluded(tmp_path: Path) -> None:
    assert refused("env -i pytest tests/test_new.py", tmp_path)
    assert excluded("env -x pytest tests/test_new.py", tmp_path) == 1
    assert excluded("env PYTEST_ADDOPTS=-x pytest tests/test_new.py", tmp_path) == 1
    assert excluded("timeout pytest tests/test_new.py", tmp_path) == 1
    assert excluded("echo pytest tests/test_new.py", tmp_path) == 1
    assert excluded("grep -n pytest README.md", tmp_path) == 1
    assert excluded("pytest tests/test_new.py < input.txt", tmp_path) == 1


def test_plan_rule_v_findings_returns_step_ids(tmp_path: Path) -> None:
    from scripts.roadmap.plan_document import load

    plan_rel = _write(tmp_path, [_step(1, "pytest tests/test_new.py"), _step(2, "pytest tests/test_new.py::t")])
    assert [step for step, _ in rv.plan_rule_v_findings(load(tmp_path / plan_rel), tmp_path)] == [1]


def test_segments_without_a_marker_are_excluded(tmp_path: Path) -> None:
    assert excluded("ruff --version", tmp_path) == 1
    assert excluded("env ls pytest tests/test_new.py", tmp_path) == 1


def test_trailing_shell_comment_is_not_argv(tmp_path: Path) -> None:
    assert not refused("pytest tests/unrelated.py::t # tests/test_new.py", tmp_path)
    assert refused("pytest tests/test_new.py # note\ntrue", tmp_path)
    assert not refused("pytest tests/unrelated.py --junit-xml tests/test_new.py", tmp_path)
