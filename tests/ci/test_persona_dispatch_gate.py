"""Truth table for scripts/ci/persona_dispatch_gate.py (Decision 205 cl.1)."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci import persona_dispatch_gate as gate

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "ci" / "persona_dispatch_gate.py"
REPO = "org/repo"

WATCHED_WORKFLOW = """name: ci-rca
on:
  workflow_run:
    workflows: ["Upstream CI"]
    types: [completed]
"""
UPSTREAM_WORKFLOW = 'name: "Upstream CI"\non: push\njobs: {}\n'
OTHER_WORKFLOW = "name: Other\non: push\njobs: {}\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/ci-rca.yml").write_text(WATCHED_WORKFLOW, encoding="utf-8")
    (tmp_path / ".github/workflows/up.yml").write_text(UPSTREAM_WORKFLOW, encoding="utf-8")
    (tmp_path / ".github/workflows/other.yml").write_text(OTHER_WORKFLOW, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "out"
    summary = tmp_path / "summary"
    for key in ("GITHUB_EVENT_NAME", "GITHUB_ACTOR", "GITHUB_TRIGGERING_ACTOR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    return tmp_path


def _event(tmp, sender="User", default="main"):
    path = tmp / "event.json"
    path.write_text(json.dumps({"sender": {"type": sender}, "repository": {"default_branch": default}}))
    return str(path)


def _set(monkeypatch, tmp, name="workflow_dispatch", sender="User", actor="alice", triggering=None):
    monkeypatch.setenv("GITHUB_EVENT_NAME", name)
    monkeypatch.setenv("GITHUB_EVENT_PATH", _event(tmp, sender))
    monkeypatch.setenv("GITHUB_ACTOR", actor)
    monkeypatch.setenv("GITHUB_TRIGGERING_ACTOR", triggering or actor)


def _run_file(tmp, **kw):
    run = {
        "repository": {"full_name": REPO},
        "head_branch": "main",
        "path": ".github/workflows/up.yml@refs/heads/main",
        "conclusion": "failure",
        "status": "completed",
    }
    run.update(kw)
    path = tmp / "run.json"
    path.write_text(json.dumps(run))
    return str(path)


def _jobs_file(tmp, jobs):
    path = tmp / "jobs.json"
    path.write_text(json.dumps({"jobs": jobs}))
    return str(path)


def _out(tmp):
    return (tmp / "out").read_text() if (tmp / "out").exists() else ""


def _own(tmp, run, jobs=None):
    argv = ["--own-failure", "--run-json", run, "--watched-from", ".github/workflows/ci-rca.yml"]
    if jobs:
        argv += ["--jobs-json", jobs]
    return argv


@pytest.mark.parametrize("name", ["push", "pull_request", "workflow_run"])
def test_non_dispatch_events_pass(env, monkeypatch, name, capsys):
    _set(monkeypatch, env, name=name, sender="Bot", actor="x[bot]")
    assert gate.main([]) == 0
    assert "refused=false" in _out(env)


def test_user_dispatch_admitted(env, monkeypatch, capsys):
    _set(monkeypatch, env)
    assert gate.main([]) == 0
    assert "PERSONA_DISPATCH_ADMITTED: user alice" in capsys.readouterr().out
    assert "refused=false" in _out(env)


def test_bot_sender_refused(env, monkeypatch, capsys):
    _set(monkeypatch, env, sender="Bot", actor="app[bot]")
    assert gate.main([]) == 1
    assert "PERSONA_DISPATCH_REFUSED:" in capsys.readouterr().err
    assert "refused=true" in _out(env)
    assert "PERSONA_DISPATCH_REFUSED" in (env / "summary").read_text()


def test_user_sender_with_bot_rerunner_refused(env, monkeypatch):
    _set(monkeypatch, env, actor="alice", triggering="renovate[bot]")
    assert gate.main([]) == 1
    assert "refused=true" in _out(env)


def test_missing_event_file_refused(env, monkeypatch):
    _set(monkeypatch, env)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(env / "nope.json"))
    assert gate.main([]) == 1
    assert "refused=true" in _out(env)


def _bot(monkeypatch, env):
    _set(monkeypatch, env, sender="Bot", actor="github-actions[bot]")


def test_own_failure_completed_failed_run_admitted(env, monkeypatch, capsys):
    _bot(monkeypatch, env)
    assert gate.main(_own(env, _run_file(env))) == 0
    assert "PERSONA_DISPATCH_ADMITTED: own-failure github-actions[bot]" in capsys.readouterr().out
    assert "refused=false" in _out(env)


def test_own_failure_in_progress_with_failed_job_admitted(env, monkeypatch):
    _bot(monkeypatch, env)
    run = _run_file(env, conclusion=None, status="in_progress")
    jobs = _jobs_file(env, [{"conclusion": "failure", "steps": []}])
    assert gate.main(_own(env, run, jobs)) == 0


def test_own_failure_in_progress_job_with_failed_step_admitted(env, monkeypatch):
    _bot(monkeypatch, env)
    run = _run_file(env, conclusion=None, status="in_progress")
    jobs = _jobs_file(env, [{"conclusion": None, "steps": [{"conclusion": "success"}, {"conclusion": "failure"}]}])
    assert gate.main(_own(env, run, jobs)) == 0


@pytest.mark.parametrize(
    "run_kw",
    [
        {"repository": {"full_name": "other/repo"}},
        {"head_branch": "feature"},
        {"path": ".github/workflows/other.yml@refs/heads/main"},
        {"conclusion": "success"},
        {"path": ".github/workflows/missing.yml@refs/heads/main"},
        {"path": "not/a/workflow.yml"},
    ],
)
def test_own_failure_non_qualifying_run_refused(env, monkeypatch, run_kw):
    _bot(monkeypatch, env)
    assert gate.main(_own(env, _run_file(env, **run_kw))) == 1
    assert "refused=true" in _out(env)


def test_own_failure_in_progress_without_failure_refused(env, monkeypatch):
    _bot(monkeypatch, env)
    run = _run_file(env, conclusion=None, status="in_progress")
    jobs = _jobs_file(env, [{"conclusion": None, "steps": [{"conclusion": "success"}]}])
    assert gate.main(_own(env, run, jobs)) == 1


def test_own_failure_unreadable_run_refused(env, monkeypatch):
    _bot(monkeypatch, env)
    assert gate.main(_own(env, str(env / "missing.json"))) == 1


def test_own_failure_in_progress_missing_jobs_refused(env, monkeypatch):
    _bot(monkeypatch, env)
    run = _run_file(env, conclusion=None, status="in_progress")
    assert gate.main(_own(env, run)) == 1


def test_own_failure_wrong_actor_refused(env, monkeypatch):
    _set(monkeypatch, env, sender="Bot", actor="other-app[bot]")
    assert gate.main(_own(env, _run_file(env))) == 1


def test_bot_refused_without_own_failure_flag(env, monkeypatch):
    _bot(monkeypatch, env)
    assert gate.main([]) == 1


def test_injected_exception_exits_2_with_error_marker(env, monkeypatch, capsys):
    _set(monkeypatch, env)

    def boom(_args):
        raise RuntimeError("x")

    monkeypatch.setattr(gate, "decide", boom)
    assert gate.main([]) == 2
    err = capsys.readouterr().err
    assert "PERSONA_DISPATCH_GATE_ERROR: RuntimeError" in err
    assert "PERSONA_DISPATCH_REFUSED" not in err
    assert _out(env) == ""


def test_module_scope_imports_are_stdlib_only():
    tree = ast.parse(GATE.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert names <= set(sys.stdlib_module_names)


def test_push_and_user_dispatch_run_under_python_without_yaml(tmp_path):
    block = tmp_path / "block"
    block.mkdir()
    (block / "yaml.py").write_text("raise ImportError('blocked')\n", encoding="utf-8")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"sender": {"type": "User"}}))
    for name in ("push", "workflow_dispatch"):
        out = tmp_path / f"out-{name}"
        env = {
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(block),
            "GITHUB_EVENT_NAME": name,
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_ACTOR": "alice",
            "GITHUB_TRIGGERING_ACTOR": "alice",
            "GITHUB_OUTPUT": str(out),
        }
        res = subprocess.run([sys.executable, "-I", str(GATE)], env=env, capture_output=True, text=True, check=False)
        assert res.returncode == 0, res.stderr
        assert "refused=false" in out.read_text()
