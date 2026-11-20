"""Verb egress-self-check: the behavioural check a covered job runs before any step that can reach a credential.

The filter step can pass while blocking nothing, so this refuses to continue unless the running agent is the read
agent binary (checked first, so a job under another binary fails before any other step) and an unlisted name, a
resolver outside the filter, a raw-IP connect, IPv6 and sudo are all refused. Stdlib only.
"""

from __future__ import annotations

from collections.abc import Sequence

from scripts.personas import egress_probe

SELF_CHECK_IDS = ("agent_binary", "unlisted_name", "dns_txt_system", "raw_ip_443", "ipv6", "sudo")


def main(argv: Sequence[str]) -> int:
    results: list[egress_probe.Result] = []
    for case_id in SELF_CHECK_IDS:
        results.extend(egress_probe.run_cases([egress_probe.BY_ID[case_id]], {case_id: "refused"}))
    egress_probe.emit("EGRESS-SELF-CHECK", results)
    bad = egress_probe.failures(results)
    print("EGRESS-SELF-CHECK PASS" if not bad else "EGRESS-SELF-CHECK FAIL " + " ".join(bad))
    return 0 if not bad else 1
