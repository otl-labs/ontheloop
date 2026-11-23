"""rec-5362 acceptance on the live tree: the Azure openings and the mid-job fail-open are closed for every covered job."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.checks import _common
from scripts.personas import egress_floor, egress_probe, egress_self_check

ROOT = Path(_common.ROOT)
EGRESS_ROWS = ROOT / "config/persona_verdict_egress.yaml"
FLOOR_PREFIXES = ("sudo python3 -I ", 'sudo python3 -I "$GITHUB_WORKSPACE/main/')


def _covered() -> list[tuple[str, str, list[dict[str, object]]]]:
    rows = yaml.safe_load(EGRESS_ROWS.read_text(encoding="utf-8"))["jobs"]
    out = []
    for rel, jobs in rows.items():
        doc = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
        out += [(rel, name, doc["jobs"][name]["steps"]) for name in jobs]
    return out


def test_every_covered_job_turns_the_filter_switch_off_and_runs_the_floor_then_the_self_check() -> None:
    covered = _covered()
    assert covered
    for rel, name, steps in covered:
        assert str(steps[0]["with"]["disable-sudo-and-containers"]).lower() == "false", (rel, name)
        runs = [str(s.get("run", "")) for s in steps]
        floors = [i for i, r in enumerate(runs) if r.startswith("sudo ") and "egress-" in r]
        assert len(floors) == 1, (rel, name)
        assert all("checkout" in str(s.get("uses", "")) for s in steps[1 : floors[0]]), (rel, name)
        last = steps[-1]
        assert str(last["run"]).endswith("egress-self-check") and last["if"] == "always()", (rel, name)


def test_the_floor_drops_the_azure_metadata_private_and_ipv6_addresses_for_non_root() -> None:
    rules = egress_floor.render_rules()
    dropped = [r.split()[3] for r in rules["v4_floor"] if r.endswith("-j DROP")]
    assert dropped == ["168.63.129.16/32", "169.254.0.0/16", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
    assert rules["v6_floor"][-1] == "-A EGRESS_FLOOR -j DROP" and "--uid-owner 0" in rules["v6_floor"][1]
    assert egress_floor.dropin_text().rstrip().endswith("-A EGRESS_FAIL_CLOSED -j DROP")


def test_the_probe_and_the_self_check_expect_the_floors_cases_refused() -> None:
    for case_id in ("dns_tcp_azure", "wireserver_http", "imds", "ipv6", "sudo", "docker_socket"):
        assert case_id in egress_self_check.SELF_CHECK_IDS
        assert case_id in egress_probe.BY_ID
    assert egress_self_check.SELF_CHECK_IDS[:2] == ("agent_binary", "default_iface")


def test_the_drill_jobs_have_rows_in_the_egress_list() -> None:
    rows = yaml.safe_load(EGRESS_ROWS.read_text(encoding="utf-8"))["jobs"][
        ".github/workflows/persona-verdict-egress-probe.yml"
    ]
    assert rows["egress-fail-closed"] == ["sts.amazonaws.com:443"]
    assert rows["egress-fail-closed-control"] == ["sts.amazonaws.com:443"]
