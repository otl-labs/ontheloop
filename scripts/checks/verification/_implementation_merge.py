"""Late-closure resolver shared by validate_merge_step, validate_verification_registry and
scripts.session.merge_step (docs/contracts/git-ops.yaml resolves_trailer LATE CLOSURE).

A rec-bundling plan's closure (implementation_declared plus its graduated registry shards) may land in its
own PR after the implementation PR has squash-merged. The implementation merge X is read from git alone;
there is no plan field naming it. A closure-only change with a resolved X is the sole evidence-bound route.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common
from scripts.ops_portal import closure_gate, trailer_closure_gate
from scripts.rec_trailer import parse_resolves_trailer
from scripts.roadmap import approved_text

_ENTRIES_PREFIX = "config/agent/verification_registry/entries/"
_PLAN_RE = _common.PLAN_PATH_RE


class LateClosureError(Exception):
    """Two or more commits qualify as the plan's implementation merge (fail closed)."""


@dataclass(frozen=True)
class ImplementationMerge:
    sha: str
    parent: str


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
        )
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def _parse_plan(text: str | None) -> dict[str, Any] | None:
    if text is None:
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _plan_at(plan_rel: str, root: Path, ref: str) -> dict[str, Any] | None:
    return _parse_plan(_git(root, "show", f"{ref}:{plan_rel}"))


def _plan_on_disk(plan_rel: str, root: Path) -> dict[str, Any] | None:
    try:
        return _parse_plan((root / plan_rel).read_text(encoding="utf-8"))
    except OSError:
        return None


def _bundled(plan: dict[str, Any] | None) -> list[str]:
    return [str(r) for r in ((plan or {}).get("bundled_recommendations") or [])]


def _scope_files(plan: dict[str, Any]) -> set[str]:
    return {s["file"] for s in plan.get("scope") or [] if isinstance(s, dict) and isinstance(s.get("file"), str)}


def graduation_check_ids(plan: dict[str, Any] | None) -> set[str]:
    steps = (plan or {}).get("verification_plan") or []
    return {s["graduation_check_id"] for s in steps if isinstance(s, dict) and s.get("graduation_check_id")}


def find_implementation_merge(plan_rel: str, root: Path, ref: str) -> ImplementationMerge | None:
    """The plan's unique implementation merge at `ref`, or None. Raises LateClosureError on >1 survivors."""
    plan = _plan_at(plan_rel, root, ref)
    bundled = set(_bundled(plan))
    if plan is None or not bundled:
        return None
    added = (_git(root, "log", "--first-parent", "--diff-filter=A", "--format=%H", ref, "--", plan_rel) or "").split()
    if not added:
        return None
    scope = _scope_files(plan) - {plan_rel}
    log = _git(root, "log", "--first-parent", "--format=%H%x01%B%x00", f"{added[-1]}..{ref}")
    survivors: list[str] = []
    for record in (log or "").split("\0"):
        sha, _, message = record.strip().partition("\x01")
        if not sha or set(parse_resolves_trailer(message)) != bundled:
            continue
        if _common._plan_declared_at_ref(plan_rel, root, sha):
            continue
        changed = closure_gate.changed_files(sha, root)
        if trailer_closure_gate.merge_leg_refuses(changed) or not (changed or set()) & scope:
            continue
        survivors.append(sha)
    if not survivors:
        return None
    if len(survivors) > 1:
        raise LateClosureError(f"{plan_rel}: competing implementation merges {', '.join(survivors)}")
    sha = survivors[0]
    parent = (_git(root, "rev-parse", f"{sha}^") or "").strip()
    return ImplementationMerge(sha=sha, parent=parent) if parent else None


def _changed(root: Path, base: str, head: str | None) -> set[str] | None:
    if head is not None:
        out = _git(root, "diff", "--name-only", f"{base}..{head}")
        return None if out is None else set(out.split())
    out = _git(root, "diff", "--name-only", base)
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "--", _ENTRIES_PREFIX)
    if out is None or untracked is None:
        return None
    return set(out.split()) | set(untracked.split())


def _only_flip(base_plan: dict[str, Any], head_plan: dict[str, Any]) -> bool:
    if head_plan.get("implementation_declared") is not True:
        return False
    return {k: v for k, v in base_plan.items() if k != "implementation_declared"} == {
        k: v for k, v in head_plan.items() if k != "implementation_declared"
    }


def late_closure_route(plan_rel: str, root: Path, base: str, head: str | None = None) -> ImplementationMerge | None:
    """X when base..head is a CLOSURE-ONLY change for `plan_rel`, else None.

    head=None is the working-tree mode: the flip is the caller's resolve_declared_plans result, the plan is read
    from disk and changed files are the working-tree diff plus untracked registry shards."""
    base_plan = _plan_at(plan_rel, root, base)
    head_plan = _plan_on_disk(plan_rel, root) if head is None else _plan_at(plan_rel, root, head)
    if base_plan is None or head_plan is None or not _only_flip(base_plan, head_plan):
        return None
    if head is not None and _common._plan_declared_at_ref(plan_rel, root, base):
        return None
    changed = _changed(root, base, head)
    allowed = {f"{_ENTRIES_PREFIX}{cid}.yaml" for cid in graduation_check_ids(base_plan)} | {plan_rel}
    if not changed or not changed <= allowed:
        return None
    trailers = _git(root, "log", "--format=%B%x00", f"{base}..{head or 'HEAD'}")
    if trailers is None or any(parse_resolves_trailer(m) for m in trailers.split("\0")):
        return None
    if not _bundled(base_plan):
        return None
    match = _PLAN_RE.match(plan_rel)
    if not match or approved_text.sidecar_state(match.group(1), root, base) != "absent":
        return None
    return find_implementation_merge(plan_rel, root, base)
