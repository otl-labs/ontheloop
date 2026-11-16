"""validate_approval_routing: gated wording needs a declared plan's sidecar (PLAN-wording-approval-routing).

Reads docs/contracts/approval-routing.yaml AT THE DIFF BASE, so a PR that creates the map never gates itself. Leg P
fails a gated change no declared plan's base sidecar binds; leg B fails a drafted mechanism Decision whose kind
cannot be trusted. Findings fail only in pull-request CI; elsewhere each prints ADVISORY.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from scripts.checks import _common, registry
from scripts.decisions_md import extract_entry_envelope, iter_decision_sections
from scripts.roadmap import approval_routing as ar
from scripts.roadmap import approved_text, plan_decision_drafts

_SIDECAR = re.compile(r"^docs/plans/approved/(.+)\.yaml$")
_PR_ONLY = "pull_request"


def _bound(declared: list[str], base: str, root: Path) -> set[str]:
    bound: set[str] = set()
    for plan_rel in declared:
        slug = Path(plan_rel).stem.removeprefix("PLAN-")
        try:
            sidecar = approved_text.load_sidecar_at(base, slug, root)
        except approved_text.ApprovedTextError:
            sidecar = None
        bound |= {b.file for b in sidecar.blocks} if sidecar else set()
    return bound


def _leg_p(m: dict, root: Path, base: str, changed: list[str]) -> list[str]:
    declared = _common.resolve_declared_plans(changed, root, base)
    bound = _bound(declared, base, root)
    findings: list[str] = []
    reasons: dict[str, list[str]] = {}
    for file, reason in ar.gated_changes(m, root, base, None, changed):
        reasons.setdefault(file, []).append(reason)
    for file, why in sorted(reasons.items()):
        if file in bound or ar.exempt(m, file, ar.text_at(root, base, file), ar.text_at(root, None, file)):
            continue
        findings.append(
            f"{file}: gated text changed ({'; '.join(why)}) with no declared plan's base sidecar binding it; route it "
            "through /plan and a sidecar, or the operator merges in the GitHub UI (Decision 83)"
        )
    _, failure = ar.reverted_files(m, root, base)
    return findings + ([f"revert detector failed: {failure}"] if failure else [])


def _kind_at_base(root: Path, base: str, number: int) -> str | None:
    for rel in ("docs/DECISIONS.md", "docs/DECISIONS_ARCHIVE.md"):
        text = ar.text_at(root, base, rel) or ""
        for heading, block in iter_decision_sections(text):
            if int(heading.group(1)) == number:
                kind = (extract_entry_envelope(block) or {}).get("kind")
                return kind.get("value") if isinstance(kind, dict) else None
    return None


def _blind_verdict(plan: dict) -> str | None:
    rows = [r for r in plan.get("gate_rounds") or [] if isinstance(r, dict) and r.get("gate") == "kind-blind"]
    return rows[-1].get("verdict") if rows else None


def _leg_b_slug(slug: str, root: Path, base: str) -> list[str]:
    plan_rel = f"docs/plans/PLAN-{slug}.yaml"
    entries = plan_decision_drafts.drafted_entries(root / plan_rel, root)
    try:
        plan = yaml.safe_load((root / plan_rel).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        plan = {}
    verdict = _blind_verdict(plan) if isinstance(plan, dict) else None
    findings: list[str] = []
    for entry in (e for e in entries if e["kind"] == "mechanism"):
        title = entry["title"][:60]
        if len(entries) > 1:
            findings.append(f"{plan_rel}: mechanism draft '{title}' is not the plan's only drafted Decision")
        if verdict is None:
            findings.append(f"{plan_rel}: mechanism draft '{title}' has no kind-blind gate_rounds row")
        elif verdict != "mechanism":
            findings.append(f"{plan_rel}: mechanism draft '{title}' has a last kind-blind row reading {verdict}")
        for number in entry["amends"]:
            if _kind_at_base(root, base, number) in (None, "direction"):
                findings.append(
                    f"{plan_rel}: mechanism draft '{title}' amends Decision {number}, a direction or kind-less entry"
                )
    return findings


def _leg_b(root: Path, base: str, changed: list[str]) -> list[str]:
    findings: list[str] = []
    for rel in changed:
        found = _SIDECAR.match(rel)
        if found and approved_text.sidecar_state(found.group(1), root, base) in ("added", "changed"):
            findings += _leg_b_slug(found.group(1), root, base)
    return findings


@registry.register("validate_approval_routing", owner="platform")
def validate_approval_routing(
    failed: list[str],
    root: Path | None = None,
    base: str | None = None,
    changed: list[str] | None = None,
    event: str | None = None,
) -> None:
    """Fail, in pull-request CI, gated wording no declared plan's base sidecar binds (docs/contracts/approval-routing.yaml)."""
    print("\n=== Approval routing (gated wording needs a declared plan's sidecar) ===")
    root = root if root is not None else _common.ROOT
    if _common.push_context_base(root) is not None:
        registry.skipped("push context")
        print("  SKIP: push context (the pull-request run is the gate)")
        return
    base = base or _common.status_diff_base(root) or "origin/main"
    text = ar.text_at(root, base, ar.MAP_REL)
    m = ar.parse_map(text)
    if text is not None and m is None:
        findings = [f"{ar.MAP_REL} at {base} does not parse (fail closed)"]
    elif m is None or not ar.active(m):
        print(f"  inert: lane not on sampled at {base}")
        registry.skipped(f"approval-routing map absent or lane not on sampled at {base}")
        return
    else:
        files = changed if changed is not None else [p for _, p in _common.get_status_aware_diff(root)]
        registry.examined(len(files), unit="changed files")
        findings = _leg_p(m, root, base, files) + _leg_b(root, base, files)
    enforcing = (os.environ.get("GITHUB_EVENT_NAME") if event is None else event) == _PR_ONLY
    for finding in findings:
        print(f"  {'FAIL' if enforcing else 'ADVISORY'}: {finding}")
    if findings and enforcing:
        failed.append("Approval routing")
    elif not findings:
        print("  PASS: no gated change lacks a binding sidecar")
