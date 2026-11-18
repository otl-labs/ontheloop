"""The execute verb: input validated before anything runs, the timeout recorded, no token in the persona's env."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts.personas import execute

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40


def _work(tmp_path: Path, route: str, body: dict[str, str] | None) -> Path:
    work = tmp_path / "work"
    prepared = work / "prepared"
    prepared.mkdir(parents=True)
    (prepared / "routes.json").write_text(json.dumps({"routes": [route], "plan_paths": []}))
    if body is not None:
        (prepared / f"input-{route}.json").write_text(json.dumps(body))
    return work


def _good(marker: str) -> dict[str, str]:
    return {"change_id": "o/r#1", "head_sha": SHA, "plan": "", "marker": marker}


def test_valid_input_runs_the_probe(tmp_path: Path) -> None:
    route = "verdict_probe.proceed"
    work = _work(tmp_path, route, _good("proceed"))
    (result,) = execute.execute(ROOT, work)
    assert result["exit_code"] == 0 and result["timed_out"] is False
    assert json.loads(result["stdout"]) == {"outcome": "success", "verdict": "PROCEED"}
    assert json.loads((work / "results" / f"{route}.json").read_text()) == result


def test_schema_invalid_input_is_recorded_without_running(tmp_path: Path) -> None:
    route = "verdict_probe.proceed"
    work = _work(tmp_path, route, {"marker": "proceed"})
    (result,) = execute.execute(ROOT, work)
    assert result == {"route": route, "failure": "input_unusable"}


def test_failure_file_is_carried_through(tmp_path: Path) -> None:
    route = "verdict_probe.revise"
    work = _work(tmp_path, route, None)
    (work / "prepared" / f"failure-{route}.json").write_text(json.dumps({"failure": "input_incomplete", "detail": "x"}))
    (result,) = execute.execute(ROOT, work)
    assert result == {"route": route, "failure": "input_incomplete"}


def test_unknown_route_is_input_unusable(tmp_path: Path) -> None:
    work = _work(tmp_path, "no_such_route", {"a": "b"})
    (result,) = execute.execute(ROOT, work)
    assert result["failure"] == "input_unusable"


def test_timeout_is_recorded(tmp_path: Path) -> None:
    route = "verdict_probe.proceed"
    root = tmp_path / "main"
    stub_dir = root / "tests" / "fixtures" / "persona_verdict"
    stub_dir.mkdir(parents=True)
    (stub_dir / "probe_schema.json").write_text((ROOT / "tests/fixtures/persona_verdict/probe_schema.json").read_text())
    (stub_dir / "probe.py").write_text("import time\ntime.sleep(30)\n")
    work = _work(tmp_path, route, _good("proceed"))
    (result,) = execute.execute(root, work, timeout=1)
    assert result["timed_out"] is True and result["exit_code"] is None


def test_subprocess_env_holds_no_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "PERSONA_SNAPSHOT"):
        monkeypatch.setenv(name, "value")
    seen: dict[str, str] = {}
    real = execute.subprocess.run

    def spy(cmd: list[str], **kwargs: object) -> object:
        seen.update(kwargs["env"])  # type: ignore[arg-type]
        assert cmd[:2] == [sys.executable, "-I"]
        return real(cmd, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(execute.subprocess, "run", spy)
    execute.execute(ROOT, _work(tmp_path, "verdict_probe.fail", _good("fail")))
    assert seen and not {"GH_TOKEN", "GITHUB_TOKEN"} & set(seen)
