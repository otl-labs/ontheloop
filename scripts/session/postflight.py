#!/usr/bin/env python3
"""Post-session deterministic automation: validate, pre-commit-sanity, commit, push, merge.

Thin CLI facade over the scripts/postflight package (SLOC decomposition, PLAN-sloc-session-postflight).
Replaces pre-commit-sanity.agent.md and session_close.prompt.md automation.

Modes:
    --validate           Run scripts/validate.py and return exit code
    --pre-commit-sanity  Check branch, scope, orphaned TODOs; output JSON
    --commit "message"   Run git add + commit with pre-commit retry (max 3)
    --push               Push, create PR, poll CI, auto-merge; output JSON
    --metrics            Run session/metrics.py + roadmap/plan_audit.py; return combined output
    --close              Pre-commit sanity and diff summary; output JSON
    --log-housekeeping   Commit and push uncommitted JSONL log files
    --auto "message"     Full session close in one command: close -> evidence -> metrics ->
                          commit -> rebase -> validate -> verify-head -> push -> portal sync
                          (PLAN-handoff-validates-committed-tree: the full tier now runs on the
                          committed, rebased, clean tree, so its evidence attests HEAD)

Usage:
    python scripts/session/postflight.py --validate
    python scripts/session/postflight.py --pre-commit-sanity
    python scripts/session/postflight.py --commit "feat: implement something"
    python scripts/session/postflight.py --push
    python scripts/session/postflight.py --metrics
    python scripts/session/postflight.py --close
    python scripts/session/postflight.py --log-housekeeping
    python scripts/session/postflight.py --auto "feat: implement something" --steps-total 10 --steps-friction 2
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # noqa: F401  (kept: facade byte-stable namespace surface, mirrors decision-80/104 precedent)
import sys
import time  # noqa: F401  (kept so session.postflight.time.{sleep,time} navigation patches still resolve)
from pathlib import Path

# Bootstrap the repo root onto sys.path BEFORE importing the scripts.postflight package: run_close
# self-reinvokes this file by FILE PATH (`scripts/session/postflight.py --pre-commit-sanity`, not
# `-m scripts.session.postflight`), and a direct file-path invocation puts sys.path[0] at this
# file's own directory (scripts/), not the repo root -- without this, `from scripts.postflight
# import ...` would fail with ModuleNotFoundError under that invocation mode.
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.postflight import _common, housekeeping, remote  # noqa: E402

# Facade re-exports (Decision 80/104 pattern): every public function and every test-referenced
# private symbol from the pre-decomposition module, so `from scripts.session.postflight import X`
# and getattr(session_postflight, X) keep resolving. Consolidated in ONE block per source module
# (tests/CLAUDE.md -- ruff format silently drops symbols from a second block of the same module's
# imports).
from scripts.postflight._common import (  # noqa: E402, F401
    _PRUNE_SKIP_NAMES,
    _SSO_PROFILE,
    ARCHIVE_DIR,
    CI_POLL_INTERVAL_SECONDS,
    CI_POLL_TIMEOUT_SECONDS,
    DEFAULT_MAX_AGE_DAYS,
    LOGS_DIR,
    MAX_COMMIT_RETRIES,
    PYTHON,
    ROOT,
    _current_branch,
    _run,
    clear_checkpoint,
    find_plan_file,
    logger,
)
from scripts.postflight.housekeeping import (  # noqa: E402, F401
    _load_max_age_days,
    _stage_document_derived_tables,
    prune_telemetry_logs,
    run_log_housekeeping,
    run_metrics,
)
from scripts.postflight.remote import run_push  # noqa: E402, F401
from scripts.session import postflight_evidence  # noqa: E402
from scripts.telemetry_capture.status_line import emit_status  # noqa: E402


def _parse_scope_table(plan_content: str) -> dict[str, str]:
    scope_match = re.search(r"## Scope\s*\n(.*?)(?=\n##|\Z)", plan_content, re.DOTALL)
    if not scope_match:
        return {}
    planned: dict[str, str] = {}
    for line in scope_match.group(1).splitlines():
        row_match = re.match(r"^\|\s*([^|]+?)\s*\|\s*(\w+)\s*\|", line)
        if row_match:
            file_path = row_match.group(1).strip().strip("`")
            action = row_match.group(2).strip()
            if re.match(r"^[-\s]*File[-\s]*$", file_path) or file_path == "File":
                continue
            planned[file_path] = action
    return planned


def _get_changed_files() -> list[str]:
    result = _common._run(["git", "diff", "--name-only", "origin/main"])
    if result.returncode == 0 and result.stdout.strip():
        return [f for f in result.stdout.strip().splitlines() if f]
    result = _common._run(["git", "diff", "--name-only", "HEAD"])
    return [f for f in result.stdout.strip().splitlines() if f] if result.returncode == 0 else []


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/")


def _paths_match(a: str, b: str) -> bool:
    na, nb = _normalize_path(a), _normalize_path(b)
    return na == nb or na.endswith(f"/{nb}") or nb.endswith(f"/{na}")


def run_validate() -> int:
    """Run the full presubmit gate (no flags) to match remote CI exactly and return its exit code."""
    result = _common._run([_common.PYTHON, "-m", "scripts.validate"])
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


def run_pre_commit_sanity() -> int:
    """Check branch, scope drift, orphaned TODOs. Output JSON."""
    branch = _common._current_branch()

    if branch == "main":
        fail_output = {"status": "FAIL", "branch": branch, "reason": "On main branch - cannot commit"}
        print(json.dumps(fail_output, indent=2))
        return 1

    plan_file = _common.find_plan_file()
    planned: dict[str, str] = {}
    plan_found = False
    if plan_file and plan_file.exists():
        planned = _parse_scope_table(plan_file.read_text(encoding="utf-8"))
        plan_found = True

    changed = _get_changed_files()

    unplanned: list[str] = []
    if plan_found:
        for cf in changed:
            if not any(_paths_match(cf, pf) for pf in planned):
                unplanned.append(cf)

    # Scan for orphaned TODOs/FIXMEs in diff
    diff_result = _common._run(["git", "diff", "origin/main"])
    if diff_result.returncode != 0:
        diff_result = _common._run(["git", "diff", "HEAD"])
    orphaned_todos: list[str] = []
    for line in (diff_result.stdout or "").splitlines():
        if re.match(r"^\+.*\b(TODO|FIXME)\b", line):
            orphaned_todos.append(line[1:].strip())

    status = "PASS"
    if unplanned:
        status = "WARN"
    if branch == "main":
        status = "FAIL"

    output: dict = {
        "status": status,
        "branch": branch,
        "planned": len(planned),
        "changed": len(changed),
        "unplanned": unplanned,
        "orphaned_todos": orphaned_todos,
    }
    print(json.dumps(output, indent=2))
    return 0


def run_commit(message: str) -> int:
    """Run git add + commit with pre-commit retry up to MAX_COMMIT_RETRIES.

    A clean tree after `git add .` is a NO-OP SUCCESS (rc 0), not a failure: re-running --auto
    after a red full tier or an evidence mismatch with no new edits must reach the full tier
    again, not burn a commit_failed attempt on nothing to commit. This also makes the standalone
    --commit idempotent.
    """
    for attempt in range(1, _common.MAX_COMMIT_RETRIES + 1):
        add_result = _common._run(["git", "add", "."])
        if add_result.returncode != 0:
            print(f"git add failed: {add_result.stderr}", file=sys.stderr)
            return 1

        status_result = _common._run(["git", "status", "--porcelain"])
        if status_result.returncode == 0 and not status_result.stdout.strip():
            print("Nothing to commit -- tree is clean.")
            return 0

        commit_result = _common._run(["git", "commit", "-m", message])
        if commit_result.returncode == 0:
            print(f"Committed on attempt {attempt}: {message}")
            print(commit_result.stdout)
            return 0

        # Pre-commit may have modified files -- check if that's the case
        stdout_lower = commit_result.stdout.lower()
        if "hook" in stdout_lower or "pre-commit" in stdout_lower or attempt < _common.MAX_COMMIT_RETRIES:
            print(f"Commit attempt {attempt} failed (pre-commit may have modified files). Retrying...")
            print(commit_result.stdout)
            continue

        # Non-retryable failure
        print(f"Commit failed: {commit_result.stdout}", file=sys.stderr)
        print(commit_result.stderr, file=sys.stderr)
        return 1

    print(f"Commit failed after {_common.MAX_COMMIT_RETRIES} attempts.", file=sys.stderr)
    return 1


def run_close() -> int:
    """Pre-commit sanity and diff summary in one shot.

    Returns JSON with keys:
      sanity_status – "PASS" | "WARN" | "FAIL"
      details       – dict with diff_summary and plan_file
    """
    plan_file = _common.find_plan_file()
    diff_stat = _common._run(["git", "diff", "--stat", "origin/main"])
    diff_summary = diff_stat.stdout.strip() if diff_stat.returncode == 0 else "(git diff unavailable)"

    # Pre-commit sanity
    sanity_result = _common._run([_common.PYTHON, "scripts/session/postflight.py", "--pre-commit-sanity"])
    sanity_status = "FAIL"
    if sanity_result.returncode == 0:
        try:
            sanity_data = json.loads(sanity_result.stdout)
            sanity_status = sanity_data.get("status", "FAIL")
        except (json.JSONDecodeError, KeyError):
            sanity_status = "FAIL"

    output: dict = {
        "sanity_status": sanity_status,
        "details": {
            "diff_summary": diff_summary,
            "plan_file": str(plan_file) if plan_file else None,
        },
    }
    print(json.dumps(output, indent=2))
    return 0


def run_rebase() -> str:
    """Fetch origin/main and rebase HEAD onto it.

    Returns "ok", "fetch_failed" (`git fetch origin main` was non-zero; no rebase attempted), or
    "rebase_failed" (`git rebase origin/main` was non-zero) -- after a rebase_failed outcome this
    ALWAYS runs `git rebase --abort` first, so a re-run never commits mid-rebase conflict markers.
    Never auto-resolves a conflict.
    """
    fetch_result = _common._run(["git", "fetch", "origin", "main"])
    if fetch_result.returncode != 0:
        print(f"git fetch origin main failed: {fetch_result.stderr}", file=sys.stderr)
        return "fetch_failed"

    rebase_result = _common._run(["git", "rebase", "origin/main"])
    if rebase_result.returncode != 0:
        print(f"git rebase origin/main failed:\n{rebase_result.stdout}", file=sys.stderr)
        _common._run(["git", "rebase", "--abort"])
        return "rebase_failed"
    return "ok"


def run_verify_head() -> int:
    """Run `scripts.checks.validation_result --verify-head` through the same transport as
    run_validate() (_common._run), print its output, and return its exit code -- 0 only when the
    just-written full-tier evidence attests the current (committed, rebased) HEAD's tree."""
    result = _common._run([_common.PYTHON, "-m", "scripts.checks.validation_result", "--verify-head"])
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


