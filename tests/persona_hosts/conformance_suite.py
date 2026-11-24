"""persona_compute_host conformance suite (Decision 212 cl.2; T4.2 c14's gating leg), reusable by every adapter.

Named outside pytest's test_*.py pattern: no default collection picks it up; pr-validate and the publish workflow pass
it explicitly. A later adapter adds an ADAPTERS entry, never a second suite. Needs a reachable Docker daemon and fails
(never skips) without one. It reaches docker only through subprocess and opens no socket in-process.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.persona_hosts.image_pin import load_pin
from src.persona_hosts.local_container import build_image, run_argv, run_image
from src.turn_capture.host_run import LOCAL_RUNTIMES, LocalRun, parse_persona_host_run, render_host_run_ref

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ADAPTERS: dict[str, Callable[..., Any]] = {"local_container": run_image}
ARGV_BUILDERS: dict[str, Callable[..., list[str]]] = {"local_container": run_argv}


def _local_container_cli(
    image: str, persona: str, input_path: Path, out: Path, env: dict[str, str], network: str
) -> list[str]:
    return [
        sys.executable, "-m", "src.persona_hosts.local_container.cli", "--image", image, "--persona", persona,
        "--input", str(input_path), "--out", str(out), "--project-ref", PROJECT_REF, "--agent", AGENT,
        "--key-variable", env["key"], "--base-url-variable", env["base"], "--network", network, "--timeout", "300",
    ]  # fmt: skip


# One entry per adapter, never a skip: the CLI leg stays part of the port's one suite (Decision 212 cl.1). A local twin's
# CLI is a measured caller, not a ports.yaml required verb.
CLI_BUILDERS: dict[str, Callable[..., list[str]]] = {"local_container": _local_container_cli}
DUMMY_KEY = "dummy-conformance-key-0000"
STUB_PORT = 8080
FIXTURE = "canary_persona"
AGENT = "canary-persona"
PROJECT_REF = "synthetic:conformance"


def _docker(*args: str, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            ["docker", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )
    except FileNotFoundError as exc:
        pytest.fail(f"docker unreachable: {exc}")
    if check and proc.returncode != 0:
        pytest.fail(f"docker {' '.join(args[:2])} failed: {proc.stderr.strip()[-800:]}")
    return proc


def _config(relative: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))


def _key_variable() -> str:
    rows = _config("docs/contracts/project-configuration.yaml")["settings"]
    return next(r["variable"] for r in rows if r["id"] == "ports.run_persona.litellm.api_key.primary")


def _base_url_variable() -> str:
    tier = _config("docs/contracts/inference-provider.yaml")["model_id_formats"]["litellm_tier_models"]["tier_1"]
    return tier["default_model"].split("/", 1)[0].upper() + "_API_BASE"


def _base_image() -> str:
    text = (ROOT / load_pin(ROOT).dockerfile).read_text(encoding="utf-8")
    match = re.search(r"^FROM\s+(\S+)", text, re.M)
    assert match, "Dockerfile has no FROM"
    return match.group(1)


@pytest.fixture(scope="module")
def daemon() -> None:
    proc = _docker("info", check=False, timeout=60)
    if proc.returncode != 0:
        pytest.fail(f"docker unreachable: {proc.stderr.strip()[-300:]}")


def _teardown_images(image_id: str, prebuilt: bool) -> list[str]:
    """A prebuilt image is an input the suite never removes; a tree-built one is removed so no run leaves it dangling."""
    return [] if prebuilt else [image_id]


def test_teardown_keeps_a_prebuilt_image() -> None:
    assert _teardown_images("sha256:" + "a" * 64, prebuilt=True) == []
    assert _teardown_images("sha256:" + "a" * 64, prebuilt=False) == ["sha256:" + "a" * 64]


@pytest.fixture(scope="module")
def image_id(daemon: None) -> Iterator[str]:
    prebuilt = os.environ.get("PERSONA_IMAGE_UNDER_TEST")
    if prebuilt is None:
        image = build_image(ROOT)
    else:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", prebuilt):
            pytest.fail(f"PERSONA_IMAGE_UNDER_TEST is not sha256:<64 hex>: {prebuilt!r}")
        if _docker("image", "inspect", prebuilt, check=False).returncode != 0:
            pytest.fail(f"PERSONA_IMAGE_UNDER_TEST names no local image: {prebuilt}")
        image = prebuilt
    try:
        yield image
    finally:
        for ref in _teardown_images(image, prebuilt is not None):
            _docker("rmi", ref, check=False)


@pytest.fixture(scope="module")
def network(daemon: None) -> Iterator[str]:
    name = f"persona-conf-{uuid.uuid4().hex[:10]}"
    _docker("network", "create", "--internal", name)
    try:
        yield name
    finally:
        _docker("network", "rm", name, check=False)


@pytest.fixture(scope="module")
def stub(network: str) -> Iterator[str]:
    name = f"persona-stub-{uuid.uuid4().hex[:10]}"
    _docker(
        "run", "-d", "--rm", "--name", name, "--network", network, "-v", f"{HERE / 'stub_provider.py'}:/stub.py:ro",
        _base_image(), "python", "-u", "/stub.py", str(STUB_PORT),
    )  # fmt: skip
    try:
        probe = f"import socket;socket.create_connection(('127.0.0.1',{STUB_PORT}),2)"
        for _ in range(30):
            if _docker("exec", name, "python", "-c", probe, check=False).returncode == 0:
                break
            time.sleep(0.5)
        else:
            pytest.fail("the stub provider did not start: " + _docker("logs", name, check=False).stderr[-400:])
        yield name
    finally:
        _docker("rm", "-f", name, check=False)


@pytest.fixture(scope="module")
def env(monkeypatch_module: pytest.MonkeyPatch, stub: str) -> dict[str, str]:
    key_variable, base_variable = _key_variable(), _base_url_variable()
    monkeypatch_module.setenv(key_variable, DUMMY_KEY)
    monkeypatch_module.setenv(base_variable, f"http://{stub}:{STUB_PORT}")
    return {"key": key_variable, "base": base_variable}


@pytest.fixture(scope="module")
def monkeypatch_module() -> Iterator[pytest.MonkeyPatch]:
    patch = pytest.MonkeyPatch()
    yield patch
    patch.undo()


@pytest.fixture(params=sorted(ADAPTERS))
def adapter(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def _hits(stub: str) -> int:
    logs = _docker("logs", stub)
    return (logs.stdout + logs.stderr).count("STUB_HIT")


def _input(name: str) -> dict[str, Any]:
    return json.loads((HERE / "fixtures" / name).read_text(encoding="utf-8"))


def _run(adapter: str, image: str, persona: str, payload: dict[str, Any], env: dict[str, str], network: str) -> Any:
    return ADAPTERS[adapter](
        image, persona, payload, key_variable=env["key"], base_url_variable=env["base"], network=network, timeout_s=300
    )


def test_complete_input_returns_a_valid_success(
    adapter: str, image_id: str, env: dict[str, str], network: str, stub: str
) -> None:
    before = _hits(stub)
    result = _run(adapter, image_id, FIXTURE, _input("input_complete.json"), env, network)
    assert result.exit_code == 0, result.stderr
    assert result.result is not None and result.result["outcome"] == "success"
    assert result.result["verdict"] in {"PROCEED", "REVISE"}
    assert _hits(stub) - before == 2
    header = (result.events or {}).get("header", {})
    assert header.get("persona") == FIXTURE
    assert re.fullmatch(r"[0-9a-f]{64}", header.get("rendered_prompt_sha256", ""))


def test_run_returns_a_host_run_sidecar_the_producer_roots(
    adapter: str, image_id: str, env: dict[str, str], network: str
) -> None:
    result = ADAPTERS[adapter](
        image_id,
        FIXTURE,
        _input("input_complete.json"),
        key_variable=env["key"],
        base_url_variable=env["base"],
        network=network,
        timeout_s=300,
        project_ref=PROJECT_REF,
        agent=AGENT,
    )
    host = parse_persona_host_run(result.host_run)
    assert host is not None, result.host_run
    if adapter in LOCAL_RUNTIMES:
        assert isinstance(host, LocalRun) and host.runtime == adapter
    assert (host.project_ref, host.agent) == (PROJECT_REF, AGENT)


def test_incomplete_input_fails_without_a_provider_call(
    adapter: str, image_id: str, env: dict[str, str], network: str, stub: str
) -> None:
    before = _hits(stub)
    result = _run(adapter, image_id, FIXTURE, _input("input_incomplete.json"), env, network)
    assert result.exit_code == 0, result.stderr
    assert result.result is not None and result.result["failure"] == "input_incomplete"
    assert _hits(stub) == before


def test_unknown_persona_exits_2(adapter: str, image_id: str, env: dict[str, str], network: str) -> None:
    result = _run(adapter, image_id, "no_such_persona", _input("input_complete.json"), env, network)
    assert result.exit_code == 2


def _probe(adapter: str, image: str, network: str, code: str) -> subprocess.CompletedProcess[str]:
    argv = ARGV_BUILDERS[adapter](
        image, FIXTURE, str(HERE), str(HERE), key_variable=None, network=network, entrypoint=["python", "-c", code]
    )
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)


def test_process_is_non_root_and_cannot_write_the_app_tree(adapter: str, image_id: str, network: str) -> None:
    code = (
        "import os\nprint(os.getuid())\ntry:\n    open('/app/probe','w')\n    print('WROTE')\n"
        "except OSError:\n    print('DENIED')"
    )
    out = _probe(adapter, image_id, network, code)
    assert out.stdout.split() == ["10001", "DENIED"], out.stderr


def test_dummy_key_never_reaches_stdout_or_events(adapter: str, image_id: str, env: dict[str, str], network: str) -> None:
    result = _run(adapter, image_id, FIXTURE, _input("input_complete.json"), env, network)
    assert result.exit_code == 0, result.stderr
    assert DUMMY_KEY not in json.dumps(result.result) + json.dumps(result.events) + result.stderr


def test_egress_to_a_public_address_is_denied(adapter: str, image_id: str, network: str) -> None:
    code = (
        "import socket\ntry:\n    socket.create_connection(('1.1.1.1', 443), 5)\n    print('CONNECTED')\n"
        "except OSError:\n    print('DENIED')"
    )
    out = _probe(adapter, image_id, network, code)
    assert out.stdout.split() == ["DENIED"], out.stderr


def test_blocking_note_yields_revise_with_findings(adapter: str, image_id: str, env: dict[str, str], network: str) -> None:
    result = _run(adapter, image_id, FIXTURE, _input("input_blocking.json"), env, network)
    assert result.exit_code == 0, result.stderr
    assert result.result is not None and result.result["verdict"] == "REVISE"
    assert [(f["id"], f["severity"]) for f in result.result["findings"]] == [("line:1", "blocking"), ("line:2", "nit")]


def test_injection_fixture_yields_a_failure_value(adapter: str, image_id: str, env: dict[str, str], network: str) -> None:
    result = _run(adapter, image_id, FIXTURE, _input("input_injection.json"), env, network)
    assert result.exit_code == 0, result.stderr
    assert result.result is not None and result.result["outcome"] == "failure"
    assert result.result["failure"] == "injection_suspected"


def test_cli_run_is_measured(adapter: str, image_id: str, env: dict[str, str], network: str, tmp_path: Path) -> None:
    out = tmp_path / "run"
    argv = CLI_BUILDERS[adapter](image_id, FIXTURE, HERE / "fixtures/input_blocking.json", out, env, network)
    proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=400, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    assert sorted(p.name for p in out.iterdir()) == ["events.json", "host_run.json", "result.json"]
    host = parse_persona_host_run((out / "host_run.json").read_bytes())
    assert host is not None and (host.project_ref, host.agent) == (PROJECT_REF, AGENT)
    rows_path = tmp_path / "rows.json"
    rows = subprocess.run(
        [sys.executable, "-m", "scripts.personas.telemetry_rows", "--events", str(out / "events.json"),
         "--host-run", str(out / "host_run.json"), "--out", str(rows_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, cwd=ROOT,
    )  # fmt: skip
    assert rows.returncode == 0, rows.stderr
    batches = json.loads(rows_path.read_text(encoding="utf-8"))["batches"]
    agents = [r for b in batches if b["table"] == "telemetry_agents" for r in b["rows"]]
    assert agents and {r["agent_type"] for r in agents} == {AGENT}
    assert {r["host_run_ref"] for r in agents} == {render_host_run_ref(host)}
