"""Verb egress-revert-drill: keyless probe jobs only (PLAN-persona-verdict-egress-floor).

Lays the floor, then schedules the filter agent's own revert side effects and its exit two minutes later, so the drill
job shows what a mid-job revert does to a job that holds the floor's exit hook (cut off) and, in the control, to one
that does not. The revert order follows the agent's RevertChanges, firewall and DNS legs.
"""

from __future__ import annotations

from collections.abc import Sequence

from scripts.personas import egress_floor

DRILL_RESOLVER = "1.1.1.1"
DELAY_SECONDS = 120
UNIT = "egress-revert-drill"
REVERT = (
    "iptables -w -F OUTPUT; iptables -w -F DOCKER-USER; "
    f"printf '[Resolve]\\nDNS={DRILL_RESOLVER}\\nDomains=~.\\n' > /etc/systemd/resolved.conf; "
    "systemctl restart systemd-resolved; systemctl daemon-reload; systemctl restart docker; "
    "systemctl kill -s KILL agent.service"
    "; sleep 3; { date -u; "
    "systemctl show agent.service -p ActiveState -p SubState -p Result -p ExecStopPost -p ExecMainStatus; "
    "journalctl -u agent.service -n 20 --no-pager -o short-precise; "
    "journalctl -u egress-revert-drill -n 20 --no-pager -o short-precise; "
    "iptables -w -t mangle -S EGRESS_FAIL_CLOSED; iptables -w -t mangle -S OUTPUT; "
    "} > /run/egress-drill-diag.txt 2>&1; chmod 644 /run/egress-drill-diag.txt"
)


def schedule_argv() -> list[str]:
    return ["systemd-run", f"--on-active={DELAY_SECONDS}", f"--unit={UNIT}", "/bin/sh", "-c", REVERT]


def _hook_present() -> bool:
    shown = egress_floor._run(["systemctl", "show", "agent.service", "-p", "ExecStopPost"])[1]
    return f"argv[]={egress_floor.hook_argv()}" in shown


def arm(hooked: bool) -> int:
    applied = egress_floor.apply()
    checks = [("apply", applied), *egress_floor.verify()]
    if not egress_floor.report(checks):
        return 1
    if not hooked:
        egress_floor.DROPIN.unlink(missing_ok=True)
        egress_floor._run(["systemctl", "daemon-reload"])
        if _hook_present():
            print("EGRESS-DRILL FAIL exit hook still loaded")
            return 1
        print("EGRESS-DRILL UNHOOKED")
    if egress_floor._run(schedule_argv())[0] != 0:
        print("EGRESS-DRILL FAIL systemd-run refused")
        return 1
    print("EGRESS-DRILL ARMED")
    return 0


def main(argv: Sequence[str]) -> int:
    return arm(hooked=True)
