"""egress_revert_drill: arms only after a passing floor read-back, with the exact scheduled revert (subprocess faked)."""

from __future__ import annotations

import pytest

from scripts.personas import egress_floor
from scripts.personas import egress_revert_drill as D


def _floor(monkeypatch: pytest.MonkeyPatch, ok: bool = True) -> None:
    monkeypatch.setattr(egress_floor, "apply", lambda: True)
    monkeypatch.setattr(egress_floor, "verify", lambda: [("x", ok)])


def test_the_schedule_is_the_agents_revert_in_order_then_the_kill() -> None:
    argv = D.schedule_argv()
    assert argv[:5] == ["systemd-run", "--on-active=120", "--unit=egress-revert-drill", "/bin/sh", "-c"]
    steps = [s.strip() for s in argv[5].split(";")]
    assert steps[0] == "iptables -w -F OUTPUT" and steps[1] == "iptables -w -F DOCKER-USER"
    assert "DNS=1.1.1.1" in steps[2] and "Domains=~." in steps[2] and steps[2].endswith("> /etc/systemd/resolved.conf")
    assert steps[3:] == [
        "systemctl restart systemd-resolved",
        "systemctl daemon-reload",
        "systemctl restart docker",
        "systemctl kill -s KILL agent.service",
    ]


def test_hooked_arm_schedules_after_a_passing_verify(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _floor(monkeypatch)
    seen: list[list[str]] = []
    monkeypatch.setattr(egress_floor, "_run", lambda argv: seen.append(list(argv)) or (0, ""))
    assert D.arm(hooked=True) == 0 and D.main([]) == 0
    assert seen[0] == D.schedule_argv() and "EGRESS-DRILL ARMED" in capsys.readouterr().out


def test_a_failing_verify_never_arms(monkeypatch: pytest.MonkeyPatch) -> None:
    _floor(monkeypatch, ok=False)
    seen: list[list[str]] = []
    monkeypatch.setattr(egress_floor, "_run", lambda argv: seen.append(list(argv)) or (0, ""))
    assert D.arm(hooked=True) == 1 and D.arm(hooked=False) == 1 and seen == []


def test_unhooked_arm_writes_the_empty_execstop_after_verify_and_requires_the_hook_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    _floor(monkeypatch)
    unhook = tmp_path / "egress-fail-closed.service.d" / "unhook.conf"
    monkeypatch.setattr(egress_floor, "UNHOOK_PATH", unhook)
    seen: list[list[str]] = []
    monkeypatch.setattr(egress_floor, "_run", lambda argv: seen.append(list(argv)) or (0, "ExecStop=\n"))
    assert D.arm(hooked=False) == 0 and unhook.read_text(encoding="utf-8") == "[Service]\nExecStop=\n"
    assert seen[0] == ["systemctl", "daemon-reload"] and seen[1] == [
        "systemctl",
        "show",
        "egress-fail-closed.service",
        "-p",
        "ExecStop",
    ]
    assert seen[-1] == D.schedule_argv() and ["systemctl", "stop", "egress-fail-closed.service"] not in seen
    out = capsys.readouterr().out
    assert out.index("EGRESS-FLOOR PASS") < out.index("EGRESS-DRILL UNHOOKED") < out.index("EGRESS-DRILL ARMED")


def test_unhooked_arm_refuses_while_the_hook_is_still_loaded(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _floor(monkeypatch)
    monkeypatch.setattr(egress_floor, "UNHOOK_PATH", tmp_path / "d" / "unhook.conf")
    shown = f"ExecStop={{ argv[]={egress_floor.hook_argv()} }}"
    seen: list[list[str]] = []
    monkeypatch.setattr(egress_floor, "_run", lambda argv: seen.append(list(argv)) or (0, shown))
    assert D.arm(hooked=False) == 1 and D.schedule_argv() not in seen


def test_a_refused_schedule_fails(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _floor(monkeypatch)
    monkeypatch.setattr(egress_floor, "_run", lambda argv: (1, ""))
    assert D.arm(hooked=True) == 1 and "systemd-run refused" in capsys.readouterr().out
