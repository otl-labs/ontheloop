"""Mirror test for the local_container adapter, without a daemon."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from src.persona_hosts.local_container import adapter
from src.persona_hosts.local_container.adapter import run_argv, run_image

REF = "ghcr.io/example/persona@sha256:" + "c" * 64
KEY = "DEEPSEEK_API_KEY"


def test_exact_argv() -> None:
    argv = run_argv(REF, "plan_critic", "/in", "/ev", key_variable=KEY, base_url_variable="DEEPSEEK_API_BASE")
    assert argv == [
        "docker", "run", "--rm", "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--user", "10001:10001", "--network", "bridge",
        "-v", "/in:/run/persona/in:ro", "-v", "/ev:/run/persona/events",
        "-e", KEY, "-e", "DEEPSEEK_API_BASE",
        REF, "--contract", "/app/docs/contracts/personas/plan_critic.yaml",
        "--input", "/run/persona/in/input.json", "--events", "/run/persona/events/events.json",
    ]  # fmt: skip


def test_entrypoint_split() -> None:
    argv = run_argv(REF, "plan_critic", "/in", "/ev", key_variable=None, entrypoint=["python", "-c", "print(1)"])
    assert argv[-5:] == ["--entrypoint", "python", REF, "-c", "print(1)"]
    assert "--contract" not in argv


def test_local_image_id_accepted() -> None:
    assert run_argv("sha256:" + "d" * 64, "p", "/i", "/e", key_variable=None)


@pytest.mark.parametrize("ref", ["ghcr.io/example/persona:latest", "persona", "", "sha256:abc"])
def test_tag_refused(ref: str) -> None:
    with pytest.raises(ValueError):
        run_argv(ref, "plan_critic", "/i", "/e", key_variable=None)


@pytest.mark.parametrize("persona", ["../x", "Plan", "a b", "", "1abc"])
def test_bad_persona_refused(persona: str) -> None:
    with pytest.raises(ValueError):
        run_argv(REF, persona, "/i", "/e", key_variable=None)


def test_unlisted_variables_refused() -> None:
    with pytest.raises(ValueError):
        run_argv(REF, "p", "/i", "/e", key_variable="AWS_SECRET_ACCESS_KEY")
    with pytest.raises(ValueError):
        run_argv(REF, "p", "/i", "/e", key_variable=KEY, base_url_variable="HTTP_PROXY")


def test_argv_has_no_forbidden_element() -> None:
    text = " ".join(run_argv(REF, "p", "/i", "/e", key_variable=KEY, base_url_variable="DEEPSEEK_API_BASE"))
    for banned in ("docker.sock", "--privileged", "--pid", "--env-file", ".git", "/home"):
        assert banned not in text


def test_run_image_modes_and_json(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        in_dir = argv[argv.index("-v") + 1].split(":")[0]
        events_dir = argv[argv.index("-v", argv.index("-v") + 1) + 1].split(":")[0]
        seen["in_mode"] = stat.S_IMODE(os.stat(in_dir).st_mode)
        seen["file_mode"] = stat.S_IMODE(os.stat(Path(in_dir) / "input.json").st_mode)
        seen["ev_mode"] = stat.S_IMODE(os.stat(events_dir).st_mode)
        seen["input"] = json.loads((Path(in_dir) / "input.json").read_text())
        (Path(events_dir) / "events.json").write_text('{"header": {}}')
        return subprocess.CompletedProcess(argv, 0, stdout='{"outcome": "success"}\n', stderr="")

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    out = run_image(REF, "plan_critic", {"a": 1}, key_variable=KEY)
    assert (out.exit_code, out.result, out.events) == (0, {"outcome": "success"}, {"header": {}})
    assert seen == {"in_mode": 0o755, "file_mode": 0o644, "ev_mode": 0o777, "input": {"a": 1}}


def test_non_json_stdout_gives_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        adapter.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 2, stdout="oops", stderr="refused")
    )
    out = run_image(REF, "plan_critic", {}, key_variable=None)
    assert (out.exit_code, out.result, out.events, out.stderr) == (2, None, None, "refused")


def test_unreachable_daemon_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(argv: list[str], **kw: Any) -> Any:
        raise FileNotFoundError("docker")

    monkeypatch.setattr(adapter.subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="docker unreachable"):
        run_image(REF, "plan_critic", {}, key_variable=None)
