"""Atomic, output-only evidence for completed full validator runs."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from scripts.checks import _common, registry


def evidence_path_for(toplevel: Path) -> Path:
    """The evidence file of an ARBITRARY worktree -- RESULT_PATH below only names the one
    importing this module. Used by --verify-head (and slice 2b) to locate the evidence file of
    whatever worktree's HEAD it is asked to attest, never assuming it is the importing one."""
    return toplevel / "logs" / "debug" / "validation-result.json"


# Anchored at scripts.checks._common.ROOT (the worktree THIS FILE lives in), never the process
# cwd -- a `git worktree add` checkout of this module resolves ROOT to itself, so evidence always
# lands in the worktree that actually ran the full tier (rec-3033's foreign-evidence fix).
RESULT_PATH = evidence_path_for(_common.ROOT)

# Accumulator of (check, label) attributions, populated by dispatch_recording() as each
# registered check runs and reset by clear() (validate.py's own start-of-run lifecycle) so
# runs never cross-contaminate.
_ATTRIBUTIONS: list[dict[str, str]] = []

# Accumulator of CheckOutcome rows (Decision 170) -- one per dispatched check, plus any scaffold
# that opens a registry.outcome_scope. Reset by clear(), same lifecycle as _ATTRIBUTIONS. Write
# mode is append-within-run then whole-file replace (see write_completed): this is a run artifact,
# never read as history, so it needs no merge key/identity/partitioning.
_OUTCOMES: list[registry.CheckOutcome] = []

# Names of scaffold steps that appended to `failed` (run_scaffold), in first-seen order. Reset by clear().
_SCAFFOLD_FAILURES: list[str] = []

# Accumulator of {check: [detail, ...]}, populated by dispatch_recording() when a check declares
# registry.failure_detail() (this plan, coverage-failure-attribution). Reset by clear(), same
# lifecycle as _ATTRIBUTIONS/_OUTCOMES. Emitted on write_completed()'s NEW top-level
# failed_check_details key -- deliberately separate from _ATTRIBUTIONS/failed_check_attributions,
# which stay byte-for-byte pinned (Decision 170 clause 1 / docs/contracts/check-accounting.yaml).
_FAILURE_DETAILS: dict[str, list[str]] = {}

# Start-of-run tree identity snapshot (PLAN-handoff-validates-committed-tree), taken by clear()
# and held IN MEMORY ONLY -- never written to disk mid-run, since the full tier's own
# validate_vp_replay may touch the evidence path before write_completed() runs. Defaults below
# apply when clear() never ran this process: tree_clean_at_start False makes attests_head() fail
# closed (dirty_start) rather than silently treating an unknown start state as clean.
_START_HEAD_TREE: str | None = None
_START_TREE_CLEAN: bool = False


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _git_output(args: list[str]) -> str | None:
    """Run a git probe with cwd=_common.ROOT (the invoking worktree), never the process cwd.
    Returns stripped stdout on success, None on any non-zero exit."""
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=_common.ROOT,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _head_tree() -> str | None:
    return _git_output(["rev-parse", "HEAD^{tree}"])


def _tree_clean() -> bool:
    """True iff the worktree has no pending changes, tracked or untracked (.gitignore respected).
    --no-optional-locks avoids .git/index.lock contention when xdist workers call clear()
    concurrently. A failed git probe (None) is treated as NOT clean -- fail closed."""
    return _git_output(["--no-optional-locks", "status", "--porcelain"]) == ""


def _worktree_toplevel() -> str | None:
    return _git_output(["rev-parse", "--show-toplevel"])


def clear(path: Path | None = None) -> None:
    """Reset all per-run state and snapshot the start-of-run tree identity.

    `path` resolves to the module-level RESULT_PATH AT CALL TIME (not at def time), so a test that
    monkeypatches RESULT_PATH before calling clear() with no argument is honoured -- the previous
    `path: Path = RESULT_PATH` default bound the ORIGINAL RESULT_PATH object once, at import time.
    """
    resolved = RESULT_PATH if path is None else path
    resolved.unlink(missing_ok=True)
    _ATTRIBUTIONS.clear()
    _OUTCOMES.clear()
    _SCAFFOLD_FAILURES.clear()
    _FAILURE_DETAILS.clear()
    global _START_HEAD_TREE, _START_TREE_CLEAN
    _START_HEAD_TREE = _head_tree()
    _START_TREE_CLEAN = _tree_clean()


