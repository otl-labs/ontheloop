"""Forge-neutral merge-overlap predicate keyed on refs (Decision 234 cl.4; the merge gate's first predicate).

  check --head REF [--base REF] [--root DIR]   one line `MERGE-OVERLAP <KIND> behind=<k> base=<sha12> files=<a,b>`;
                                                exit 0 CLEAR, 1 OVERLAP, 2 UNAVAILABLE

OVERLAP means main changed, since the merge-base, a file the head also changes. A heuristic: CLEAR never
proves the combined tree passes. It never fetches (callers fetch) and writes nothing.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLEAR = "CLEAR"
OVERLAP = "OVERLAP"
UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class Verdict:
    kind: str
    merge_base: str = ""
    behind: int = 0
    files: tuple[str, ...] = ()
    reason: str = ""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _unavailable(reason: str) -> Verdict:
    return Verdict(UNAVAILABLE, reason=reason)


def _changed(root: Path, merge_base: str, ref: str) -> set[str] | None:
    result = _git(root, "diff", "--no-renames", "--name-only", merge_base, ref)
    if result.returncode != 0:
        return None
    return {line for line in result.stdout.splitlines() if line}


def overlap(root: Path, head_ref: str, base_ref: str = "origin/main") -> Verdict:
    try:
        base = _git(root, "merge-base", base_ref, head_ref)
        if base.returncode != 0 or not base.stdout.strip():
            return _unavailable(f"no merge-base of {base_ref} and {head_ref}: {base.stderr.strip() or 'unresolved'}")
        merge_base = base.stdout.strip()
        on_base = _changed(root, merge_base, base_ref)
        on_head = _changed(root, merge_base, head_ref)
        count = _git(root, "rev-list", "--count", f"{head_ref}..{base_ref}")
    except OSError as exc:
        return _unavailable(f"git could not run: {exc}")
    if on_base is None or on_head is None or count.returncode != 0:
        return _unavailable("git diff or rev-list failed")
    try:
        behind = int(count.stdout.strip())
    except ValueError:
        return _unavailable(f"unparseable rev-list count {count.stdout.strip()!r}")
    files = tuple(sorted(on_base & on_head))
    return Verdict(OVERLAP if files else CLEAR, merge_base, behind, files)


def format_line(verdict: Verdict) -> str:
    if verdict.kind == UNAVAILABLE:
        return f"MERGE-OVERLAP UNAVAILABLE reason={verdict.reason}"
    return (
        f"MERGE-OVERLAP {verdict.kind} behind={verdict.behind} base={verdict.merge_base[:12]} files={','.join(verdict.files)}"
    )


_EXIT = {CLEAR: 0, OVERLAP: 1, UNAVAILABLE: 2}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="merge_overlap")
    sub = parser.add_subparsers(dest="cmd", required=True)
    check = sub.add_parser("check")
    check.add_argument("--head", required=True)
    check.add_argument("--base", default="origin/main")
    check.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    verdict = overlap(args.root, args.head, args.base)
    print(format_line(verdict))
    return _EXIT[verdict.kind]


if __name__ == "__main__":
    sys.exit(main())
