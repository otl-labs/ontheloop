"""canary_persona through run_image on the stub provider with no Docker daemon (rec-5548; the shim proves the layout)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.persona_hosts.local_container import run_image
from src.turn_capture.host_run import parse_persona_host_run
from tests.persona_hosts import docker_shim
from tests.persona_hosts.conformance_suite import _base_url_variable, _key_variable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
IMAGE = "sha256:" + "0" * 64
PERSONA = "canary_persona"
PROJECT_REF = "synthetic:fixture-run"
AGENT = "canary-persona"


@pytest.fixture(scope="module")
def stub_url() -> Iterator[str]:
    proc = subprocess.Popen(
        [sys.executable, "-u", str(HERE / "stub_provider.py"), "0"], stdout=subprocess.PIPE, text=True, encoding="utf-8"
    )
    try:
        assert proc.stdout is not None
        first = proc.stdout.readline().split()
        assert first[:1] == ["STUB_PORT"], first
        yield f"http://127.0.0.1:{first[1]}"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def docker_wrapper(tmp_path: Path) -> str:
    wrapper = tmp_path / "docker"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{HERE / "docker_shim.py"}" "$@"\n', encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return str(wrapper)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, stub_url: str) -> dict[str, str]:
    key, base = _key_variable(), _base_url_variable()
    monkeypatch.setenv(key, "dummy-shim-key-0000")
    monkeypatch.setenv(base, stub_url)
    return {"key": key, "base": base}


def _run(name: str, env: dict[str, str], docker: str) -> Any:
    payload = json.loads((HERE / "fixtures" / name).read_text(encoding="utf-8"))
    return run_image(
        IMAGE, PERSONA, payload, key_variable=env["key"], base_url_variable=env["base"], docker=docker,
        timeout_s=120, project_ref=PROJECT_REF, agent=AGENT,
    )  # fmt: skip


def test_fixture_persona_runs_through_run_image_on_stub_provider(env: dict[str, str], docker_wrapper: str) -> None:
    contract = yaml.safe_load((ROOT / f"docs/contracts/personas/{PERSONA}.yaml").read_text(encoding="utf-8"))
    complete = _run("input_complete.json", env, docker_wrapper)
    assert complete.exit_code == 0, complete.stderr
    assert complete.result is not None and complete.result["outcome"] == "success"
    assert (complete.result["verdict"], complete.result["findings"]) == ("PROCEED", [])
    header = (complete.events or {}).get("header", {})
    assert header.get("persona") == PERSONA
    assert header.get("rendered_prompt_sha256") == contract["rendered_prompt_sha256"]
    host = parse_persona_host_run(complete.host_run)
    assert host is not None and (host.project_ref, host.agent) == (PROJECT_REF, AGENT)

    blocking = _run("input_blocking.json", env, docker_wrapper)
    assert blocking.exit_code == 0, blocking.stderr
    assert blocking.result is not None and blocking.result["verdict"] == "REVISE"
    assert [(f["id"], f["severity"]) for f in blocking.result["findings"]] == [("line:1", "blocking"), ("line:2", "nit")]


def test_injection_input_yields_the_failure_value(env: dict[str, str], docker_wrapper: str) -> None:
    result = _run("input_injection.json", env, docker_wrapper)
    assert result.exit_code == 0, result.stderr
    assert result.result is not None
    assert (result.result["outcome"], result.result["failure"]) == ("failure", "injection_suspected")


def test_shim_refuses_unsupported_instructions_and_flags(tmp_path: Path) -> None:
    with pytest.raises(docker_shim.ShimError, match="ARG"):
        docker_shim.instructions("ARG BASE\nFROM x\n")
    with pytest.raises(docker_shim.ShimError, match="--privileged"):
        docker_shim.parse_run(["--privileged", "img"])
    mounts, names, image, override, rest = docker_shim.parse_run(
        ["--rm", "-v", "/a:/run/persona/in:ro", "-e", "K", "img", "x"]
    )
    assert (mounts, names, image, override, rest) == ({"/run/persona/in": "/a"}, ["K"], "img", [], ["x"])


def test_shim_layout_has_the_personas_tree(tmp_path: Path) -> None:
    root = tmp_path / "rootfs"
    root.mkdir()
    env, workdir, entrypoint = docker_shim.build_layout(root)
    assert (root / f"app/docs/contracts/personas/{PERSONA}.yaml").is_file()
    assert (root / "app/scripts/run_persona.py").is_file()
    assert not list(root.rglob("__pycache__"))
    assert (env["HOME"], workdir, entrypoint[-1]) == ("/tmp", "/app", "/app/scripts/run_persona.py")
    assert os.path.isdir(root / "app/src/persona_backends")
