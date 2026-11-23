"""rec-5437: a litellm run on a local persona compute host is rooted under the local: host_run_ref prefix."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from src.persona_hosts.local_container import adapter
from src.turn_capture import litellm_producer as lp
from src.turn_capture.host_run import LocalRun, parse_host_run, parse_persona_host_run
from tests.fixtures.turn_capture_litellm_cases import HOST_RUN, HOST_RUN_LOCAL, LITELLM_BUILDERS, load_litellm_case

RUN = json.loads(HOST_RUN_LOCAL)["host"]["run_id"]
ROOT = f"litellm:local:local_container/{RUN}"
IMAGE = "ghcr.io/example/persona@sha256:" + "c" * 64


def _events() -> dict[str, Any]:
    return json.loads(load_litellm_case("local_container_run.json")[0]["events.json"])


def _mapped(sidecar: bytes) -> lp.Mapped:
    mapped = lp.map_run(_events(), sidecar)
    assert isinstance(mapped, lp.Mapped)
    return mapped


def _rows(mapped: lp.Mapped, table: str) -> list[dict[str, Any]]:
    return next(rows for name, rows in mapped.batches if name == table)


def test_local_sidecar_maps_to_a_local_root() -> None:
    mapped = _mapped(HOST_RUN_LOCAL.encode())
    sessions, agents, calls = (_rows(mapped, t) for t in ("telemetry_sessions", "telemetry_agents", "telemetry_observations"))
    assert [r["event_kind"] for r in sessions] == ["open", "close"]
    assert {r["entity_ref"] for r in sessions} == {ROOT}
    assert {r["execution_attempt"] for r in sessions} == {1}
    assert {r["session_ref"] for r in agents} == {ROOT} == {r["session_ref"] for r in calls}
    for row in agents:
        assert row["trigger"] == "manual" and row.get("workflow_run_id") is None
        assert row["host_run_ref"] == f"local:local_container/{RUN}"
    assert calls


def test_a_gha_sidecar_keeps_its_gha_root() -> None:
    mapped = _mapped(HOST_RUN.encode())
    assert {r["session_ref"] for r in _rows(mapped, "telemetry_agents")} == {"litellm:gha:123456789/1/rca/1"}


def _local(**host: Any) -> bytes:
    body = json.loads(HOST_RUN_LOCAL)
    body["host"] = {**body["host"], **host}
    return json.dumps(body).encode()


@pytest.mark.parametrize(
    "sidecar",
    [
        _local(runtime="fargate"),
        _local(run_id="a:b"),
        _local(run_id="a/b"),
        _local(run_id=""),
        _local(extra="x"),
        _local(run_id=None),
        _local(run_id=7),
    ],
)
def test_invalid_local_sidecars_defer(sidecar: bytes) -> None:
    assert isinstance(lp.map_run(_events(), sidecar), lp.Deferred)


def test_a_missing_local_run_id_defers() -> None:
    body = json.loads(HOST_RUN_LOCAL)
    del body["host"]["run_id"]
    assert isinstance(lp.map_run(_events(), json.dumps(body).encode()), lp.Deferred)


def test_the_claude_code_reader_stays_gha_only() -> None:
    assert parse_host_run(HOST_RUN_LOCAL.encode()) is None
    assert isinstance(parse_persona_host_run(HOST_RUN_LOCAL.encode()), LocalRun)


def _fake_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        adapter.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")
    )


def test_run_image_sidecar_roots_and_an_outer_gha_sidecar_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_docker(monkeypatch)
    minted = adapter.run_image(
        IMAGE, "plan_critic", {}, key_variable=None, project_ref="example/project", agent="plan-critique"
    )
    assert minted.host_run is not None
    local = _mapped(minted.host_run)
    (ref,) = {r["session_ref"] for r in _rows(local, "telemetry_agents")}
    assert ref.startswith("litellm:local:local_container/")
    outer = adapter.run_image(IMAGE, "plan_critic", {}, key_variable=None, host_run=HOST_RUN.encode())
    assert outer.host_run == HOST_RUN.encode()
    gha = _mapped(outer.host_run)
    assert {r["session_ref"] for r in _rows(gha, "telemetry_agents")} == {"litellm:gha:123456789/1/rca/1"}


def test_the_local_case_has_a_builder() -> None:
    assert "local_container_run.json" in LITELLM_BUILDERS
