"""The verdict shell's egress list file, the probe workflow and the probe constants agree (PLAN-persona-verdict-shell-deny)."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.checks import _common
from scripts.checks.misc import validate_persona_verdict_egress as check
from scripts.personas import egress_probe

ROOT = Path(_common.ROOT)


def _config() -> dict:
    return yaml.safe_load((ROOT / check.LIST_FILE).read_text(encoding="utf-8"))


def test_the_live_tree_passes_check_tree() -> None:
    problems, examined = check.check_tree(ROOT)
    assert problems == [] and examined >= 1


def test_the_probe_workflow_filter_step_equals_the_files_filter() -> None:
    filt = _config()["filter"]
    workflow = yaml.safe_load((ROOT / check.PROBE).read_text(encoding="utf-8"))
    step0 = workflow["jobs"]["egress-probe"]["steps"][0]
    assert step0["uses"] == f"{filt['action']}@{filt['sha']}"
    assert f"# {filt['version']}" in (ROOT / check.PROBE).read_text(encoding="utf-8")


def test_the_probe_constants_equal_the_files_rows() -> None:
    cfg = _config()
    row = cfg["jobs"][check.PROBE]["egress-probe"]
    assert row == [f"{egress_probe.LISTED[0]}:{egress_probe.LISTED[1]}"]
    assert egress_probe.AGENT_SHA256 == cfg["filter"]["agent_sha256"]


def test_the_filter_supplied_snapshot_is_not_empty_or_stale_shaped() -> None:
    supplied = _config()["filter_supplied"]
    assert any(host == "github.com" for host in supplied)
    assert any(host.startswith("productionresultssa*") for host in supplied)
