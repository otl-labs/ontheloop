"""Ranking pass for the derived-list scan (rec-5516): coupled appends over recent history, times size pressure.

A coupled append is an added line of a scanned file that names (path or non-generic stem) a file added or renamed in
the same commit, so an unrelated file added in the same squash commit never counts. score = coupled-append commits x
(1 + size pressure), where size pressure is used / limit from the file's SLOC (.py) or structural budget. On shallow
history, or fewer than `min_commits` commits in the window, every score is 0 and the ranking carries a note.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Optional

import yaml

GENERIC_STEMS = frozenset({"__init__", "README", "main", "index", "conftest"})
SLOC_LIMIT = 500
_MARK = "\x1fC"


class RankError(Exception):
    """A git read the ranking needs failed (the scan exits 2)."""


@dataclass(frozen=True)
class Score:
    coupled_appends: int
    size_pressure: float
    score: float


@dataclass(frozen=True)
class Ranking:
    scores: dict[str, Score]
    note: Optional[str]


def _git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
        )
    except OSError as exc:
        raise RankError(f"git {args[0]}: {exc}") from exc
    if proc.returncode != 0:
        raise RankError(f"git {args[0]} exited {proc.returncode}: {proc.stderr.strip()[:200]}")
    return proc.stdout


def effective_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip() and not line.strip().startswith("#"))


def _load(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def _structural_limit(root: Path, rel: str) -> int:
    data = _load(root / "config/structural_size_budgets.yaml")
    if not isinstance(data, dict):
        return 0
    override = (data.get("budgets") or {}).get(rel)
    if isinstance(override, int):
        return override
    for row in data.get("classes") or []:
        include = row.get("include") or []
        if row.get("slug") == "residual" or any(fnmatch.fnmatch(rel, glob) for glob in include):
            limit = row.get("limit")
            return limit if row.get("governed") and isinstance(limit, int) else 0
    return 0


def size_limit(root: Path, rel: str) -> int:
    if rel.endswith(".py"):
        data = _load(root / "config/sloc_budgets.yaml")
        budget = (data.get("budgets") or {}).get(rel) if isinstance(data, dict) else None
        return budget if isinstance(budget, int) else SLOC_LIMIT
    return _structural_limit(root, rel)


def size_pressure(root: Path, rel: str) -> float:
    limit = size_limit(root, rel)
    try:
        used = effective_lines((root / rel).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return 0.0
    return round(used / limit, 3) if limit > 0 else 0.0


def added_files_by_commit(root: Path, since: str) -> dict[str, set[str]]:
    """Paths each non-merge commit in the window added, or renamed to."""
    out = _git(root, "log", "--no-merges", f"--since={since}", f"--format={_MARK}%H", "--name-status", "-M")
    commits: dict[str, set[str]] = {}
    current: Optional[str] = None
    for line in out.splitlines():
        if line.startswith(_MARK):
            current = line[len(_MARK) :]
            commits[current] = set()
        elif current is not None and line[:1] in ("A", "R"):
            commits[current].add(line.split("\t")[-1])
    return commits


def added_lines_by_commit(root: Path, since: str, paths: list[str]) -> dict[str, dict[str, list[str]]]:
    """Added lines per commit per scanned path (-U0 hunks; a header line is never read as content)."""
    out = _git(root, "log", "--no-merges", f"--since={since}", f"--format={_MARK}%H", "-U0", "--no-color", "--", *paths)
    commits: dict[str, dict[str, list[str]]] = {}
    lines: dict[str, list[str]] = {}
    target: Optional[str] = None
    in_hunk = False
    for line in out.splitlines():
        if line.startswith(_MARK):
            lines, target, in_hunk = {}, None, False
            commits[line[len(_MARK) :]] = lines
        elif line.startswith("diff --git "):
            target, in_hunk = None, False
        elif in_hunk and line.startswith("+"):
            if target is not None:
                lines.setdefault(target, []).append(line[1:])
        elif line.startswith("@@"):
            in_hunk = True
        elif line.startswith("+++ b/"):
            target = line[len("+++ b/") :]
    return commits


def _needle_re(paths: set[str]) -> Optional[re.Pattern[str]]:
    needles: set[str] = set()
    for path in paths:
        needles.add(path)
        stem = PurePosixPath(path).stem
        if stem and stem not in GENERIC_STEMS:
            needles.add(stem)
    if not needles:
        return None
    alternation = "|".join(re.escape(n) for n in sorted(needles, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9_])(?:{alternation})(?![A-Za-z0-9_])")


def coupled_counts(added_files: dict[str, set[str]], added_lines: dict[str, dict[str, list[str]]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for commit, per_file in added_lines.items():
        new_files = added_files.get(commit, set())
        for path, lines in per_file.items():
            needle = _needle_re(new_files - {path})
            if needle is not None and any(needle.search(line) for line in lines):
                counts[path] += 1
    return counts


def shallow_note(root: Path, since: str, min_commits: int) -> Optional[str]:
    if _git(root, "rev-parse", "--is-shallow-repository").strip() == "true":
        return "history is shallow, so every score is 0 (fetch full history to rank)"
    count = int(_git(root, "rev-list", "--count", "--no-merges", f"--since={since}", "HEAD").strip() or 0)
    if count < min_commits:
        return f"only {count} commits in the window (fewer than {min_commits}), so every score is 0"
    return None


def rank(root: Path, paths: list[str], *, history_days: int = 90, min_commits: int = 30) -> Ranking:
    since = f"{history_days} days ago"
    pressures = {p: size_pressure(root, p) for p in paths}
    note = shallow_note(root, since, min_commits)
    coupled: Counter[str] = Counter()
    if note is None and paths:
        coupled = coupled_counts(added_files_by_commit(root, since), added_lines_by_commit(root, since, paths))
    scores = {p: Score(coupled[p], pressures[p], round(coupled[p] * (1 + pressures[p]), 3)) for p in paths}
    return Ranking(scores, note)
