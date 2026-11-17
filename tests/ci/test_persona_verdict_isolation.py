"""rec-5041 part 1: the head-tree snapshot never reaches the import path of the verdict workflow's materialise step.

Lays out the runner's directories in tmp_path and runs the workflow's own materialise argv, working directory and
PERSONA_SNAPSHOT (parsed from persona-verdict.yml) under a hostile environment. The negative control drops -I.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "persona-verdict.yml"
PLANTED = (
    "json.py",
    "yaml.py",
    "sitecustomize.py",
    "conftest.py",
    "scripts/__init__.py",
    "scripts/personas/__init__.py",
    "planted.pth",
)
ROUTE = "verdict_probe.proceed"
SHA = "a" * 40


def _materialise_step() -> dict[str, object]:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    (step,) = [s for s in jobs["prepare"]["steps"] if str(s.get("run", "")).endswith(" materialise")]
    return step


def _layout(tmp_path: Path) -> dict[str, Path]:
    workspace = tmp_path / "workspace"
    main = workspace / "main"
    for rel in ("scripts/personas", "scripts/session", "scripts/ci"):
        (main / rel).mkdir(parents=True)
    (main / "scripts/__init__.py").touch()
    for src in (ROOT / "scripts/personas").glob("*.py"):
        shutil.copy(src, main / "scripts/personas" / src.name)
    for rel in ("scripts/session/__init__.py", "scripts/session/merge_checks.py", "scripts/ci/push_scope.py"):
        shutil.copy(ROOT / rel, main / rel)
    temp = tmp_path / "runner_temp"
    prepared = temp / "work" / "prepared"
    prepared.mkdir(parents=True)
    (prepared / "resolution.json").write_text(json.dumps({"change_id": "o/r#1", "head_sha": SHA}))
    (prepared / "routes.json").write_text(json.dumps({"routes": [ROUTE], "plan_paths": []}))
    (prepared / "snapshot.json").write_text(json.dumps({"ok": True}))
    snap = temp / "snapshot"
    sentinels = tmp_path / "sentinels"
    sentinels.mkdir()
    marker = snap / "tests/fixtures/persona_verdict/fixture_pr"
    marker.mkdir(parents=True)
    (marker / "proceed.txt").write_text("proceed")
    for rel in PLANTED:
        target = snap / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        stem = rel.replace("/", "_")
        body = f"open({str(sentinels / stem)!r}, 'w').close()\n"
        target.write_text(body if not rel.endswith(".pth") else f"import sys; open({str(sentinels / stem)!r}, 'w').close()\n")
    return {"workspace": workspace, "temp": temp, "snap": snap, "sentinels": sentinels, "prepared": prepared}


def _sub(text: str, dirs: dict[str, Path]) -> str:
    return (
        text.replace("${{ runner.temp }}", str(dirs["temp"]))
        .replace("${{ github.workspace }}", str(dirs["workspace"]))
        .replace("$GITHUB_WORKSPACE", str(dirs["workspace"]))
    )


def _run(
    tmp_path: Path, drop_isolation: bool = False, verbose: bool = False
) -> tuple[subprocess.CompletedProcess[str], dict[str, Path]]:
    dirs = _layout(tmp_path)
    step = _materialise_step()
    argv = shlex.split(_sub(str(step["run"]), dirs))
    assert argv[0] == "python3" and argv[1] == "-I"
    argv[0] = sys.executable
    if drop_isolation:
        argv.remove("-I")
    if verbose:
        argv.insert(1, "-v")
    env = {k: _sub(str(v), dirs) for k, v in dict(step["env"]).items()}  # type: ignore[call-overload]
    assert env["PERSONA_SNAPSHOT"] == str(dirs["snap"])
    env.update({"PYTHONPATH": str(dirs["snap"]), "PATH": "/usr/bin:/bin"})
    cwd = _sub(str(step["working-directory"]), dirs)
    assert cwd == str(dirs["temp"] / "work")
    done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    return done, dirs


def test_planted_snapshot_modules_are_never_processed(tmp_path: Path) -> None:
    done, dirs = _run(tmp_path)
    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in dirs["sentinels"].iterdir()) == []
    assert (dirs["prepared"] / f"input-{ROUTE}.json").exists()


def test_no_loaded_module_comes_from_the_snapshot(tmp_path: Path) -> None:
    done, dirs = _run(tmp_path, verbose=True)
    assert done.returncode == 0
    assert str(dirs["snap"]) not in done.stderr


def test_negative_control_fires_without_isolation(tmp_path: Path) -> None:
    _, dirs = _run(tmp_path, drop_isolation=True)
    assert "sitecustomize.py" in {p.name for p in dirs["sentinels"].iterdir()}


@pytest.mark.parametrize("field", ["run", "working-directory"])
def test_step_shape_is_the_one_the_test_parses(field: str) -> None:
    assert field in _materialise_step()
