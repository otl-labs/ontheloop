"""egress_container_control: the control's argv, expectations, exit codes and the fixed request verb."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from scripts.personas import egress_container_control as C


def test_request_verb_prints_the_fixed_probe_request(capsys: pytest.CaptureFixture[str]) -> None:
    assert C.request_main([]) == 0
    assert json.loads(capsys.readouterr().out) == {"persona": "fixture_probe", "provider": "deepseek/", "input": {}}


def test_argv_uses_the_default_bridge_with_no_launcher_proxy_or_rules() -> None:
    argv = C.run_argv("img@sha256:x", "/in")
    assert "--network" not in argv and "--dns" not in argv and "sudo" not in argv
    assert argv[:2] == ["docker", "run"] and "--read-only" in argv and "/in:/run/persona/in:ro" in argv


def test_judge_expects_three_reached_and_reports_the_rest() -> None:
    got = {"raw_ip_443": "reached", "unlisted_name": "reached", "dns_txt_system": "reached", "imds": "refused"}
    assert C.judge(got) == ([], ["imds=refused"])
    assert C.judge({"raw_ip_443": "refused"})[0] == ["raw_ip_443", "unlisted_name", "dns_txt_system"]


def _run(monkeypatch: pytest.MonkeyPatch, pull: int, out: str) -> int:
    def fake(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, pull if argv[1] == "pull" else 0, out, "")

    monkeypatch.setattr(C.subprocess, "run", fake)
    return C.main([])


def test_pass_and_fail_exit_codes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    good = "".join(f"CONTAINER-PROBE case={c} expect=refused got=reached\n" for c in C.EXPECT_REACHED)
    assert _run(monkeypatch, 0, good) == 0 and "CONTAINER-CONTROL PASS" in capsys.readouterr().out
    assert _run(monkeypatch, 0, "") == 1 and "CONTAINER-CONTROL FAIL raw_ip_443" in capsys.readouterr().out
    assert _run(monkeypatch, 1, good) == 1 and "FAIL pull" in capsys.readouterr().out
