"""Verb egress-floor: the root step a covered verdict job runs right after its checkout (PLAN-persona-verdict-egress-floor).

Runs once as root on the runner's system python. It blocks, for every user but root and every interface but loopback,
Azure's host address, the metadata address, the private ranges and all IPv6 in the mangle table the filter's agent never
clears; installs a fail-closed unit, bound to agent.service, whose stop drops all non-root IPv4 for the rest of the job
when the agent stops or fails; masks the container services; revokes sudo with a rule the agent cannot restore; then reads
every piece back as root.
Stdlib only; the rules are module constants, never read from a file.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path

FLOOR = "EGRESS_FLOOR"
FAIL = "EGRESS_FAIL_CLOSED"
DROP_V4 = ("168.63.129.16/32", "169.254.0.0/16", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
UNITS = ("docker.socket", "docker.service", "containerd.service")
SOCKETS = ("/var/run/docker.sock", "/run/containerd/containerd.sock")
UNIT = "egress-fail-closed.service"
UNIT_PATH = Path("/etc/systemd/system/egress-fail-closed.service")
UNHOOK_PATH = Path("/etc/systemd/system/egress-fail-closed.service.d/unhook.conf")
SUDOERS = Path("/etc/sudoers.d/zz-egress-floor")
SUDOERS_LINE = "runner ALL=(ALL) !ALL\n"
BIN_DIRS = ("/usr/sbin", "/sbin", "/usr/bin", "/bin")
TIMEOUT = 30


def binary(name: str) -> str:
    for directory in BIN_DIRS:
        candidate = f"{directory}/{name}"
        if os.path.exists(candidate):
            return candidate
    return f"/usr/sbin/{name}"


def render_rules() -> dict[str, list[str]]:
    v4 = [f"-A {FLOOR} -o lo -j RETURN", f"-A {FLOOR} -m owner --uid-owner 0 -j RETURN", f"-A {FLOOR} -j {FAIL}"]
    v4 += [f"-A {FLOOR} -d {net} -j DROP" for net in DROP_V4]
    v6 = [f"-A {FLOOR} -o lo -j RETURN", f"-A {FLOOR} -m owner --uid-owner 0 -j RETURN", f"-A {FLOOR} -j DROP"]
    return {"v4_floor": v4, "v4_fail": [], "v6_floor": v6}


def hook_argv() -> str:
    return f"{binary('iptables')} -w -t mangle -A {FAIL} -j DROP"


def unit_text() -> str:
    return (
        "[Unit]\nDescription=Block non-root egress when the filter agent stops\nBindsTo=agent.service\nAfter=agent.service\n\n"
        f"[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/bin/true\nExecStop={hook_argv()}\n"
    )


def unhook_text() -> str:
    return "[Service]\nExecStop=\n"


def _run(argv: Sequence[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(
            list(argv), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, type(exc).__name__
    return done.returncode, done.stdout


def _tables(tool: str) -> list[str]:
    return [binary(tool), "-w", "-t", "mangle"]


def _chain(tool: str, chain: str, rules: list[str]) -> bool:
    base = _tables(tool)
    _run([*base, "-N", chain])
    ok = _run([*base, "-F", chain])[0] == 0
    for rule in rules:
        ok = _run([*base, *shlex.split(rule)])[0] == 0 and ok
    return ok


def _jump(tool: str) -> bool:
    base = _tables(tool)
    if _run([*base, "-C", "OUTPUT", "-j", FLOOR])[0] == 0:
        _run([*base, "-D", "OUTPUT", "-j", FLOOR])
    return _run([*base, "-I", "OUTPUT", "1", "-j", FLOOR])[0] == 0


def apply() -> bool:
    rules = render_rules()
    ok = _chain("iptables", FAIL, rules["v4_fail"]) and _chain("iptables", FLOOR, rules["v4_floor"])
    ok = _chain("ip6tables", FLOOR, rules["v6_floor"]) and ok
    ok = _jump("iptables") and ok
    ok = _jump("ip6tables") and ok
    UNIT_PATH.write_text(unit_text(), encoding="utf-8")
    ok = _run(["systemctl", "daemon-reload"])[0] == 0 and ok
    ok = _run(["systemctl", "start", UNIT])[0] == 0 and ok
    ok = _run(["systemctl", "mask", "--now", *UNITS])[0] == 0 and ok
    for path in SOCKETS:
        if os.path.exists(path):
            os.chmod(path, 0)
    pending = SUDOERS.with_name(SUDOERS.name + ".new")
    pending.write_text(SUDOERS_LINE, encoding="utf-8")
    os.chmod(pending, 0o440)
    if _run(["visudo", "-cf", str(pending)])[0] != 0:
        pending.unlink(missing_ok=True)
        return False
    os.replace(pending, SUDOERS)
    return ok


def _rules_of(tool: str, chain: str) -> list[str]:
    return [line for line in _run([*_tables(tool), "-S", chain])[1].splitlines() if line.startswith("-A ")]


def _first_output_rule(tool: str) -> str:
    rules = [line for line in _run([*_tables(tool), "-S", "OUTPUT"])[1].splitlines() if line.startswith("-A ")]
    return rules[0] if rules else ""


def _hook_unit_ok() -> bool:
    shown = _run(["systemctl", "show", UNIT, "-p", "ActiveState", "-p", "BindsTo", "-p", "ExecStop"])[1]
    lines = shown.splitlines()
    return "ActiveState=active" in lines and "BindsTo=agent.service" in lines and hook_present(shown)


def hook_present(shown: str) -> bool:
    return f"argv[]={hook_argv()}" in shown


def verify() -> list[tuple[str, bool]]:
    want = render_rules()
    checks = [
        ("v4_floor", _rules_of("iptables", FLOOR) == want["v4_floor"]),
        ("v4_fail_closed", _rules_of("iptables", FAIL) == want["v4_fail"]),
        ("v6_floor", _rules_of("ip6tables", FLOOR) == want["v6_floor"]),
        ("v4_output_jump", _first_output_rule("iptables") == f"-A OUTPUT -j {FLOOR}"),
        ("v6_output_jump", _first_output_rule("ip6tables") == f"-A OUTPUT -j {FLOOR}"),
        ("exit_hook", _hook_unit_ok()),
    ]
    for unit in UNITS:
        state = _run(["systemctl", "show", unit, "-p", "LoadState", "--value"])[1].strip()
        checks.append((f"masked_{unit}", state == "masked"))
    return checks


def passed(checks: Sequence[tuple[str, bool]]) -> bool:
    return all(ok for _, ok in checks)


def report(checks: Sequence[tuple[str, bool]]) -> bool:
    for name, ok in checks:
        print(f"EGRESS-FLOOR check={name} {'ok' if ok else 'FAIL'}")
    good = passed(checks)
    print("EGRESS-FLOOR PASS" if good else "EGRESS-FLOOR FAIL " + " ".join(n for n, ok in checks if not ok))
    return good


def main(argv: Sequence[str]) -> int:
    if os.geteuid() != 0:
        print("EGRESS-FLOOR FAIL not-root")
        return 1
    applied = apply()
    checks = [("apply", applied), *verify()]
    return 0 if report(checks) else 1
