"""Git channel over first-parent HEAD history: change coupling and rename/split aliases.

The repo squash-merges, so a first-parent commit is one PR's file set. A shallow clone or any git failure marks the
channel unavailable in the envelope; this module never raises on a git problem and never reports a silent zero
(Decision 215 cl.2). Generic over files: no recommendation vocabulary (Decision 197 cl.7).
"""

from __future__ import annotations

import posixpath
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from scripts.recs_related.envelope import CHANNEL_OK, unavailable

HISTORY_COMMITS = 3000
MAX_COMMIT_FILES = 30
MAX_PARTNERS = 200
_GIT_TIMEOUT_S = 120


@dataclass(frozen=True)
class Coupling:
    """A file that changed in the same commits as the query file: `shared` commits, `confidence` = shared / commits."""

    path: str
    shared: int
    confidence: float


@dataclass(frozen=True)
class FileHistory:
    coupling: list[Coupling]
    aliases: list[str]
    status: str


@dataclass
class HistoryIndex:
    """The parsed first-parent log (newest first), or the reason it could not be read."""

    status: str = CHANNEL_OK
    commits: list[frozenset[str]] = field(default_factory=list)
    renames: list[tuple[str, str]] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)

    def for_file(self, path: str, tracked: frozenset[str]) -> FileHistory:
        if path not in tracked:
            return FileHistory([], [], unavailable(f"{path} is untracked, so it has no history"))
        if self.status != CHANNEL_OK:
            return FileHistory([], [], self.status)
        aliases = self._aliases(path, tracked)
        names = {path, *aliases}
        considered = [c for c in self.commits if len(c) <= MAX_COMMIT_FILES and c & names]
        partners: Counter[str] = Counter()
        for commit in considered:
            partners.update(commit - names)
        coupling = [Coupling(p, n, n / len(considered)) for p, n in partners.items()]
        coupling.sort(key=lambda c: (-c.shared, c.path))
        return FileHistory(coupling[:MAX_PARTNERS], aliases, CHANNEL_OK)

    def _aliases(self, path: str, tracked: frozenset[str]) -> list[str]:
        found: list[str] = []
        names = {path}
        for old, new in self.renames:
            if new in names and old not in names:
                names.add(old)
                found.append(old)
        here = posixpath.dirname(path)
        while here:
            candidate = f"{here}.py"
            if candidate in self.seen and candidate not in tracked and candidate not in names:
                found.append(candidate)
            here = posixpath.dirname(here)
        return found


def run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str] | str:
    """Run git in *repo_root*; the CompletedProcess, or the failure reason when git could not run."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), "-c", "core.quotepath=off", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"git could not run: {exc}"


def _parse(log: str) -> HistoryIndex:
    index = HistoryIndex()
    current: set[str] | None = None
    for line in log.splitlines():
        if line.startswith("@@"):
            if current is not None:
                index.commits.append(frozenset(current))
            current = set()
        elif line.strip() and current is not None:
            fields = line.split("\t")
            if fields[0][:1] in ("R", "C") and len(fields) >= 3:
                if fields[0][:1] == "R":
                    index.renames.append((fields[1], fields[2]))
                index.seen.add(fields[1])
                fields = fields[:1] + fields[2:]
            current.add(fields[-1])
            index.seen.add(fields[-1])
    if current is not None:
        index.commits.append(frozenset(current))
    return index


def load_history(repo_root: Path) -> HistoryIndex:
    """Parse the first-parent log once; every per-file question is then answered from the index."""
    shallow = run_git(repo_root, "rev-parse", "--is-shallow-repository")
    if isinstance(shallow, str):
        return HistoryIndex(status=unavailable(shallow))
    if shallow.returncode != 0:
        return HistoryIndex(status=unavailable(f"git failed: {shallow.stderr.strip()[:200]}"))
    if shallow.stdout.strip() == "true":
        return HistoryIndex(status=unavailable("shallow clone: history is truncated"))
    log = run_git(repo_root, "log", "--first-parent", "-M", "--name-status", "--format=@@%H", f"--max-count={HISTORY_COMMITS}")
    if isinstance(log, str):
        return HistoryIndex(status=unavailable(log))
    if log.returncode != 0:
        return HistoryIndex(status=unavailable(f"git log failed: {log.stderr.strip()[:200]}"))
    return _parse(log.stdout)
