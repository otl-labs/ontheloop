"""egress_fail_closed: the LATE, NO-DRILL, PASS and FAIL paths (systemctl and sockets faked)."""

from __future__ import annotations

import pytest

from scripts.personas import egress_fail_closed as FC
from scripts.personas import egress_probe


def _setup(monkeypatch: pytest.MonkeyPatch, active: list[bool], reached: set[str]) -> None:
    states = iter(active)
    monkeypatch.setattr(FC, "agent_active", lambda: next(states, False))
    monkeypatch.setattr(FC.time, "sleep", lambda s: None)
    monkeypatch.setattr(egress_probe, "attempt", lambda case: case[0] in reached)


def test_late_when_the_agent_is_not_running(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _setup(monkeypatch, [False], {"listed"})
    assert FC.main([]) == 1 and "EGRESS-FAIL-CLOSED LATE" in capsys.readouterr().out


def test_late_when_the_listed_host_is_not_reached(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _setup(monkeypatch, [True], set())
    assert FC.main([]) == 1 and "EGRESS-FAIL-CLOSED LATE" in capsys.readouterr().out


def test_no_drill_when_the_agent_never_exits(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _setup(monkeypatch, [True] * 500, {"listed"})
    assert FC.main([]) == 1 and "EGRESS-FAIL-CLOSED NO-DRILL" in capsys.readouterr().out


def test_pass_when_all_three_are_refused_after_the_agent_exits(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    _setup(monkeypatch, [True, True, True, False], set())
    monkeypatch.setattr(egress_probe, "attempt", lambda case: calls.append(case[0]) or case[0] == "listed" and len(calls) == 1)
    assert FC.main([]) == 0
    out = capsys.readouterr().out
    assert calls == ["listed", "listed", "raw_ip_443", "docker_socket"] and out.rstrip().endswith("EGRESS-FAIL-CLOSED PASS")


def test_fail_names_each_case_still_reached(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _setup(monkeypatch, [True, False], {"listed", "raw_ip_443"})
    assert FC.main([]) == 1
    out = capsys.readouterr().out
    assert "case=raw_ip_443 got=reached" in out and "EGRESS-FAIL-CLOSED FAIL listed raw_ip_443" in out


def test_agent_active_reads_systemctl(monkeypatch: pytest.MonkeyPatch) -> None:
    class Done:
        stdout = "active\n"

    monkeypatch.setattr(FC.subprocess, "run", lambda *a, **k: Done())
    assert FC.agent_active() is True
    Done.stdout = "inactive\n"
    assert FC.agent_active() is False

    def boom(*a: object, **k: object) -> None:
        raise OSError

    monkeypatch.setattr(FC.subprocess, "run", boom)
    assert FC.agent_active() is False
