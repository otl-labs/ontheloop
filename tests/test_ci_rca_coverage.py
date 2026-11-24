"""Tests for scripts/ci_rca/coverage.py (rec-5537): derived CI RCA coverage table."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from scripts import dependency_graph
from scripts.ci_rca import coverage


def _w(root: Path, rel: str, text: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _tree(tmp_path: Path, steps: list[dict], rows: dict | None = None, known: list[str] | None = None) -> Path:
    dependency_graph.clear_graph_cache()
    _w(tmp_path, coverage.WORKFLOW_REL, yaml.safe_dump({"jobs": {"j": {"steps": steps}}}))
    table = {
        "proven_by_values": {"main_smoke": "m", "watchdog_only": "w"},
        "known_top_level": known or [],
        "steps": rows or {},
    }
    _w(tmp_path, coverage.TABLE_REL, yaml.safe_dump(table))
    _w(tmp_path, "scripts/__init__.py")
    return tmp_path


def _errs(cov: coverage.Coverage) -> list[str]:
    return cov.errors + [e for scan in cov.steps.values() for e in scan.errors]


def _load(root: Path) -> coverage.Coverage:
    return coverage.load(root=root)


def test_step_keys(tmp_path: Path) -> None:
    root = _tree(tmp_path, [{"id": "a", "run": "true"}, {"name": "Do B!", "run": "true"}, {"run": "true"}])
    assert sorted(_load(root).steps) == ["j::#2", "j::a", "j::do-b"]


def test_duplicate_key_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, [{"id": "a", "run": "true"}, {"id": "a", "run": "true"}])
    assert any("duplicate step key j::a" in p for p in _load(root).check())


def test_token_shapes(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        [
            {
                "id": "a",
                "if": "hashFiles('config/h.txt') != ''",
                "env": {"E": "config/e.txt"},
                "with": {"w": "config/w.txt"},
                "run": "cat config/r.txt # config/commented.txt\ncat /tmp/scripts/ignored.txt ${{ steps.x.outputs.y }}\n"
                ".venv/bin/pip install z\ncat ${GITHUB_WORKSPACE}/config/ws.txt $1/config/pos.txt\nls .mcp.json\n",
            }
        ],
        known=[".mcp.json"],
    )
    for rel in ("h", "e", "w", "r", "ws", "pos"):
        _w(root, f"config/{rel}.txt")
    _w(root, ".mcp.json", json.dumps({"s": {"command": "bin/venv-python", "args": ["-m", "scripts.srv"]}}))
    _w(root, "bin/venv-python")
    _w(root, "scripts/srv.py", "import scripts.dep\n")
    _w(root, "scripts/dep.py")
    cov = _load(root)
    assert _errs(cov) == []
    assert cov.direct("j::a") == {
        "config/h.txt",
        "config/e.txt",
        "config/w.txt",
        "config/r.txt",
        "config/ws.txt",
        "config/pos.txt",
        ".mcp.json",
        "bin/venv-python",
        "scripts/srv.py",
    }
    assert "scripts/dep.py" in cov.closure("j::a")
    assert not any(re.search(r"-m\s+src\.", t) for t in cov.steps["j::a"].texts)


def test_unresolved_path_and_module_fail_closed(tmp_path: Path) -> None:
    root = _tree(tmp_path, [{"id": "a", "run": "cat config/nope.txt\npython -m scripts.nope\n"}])
    problems = _load(root).check()
    assert any("config/nope.txt resolves to no file" in p for p in problems)
    assert any("module scripts.nope resolves to no file" in p for p in problems)


def test_directory_token(tmp_path: Path) -> None:
    root = _tree(tmp_path, [{"id": "a", "run": "ls config/sub/"}])
    _w(root, "config/sub/x.txt")
    assert _load(root).direct("j::a") == {"config/sub/"}


def test_local_action_recurses(tmp_path: Path) -> None:
    root = _tree(tmp_path, [{"id": "a", "uses": "./.github/actions/act"}, {"id": "b", "uses": "actions/checkout@abc"}])
    _w(
        root,
        ".github/actions/act/action.yml",
        yaml.safe_dump(
            {"runs": {"steps": [{"run": 'bash "${{ github.action_path }}/go.sh"'}, {"uses": "./.github/actions/act"}]}}
        ),
    )
    _w(root, ".github/actions/act/go.sh", 'cat "$(dirname "$0")/../../../config/z.txt"\npython -m scripts.pkg\n')
    _w(root, "config/z.txt")
    _w(root, "scripts/pkg/__init__.py")
    _w(root, "scripts/pkg/__main__.py", "import scripts.pkg.only_main\n")
    _w(root, "scripts/pkg/only_main.py")
    cov = _load(root)
    assert _errs(cov) == []
    assert {".github/actions/act/action.yml", ".github/actions/act/go.sh", "config/z.txt"} <= cov.direct("j::a")
    assert "scripts/pkg/__main__.py" in cov.direct("j::a")
    assert "scripts/pkg/only_main.py" in cov.closure("j::a")
    assert cov.steps["j::b"].externals == {"actions/checkout@abc"}


def test_missing_action_and_package_main(tmp_path: Path) -> None:
    root = _tree(tmp_path, [{"id": "a", "uses": "./.github/actions/none"}, {"id": "b", "run": "python -m scripts.nomain"}])
    _w(root, "scripts/nomain/__init__.py")
    problems = _load(root).check()
    assert any("has no action.yml" in p for p in problems)
    assert any("has no __main__.py" in p for p in problems)


def test_ancestor_packages(tmp_path: Path) -> None:
    root = _tree(tmp_path, [{"id": "a", "run": "python -m scripts.pkg.sub.mod"}])
    _w(root, "scripts/pkg/__init__.py", "import scripts.pkg.other\n")
    _w(root, "scripts/pkg/other.py")
    _w(root, "scripts/pkg/sub/__init__.py")
    _w(root, "scripts/pkg/sub/mod.py")
    closure = _load(root).closure("j::a")
    assert {"scripts/__init__.py", "scripts/pkg/__init__.py", "scripts/pkg/sub/__init__.py", "scripts/pkg/other.py"} <= closure


def test_namespace_ancestor_skipped_and_path_script_adds_none(tmp_path: Path) -> None:
    root = _tree(tmp_path, [{"id": "a", "run": "python scripts/ns/run.py"}])
    _w(root, "scripts/ns/run.py", "import scripts.dep\n")
    _w(root, "scripts/dep.py")
    cov = _load(root)
    assert _errs(cov) == []
    assert cov.closure("j::a") == {"scripts/dep.py", "scripts/__init__.py"}
    assert "scripts/ns/__init__.py" not in cov.closure("j::a")


def test_declared_py_seeds_closure_and_declared_joins(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        [{"id": "a", "run": "true"}],
        rows={"j::a": {"proven_by": "watchdog_only", "declared_inputs": ["scripts/d.py", "config/c.txt"]}},
    )
    _w(root, "scripts/d.py", "import scripts.e\n")
    _w(root, "scripts/e.py")
    _w(root, "config/c.txt")
    cov = _load(root)
    assert "scripts/e.py" in cov.closure("j::a")
    assert {"scripts/d.py", "config/c.txt", "scripts/e.py"} <= cov.inputs("j::a")


def test_requirements_top_level_and_check_builds_no_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _tree(tmp_path, [{"id": "a", "run": "pip install -r requirements.txt"}], known=["requirements.txt"])
    _w(root, "requirements.txt")
    monkeypatch.setattr(dependency_graph, "build_graph", lambda *a, **k: pytest.fail("graph built"))
    cov = _load(root)
    assert cov.direct("j::a") == {"requirements.txt"}
    assert any("no row" in p for p in cov.check())


def test_paths_proven_by_unions_only_matching_rows(tmp_path: Path) -> None:
    rows = {
        "j::a": {"proven_by": "main_smoke", "declared_inputs": []},
        "j::b": {"proven_by": "watchdog_only", "declared_inputs": []},
    }
    root = _tree(tmp_path, [{"id": "a", "run": "cat config/a.txt"}, {"id": "b", "run": "cat config/b.txt"}], rows=rows)
    _w(root, "config/a.txt")
    _w(root, "config/b.txt")
    cov = _load(root)
    assert cov.paths_proven_by("main_smoke") == ["config/a.txt"]
    assert cov.proven_by("j::zzz") is None


def test_overlaps_flags_derivable_declared_input(tmp_path: Path) -> None:
    rows = {"j::a": {"proven_by": "watchdog_only", "declared_inputs": ["config/a.txt"]}}
    root = _tree(tmp_path, [{"id": "a", "run": "cat config/a.txt"}], rows=rows)
    _w(root, "config/a.txt")
    assert coverage.overlaps(_load(root)) == ["row j::a: declared input config/a.txt is already derivable"]


def test_missing_table_loads_empty(tmp_path: Path) -> None:
    root = _tree(tmp_path, [{"id": "a", "run": "true"}])
    (root / coverage.TABLE_REL).unlink()
    assert any("no row" in p for p in _load(root).check())


def test_real_tree_checks_clean() -> None:
    cov = coverage.load()
    assert cov.check() == []
    assert coverage.overlaps(cov) == []
    assert not any(re.search(r"-m\s+src\.", t) for scan in cov.steps.values() for t in scan.texts)
    assert {"scripts/ci/claude_p_retry.sh", ".mcp.json", "scripts/agent_sdk/mcp_server.py", "scripts/ci_rca/filing.py"} <= (
        cov.inputs("rca::rca_agent")
    )


def test_cli(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert coverage.main(["check"]) == 0
    assert coverage.main(["derive", "--step", "rca::rca_agent"]) == 0
    assert "claude_p_retry.sh" in capsys.readouterr().out
    assert coverage.main(["derive"]) == 0
    assert coverage.main(["derive", "--step", "nope"]) == 1
    assert coverage.main(["paths", "--proven-by", "watchdog_only"]) == 0
    assert "scripts/ci_rca/watchdog.py" in capsys.readouterr().out


def test_cli_check_reports_problems(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = _tree(tmp_path, [{"id": "a", "run": "true"}])
    real_load = coverage.load
    monkeypatch.setattr(coverage, "load", lambda *a, **k: real_load(root=root))
    assert coverage.main(["check"]) == 1
    assert "no row" in capsys.readouterr().out


def test_check_row_problems(tmp_path: Path) -> None:
    rows = {
        "j::a": {"proven_by": "nope", "declared_inputs": ["config/missing.txt"]},
        "j::stale": {"proven_by": "watchdog_only", "declared_inputs": []},
    }
    root = _tree(tmp_path, [{"id": "a", "run": "true"}], rows=rows)
    problems = _load(root).check()
    assert "row j::stale: no such step in .github/workflows/ci-rca.yml" in problems
    assert "row j::a: proven_by 'nope' is not in proven_by_values" in problems
    assert "row j::a: declared input config/missing.txt does not exist" in problems


SMOKE_REL = ".github/workflows/ci-rca-smoke.yml"
VERIFY = "bin/venv-python -m scripts.ci_rca.smoke verify --smoke-id X --bundles-dir /tmp/b"


def _smoke_tree(tmp_path: Path, mutate=None, rows: dict | None = None) -> Path:
    dependency_graph.clear_graph_cache()
    _w(tmp_path, "scripts/__init__.py")
    _w(tmp_path, "scripts/ci_rca/__init__.py")
    _w(tmp_path, "scripts/ci_rca/smoke.py")
    _w(tmp_path, "scripts/x.sh")
    _w(tmp_path, "bin/venv-python")
    _w(tmp_path, "config/decl.yaml")
    ci_wf = {"jobs": {"j": {"steps": [{"id": "a", "uses": "actions/x@aaa", "run": "bash scripts/x.sh"}]}}}
    smoke_wf = {
        "jobs": {
            "select": {"steps": [{"id": "s", "run": "true"}]},
            "smoke": {
                "needs": "select",
                "if": "needs.select.outputs.run == 'true'",
                "steps": [
                    {"id": "a", "uses": "actions/x@aaa", "run": "bash scripts/x.sh"},
                    {"id": "v", "run": VERIFY},
                ],
            },
        }
    }
    table = {
        "proven_by_values": {"main_smoke": "m", "watchdog_only": "w"},
        "known_top_level": [],
        "smoke_workflow": SMOKE_REL,
        "smoke_verify_step": "smoke::v",
        "smoke_verify_run": VERIFY,
        "steps": rows
        or {"j::a": {"proven_by": "main_smoke", "smoke_step": "smoke::a", "declared_inputs": ["config/decl.yaml"]}},
    }
    if mutate is not None:
        mutate(ci_wf, smoke_wf, table)
    _w(tmp_path, coverage.WORKFLOW_REL, yaml.safe_dump(ci_wf))
    if table.get("smoke_workflow") == SMOKE_REL:
        _w(tmp_path, SMOKE_REL, yaml.safe_dump(smoke_wf))
    _w(tmp_path, coverage.TABLE_REL, yaml.safe_dump(table))
    return tmp_path


def _smoke_steps(smoke_wf: dict) -> list[dict]:
    return smoke_wf["jobs"]["smoke"]["steps"]


def _problems(root: Path) -> list[str]:
    return _load(root).check()


def _joined(root: Path) -> str:
    return "\n".join(_problems(root))


def test_smoke_clean_fixture_checks_clean(tmp_path: Path) -> None:
    assert _problems(_smoke_tree(tmp_path)) == []


def test_smoke_step_missing_or_unknown(tmp_path: Path) -> None:
    row = {"proven_by": "main_smoke", "declared_inputs": []}
    assert "smoke_step None is not a step" in _joined(_smoke_tree(tmp_path / "a", rows={"j::a": row}))
    row2 = {**row, "smoke_step": "smoke::nope"}
    assert "smoke_step 'smoke::nope' is not a step" in _joined(_smoke_tree(tmp_path / "b", rows={"j::a": row2}))


def test_smoke_step_missing_a_direct_input_or_with_a_different_pin(tmp_path: Path) -> None:
    def drop_input(_c, s, _t):
        _smoke_steps(s)[0]["run"] = "true"

    assert "lacks direct inputs ['scripts/x.sh']" in _joined(_smoke_tree(tmp_path / "a", drop_input))

    def new_pin(_c, s, _t):
        _smoke_steps(s)[0]["uses"] = "actions/x@bbb"

    assert "uses a different action pin" in _joined(_smoke_tree(tmp_path / "b", new_pin))


@pytest.mark.parametrize("value", [True, "${{ steps.x.outcome == 'success' }}", "false"])
def test_smoke_step_continue_on_error_is_masking(tmp_path: Path, value: object) -> None:
    def mask(_c, s, _t):
        _smoke_steps(s)[0]["continue-on-error"] = value

    assert "carries continue-on-error" in _joined(_smoke_tree(tmp_path, mask))


def test_smoke_step_continue_on_error_literal_false_is_fine(tmp_path: Path) -> None:
    def literal_false(_c, s, _t):
        _smoke_steps(s)[0]["continue-on-error"] = False

    assert _problems(_smoke_tree(tmp_path, literal_false)) == []


@pytest.mark.parametrize("value", ["always()", "github.event_name == 'push'", "${{ failure() }}", True])
def test_smoke_step_skipping_if_is_masking(tmp_path: Path, value: object) -> None:
    def skip(_c, s, _t):
        _smoke_steps(s)[0]["if"] = value

    assert "has a skipping if:" in _joined(_smoke_tree(tmp_path, skip))


@pytest.mark.parametrize("value", ["success()", "!cancelled()", "${{ !cancelled() }}", "${{ success() }}"])
def test_smoke_step_allowed_ifs(tmp_path: Path, value: str) -> None:
    def allowed(_c, s, _t):
        _smoke_steps(s)[0]["if"] = value

    assert _problems(_smoke_tree(tmp_path, allowed)) == []


def test_smoke_job_level_masking(tmp_path: Path) -> None:
    def job_coe(_c, s, _t):
        s["jobs"]["smoke"]["continue-on-error"] = True

    assert "smoke job smoke carries continue-on-error" in _joined(_smoke_tree(tmp_path / "a", job_coe))

    def job_if(_c, s, _t):
        s["jobs"]["smoke"]["if"] = "always()"

    assert "has an if: other than" in _joined(_smoke_tree(tmp_path / "b", job_if))

    def job_if_wrapped(_c, s, _t):
        s["jobs"]["smoke"]["if"] = "${{ needs.select.outputs.run == 'true' }}"

    assert _problems(_smoke_tree(tmp_path / "c", job_if_wrapped)) == []

    def needed_coe(_c, s, _t):
        s["jobs"]["select"]["continue-on-error"] = "${{ true }}"

    assert "needs select, which carries continue-on-error" in _joined(_smoke_tree(tmp_path / "d", needed_coe))

    def needs_list(_c, s, _t):
        s["jobs"]["smoke"]["needs"] = ["select"]

    assert _problems(_smoke_tree(tmp_path / "e", needs_list)) == []


def test_smoke_step_on_a_non_main_smoke_row_fails(tmp_path: Path) -> None:
    rows = {"j::a": {"proven_by": "watchdog_only", "smoke_step": "smoke::a", "declared_inputs": []}}
    assert "smoke_step on a row that is not main_smoke" in _joined(_smoke_tree(tmp_path, rows=rows))


def test_main_smoke_row_without_a_smoke_workflow_is_reported(tmp_path: Path) -> None:
    def drop(_c, _s, t):
        del t["smoke_workflow"]

    assert "smoke_workflow None is missing" in _joined(_smoke_tree(tmp_path / "a", drop))

    def missing_file(_c, _s, t):
        t["smoke_workflow"] = ".github/workflows/nope.yml"

    assert "is missing or names no file" in _joined(_smoke_tree(tmp_path / "b", missing_file))


def test_smoke_steps_spread_over_two_jobs_are_reported(tmp_path: Path) -> None:
    def spread(c, s, t):
        c["jobs"]["j"]["steps"].append({"id": "b", "run": "bash scripts/x.sh"})
        s["jobs"]["select"]["steps"].append({"id": "b", "run": "bash scripts/x.sh"})
        t["steps"]["j::b"] = {"proven_by": "main_smoke", "smoke_step": "select::b", "declared_inputs": []}

    assert "spread over more than one job" in _joined(_smoke_tree(tmp_path, spread))


def test_smoke_verify_step_missing_unknown_or_in_another_job(tmp_path: Path) -> None:
    def drop(_c, _s, t):
        del t["smoke_verify_step"]

    assert "smoke_verify_step None is not a step" in _joined(_smoke_tree(tmp_path / "a", drop))

    def unknown(_c, _s, t):
        t["smoke_verify_step"] = "smoke::nope"

    assert "smoke_verify_step 'smoke::nope' is not a step" in _joined(_smoke_tree(tmp_path / "b", unknown))

    def elsewhere(_c, s, t):
        s["jobs"]["select"]["steps"].append({"id": "v", "run": VERIFY})
        t["smoke_verify_step"] = "select::v"

    assert "is not in the smoke job" in _joined(_smoke_tree(tmp_path / "c", elsewhere))


def test_smoke_verify_step_masked_or_shelled(tmp_path: Path) -> None:
    def masked(_c, s, _t):
        _smoke_steps(s)[1]["continue-on-error"] = True

    assert "smoke_verify_step: smoke step smoke::v carries continue-on-error" in _joined(_smoke_tree(tmp_path / "a", masked))

    def step_shell(_c, s, _t):
        _smoke_steps(s)[1]["shell"] = "bash {0} || true"

    def job_shell(_c, s, _t):
        s["jobs"]["smoke"]["defaults"] = {"run": {"shell": "bash {0} || true"}}

    def wf_shell(_c, s, _t):
        s["defaults"] = {"run": {"shell": "bash {0} || true"}}

    for index, mutate in enumerate((step_shell, job_shell, wf_shell)):
        assert "has or inherits a shell:" in _joined(_smoke_tree(tmp_path / f"s{index}", mutate))

    def other_defaults(_c, s, _t):
        s["defaults"] = {"run": {"working-directory": "."}}

    assert _problems(_smoke_tree(tmp_path / "ok", other_defaults)) == []


@pytest.mark.parametrize(
    "run",
    [
        VERIFY + " || true",
        VERIFY + " &",
        VERIFY + "\ntrue",
        "bin/venv-python -m scripts.ci_rca.smoke select --before A",
        VERIFY.replace("--smoke-id X", "--smoke-id Y"),
    ],
)
def test_smoke_verify_run_must_equal_the_table_string(tmp_path: Path, run: str) -> None:
    def swap(_c, s, _t):
        _smoke_steps(s)[1]["run"] = run

    assert "run text differs from smoke_verify_run" in _joined(_smoke_tree(tmp_path, swap))


def test_smoke_verify_run_surrounding_whitespace_is_ignored(tmp_path: Path) -> None:
    def pad(_c, s, _t):
        _smoke_steps(s)[1]["run"] = "  " + VERIFY + "\n"

    assert _problems(_smoke_tree(tmp_path, pad)) == []


@pytest.mark.parametrize("suffix", [" || true", " & ", "; true", " `x`", " $(x)", " > /tmp/o", "\nmore"])
def test_smoke_verify_run_reference_cannot_carry_an_escape(tmp_path: Path, suffix: str) -> None:
    def bad_ref(_c, s, t):
        t["smoke_verify_run"] = VERIFY + suffix
        _smoke_steps(s)[1]["run"] = VERIFY + suffix

    assert "smoke_verify_run must be one plain line" in _joined(_smoke_tree(tmp_path, bad_ref))


def test_smoke_verify_run_must_start_with_the_verify_command(tmp_path: Path) -> None:
    def other(_c, s, t):
        t["smoke_verify_run"] = "true"
        _smoke_steps(s)[1]["run"] = "true"

    assert "smoke_verify_run must be one plain line" in _joined(_smoke_tree(tmp_path, other))


def test_workflow_inputs_include_the_workflow_file_and_mapped_declared_inputs(tmp_path: Path) -> None:
    cov = _load(_smoke_tree(tmp_path))
    inputs = cov.workflow_inputs(SMOKE_REL)
    assert SMOKE_REL in inputs
    assert "scripts/x.sh" in inputs
    assert "config/decl.yaml" in inputs
    assert "scripts/ci_rca/smoke.py" in inputs
    assert cov.workflow_inputs(".github/workflows/other.yml") == set()


def test_real_tree_flipped_rows_name_real_smoke_steps() -> None:
    cov = coverage.load()
    rows = cov._smoke_rows()
    assert rows
    assert all(row["smoke_step"] in cov.smoke for _, row in rows)
