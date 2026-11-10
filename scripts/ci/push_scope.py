#!/usr/bin/env python3
"""Push scope classifier for ci.yml's main-validate job (Decision 205 cl.5, rec-4114).

A scope classifier, not a diff base: --pre's own diff base stays scripts/checks/_common.py push_context_base
(Decision 159 cl.1). A push to main whose diff is confined to docs/plans/ runs the fast tier; every doubtful input
runs the full tier (fail closed, Decision 170). PLAN_PREFIX is shared with scripts/ops_portal/trailer_closure_gate.py's
merge_leg_refuses (opposite polarity on an empty diff), pinned equal by test_prefix_matches_trailer_gate rather than
imported, because the gate module is reached by gated --pre Entries whose pre_globs would otherwise need this file
added in four manifests.

Stdlib only at import so `python3 -I scripts/ci/push_scope.py classify` runs before setup-python.
"""

from __future__ import annotations

import os
import subprocess
import sys

PLAN_PREFIX = "docs/plans/"
_ZERO_SHA = "0" * 40


def confined_to_plans(changed: set[str] | None) -> bool:
    if not changed:
        return False
    return all(path.startswith(PLAN_PREFIX) for path in changed)


def _git(cwd: str | None, *args: str) -> tuple[int, str]:
    result = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    return result.returncode, result.stdout


def changed_paths(before: str, after: str, cwd: str | None = None) -> set[str] | None:
    before = (before or "").strip()
    after = (after or "").strip()
    if not before or set(before) == {"0"} or not after:
        return None
    for sha in (before, after):
        if _git(cwd, "cat-file", "-e", f"{sha}^{{commit}}")[0] != 0:
            return None
    rc, out = _git(cwd, "diff", "--name-only", "--no-renames", before, after)
    if rc != 0:
        return None
    return {line for line in out.splitlines() if line.strip()}


def classify(before: str, after: str, forced: str, cwd: str | None = None) -> tuple[bool, str]:
    if (forced or "").strip().lower() == "true":
        return False, "forced push"
    changed = changed_paths(before, after, cwd)
    if changed is None:
        return False, "before is zero or unresolvable, or git failed"
    if not confined_to_plans(changed):
        return False, "diff is empty or reaches outside " + PLAN_PREFIX
    return True, f"{len(changed)} path(s), all under {PLAN_PREFIX}"


def _emit(plans_only: bool, reason: str) -> None:
    value = "true" if plans_only else "false"
    print(f"push_scope: plans_only={value} ({reason})")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(f"plans_only={value}\n")


def main(argv: list[str]) -> int:
    if argv[1:2] != ["classify"]:
        print("usage: push_scope.py classify", file=sys.stderr)
        return 0
    try:
        plans_only, reason = classify(os.environ.get("BEFORE", ""), os.environ.get("AFTER", ""), os.environ.get("FORCED", ""))
    except Exception as exc:  # fail closed to the full tier
        plans_only, reason = False, f"error: {exc!r}"
    try:
        _emit(plans_only, reason)
    except Exception as exc:
        print(f"push_scope: plans_only=false (emit error: {exc!r})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