def _harvest_declared_outcome(name: str, kind: str, appended_to_failed: bool) -> None:
    declaration = registry.pop_declaration()
    _OUTCOMES.append(registry.build_outcome(name, kind, declaration, appended_to_failed))


def record_scaffold_outcome(name: str, before: int, failed: list[str]) -> None:
    """Harvest a non-check scaffold's declared outcome after its registry.outcome_scope(...)
    block has exited. `before` is len(failed) captured BEFORE the scope opened."""
    _harvest_declared_outcome(name, "scaffold", appended_to_failed=bool(failed[before:]))


def dispatch_recording(name: str, failed: list[str], fn: Callable[[list[str]], None]) -> None:
    """Run a registered check via `fn(failed)`, attributing each label it newly appends to
    `failed` back to `name`. `fn` is resolved by the CALLER (scripts.checks.registry.resolve(name),
    late-bound at call time -- Decision 169, amending Decision 104's namespace-dict dispatch) so a
    `patch("<the check's defining module>.<name>", ...)` interception still resolves through a
    real dispatch pass.

    Brackets the call with registry.outcome_scope() (Decision 170) so the check's own
    examined()/skipped() declaration -- or lack of one -- is harvested into _OUTCOMES alongside
    the existing failed-label attribution.
    """
    before = len(failed)
    with registry.outcome_scope(name, kind="check"):
        fn(failed)
    appended = failed[before:]
    for label in appended:
        _ATTRIBUTIONS.append({"check": name, "label": label})
    detail = registry.pop_failure_detail()
    if detail is not None:
        _FAILURE_DETAILS[name] = detail
    _harvest_declared_outcome(name, "check", appended_to_failed=bool(appended))


def run_scaffold(name: str, failed: list[str], fn: Callable[[], None]) -> None:
    """Run a non-check scaffold step, recording its name when it newly appends to `failed`."""
    before = len(failed)
    fn()
    if failed[before:]:
        _SCAFFOLD_FAILURES.append(name)


def failure_markers() -> list[str]:
    """One gate marker per failed check (first-seen order), then per failed scaffold step."""
    from src.turn_capture import markers  # noqa: PLC0415 -- deferred: keeps src/ out of this module's import closure

    names = list(dict.fromkeys(a["check"] for a in _ATTRIBUTIONS))
    names.extend(n for n in dict.fromkeys(_SCAFFOLD_FAILURES) if n not in names)
    return [markers.format_gate_marker(n) for n in names]


def print_failure_summary(failed: list[str]) -> None:
    print("Failed checks:")
    for f in failed:
        print(f"  - {f}")
    for line in failure_markers():
        print(line)


def git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=_common.ROOT,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


# Status -> rollup-key map (Decision 170). "failed" checks are already tracked by the existing
# failed_checks/failed_check_attributions fields, so they are deliberately NOT double-counted in
# a rollup bucket here.
_ROLLUP_KEY_BY_STATUS: dict[str, str] = {
    "enforced": "ran_checks",
    "skipped": "skipped_checks",
    "vacuous": "vacuous_checks",
    "undeclared": "undeclared_checks",
}


def _rollups(outcomes: list[registry.CheckOutcome]) -> dict[str, int]:
    counts = dict.fromkeys(_ROLLUP_KEY_BY_STATUS.values(), 0)
    for outcome in outcomes:
        key = _ROLLUP_KEY_BY_STATUS.get(outcome.status)
        if key is not None:
            counts[key] += 1
    return counts


