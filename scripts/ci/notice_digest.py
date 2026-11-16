#!/usr/bin/env python3
"""Notice digest: one issue comment per merged commit with notice-class changes (PLAN-wording-approval-routing).

Reads docs/contracts/approval-routing.yaml at each commit's parent (or the one --map-file map, the replay seam); a
commit whose map is absent or not on sampled adds no row. Imports only stdlib, yaml and
scripts.roadmap.approval_routing: the workflow installs pyyaml alone.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from scripts.roadmap import approval_routing as ar

ROOT = Path(__file__).resolve().parents[2]
LABEL = "notice-digest"
TITLE = "Notice digest (wording approval lane)"
LIMIT = 65536
_ZERO = re.compile(r"^0+$")
_PR = re.compile(r"\(#(\d+)\)\s*$")


def _git(*args: str) -> str | None:
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.stdout.strip() if done.returncode == 0 else None


def _resolve(rev: str) -> str:
    return rev if _ZERO.match(rev) else (_git("rev-parse", "--verify", f"{rev}^{{commit}}") or rev)


def _commits(before: str, after: str) -> list[str]:
    if _ZERO.match(before):
        return [after]
    return (_git("rev-list", "--reverse", f"{before}..{after}") or "").split()


def _intent(sha: str, slug: str) -> str:
    try:
        plan = yaml.safe_load(ar.text_at(ROOT, sha, f"docs/plans/PLAN-{slug}.yaml") or "")
    except yaml.YAMLError:
        plan = None
    text = " ".join(str(plan.get("intent", "")).split()) if isinstance(plan, dict) else ""
    return re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0][:200] or "no intent recorded"


def compose_row(sha: str, parent: str, m: dict) -> str | None:
    changed = [f for f in (_git("diff", "--name-only", "--no-renames", parent, sha) or "").splitlines() if f]
    notice = ar.notice_changes(m, ROOT, parent, sha, changed)
    if not notice:
        return None
    subject = _git("log", "-1", "--format=%s", sha) or ""
    pr = _PR.search(subject)
    slug = ar.declared_plan(ROOT, sha)
    bound = ar.bound_files(ROOT, parent, slug) if slug else set()
    trip = sorted(
        {
            f
            for f, why in ar.gated_changes(m, ROOT, parent, sha, changed)
            if f not in bound or "demotions" in why or "reverted" in why
        }
    )
    plan = f"plan {slug}: {_intent(sha, slug)}" if slug else "plan none"
    return (
        f"PR #{pr.group(1) if pr else sha[:8]} {subject} | {plan} | notice: {', '.join(f'{f} (notice)' for f in notice)} | "
        f"tripwires: {', '.join(trip) or 'none'} | sample: {'yes' if int(sha, 16) % 5 == 0 else 'no'}"
    )


def rows(before: str, after: str, map_file: str | None) -> list[str]:
    fixed = ar.parse_map(Path(map_file).read_text(encoding="utf-8")) if map_file else None
    out: list[str] = []
    for sha in _commits(_resolve(before), _resolve(after)):
        parent = _git("rev-parse", "--verify", f"{sha}^") or ""
        m = (fixed or ar.load_map(ROOT, parent)) if parent else None
        if m is None or not ar.active(m):
            print(f"inert: lane not on sampled at {parent or sha}")
            continue
        row = compose_row(sha, parent, m)
        if row:
            out.append(row)
    return out


def _gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run(["gh", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and done.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {done.stderr.strip()}")
    return done


def _body_file(text: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(text)
        return handle.name


def _fit(row: str) -> str:
    if len(row) <= LIMIT:
        return row
    keep = row[: LIMIT - 60]
    dropped = row[len(keep) :].count(" (notice)") + 1
    return f"{keep}\n+{dropped} more files: see the PR"[:LIMIT]


def _issue_number() -> int | None:
    listed = _gh("issue", "list", "--label", LABEL, "--state", "open", "--json", "number", "--limit", "100").stdout
    numbers = [item["number"] for item in json.loads(listed or "[]")]
    return min(numbers) if numbers else None


def _ensure_issue() -> int:
    number = _issue_number()
    if number is not None:
        return number
    made = _gh("label", "create", LABEL, "--description", "Wording approval lane digest", "--color", "ededed", check=False)
    if made.returncode != 0 and "already exists" not in made.stderr:
        raise RuntimeError(f"gh label create failed: {made.stderr.strip()}")
    _gh("issue", "create", "--title", TITLE, "--label", LABEL, "--body-file", _body_file("One comment per merged commit."))
    number = _issue_number()
    if number is None:
        raise RuntimeError("the notice-digest issue was not found after creating it")
    return number


def post(lines: list[str]) -> None:
    number = _ensure_issue()
    for line in lines:
        _gh("issue", "comment", str(number), "--body-file", _body_file(_fit(line)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Post notice-class change rows to the notice-digest issue")
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--map-file")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    lines = rows(args.before, args.after, args.map_file)
    if not lines:
        print("no notice-class changes")
        return 0
    for line in lines:
        print(line)
    if not args.dry_run:
        try:
            post(lines)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
