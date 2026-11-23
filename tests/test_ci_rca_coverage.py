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
