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
from src.turn_capture.host_run import parse_persona_host_run

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


def test_non_mapping_contract_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "docs/contracts").mkdir(parents=True)
    (tmp_path / "docs/contracts/project-configuration.yaml").write_text("- a\n", encoding="utf-8")
    monkeypatch.setattr(adapter, "_ROOT", tmp_path)
    with pytest.raises(ValueError, match="not a YAML mapping"):
        adapter._key_variables()


def test_daemon_error_from_docker_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 125, stdout="", stderr="Cannot connect to the Docker daemon"),
    )
    with pytest.raises(RuntimeError, match="docker unreachable"):
        run_image(REF, "plan_critic", {}, key_variable=None)


def _fake_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        adapter.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")
    )


def test_run_image_mints_a_fresh_local_sidecar_per_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_docker(monkeypatch)
    first = run_image(REF, "plan_critic", {}, key_variable=KEY, project_ref="example/project", agent="plan-critique")
    second = run_image(REF, "plan_critic", {}, key_variable=KEY, project_ref="example/project", agent="plan-critique")
    assert first.host_run is not None and second.host_run is not None and first.host_run != second.host_run
    body = json.loads(first.host_run)
    assert first.host_run == json.dumps(body, sort_keys=True).encode()
    assert body["host"]["kind"] == "local" and body["host"]["runtime"] == "local_container"
    assert parse_persona_host_run(first.host_run) is not None


def test_run_image_passes_an_outer_sidecar_through_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_docker(monkeypatch)
    outer = b'{"opaque": "outer host bytes"}'
    assert run_image(REF, "plan_critic", {}, key_variable=KEY, host_run=outer).host_run == outer


def test_run_image_without_sidecar_arguments_defers(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_docker(monkeypatch)
    assert run_image(REF, "plan_critic", {}, key_variable=KEY).host_run is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host_run": b"{}", "project_ref": "example/project"},
        {"host_run": b"{}", "agent": "plan-critique"},
        {"project_ref": "example/project"},
        {"agent": "plan-critique"},
        {"project_ref": "example/project", "agent": "Plan Critique"},
        {"project_ref": "not a ref", "agent": "plan-critique"},
    ],
)
def test_run_image_refuses_mixed_or_bad_sidecar_arguments(monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any]) -> None:
    def no_docker(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError("docker must not run")

    monkeypatch.setattr(adapter.subprocess, "run", no_docker)
    with pytest.raises(ValueError):
        run_image(REF, "plan_critic", {}, key_variable=KEY, **kwargs)
