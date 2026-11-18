"""Verb egress-control: the unfiltered control job (PLAN-persona-verdict-shell-deny).

Every refusal the filtered probe shows must be the filter's, so each probe case except ipv6, dns_tcp_azure and
agent_binary is expected reached here (ipv6 and dns_tcp_azure are reported, not asserted; agent_binary is skipped: no
agent runs here). It then fetches the vendor's host list, normalises it as config/persona_verdict_egress.yaml's
filter_supplied is, and fails if the vendor's list holds a host the file's snapshot lacks.
"""

from __future__ import annotations

import json
import re
import urllib.request
from collections.abc import Iterable, Sequence
from pathlib import Path

import yaml

from scripts.personas import egress_probe

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "persona_verdict_egress.yaml"
VENDOR_LIST = "https://agent.api.stepsecurity.io/v1/github/meta"
STORE_NAME = re.compile(r"^productionresultssa\d+(\.blob\.core\.windows\.net)$")
REPORTED = ("ipv6", "dns_tcp_azure")
SKIPPED = ("agent_binary",)


def wildcard_matches(pattern: str, target: str) -> bool:
    prefix, _, suffix = pattern.partition("*")
    return target.startswith(prefix) and target.endswith(suffix) and len(target) >= len(prefix) + len(suffix)


def normalise(hosts: Iterable[str], hard_coded: Iterable[str] = ()) -> list[str]:
    folded = {STORE_NAME.sub(r"productionresultssa*\1", host.strip().lower().rstrip(".")) for host in hosts if host.strip()}
    wildcards = {host for host in folded | set(hard_coded) if "*" in host}
    kept = {host for host in folded if "*" in host or not any(wildcard_matches(w, host) for w in wildcards)}
    return sorted(kept)


def fetch_vendor_hosts() -> list[str]:
    with urllib.request.urlopen(VENDOR_LIST, timeout=15) as response:  # noqa: S310
        body = json.load(response)
    return [str(host) for host in body["domains"]["actions"]]


def vendor_widening(served: Iterable[str], snapshot: Iterable[str]) -> list[str]:
    known = list(snapshot)
    wildcards = [host for host in known if "*" in host]
    return [
        host
        for host in normalise(served, known)
        if host not in known and not any(wildcard_matches(w, host) for w in wildcards)
    ]


def main(argv: Sequence[str]) -> int:
    cases = [case for case in egress_probe.CASES if case[0] not in SKIPPED]
    expect = {case[0]: "reported" if case[0] in REPORTED else "reached" for case in cases}
    results = egress_probe.run_cases(cases, expect)
    egress_probe.emit("EGRESS-CONTROL", results)
    bad = egress_probe.failures(results)
    try:
        snapshot = [str(host) for host in yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filter_supplied"]]
        wider = vendor_widening(fetch_vendor_hosts(), snapshot)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f"EGRESS-CONTROL VENDOR-LIST fetch-failed: {type(exc).__name__}")
        bad.append("vendor_list")
    else:
        print("EGRESS-CONTROL VENDOR-LIST ok" if not wider else "EGRESS-CONTROL VENDOR-LIST wider: " + " ".join(wider))
        bad.extend(["vendor_list"] if wider else [])
    print("EGRESS-CONTROL PASS" if not bad else "EGRESS-CONTROL FAIL " + " ".join(bad))
    return 0 if not bad else 1
