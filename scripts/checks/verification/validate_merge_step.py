"""Merge-step check (docs/contracts/git-ops.yaml approved_text_binding, resolves_trailer TRANSPORT).

For every plan whose implementation_declared flips in this diff:
  (T) outside push context, when the plan bundles recs and the declaration is COMMITTED, the union of
      the Resolves trailers over the branch commits (base..HEAD) must equal the bundled set exactly --
      a trailer only in the PR body never reaches the squash commit. A declaration present only in the
      working tree prints PENDING (the Commit Flow's --pre runs before the commit).
  (A) the approved-text sidecar docs/plans/approved/<slug>.yaml is read AT THE BASE: a build may not add,
      edit or delete it, and when present every bound file must carry exactly the approved words.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry
from scripts.rec_trailer import parse_resolves_trailer
from scripts.roadmap import approved_text

_REMEDY_ADD = "add a commit whose message carries: Resolves: {ids}"
_REMEDY_REWRITE = (
    "rewrite the trailer-carrying commit's message (`git commit --amend` when it is the last commit, else "
    "`git reset --soft <merge-base>` and recommit with the corrected trailer) and push with --force-with-lease"
)
_UNSET: Any = object()
_PLAN_PREFIX = "PLAN-"


def trailer_remedy(expected: list[str], found: list[str]) -> list[str]:
    """One remedy per failing arm: a missing rec, an extra rec (shared with merge_step premerge)."""
    remedies = []
    if set(expected) - set(found):
        remedies.append(_REMEDY_ADD.format(ids=", ".join(expected)))
    if set(found) - set(expected):
        remedies.append(_REMEDY_REWRITE)
    return remedies


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _committed_trailer_union(root: Path, base: str) -> list[str]:
    log = _git(root, "log", "--format=%B%x00", f"{base}..HEAD").stdout
    found: list[str] = []
    for message in log.split("\0"):
        for rec_id in parse_resolves_trailer(message):
            if rec_id not in found:
                found.append(rec_id)
    return found


def _declared_at_head(plan_rel: str, root: Path) -> bool:
    return _common._plan_declared_at_ref(plan_rel, root, "HEAD")


def _check_trailer(plan_rel: str, bundled: list[str], root: Path, base: str) -> str | None:
    if not _declared_at_head(plan_rel, root):
        print(f"  PENDING trailer: declaration not committed ({plan_rel})")
        return None
    found = _committed_trailer_union(root, base)
    if set(found) == set(bundled):
        return None
    remedies = "; ".join(trailer_remedy(bundled, found))
    return f"{plan_rel}: branch-commit Resolves trailers {found} != bundled_recommendations {bundled}; {remedies}"


def _check_sidecar(slug: str, root: Path, base: str) -> list[str]:
    state = approved_text.sidecar_state(slug, root, base)
    if state in ("added", "changed", "deleted"):
        rel = approved_text.sidecar_rel(slug)
        return [f"{rel}: sidecar {state} on the branch; an approval is read at the base, never edited by its own build"]
    if state == "absent":
        return []
    try:
        sidecar = approved_text.load_sidecar_at(base, slug, root)
    except approved_text.ApprovedTextError as exc:
        return [f"{approved_text.sidecar_rel(slug)}: {exc}"]
    return approved_text.verify(sidecar, root, base) if sidecar else []


@registry.register("validate_merge_step", owner="platform")
def validate_merge_step(
    failed: list[str],
    root: Path | None = None,
    base: str | None = None,
    changed: list[str] | None = None,
    push_base: Any = _UNSET,
) -> None:
    """Fail a declared plan whose committed trailer or approved text breaks the merge-step contract.
    `root`, `base`, `changed` and `push_base` are fixture seams; real dispatch derives all four."""
    print("\n=== Merge-step validation ===")
    root = root if root is not None else _common.ROOT
    diff_base = base or _common.status_diff_base(root) or "origin/main"
    changed_files = changed if changed is not None else _common.get_changed_files(root)
    in_push = (_common.push_context_base(root) if push_base is _UNSET else push_base) is not None
    plans = _common.resolve_declared_plans(changed_files, root, diff_base)
    errors: list[str] = []
    for plan_rel in plans:
        data = yaml.safe_load((root / plan_rel).read_text(encoding="utf-8")) or {}
        bundled = list(data.get("bundled_recommendations") or [])
        if not in_push and bundled:
            error = _check_trailer(plan_rel, bundled, root, diff_base)
            if error:
                errors.append(error)
        slug = Path(plan_rel).stem.removeprefix(_PLAN_PREFIX)
        errors.extend(f"{plan_rel}: approved text: {m}" for m in _check_sidecar(slug, root, diff_base))
    for error in errors:
        print(f"  FAIL: {error}")
    failed.extend(errors)
    if not errors:
        print(f"  PASS: {len(plans)} declared plan(s) examined.")
    registry.examined(len(plans), unit="plans")
