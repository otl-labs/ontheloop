"""Pure repository measures for reversal-condition predicates (rec-5264). No reader, no network.

``index_bytes`` is the size of the decision index file. ``entries_per_plan`` counts, over a window of
first-parent history anchored at ``as_of``, the Decisions added per plan file added, so a window anchored
at a past review date stays fixed as main moves on. Both raise rather than guess.
"""

from __future__ import annotations

import re
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_DECISION_ID_RE = re.compile(r"^## Decision (\d+):", re.MULTILINE)
_DECISION_FILES = ("docs/DECISIONS.md", "docs/DECISIONS_ARCHIVE.md")


def index_bytes(path: str | Path = "docs/decisions-index.json") -> int:
    return Path(path).stat().st_size


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if proc.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _commit_before(root: Path, day: date) -> str:
    return _git(root, "rev-list", "-1", "--first-parent", f"--before={day.isoformat()}T00:00:00Z", "HEAD").strip()


def _decision_ids(root: Path, rev: str) -> set[int]:
    ids: set[int] = set()
    for rel in _DECISION_FILES:
        proc = subprocess.run(
            ["git", "show", f"{rev}:{rel}"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode == 0:
            ids.update(int(m) for m in _DECISION_ID_RE.findall(proc.stdout))
    return ids


def entries_per_plan(window_days: int, as_of: date | None = None, root: str | Path = ".") -> tuple[int, int]:
    """(new Decision entries, plan files added) over the first-parent window ending at as_of, inclusive."""
    repo = Path(root)
    anchor = as_of if as_of is not None else datetime.now(timezone.utc).date()
    start = anchor - timedelta(days=window_days) + timedelta(days=1)
    window = f"{start.isoformat()}..{anchor.isoformat()}"
    end = _commit_before(repo, anchor + timedelta(days=1))
    base = _commit_before(repo, start)
    if not end or not base:
        raise ValueError(f"window {window} has no base commit (shallow or short history)")
    entries = len(_decision_ids(repo, end) - _decision_ids(repo, base))
    added = _git(
        repo,
        "log",
        "--first-parent",
        "--diff-filter=A",
        "--name-only",
        "--pretty=format:",
        f"{base}..{end}",
        "--",
        "docs/plans/PLAN-*.yaml",
    )
    plans = len({line for line in added.splitlines() if line.strip()})
    if plans == 0:
        raise ValueError(f"window {window} adds no plan, so entries per plan is undefined")
    return entries, plans
