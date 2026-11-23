"""egress_floor: rule rendering, the hook drop-in, the sudoers line, the read-back and the root refusal (subprocess faked)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.personas import egress_floor as F

HOOK = "-w -t mangle -A EGRESS_FAIL_CLOSED -j DROP"


def test_rules_drop_the_azure_metadata_and_private_ranges_for_non_root_after_loopback_and_root() -> None:
    rules = F.render_rules()["v4_floor"]
    assert rules[:3] == [
        "-A EGRESS_FLOOR -o lo -j RETURN",
        "-A EGRESS_FLOOR -m owner --uid-owner 0 -j RETURN",
        "-A EGRESS_FLOOR -j EGRESS_FAIL_CLOSED",
    ]
    drops = [r.split()[3] for r in rules[3:]]
    assert drops == ["168.63.129.16/32", "169.254.0.0/16", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
    assert all(r.endswith("-j DROP") for r in rules[3:]) and F.render_rules()["v4_fail"] == []


def test_ipv6_is_dropped_for_non_root_but_loopback() -> None:
    assert F.render_rules()["v6_floor"] == [
        "-A EGRESS_FLOOR -o lo -j RETURN",
        "-A EGRESS_FLOOR -m owner --uid-owner 0 -j RETURN",
        "-A EGRESS_FLOOR -j DROP",
    ]


def test_the_fail_closed_unit_is_bound_to_the_agent_and_stops_with_an_absolute_hook() -> None:
    text = F.unit_text()
    assert F.UNIT == "egress-fail-closed.service" and str(F.UNIT_PATH).endswith("/egress-fail-closed.service")
    assert (
        "BindsTo=agent.service\nAfter=agent.service" in text
        and "Type=oneshot\nRemainAfterExit=yes\nExecStart=/bin/true" in text
    )
    assert "\nExecStop=/" in text and text.rstrip().endswith(HOOK)
    assert (
        str(F.UNHOOK_PATH).endswith("/egress-fail-closed.service.d/unhook.conf")
        and F.unhook_text() == "[Service]\nExecStop=\n"
    )


def _fake(monkeypatch: pytest.MonkeyPatch, replies: dict[tuple[str, ...], tuple[int, str]]) -> list[list[str]]:
    seen: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        seen.append(list(argv))
        for key, reply in replies.items():
            if tuple(argv[-len(key) :]) == key:
                return reply
        return 0, ""

    monkeypatch.setattr(F, "_run", run)
    return seen


def _good_replies() -> dict[tuple[str, ...], tuple[int, str]]:
    want = F.render_rules()
    v4 = "-N EGRESS_FLOOR\n" + "\n".join(want["v4_floor"]) + "\n"
    out = "-P OUTPUT ACCEPT\n-A OUTPUT -j EGRESS_FLOOR\n-A OUTPUT -o eth0 -j ACCEPT\n"
    shown = f"ActiveState=active\nBindsTo=agent.service\nExecStop={{ path=x ; argv[]={F.hook_argv()} ; }}\n"
    replies: dict[tuple[str, ...], tuple[int, str]] = {
        ("-S", "EGRESS_FLOOR"): (0, v4),
        ("-S", "EGRESS_FAIL_CLOSED"): (0, "-N EGRESS_FAIL_CLOSED\n"),
        ("-S", "OUTPUT"): (0, out),
        ("ExecStop",): (0, shown),
        ("--value",): (0, "masked\n"),
    }
    return replies


def _v6_aware(monkeypatch: pytest.MonkeyPatch, replies: dict[tuple[str, ...], tuple[int, str]]) -> None:
    v6 = "-N EGRESS_FLOOR\n" + "\n".join(F.render_rules()["v6_floor"]) + "\n"

    def run(argv: list[str]) -> tuple[int, str]:
        if argv[0].endswith("ip6tables") and argv[-2:] == ["-S", "EGRESS_FLOOR"]:
            return 0, v6
        for key, reply in replies.items():
            if tuple(argv[-len(key) :]) == key:
                return reply
        return 0, ""

    monkeypatch.setattr(F, "_run", run)


def test_verify_passes_when_every_read_back_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    _v6_aware(monkeypatch, _good_replies())
    checks = F.verify()
    assert F.passed(checks) and len(checks) == 9


@pytest.mark.parametrize(
    ("replies", "failing"),
    [
        ({("-S", "EGRESS_FLOOR"): (0, "-N EGRESS_FLOOR\n")}, "v4_floor"),
        ({("-S", "EGRESS_FAIL_CLOSED"): (0, "-A EGRESS_FAIL_CLOSED -j DROP\n")}, "v4_fail_closed"),
        ({("-S", "OUTPUT"): (0, "-A OUTPUT -o eth0 -j ACCEPT\n-A OUTPUT -j EGRESS_FLOOR\n")}, "v4_output_jump"),
        ({("ExecStop",): (0, "ActiveState=active\nBindsTo=agent.service\nExecStop=\n")}, "exit_hook"),
        ({("ExecStop",): (0, f"ActiveState=inactive\nBindsTo=agent.service\nExecStop=argv[]={F.hook_argv()}\n")}, "exit_hook"),
        ({("ExecStop",): (0, f"ActiveState=active\nBindsTo=\nExecStop=argv[]={F.hook_argv()}\n")}, "exit_hook"),
        ({("--value",): (0, "loaded\n")}, "masked_docker.socket"),
    ],
)
def test_verify_names_each_failed_read_back(
    monkeypatch: pytest.MonkeyPatch, replies: dict[tuple[str, ...], tuple[int, str]], failing: str
) -> None:
    _v6_aware(monkeypatch, {**_good_replies(), **replies})
    bad = [name for name, ok in F.verify() if not ok]
    assert failing in bad and not F.passed(F.verify())


def test_report_prints_one_line_per_check_and_a_verdict(capsys: pytest.CaptureFixture[str]) -> None:
    assert F.report([("a", True), ("b", False)]) is False
    out = capsys.readouterr().out
    assert "EGRESS-FLOOR check=a ok" in out and "EGRESS-FLOOR check=b FAIL" in out and out.rstrip().endswith("FAIL b")
    assert F.report([("a", True)]) is True and "EGRESS-FLOOR PASS" in capsys.readouterr().out


def test_main_refuses_unless_root(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    seen = _fake(monkeypatch, {})
    monkeypatch.setattr(F.os, "geteuid", lambda: 1000)
    assert F.main([]) == 1 and seen == [] and "not-root" in capsys.readouterr().out


def _root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(F, "UNIT_PATH", tmp_path / "egress-fail-closed.service")
    monkeypatch.setattr(F, "SUDOERS", tmp_path / "zz-egress-floor")
    monkeypatch.setattr(F, "SOCKETS", ())
    monkeypatch.setattr(F.os, "geteuid", lambda: 0)


def test_apply_writes_and_starts_the_unit_masks_the_units_and_installs_the_sudoers_rule_last(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _root(monkeypatch, tmp_path)
    seen = _fake(monkeypatch, {})
    assert F.apply() is True
    assert F.UNIT_PATH.read_text(encoding="utf-8") == F.unit_text()
    assert F.SUDOERS.read_text(encoding="utf-8") == "runner ALL=(ALL) !ALL\n" and F.SUDOERS.stat().st_mode & 0o777 == 0o440
    assert ["systemctl", "mask", "--now", *F.UNITS] in seen
    assert seen[-1][0] == "visudo"
    assert seen.index(["systemctl", "daemon-reload"]) < seen.index(["systemctl", "start", F.UNIT])


def test_apply_inserts_the_jump_at_position_one_for_both_families(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root(monkeypatch, tmp_path)
    seen = _fake(monkeypatch, {})
    F.apply()
    jumps = [a for a in seen if a[-4:] == ["-I", "OUTPUT", "1", "-j"] or a[-5:] == ["-I", "OUTPUT", "1", "-j", "EGRESS_FLOOR"]]
    assert len(jumps) == 2 and {a[0].rsplit("/", 1)[1] for a in jumps} == {"iptables", "ip6tables"}


def test_apply_fails_and_leaves_no_sudoers_file_when_visudo_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root(monkeypatch, tmp_path)
    _fake(monkeypatch, {("visudo",): (1, "")})
    monkeypatch.setattr(F, "_run", lambda argv: (1, "") if argv[0] == "visudo" else (0, ""))
    assert F.apply() is False and not F.SUDOERS.exists() and not (tmp_path / "zz-egress-floor.new").exists()


def test_apply_reports_failure_when_a_rule_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _root(monkeypatch, tmp_path)
    monkeypatch.setattr(F, "_run", lambda argv: (1, "") if argv[-1] == "DROP" and "168.63.129.16/32" in argv else (0, ""))
    assert F.apply() is False


def test_main_runs_apply_then_prints_the_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _root(monkeypatch, tmp_path)
    monkeypatch.setattr(F, "apply", lambda: True)
    monkeypatch.setattr(F, "verify", lambda: [("x", True)])
    assert F.main([]) == 0 and "EGRESS-FLOOR PASS" in capsys.readouterr().out
    monkeypatch.setattr(F, "verify", lambda: [("x", False)])
    assert F.main([]) == 1


def test_run_returns_127_when_the_binary_is_missing() -> None:
    assert F._run(["/nonexistent/binary-xyz"])[0] == 127


def test_binary_prefers_an_existing_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F.os.path, "exists", lambda p: p == "/sbin/iptables")
    assert F.binary("iptables") == "/sbin/iptables"
    monkeypatch.setattr(F.os.path, "exists", lambda p: False)
    assert F.binary("iptables") == "/usr/sbin/iptables"
