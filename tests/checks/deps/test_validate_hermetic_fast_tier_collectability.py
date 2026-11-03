"""Mirror test for validate_hermetic_fast_tier_collectability (rec-2809, vp-red-before carve-out iii)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.checks import _common, registry
from scripts.checks.deps import validate_hermetic_fast_tier_collectability as mod
from scripts.checks.verification import _vp_replay_classify

GUARD_SKIP = 'import pytest\n\npytest.importorskip("duckdb")\n\n\ndef test_a() -> None:\n    pass\n'
PLAIN = "def test_a() -> None:\n    pass\n"
PLAN = "docs/plans/PLAN-x.yaml"


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _step(command: str, *, hermetic: object = True, phase: str = "pre-deploy", number: int = 1) -> dict:
    return {"step": number, "phase": phase, "command": command, "hermetic": hermetic}


def _setup(root: Path, files: dict[str, str], steps: object, *, plan: dict | None = None, status: str = "A") -> None:
    _write(root, "requirements.in", "duckdb\nboto3\npytest\npsycopg2-binary\n")
    _write(root, "requirements-fast.txt", "boto3>=1\n")
    _write(root, "requirements-dev.txt", "pytest>=8  # dev\n")
    for rel, text in files.items():
        _write(root, rel, text)
    body = plan if plan is not None else {"verification_plan": steps}
    _write(root, PLAN, yaml.safe_dump(body))
    (root / "status").write_text(status, encoding="utf-8")


def _run(root: Path, monkeypatch: pytest.MonkeyPatch, *, reachable: bool = True) -> tuple[list[str], str]:
    status = (root / "status").read_text(encoding="utf-8")
    monkeypatch.setattr(_common, "origin_main_reachable", lambda _root: reachable)
    monkeypatch.setattr(_common, "get_status_aware_diff", lambda _root=None: [(status, PLAN), ("M", "other.py")])
    failed: list[str] = []
    mod.validate_hermetic_fast_tier_collectability(failed, root=root)
    return failed, ""


GUARDED_CASES = {
    "module_importorskip": ({"tests/t/test_g.py": GUARD_SKIP}, "pytest tests/t/test_g.py -q", "tests/t/test_g.py"),
    "bare_importorskip": (
        {"tests/t/test_g.py": 'import pytest\npytest.importorskip("psycopg2")\n'},
        "bin/venv-python -m pytest tests/t/test_g.py",
        "psycopg2",
    ),
    "assigned_dotted_importorskip": (
        {"tests/t/test_g.py": 'import pytest\nd = pytest.importorskip("duckdb.sub")\n'},
        ".venv/bin/pytest tests/t/test_g.py",
        "duckdb",
    ),
    "module_import": ({"tests/t/test_g.py": "import duckdb\n"}, "pytest tests/t/test_g.py", "duckdb"),
    "module_from_import": ({"tests/t/test_g.py": "from duckdb import x\n"}, "pytest tests/t/test_g.py", "duckdb"),
    "import_under_if": (
        {"tests/t/test_g.py": "import sys\nif sys.platform:\n    import duckdb\n"},
        "pytest tests/t/test_g.py",
        "duckdb",
    ),
    "conftest_guard": (
        {"tests/t/test_g.py": PLAIN, "tests/t/conftest.py": GUARD_SKIP},
        "pytest tests/t/test_g.py",
        "tests/t/conftest.py",
    ),
    "tests_root_conftest_guard": (
        {"tests/t/test_g.py": PLAIN, "tests/conftest.py": GUARD_SKIP},
        "pytest tests/t/test_g.py",
        "tests/conftest.py",
    ),
    "node_selector": (
        {"tests/t/test_g.py": GUARD_SKIP},
        "bin/venv-python -m pytest tests/t/test_g.py::TestC::test_a -q",
        "tests/t/test_g.py",
    ),
    "mixed_selectors": (
        {"tests/t/test_g.py": GUARD_SKIP, "tests/t/test_ok.py": PLAIN},
        "pytest tests/t/test_ok.py tests/t/test_g.py",
        "tests/t/test_g.py",
    ),
    "quoted_true": ({"tests/t/test_g.py": GUARD_SKIP}, "pytest tests/t/test_g.py", "tests/t/test_g.py"),
    "after_chain": (
        {"tests/t/test_g.py": GUARD_SKIP},
        "echo hi && pytest tests/t/test_g.py -q | tail",
        "tests/t/test_g.py",
    ),
}


class TestFlagsGuardedTarget:
    @pytest.mark.parametrize("case", sorted(GUARDED_CASES))
    def test_flags(self, case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        files, command, needle = GUARDED_CASES[case]
        hermetic: object = "true" if case == "quoted_true" else True
        _setup(tmp_path, files, [_step(command, hermetic=hermetic)])
        failed, _ = _run(tmp_path, monkeypatch)
        out = capsys.readouterr().out
        assert failed == [mod._LABEL]
        assert PLAN in out and "step 1" in out and needle in out and "carve-out (iii)" in out
        assert "hermetic: false" in out

    def test_directory_selector_names_member_without_ignore_advice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        files = {"tests/d/test_ok.py": PLAIN, "tests/d/sub/test_bad.py": GUARD_SKIP}
        _setup(tmp_path, files, [_step("pytest tests/d")])
        failed, _ = _run(tmp_path, monkeypatch)
        out = capsys.readouterr().out
        assert failed == [mod._LABEL]
        assert "member tests/d/sub/test_bad.py" in out and "test_ok.py" not in out
        assert "list its unguarded member files" in out and "--ignore" not in out

    def test_uncoercible_hermetic_value_is_a_finding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        _setup(tmp_path, {}, [_step("pytest tests/x.py", hermetic="maybe")])
        failed, _ = _run(tmp_path, monkeypatch)
        assert failed == [mod._LABEL] and "'maybe' is not a bool" in capsys.readouterr().out

    def test_unreadable_plan_and_target_are_findings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        _setup(tmp_path, {"tests/t/test_bad.py": "def (:\n"}, [_step("pytest tests/t/test_bad.py")])
        failed, _ = _run(tmp_path, monkeypatch)
        assert failed == [mod._LABEL] and "unreadable" in capsys.readouterr().out
        _setup(tmp_path, {}, None)
        (tmp_path / PLAN).write_text("a: [unclosed\n", encoding="utf-8")
        failed, _ = _run(tmp_path, monkeypatch)
        assert failed == [mod._LABEL] and "unreadable plan" in capsys.readouterr().out


UNGUARDED_CASES = {
    "function_scope_importorskip": (
        {"tests/t/test_u.py": 'import pytest\n\n\ndef test_a() -> None:\n    pytest.importorskip("duckdb")\n'},
        _step("pytest tests/t/test_u.py"),
    ),
    "guard_on_name_present_in_ci": (
        {"tests/t/test_u.py": 'import pytest\npytest.importorskip("boto3")\nimport boto3\n'},
        _step("pytest tests/t/test_u.py"),
    ),
    "guard_inside_try": (
        {"tests/t/test_u.py": "try:\n    import duckdb\nexcept ImportError:\n    duckdb = None\n"},
        _step("pytest tests/t/test_u.py"),
    ),
    "relative_import": ({"tests/t/test_u.py": "from . import duckdb\nimport os.path\n"}, _step("pytest tests/t/test_u.py")),
    "non_literal_importorskip": (
        {"tests/t/test_u.py": "import pytest\nn = 'duckdb'\npytest.importorskip(n)\nprint(pytest.importorskip())\n"},
        _step("pytest tests/t/test_u.py"),
    ),
    "plain_call_statement": ({"tests/t/test_u.py": "print('x')\nx = 1\n"}, _step("pytest tests/t/test_u.py")),
    "hermetic_false": ({"tests/t/test_u.py": GUARD_SKIP}, _step("pytest tests/t/test_u.py", hermetic=False)),
    "hermetic_false_string": ({"tests/t/test_u.py": GUARD_SKIP}, _step("pytest tests/t/test_u.py", hermetic="no")),
    "hermetic_int_zero": ({"tests/t/test_u.py": GUARD_SKIP}, _step("pytest tests/t/test_u.py", hermetic=0)),
    "post_deploy": ({"tests/t/test_u.py": GUARD_SKIP}, _step("pytest tests/t/test_u.py", phase="post-deploy")),
    "non_pytest_command": ({"tests/t/test_u.py": GUARD_SKIP}, _step("cat tests/t/test_u.py")),
    "chained_non_pytest_segment": (
        {"tests/t/test_u.py": GUARD_SKIP},
        _step("pytest tests/ok.py && cat tests/t/test_u.py"),
    ),
    "missing_command": ({"tests/t/test_u.py": GUARD_SKIP}, {"step": 1, "phase": "pre-deploy", "hermetic": True}),
    "unbalanced_quote": ({"tests/t/test_u.py": GUARD_SKIP}, _step("pytest 'tests/t/test_u.py")),
    "directory_all_unguarded": ({"tests/d/test_a.py": PLAIN, "tests/d/test_b.py": PLAIN}, _step("pytest tests/d")),
    "deselect_value": (
        {"tests/t/test_u.py": GUARD_SKIP, "tests/t/test_ok.py": PLAIN},
        _step("pytest tests/t/test_ok.py --deselect tests/t/test_u.py::t -k tests/t/test_u.py --ignore=tests/t/test_u.py"),
    ),
    "unguarded_with_flag_selector": ({"tests/t/test_ok.py": PLAIN}, _step("pytest -q tests/t/test_ok.py")),
    "non_tests_selector": ({"tests/t/test_u.py": GUARD_SKIP}, _step("pytest src/x.py")),
}


class TestPassesUnguardedTarget:
    @pytest.mark.parametrize("case", sorted(UNGUARDED_CASES))
    def test_passes(self, case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        files, step = UNGUARDED_CASES[case]
        _setup(tmp_path, files, [step])
        failed, _ = _run(tmp_path, monkeypatch)
        assert failed == []

    def test_missing_selector_path_is_skipped_and_counted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        _setup(tmp_path, {}, [_step("pytest tests/t/test_gone.py::t")])
        failed, _ = _run(tmp_path, monkeypatch)
        out = capsys.readouterr().out
        assert failed == [] and "1 selector path(s) absent on disk" in out

    @pytest.mark.parametrize(
        ("plan", "status", "note"),
        [
            ({"implementation_declared": True, "verification_plan": [_step("pytest tests/t/test_g.py")]}, "A", ""),
            ({"verification_plan": [_step("pytest tests/t/test_g.py")]}, "M", ""),
            ({"verification_plan": "nope"}, "A", "verification_plan is not a list"),
            ({"verification_plan": ["nope"]}, "A", "non-mapping verification step"),
        ],
    )
    def test_out_of_population_or_malformed_plan(
        self,
        plan: dict,
        status: str,
        note: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        _setup(tmp_path, {"tests/t/test_g.py": GUARD_SKIP}, None, plan=plan, status=status)
        failed, _ = _run(tmp_path, monkeypatch)
        out = capsys.readouterr().out
        assert failed == [] and note in out

    def test_non_mapping_plan_root_is_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _setup(tmp_path, {}, None)
        (tmp_path / PLAN).write_text("- a\n- b\n", encoding="utf-8")
        failed, _ = _run(tmp_path, monkeypatch)
        assert failed == []

    def test_unreachable_origin_skips(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _setup(tmp_path, {"tests/t/test_g.py": GUARD_SKIP}, [_step("pytest tests/t/test_g.py")])
        failed, _ = _run(tmp_path, monkeypatch, reachable=False)
        assert failed == [] and registry._CURRENT_DECLARATION.kind == "skipped"


class TestAbsentSetDerivation:
    def test_fixture_triple(self, tmp_path: Path) -> None:
        _setup(tmp_path, {}, [])
        assert mod.absent_import_names(tmp_path) == {"duckdb", "psycopg2"}

    def test_live_repo_set(self) -> None:
        live = mod.absent_import_names(_common.ROOT)
        assert "duckdb" in live and "boto3" not in live


class TestAccounting:
    def test_examined_declared_with_step_count(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _setup(tmp_path, {"tests/t/test_ok.py": PLAIN}, [_step("pytest tests/t/test_ok.py"), _step("true", number=2)])
        _run(tmp_path, monkeypatch)
        declaration = registry._CURRENT_DECLARATION
        assert (declaration.kind, declaration.count, declaration.unit) == ("examined", 1, "hermetic_pytest_steps")

    def test_zero_added_plans_declares_empty_domain(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _setup(tmp_path, {}, [], status="M")
        _run(tmp_path, monkeypatch)
        assert registry._CURRENT_DECLARATION.count == 0


class TestSegmentSplitEquivalence:
    @pytest.mark.parametrize("command", ["a && b", "a || b", "a ; b", "a | b", "a && b | c ; d", "plain", "x||y&&z", ""])
    def test_local_regex_matches_classifier_regex(self, command: str) -> None:
        assert mod._SHELL_SEGMENT_SPLIT_RE.split(command) == _vp_replay_classify._SHELL_SEGMENT_SPLIT_RE.split(command)