def run_auto(commit_message: str, steps_total: int = 0, steps_friction: int = 0) -> int:
    """Full session close: close -> evidence -> metrics -> commit -> rebase -> validate (full
    tier) -> verify-head -> push -> portal sync (PLAN-handoff-validates-committed-tree).

    The full tier now runs on the COMMITTED, rebased, clean tree rather than before commit, so its
    evidence can attest HEAD's tree (verify-head). Returns combined JSON status printed to stdout.
    Stops at the first non-zero/non-"ok" outcome -- run_commit treats a clean tree as a no-op
    success, so a re-run after a red full tier or an evidence mismatch with no new edits reaches
    the full tier again instead of failing at commit.

    Output JSON fields::

        {
          "close_exit": int,
          "sanity_status": "PASS" | "FAIL" | "UNKNOWN",
          "evidence": "PASS",
          "commit": "PASS" | "FAIL",
          "rebase": "ok" | "fetch_failed" | "rebase_failed",
          "validate": "PASS" | "FAIL",
          "verify_head": "PASS" | "FAIL",
          "status": "merged" | "sanity_failed" | "evidence_failed" | "commit_failed"
                    | "fetch_failed" | "rebase_failed" | "validate_failed" | "evidence_mismatch"
                    | "ci_failed" | "ci_timeout" | "push_failed" | "unknown",
          "pr_url": str,
          "error_summary": str
        }
    """
    import contextlib
    import io

    if not commit_message.strip():
        print(json.dumps({"status": "commit_failed", "error_summary": "commit_message is empty"}))
        return 1

    results: dict = {}

    # 1. Close (capture output so we can surface key fields)
    print("[auto] Running --close...", flush=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = run_close()
    close_raw = buf.getvalue().strip()
    results["close_exit"] = rc
    try:
        close_data = json.loads(close_raw)
        results["sanity_status"] = close_data.get("sanity_status", "UNKNOWN")
    except (json.JSONDecodeError, ValueError):
        results["sanity_status"] = "UNKNOWN"
    print(close_raw)
    if results.get("sanity_status") == "FAIL":
        results["status"] = "sanity_failed"
        print(json.dumps(results, indent=2))
        return 1

    # 2. Evidence
    try:
        evidence = json.loads(postflight_evidence.EVIDENCE_PATH.read_text(encoding="utf-8"))
        postflight_evidence.validate(evidence)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        results["status"] = "evidence_failed"
        results["error_summary"] = type(exc).__name__
        print(json.dumps(results, indent=2))
        return 1
    results["evidence"] = "PASS"

    # 3. Metrics
    print("[auto] Running --metrics...", flush=True)
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        housekeeping.run_metrics(steps_total, steps_friction)
    print(buf2.getvalue().strip())

    # 4. Commit (a clean tree is a no-op success -- see run_commit)
    print(f"[auto] Running --commit '{commit_message}'...", flush=True)
    rc = run_commit(commit_message)
    results["commit"] = "PASS" if rc == 0 else "FAIL"
    if rc != 0:
        results["status"] = "commit_failed"
        print(json.dumps(results, indent=2))
        return rc

    # 5. Rebase onto origin/main (never auto-resolves a conflict)
    print("[auto] Running rebase...", flush=True)
    rebase_status = run_rebase()
    results["rebase"] = rebase_status
    if rebase_status != "ok":
        results["status"] = rebase_status
        print(json.dumps(results, indent=2))
        return 1

    # 6. Validate -- the full tier, now on the committed, rebased, clean tree
    print("[auto] Running --validate...", flush=True)
    rc = run_validate()
    results["validate"] = "PASS" if rc == 0 else "FAIL"
    if rc != 0:
        results["status"] = "validate_failed"
        print(json.dumps(results, indent=2))
        return rc

    # 7. Verify-head -- the full tier's own evidence must attest this exact HEAD's tree
    print("[auto] Running verify-head...", flush=True)
    rc = run_verify_head()
    results["verify_head"] = "PASS" if rc == 0 else "FAIL"
    if rc != 0:
        results["status"] = "evidence_mismatch"
        print(json.dumps(results, indent=2))
        return rc

    # 8. Push
    print("[auto] Running --push...", flush=True)
    buf3 = io.StringIO()
    with contextlib.redirect_stdout(buf3):
        rc = remote.run_push()
    push_raw = buf3.getvalue().strip()
    print(push_raw)
    try:
        push_data = json.loads(push_raw)
        results["status"] = push_data.get("status", "unknown")
        results["pr_url"] = push_data.get("pr_url", "")
        results["error_summary"] = push_data.get("error_summary", "")
    except (json.JSONDecodeError, ValueError):
        results["status"] = "push_failed" if rc != 0 else "unknown"

    # (retired) The post-push log-housekeeping leg is gone (Decision 84 I-4): nothing under logs/
    # is tracked, so it was already a no-op, and pushing it here would have committed a tree the
    # full tier above never validated. housekeeping.run_log_housekeeping() and the standalone
    # --log-housekeeping CLI stay for a caller that wants them directly.

    # 7b. (retired) The pending-outbox drain was removed with the outbox itself (Decision 84 I-4):
    # file_rec/file_decision now fail loudly at the call site instead of queueing.

    # 9. Refresh the local read-cache from the DuckLake reader
    try:
        from scripts.ops_data_portal import sync as _portal_sync  # noqa: PLC0415

        _portal_sync()
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: Ops sync skipped (non-critical): {exc}", file=sys.stderr)

    print(json.dumps(results, indent=2))
    return 0 if results.get("status") == "merged" else rc


def run_evidence(path: str) -> int:
    """Write and render structured implementation evidence, reporting the outcome as a status line."""
    try:
        evidence = json.loads(Path(path).read_text(encoding="utf-8"))
        postflight_evidence.validate(evidence)
    except Exception:
        emit_status("postflight.evidence", "failed", "invalid_evidence")
        raise
    try:
        postflight_evidence.write(evidence)
        print(postflight_evidence.render(evidence))
    except Exception:
        emit_status("postflight.evidence", "failed", "evidence_error")
        raise
    emit_status("postflight.evidence", "done")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Post-session automation")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--validate", action="store_true", help="Run validate.py")
    group.add_argument("--pre-commit-sanity", action="store_true", dest="pre_commit_sanity", help="Scope and branch check")
    group.add_argument("--commit", metavar="MESSAGE", help="Commit with message")
    group.add_argument("--push", action="store_true", help="Push, create PR, poll CI, merge")
    group.add_argument("--metrics", action="store_true", help="Run session metrics + plan audit")
    group.add_argument("--evidence", metavar="JSON_FILE", help="Write and render structured implementation evidence")
    group.add_argument("--close", action="store_true", help="Pre-commit sanity and diff summary")
    group.add_argument(
        "--log-housekeeping",
        action="store_true",
        dest="log_housekeeping",
        help="Commit uncommitted JSONL logs",
    )
    group.add_argument(
        "--auto",
        metavar="MESSAGE",
        help="Full session close: close->evidence->metrics->commit->rebase->validate->verify-head->push->portal sync",
    )
    parser.add_argument(
        "--steps-total",
        type=int,
        default=0,
        metavar="N",
        help="Total Ordered Execution Steps (forwarded to session_metrics.py).",
    )
    parser.add_argument(
        "--steps-friction",
        type=int,
        default=0,
        metavar="M",
        help="Steps with retro-lite friction (forwarded to session_metrics.py).",
    )

    args = parser.parse_args()

    if args.validate:
        return run_validate()
    if args.pre_commit_sanity:
        return run_pre_commit_sanity()
    if args.commit:
        return run_commit(args.commit)
    if args.push:
        return remote.run_push()
    if args.metrics:
        return housekeeping.run_metrics(args.steps_total, args.steps_friction)
    if args.evidence:
        return run_evidence(args.evidence)
    if args.close:
        return run_close()
    if args.log_housekeeping:
        return housekeeping.run_log_housekeeping()
    if args.auto:
        return run_auto(args.auto, args.steps_total, args.steps_friction)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
