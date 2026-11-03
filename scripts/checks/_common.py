"""Sole source of the shared primitives every extracted check depends on (Decision 104).

Extracted check modules reference these via the module object (``_common.run``,
``_common.ROOT``, etc.) rather than importing the bare names, so a single patch
target (``scripts.checks._common.run`` / ``.ROOT`` / ...) intercepts every moved
body. No scripts/checks module may recompute ROOT locally.

Has no dependency on scripts.validate (avoids an import cycle: validate.py
imports from scripts.checks.*, so scripts.checks.* must not import validate.py
at module scope).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
PYTHON = sys.executable  # Use same interpreter that's running this script

PLAN_PATH_RE = re.compile(r"^docs/plans/PLAN-([^/]+)\.yaml$")
_ZERO_SHA = "0" * 40


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, **kwargs)


def invoke_step(name: str, cmd: list[str], failed: list[str], cwd: Path | None = None) -> None:
    print(f"\n=== {name} ===")
    result = run(cmd, cwd=cwd or ROOT)
    if result.returncode != 0:
        failed.append(name)


def push_context_base(root: Path | None = None) -> str | None:
    """Return the diff base for a POST-MERGE (push-context) validation run, else None.

    Push context is GITHUB_EVENT_NAME == "push", OR (current branch == "main" AND
    merge-base(origin/main, HEAD) == HEAD). The branch-name conjunct is load-bearing:
    merge-base == HEAD alone also matches every fresh harness session branch before its
    first commit, which would otherwise pull the previous merge's files into every
    session-start --pre run.

    In push context, prefers GITHUB_EVENT_BEFORE when it names a non-zero commit present
    locally, else HEAD~1. Returns None (with a loud warning) when HEAD~1 does not resolve
    (root/shallow checkout) so callers fall back to their own existing base instead of
    silently mis-scoping.

    Outside push context, always returns None -- each consumer then keeps its own current
    base unchanged (Decision 135 pt 3: contract unchanged for existing callers).

    `root` scopes every git probe (cwd defaults to `ROOT` when None, so existing callers
    are byte-identical); the GITHUB_EVENT_NAME / GITHUB_EVENT_BEFORE env signal stays
    job-global regardless of `root` (Decision 159 cl.1 amendment).
    """
    cwd = root if root is not None else ROOT
    is_push_event = os.environ.get("GITHUB_EVENT_NAME") == "push"
    on_main = False
    if not is_push_event:
        branch_result = run(["git", "branch", "--show-current"], capture_output=True, text=True, encoding="utf-8", cwd=cwd)
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
        if branch == "main":
            merge_base_result = run(
                ["git", "merge-base", "origin/main", "HEAD"], capture_output=True, text=True, encoding="utf-8", cwd=cwd
            )
            head_result = run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, encoding="utf-8", cwd=cwd)
            on_main = (
                merge_base_result.returncode == 0
                and head_result.returncode == 0
                and bool(merge_base_result.stdout.strip())
                and merge_base_result.stdout.strip() == head_result.stdout.strip()
            )

    if not (is_push_event or on_main):
        return None

    event_before = os.environ.get("GITHUB_EVENT_BEFORE", "").strip()
    if event_before and event_before != _ZERO_SHA:
        exists_result = run(["git", "cat-file", "-e", event_before], capture_output=True, text=True, encoding="utf-8", cwd=cwd)
        if exists_result.returncode == 0:
            return event_before

    head_tilde_result = run(
        ["git", "rev-parse", "--verify", "-q", "HEAD~1"], capture_output=True, text=True, encoding="utf-8", cwd=cwd
    )
    if head_tilde_result.returncode == 0 and head_tilde_result.stdout.strip():
        return head_tilde_result.stdout.strip()

    print(
        "WARNING: push_context_base() detected push context but HEAD~1 does not resolve "
        "(root/shallow checkout) -- falling back to the caller's existing behaviour.",
        file=sys.stderr,
    )
    return None


def status_diff_base(root: Path | None = None) -> str | None:
    """The diff base `get_status_aware_diff` resolves against: `push_context_base(root)`, else
    the merge-base of origin/main and HEAD, else None (Decision 159: no second resolver -- every
    status-aware-diff caller shares this one base-resolution path rather than recomputing its
    own merge-base).

    `root` scopes every git probe (cwd defaults to `ROOT` when None).
    """
    cwd = root if root is not None else ROOT
    push_base = push_context_base(root)
    if push_base is not None:
        return push_base
    merge_base_result = run(
        ["git", "merge-base", "origin/main", "HEAD"], capture_output=True, text=True, encoding="utf-8", cwd=cwd
    )
    if merge_base_result.returncode == 0 and merge_base_result.stdout.strip():
        return merge_base_result.stdout.strip()
    return None


def get_changed_files(root: Path | None = None) -> list[str]:
    """Get files changed vs origin/main, falling back to HEAD. Excludes deleted paths.

    `root` scopes every git subprocess and the existence filter (cwd/base defaults to
    `ROOT` when None, so existing callers are byte-identical).
    """
    cwd = root if root is not None else ROOT
    push_base = push_context_base(root)
    base = push_base if push_base is not None else "origin/main"
    result = run(["git", "diff", "--name-only", base], capture_output=True, text=True, encoding="utf-8", cwd=cwd)
    if result.returncode == 0:
        files = result.stdout.strip().splitlines()
    else:
        result = run(["git", "diff", "--name-only", "HEAD"], capture_output=True, text=True, encoding="utf-8", cwd=cwd)
        files = result.stdout.strip().splitlines()
    return [f for f in files if f and (cwd / f).exists()]


def get_status_aware_diff(root: Path | None = None) -> list[tuple[str, str]]:
    """Status-aware diff vs the origin/main merge-base, PLUS untracked new files.

    A NEW primitive alongside get_changed_files() (Decision affected-set-selection,
    amends Decision 73) -- it does not replace or change get_changed_files()'s own
    contract (deleted-path filtering) for that function's existing callers. This is the
    sole extra diff surface the live affected-set derivation
    (scripts/checks/deps/affected_tests.py) consumes.

    Returns a list of (status, path) tuples:
      - "A" / "M" for added/modified tracked paths (git diff --name-status --no-renames
        against the merge-base with origin/main, falling back to HEAD if the merge-base
        lookup fails) -- existence-filtered, like get_changed_files().
      - "D" for deleted tracked paths -- NEVER existence-filtered (a deleted path cannot
        exist on disk by definition; dropping it here would silently blind the Incident-B
        deleted-.py-bytes data-edge channel).
      - "??" for untracked new files (git ls-files --others --exclude-standard) --
        existence-filtered (rec-2638: local --pre under-checking of new, never-added
        files; a CI checkout only ever contains committed files, so this leg is
        primarily a local-session fix).

    --no-renames keeps the output to the plain A/M/D three-letter vocabulary (no R100
    two-path rename records) -- this function's callers reason about single-path status
    entries only.

    `root` scopes every git subprocess and the two existence filters (cwd/merge-base
    defaults to `ROOT` when None, so existing callers are byte-identical).
    """
    cwd = root if root is not None else ROOT
    entries: list[tuple[str, str]] = []

    base_ref = status_diff_base(root)

    diff_result = run(
        ["git", "diff", "--name-status", "--no-renames", base_ref or "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
    )
    if diff_result.returncode == 0:
        for line in diff_result.stdout.strip().splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) != 2:
                continue
            status_raw, path = parts[0].strip(), parts[1].strip()
            if not status_raw or not path:
                continue
            status = status_raw[0]
            if status not in ("A", "M", "D"):
                continue
            if status == "D" or (cwd / path).exists():
                entries.append((status, path))

    untracked_result = run(
        ["git", "ls-files", "--others", "--exclude-standard"], capture_output=True, text=True, encoding="utf-8", cwd=cwd
    )
    if untracked_result.returncode == 0:
        for line in untracked_result.stdout.strip().splitlines():
            path = line.strip()
            if path and (cwd / path).exists():
                entries.append(("??", path))

    return entries


def plan_paths_from_changed(changed_files: list[str]) -> list[str]:
    return sorted(f for f in changed_files if PLAN_PATH_RE.match(f))


def load_plan(rel_path: str, root: Path):
    """Load a PlanDocument via scripts.roadmap.plan_document.load(), injecting repo root onto sys.path."""
    root_str = str(root)
    import sys as _sys  # noqa: PLC0415

    injected = root_str not in _sys.path
    if injected:
        _sys.path.insert(0, root_str)
    try:
        from scripts.roadmap.plan_document import load  # noqa: PLC0415

        return load(root / rel_path)
    finally:
        if injected and root_str in _sys.path:
            _sys.path.remove(root_str)


def _plan_declared_at_ref(plan_rel: str, root: Path, ref: str) -> bool:
    """Whether `implementation_declared` reads true for `plan_rel` at git ref `ref`.

    Mirrors check_graduation_guard.py:98-105's `git show` + returncode-!=0-is-empty-baseline
    rule: an unresolvable ref, a missing path at that ref, or an unparseable/non-dict payload
    are all a legitimate "not declared at this ref" rather than an error -- never raised.
    """
    result = run(["git", "show", f"{ref}:{plan_rel}"], capture_output=True, text=True, encoding="utf-8", cwd=root)
    if result.returncode != 0:
        return False
    try:
        data = yaml.safe_load(result.stdout)
    except yaml.YAMLError:
        return False
    return isinstance(data, dict) and bool(data.get("implementation_declared"))


def resolve_declared_plans(changed_files: list[str], root: Path, base: str) -> list[str]:
    """Content-keyed plan resolution (Decision 148 sole home; Decision 170's declaration
    channel is the consumer, not this function).

    Returns the diff-present `docs/plans/PLAN-*.yaml` paths whose `implementation_declared`
    field is true in the WORKING TREE but was not true at `base` -- an edge-triggered
    False->True flip (absence at `base` counts as False, so a net-new declared plan resolves
    too), mirroring check_graduation_guard.py:111-116's flip detector.

    `base` is an injected parameter, never derived here (callers default it from
    `push_context_base() or "origin/main"` at their own call site -- this function must never
    call push_context_base() itself, or a check running against an injected `root` would read
    the REAL repository's base while evaluating the FIXTURE repository). The candidate set is
    likewise derived from the injected `changed_files` via `plan_paths_from_changed` -- this
    function never calls get_changed_files() or globs docs/plans/ itself.

    A candidate absent from the working tree (deleted in this diff) is silently skipped -- that
    is the caller's concern (each check already prints its own "deleted in diff" SKIP), not an
    error here. A candidate PRESENT on disk is read with a raw `yaml.safe_load`, never
    `PlanDocument` validation (schema validity is `validate_plan_documents`' concern, not this
    resolver's) -- an `OSError` or `yaml.YAMLError` on that WORKING-TREE read propagates
    (fail-loud, Decision 55). The baseline read never raises (see `_plan_declared_at_ref`).
    """
    resolved: list[str] = []
    for plan_rel in plan_paths_from_changed(changed_files):
        plan_path = root / plan_rel
        if not plan_path.exists():
            continue
        working_data = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        if not (isinstance(working_data, dict) and working_data.get("implementation_declared")):
            continue
        if not _plan_declared_at_ref(plan_rel, root, base):
            resolved.append(plan_rel)
    return resolved


def origin_main_reachable(root: Path) -> bool:
    result = run(
        ["git", "rev-parse", "--verify", "-q", "origin/main"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=root,
    )
    return result.returncode == 0
