"""Actions capacity contract check (rec-5022, Decision 168): schema and cross-reference rules for
docs/contracts/actions-capacity.yaml.

Fails on: a parse or unknown-key error; a ledger row whose unit or source is outside the contract's closed sets; an analog
row without analog jobs or an assumed row without job_minutes_per_unit; a queue, analog or live_signal workflow path that
does not exist under .github/workflows/, or a job name (other than "*") the workflow does not define (the API reports the
display name, so a job declaring name: other than its id fails too); an item id that is neither rec-NNNN nor a tier_item
in the roadmap or its archive; a slot_demand threshold at or above the cap; duplicate ledger ids; an alarm source not
registered in config/agent/data_quality/source_registry.yaml.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from scripts.checks import _common, registry
from scripts.ci.actions_capacity_contract import CONTRACT_BASENAME, Contract, JobRef, load_contract

_CHECK_LABEL = "Actions capacity contract"
_WORKFLOW_DIR = ".github/workflows/"
_ROADMAPS = ("docs/ROADMAP-PLATFORM.yaml", "docs/ROADMAP-PLATFORM-ARCHIVE.yaml")


def _item_ids(root: Path) -> set[str]:
    ids: set[str] = set()
    for rel in _ROADMAPS:
        path = root / rel
        if not path.is_file():
            continue
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        ids |= {str(i["id"]) for i in doc.get("tier_items", []) if isinstance(i, dict) and "id" in i}
    return ids


def _job_refs(contract: Contract) -> list[tuple[str, JobRef]]:
    refs = [("queue_p90.job", contract.trigger.queue_p90.job)]
    for row in contract.ledger.rows:
        refs += [(f"ledger {row.id} analog", a) for a in row.analog or []]
        if row.live_signal is not None:
            refs.append((f"ledger {row.id} live_signal", row.live_signal))
    return refs


def _check_job(where: str, ref: JobRef, root: Path) -> list[str]:
    if not ref.workflow.startswith(_WORKFLOW_DIR) or not (root / ref.workflow).is_file():
        return [f"{where}: workflow {ref.workflow} does not exist under {_WORKFLOW_DIR}"]
    if ref.job == "*":
        return []
    try:
        jobs = (yaml.safe_load((root / ref.workflow).read_text(encoding="utf-8")) or {}).get("jobs") or {}
    except yaml.YAMLError as exc:
        return [f"{where}: {ref.workflow} is not parseable: {exc}"]
    if ref.job not in jobs:
        return [f"{where}: workflow {ref.workflow} defines no job {ref.job!r}"]
    declared = (jobs[ref.job] or {}).get("name")
    if declared is not None and declared != ref.job:
        reason = f"declares name {declared!r}; the API reports the display name, so ids and names must match"
        return [f"{where}: job {ref.job!r} {reason}"]
    return []


def _registered_sources(root: Path) -> set[str]:
    path = root / "config" / "agent" / "data_quality" / "source_registry.yaml"
    entries = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("entries", [])
    return {str(e.get("canonical_id")) for e in entries if isinstance(e, dict)}


def _findings(contract: Contract, root: Path) -> list[str]:
    findings: list[str] = []
    if contract.trigger.slot_demand.threshold >= contract.cap.concurrent_jobs:
        findings.append("trigger.slot_demand.threshold is at or above cap.concurrent_jobs")
    ids = [r.id for r in contract.ledger.rows]
    findings += [f"duplicate ledger id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    known = _item_ids(root)
    for row in contract.ledger.rows:
        if row.unit not in contract.ledger.units:
            findings.append(f"ledger {row.id}: unit {row.unit!r} is outside ledger.units")
        if row.source not in contract.ledger.sources:
            findings.append(f"ledger {row.id}: source {row.source!r} is outside ledger.sources")
        if row.source == "analog" and not row.analog:
            findings.append(f"ledger {row.id}: analog row without analog jobs")
        if row.source == "assumed" and row.job_minutes_per_unit is None:
            findings.append(f"ledger {row.id}: assumed row without job_minutes_per_unit")
        findings += [
            f"ledger {row.id}: item {i!r} is neither rec-NNNN nor a roadmap tier_item"
            for i in row.items
            if not re.fullmatch(r"rec-\d+", i) and i not in known
        ]
    for where, ref in _job_refs(contract):
        findings += _check_job(where, ref, root)
    if contract.alarm.source not in _registered_sources(root):
        findings.append(f"alarm source {contract.alarm.source!r} is not registered in source_registry.yaml")
    return findings


@registry.register("validate_actions_capacity", owner="platform")
def validate_actions_capacity(failed: list[str], root: Path | None = None) -> None:
    """Fail if docs/contracts/actions-capacity.yaml violates its schema or any cross-reference rule."""
    print(f"\n=== Actions capacity contract ({CONTRACT_BASENAME}) ===")
    base = root if root is not None else _common.ROOT
    try:
        contract = load_contract(base)
        findings = _findings(contract, base)
    except (OSError, yaml.YAMLError, ValidationError, KeyError, TypeError, ValueError) as exc:
        failed.append(_CHECK_LABEL)
        print(f"  FAIL: {base / 'docs' / 'contracts' / CONTRACT_BASENAME}: {exc}")
        registry.skipped(f"{CONTRACT_BASENAME} not loadable")
        return
    if findings:
        failed.append(_CHECK_LABEL)
        for finding in findings:
            print(f"  FAIL: {finding}")
    else:
        print(f"  PASS: {CONTRACT_BASENAME} satisfies every rule ({len(contract.ledger.rows)} ledger rows).")
    registry.examined(len(contract.ledger.rows), unit="ledger_rows")