def write_completed(
    *, started_at: str, exit_code: int, failed_checks: list[str], path: Path = RESULT_PATH
) -> dict[str, object]:
    outcomes_snapshot = list(_OUTCOMES)
    details_snapshot = dict(_FAILURE_DETAILS)
    record: dict[str, object] = {
        "schema_version": 4,
        "command": "bin/venv-python -m scripts.validate",
        "scope": "all",
        "git_head": git_head(),
        "started_at": started_at,
        "completed_at": utc_now(),
        "exit_code": exit_code,
        "failed_checks": failed_checks,
        "failed_check_attributions": list(_ATTRIBUTIONS),
        "check_outcomes": [dataclasses.asdict(outcome) for outcome in outcomes_snapshot],
        **_rollups(outcomes_snapshot),
        # Schema v4 additions (PLAN-handoff-validates-committed-tree): the committed-tree identity
        # a handoff must attest before push. Start values come from clear()'s in-memory snapshot;
        # end values are read fresh right now, at write_completed() time.
        "head_tree_at_start": _START_HEAD_TREE,
        "head_tree_at_end": _head_tree(),
        "tree_clean_at_start": _START_TREE_CLEAN,
        "tree_clean_at_end": _tree_clean(),
        "worktree_toplevel": _worktree_toplevel(),
    }
    if details_snapshot:
        # NEW top-level key (Decision 170 clause 1 / check-accounting.yaml validation_result_
        # schema_v3): deliberately NOT merged into failed_check_attributions, which stays
        # byte-for-byte pinned. Present only when at least one check declared detail this run.
        record["failed_check_details"] = details_snapshot
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return record


def write_completed_visible(**kwargs: object) -> None:
    try:
        write_completed(**kwargs)  # type: ignore[arg-type]
    except OSError as exc:
        print(f"WARNING: validation evidence could not be written: {type(exc).__name__}")


def load_record(path: Path) -> tuple[dict | None, str]:
    """Read and parse an evidence file. Returns (record, "ok"), (None, "missing") when `path`
    does not exist, or (None, "unreadable") for anything else that stops it being a usable dict
    (OSError, invalid JSON, or valid JSON that is not an object)."""
    if not path.exists():
        return None, "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, "unreadable"
    if not isinstance(data, dict):
        return None, "unreadable"
    return data, "ok"


# The stable rule vocabulary attests_head() (and its load_record() composition in verify_head())
# return, evaluated in EXACTLY this order -- so "failed_run" is reachable only once every identity
# rule already holds, and "ok" only when nothing else fired. Slice 2b (the mechanical push gate)
# branches on this literal set; treat it as a public contract.
ATTEST_RULES: tuple[str, ...] = (
    "missing",
    "unreadable",
    "schema",
    "scope",
    "worktree",
    "dirty_start",
    "dirty_end",
    "head_moved",
    "tree_mismatch",
    "failed_run",
    "ok",
)


def attests_head(record: dict | None, *, head_tree: str, toplevel: Path) -> tuple[bool, str]:
    """Pure predicate: does `record` attest that `head_tree` was fully, cleanly validated from
    `toplevel`? True only for schema_version 4, scope "all", a matching worktree_toplevel, both
    clean flags true, head_tree_at_start == head_tree_at_end == head_tree, and exit_code 0 with an
    empty failed_checks. See ATTEST_RULES for the returned rule vocabulary and evaluation order.
    """
    if record is None:
        return False, "missing"
    if not isinstance(record, dict) or "schema_version" not in record:
        return False, "unreadable"
    if record.get("schema_version") != 4:
        return False, "schema"
    if record.get("scope") != "all":
        return False, "scope"
    if record.get("worktree_toplevel") != str(toplevel):
        return False, "worktree"
    if not record.get("tree_clean_at_start"):
        return False, "dirty_start"
    if not record.get("tree_clean_at_end"):
        return False, "dirty_end"
    start_tree = record.get("head_tree_at_start")
    end_tree = record.get("head_tree_at_end")
    if start_tree != end_tree:
        return False, "head_moved"
    if start_tree != head_tree:
        return False, "tree_mismatch"
    if record.get("exit_code") != 0 or record.get("failed_checks"):
        return False, "failed_run"
    return True, "ok"


def verify_head() -> tuple[bool, str]:
    """Compose load_record() + attests_head() against the CURRENT worktree's HEAD tree and
    toplevel. The thin CLI (`--verify-head`) below is a print+exit-code wrapper over this."""
    toplevel_str = _worktree_toplevel()
    toplevel = Path(toplevel_str) if toplevel_str is not None else _common.ROOT
    head_tree = _head_tree() or ""
    record, status = load_record(evidence_path_for(toplevel))
    if status != "ok":
        return False, status
    return attests_head(record, head_tree=head_tree, toplevel=toplevel)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.checks.validation_result")
    parser.add_argument(
        "--verify-head",
        action="store_true",
        help="Exit 0 only when logs/debug/validation-result.json attests the current HEAD's tree.",
    )
    args = parser.parse_args(argv)
    if not args.verify_head:
        parser.print_help()
        return 1
    ok, rule = verify_head()
    print(rule)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
