"""Verb egress-fail-closed: the drill job's runner-side check (PLAN-persona-verdict-egress-floor).

Waits for the simulated revert to end the agent, then asks whether the job can still reach the listed host, a raw IP and
the Docker socket. With the floor's exit hook the runner is cut off and this step never finishes; without it the raw IP
is reached and the step fails visibly. Stdlib only.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Sequence

from scripts.personas import egress_probe

POLL_SECONDS = 180
SETTLE_SECONDS = 2
CASE_IDS = ("listed", "raw_ip_443", "docker_socket")


def agent_active() -> bool:
    try:
        done = subprocess.run(
            ["systemctl", "is-active", "agent.service"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.stdout.strip() == "active"


def main(argv: Sequence[str]) -> int:
    if not agent_active() or not egress_probe.attempt(egress_probe.BY_ID["listed"]):
        print("EGRESS-FAIL-CLOSED LATE")
        return 1
    for _ in range(POLL_SECONDS):
        if not agent_active():
            break
        time.sleep(1)
    else:
        print("EGRESS-FAIL-CLOSED NO-DRILL")
        return 1
    time.sleep(SETTLE_SECONDS)
    reached = []
    for case_id in CASE_IDS:
        got = egress_probe.attempt(egress_probe.BY_ID[case_id])
        print(f"EGRESS-FAIL-CLOSED case={case_id} got={'reached' if got else 'refused'}")
        if got:
            reached.append(case_id)
    print("EGRESS-FAIL-CLOSED PASS" if not reached else "EGRESS-FAIL-CLOSED FAIL " + " ".join(reached))
    return 0 if not reached else 1
