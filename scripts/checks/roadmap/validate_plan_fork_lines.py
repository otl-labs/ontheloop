"""Plan fork-line gate (rec-3530): judges only the context[] fork lines a diff adds or changes.

A line already on the diff base is never re-judged, with no waiver, marker or grandfather (Decision 163 point 1). The
engine (scripts.roadmap.plan_fork_lines) is pure; this module binds it to the diff through injected seams and never
globs or lists docs/plans.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Callable

import yaml

from scripts.checks import _common, registry
from scripts.roadmap import plan_fork_lines as engine

_PLAN_RE = re.compile(r"docs/plans/PLAN-[^/]+\.yaml")
_SUMMARY = "Plan fork-line validation"


def _read_worktree(root: Path, rel: str) -> str | None:
    try:
        return (root / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _read_at_ref(root: Path, ref: str, rel: str) -> str | None:
    result = _common.run(["git", "show", f"{ref}:{rel}"], capture_output=True, text=True, encoding="utf-8", cwd=root)
    if result.returncode != 0:
        return None
    return result.stdout


def _context(text: str | None) -> list | None:
    if text is None:
        return None
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(doc, dict):
        return None
    context = doc.get("context")
    return context if isinstance(context, list) else []


def _contract_drift(failed: list[str], read: engine.Reader) -> bool:
    grammar = engine.load_record_grammar(read(engine.CONTRACT))
    if grammar is None:
        failed.append(f"{engine.CONTRACT}: autonomy_tiers.plan_fork_classification.record_grammar is missing or unreadable")
        print(f"FAIL {engine.CONTRACT}: record_grammar is missing or unreadable")
        return False
    problems = engine.grammar_mismatches(grammar)
    for problem in problems:
        failed.append(f"{engine.CONTRACT}: record_grammar disagrees with the engine -- {problem}")
        print(f"FAIL {engine.CONTRACT}: {problem}")
    return not problems


def run_check(
    failed: list[str],
    *,
    root: Path,
    base: str | None,
    changed: list[tuple[str, str]],
    read_base: Callable[[str], str | None],
    read_head: Callable[[str], str | None],
) -> None:
    read = engine.cached(read_head)
    if not _contract_drift(failed, read):
        return
    plans = sorted({p for status, p in changed if status in {"A", "M", "??"} and _PLAN_RE.fullmatch(p)})
    judged = 0
    broke = False
    for plan_path in plans:
        head_context = _context(read(plan_path))
        if head_context is None:
            print(
                f"WARN {plan_path}: head version does not parse as a plan mapping -- skipped (validate_plan_documents owns it)"
            )
            continue
        added = any(s == "A" or s == "??" for s, p in changed if p == plan_path)
        base_context = [] if added else (_context(read_base(plan_path)) or [])
        unseen = Counter(r.text for r in engine.fork_records(base_context))
        for record in engine.fork_records(head_context):
            if unseen[record.text] > 0:
                unseen[record.text] -= 1
                continue
            judged += 1
            for finding in engine.judge_line(record.text, plan_path=plan_path, read=read):
                print(f"{finding.severity.upper()} {plan_path} {record.label}: {finding.rule}: {finding.message}")
                if finding.severity == "fail":
                    failed_line = f"{plan_path} {record.label}: {finding.rule}: {finding.message}"
                    failed.append(failed_line)
                    broke = True
    if broke:
        failed.append(_SUMMARY)
    registry.examined(judged, unit="fork_lines")


@registry.register("validate_plan_fork_lines", owner="platform")
def validate_plan_fork_lines(failed: list[str], root: Path | None = None) -> None:
    print("\n=== Plan fork-line validation (rec-3530) ===")
    root = root if root is not None else _common.ROOT
    base = _common.status_diff_base(root)
    if base is None:
        print("  SKIP: diff base unreachable.")
        registry.skipped("diff base unreachable")
        return
    run_check(
        failed,
        root=root,
        base=base,
        changed=_common.get_status_aware_diff(root),
        read_base=lambda rel: _read_at_ref(root, base, rel),
        read_head=lambda rel: _read_worktree(root, rel),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan fork-line gate (rec-3530).")
    parser.add_argument("--base", default=None, help="Diff base ref (default: status_diff_base(root)).")
    parser.add_argument("--head", default=None, help="Diff head ref (default: the working tree).")
    args = parser.parse_args(argv)

    root = _common.ROOT
    base = args.base if args.base is not None else _common.status_diff_base(root)
    if base is None:
        print("SKIP: diff base unreachable.")
        return 0

    if args.head is not None:
        diff = _common.run(
            ["git", "diff", "--name-status", "--no-renames", f"{base}..{args.head}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=root,
        )
        changed: list[tuple[str, str]] = []
        for line in (diff.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) == 2 and parts[0].strip()[:1] in {"A", "M", "D"}:
                changed.append((parts[0].strip()[:1], parts[1].strip()))
        read_head: Callable[[str], str | None] = lambda rel: _read_at_ref(root, args.head, rel)  # noqa: E731
    else:
        changed = _common.get_status_aware_diff(root)
        read_head = lambda rel: _read_worktree(root, rel)  # noqa: E731

    failed: list[str] = []
    run_check(
        failed,
        root=root,
        base=base,
        changed=changed,
        read_base=lambda rel: _read_at_ref(root, base, rel),
        read_head=read_head,
    )
    if failed:
        print("\nFAILED:")
        for item in failed:
            print(f"  - {item}")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
