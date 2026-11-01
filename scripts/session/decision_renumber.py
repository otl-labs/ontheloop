"""The decision_numbering class of docs/contracts/mechanical-conflicts.yaml (Decision 217 clause 2, rec-4323).

Two readers of the Decision-numbering race, both side-effect free at import:

  collisions(root, onto)  branch-added Decision numbers that `onto` already holds, mapped to MAX + 1, + 2, ...
                          (read only; used by merge_step premerge)
  renumber(root, onto)    on the rebased working tree, move those numbers and rewrite each recognised citation
                          on the lines the branch adds; every other occurrence of an old number is a bare hit

Headings are read only through scripts.decisions_md.iter_decision_headings (Decision 134 clause 3).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from scripts.decisions_md import iter_decision_headings

LIVE = "docs/DECISIONS.md"
ARCHIVE = "docs/DECISIONS_ARCHIVE.md"
CORPUS = (LIVE, ARCHIVE)

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_DECISIONS_RUN_RE = re.compile(r"\bDecisions\s+(\d+(?:(?:\s*,\s*and\s+|\s*,\s*|\s*/\s*|\s+and\s+)\d+)*)(?!\d)")
_DECISION_RE = re.compile(r"\bDecision\s+(\d+)(?![\d])")
_DEC_ID_RE = re.compile(r"\bdec-(\d+)(?!\d)")
_KEY_RE = re.compile(r"(?<![\w])\"?(?:number|decision|decision_id)\"?\s*:\s*(\d+)(?!\d)")
_BLOCKED_RUN_RE = re.compile(r"\d+(?:\s*(?:\+|/)\s*\d+)+")
_TOKEN_RE = re.compile(r"(?<![\w.\-])\d+(?![\w\-]|\.\d)")
_DIGITS_RE = re.compile(r"\d+")


@dataclass
class Result:
    mapping: dict[int, int] = field(default_factory=dict)
    rewrites: list[tuple[str, int, str, str]] = field(default_factory=list)
    bare: list[tuple[str, int, str]] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _text(root: Path, rel: str, ref: str | None) -> str:
    if ref is None:
        path = root / rel
        return path.read_text(encoding="utf-8") if path.exists() else ""
    shown = _git(root, "show", f"{ref}:{rel}")
    return shown.stdout if shown.returncode == 0 else ""


def _heading_numbers(text: str) -> list[int]:
    return [int(m.group(1)) for m in iter_decision_headings(text)]


def numbers_at(root: Path, ref: str | None) -> set[int]:
    """Decision numbers headed in DECISIONS.md and DECISIONS_ARCHIVE.md at `ref` (None: the working tree)."""
    found: set[int] = set()
    for rel in CORPUS:
        found.update(_heading_numbers(_text(root, rel, ref)))
    return found


def branch_added(root: Path, base: str, ref: str | None = "HEAD") -> list[int]:
    """Numbers headed in DECISIONS.md at `ref` that neither corpus file headed at `base`."""
    existing = numbers_at(root, base)
    return sorted({n for n in _heading_numbers(_text(root, LIVE, ref)) if n not in existing})


def _ascending_map(colliding: list[int], top: int) -> dict[int, int]:
    return {old: top + 1 + i for i, old in enumerate(sorted(colliding))}


def collisions(root: Path, onto: str = "origin/main") -> dict[int, int]:
    """Branch-added numbers `onto` already holds, mapped to MAX + 1, + 2, ... Writes nothing."""
    merge_base = _git(root, "merge-base", onto, "HEAD").stdout.strip()
    if not merge_base:
        return {}
    added = branch_added(root, merge_base)
    theirs = numbers_at(root, onto)
    colliding = [n for n in added if n in theirs]
    if not colliding:
        return {}
    return _ascending_map(colliding, max(theirs | set(added)))


def _added_lines(root: Path, onto: str) -> dict[str, list[tuple[int, str]]]:
    diff = _git(root, "diff", "-U0", "--no-color", "--no-ext-diff", "--no-renames", f"{onto}...HEAD").stdout
    out: dict[str, list[tuple[int, str]]] = {}
    path, line_no = "", 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:]
            path = path[2:] if path.startswith("b/") else ""
            continue
        if raw.startswith(("--- ", "diff ")):
            continue
        hunk = _HUNK_RE.match(raw)
        if hunk:
            line_no = int(hunk.group(1))
            continue
        if raw.startswith("+") and path:
            out.setdefault(path, []).append((line_no, raw[1:]))
            line_no += 1
    return out


def _span_overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < other[1] and other[0] < span[1] for other in spans)


def _new_digits(old_digits: str, new: int) -> str:
    return f"{new:0{max(len(old_digits), 3)}d}" if old_digits.startswith("0") else str(new)


def _rewrite_line(line: str, mapping: dict[int, int], is_decisions: bool) -> tuple[str, list[tuple[str, str]], list[str]]:
    """(new line, [(old, new)], [bare tokens]) with every substitution made in one pass over the ORIGINAL line."""
    runs = [m.span() for m in _DECISIONS_RUN_RE.finditer(line)]
    blocked = [m.span() for m in _BLOCKED_RUN_RE.finditer(line) if not _span_overlaps(m.span(), runs)]
    subs: list[tuple[int, int, str, str]] = []

    def consider(start: int, end: int) -> None:
        old_digits = line[start:end]
        old = int(old_digits)
        if (
            old in mapping
            and not _span_overlaps((start, end), blocked)
            and not _span_overlaps((start, end), [(s[0], s[1]) for s in subs])
        ):
            subs.append((start, end, old_digits, _new_digits(old_digits, mapping[old])))

    for run in _DECISIONS_RUN_RE.finditer(line):
        for number in _DIGITS_RE.finditer(run.group(1)):
            consider(run.start(1) + number.start(), run.start(1) + number.end())
    for pattern in (_DECISION_RE, _DEC_ID_RE):
        for match in pattern.finditer(line):
            consider(match.start(1), match.end(1))
    if is_decisions:
        for match in _KEY_RE.finditer(line):
            consider(match.start(1), match.end(1))
    taken = [(s[0], s[1]) for s in subs]
    bare = [m.group(0) for m in _TOKEN_RE.finditer(line) if int(m.group(0)) in mapping and not _span_overlaps(m.span(), taken)]
    new_line = line
    for start, end, _old, new in sorted(subs, reverse=True):
        new_line = new_line[:start] + new + new_line[end:]
    return new_line, [(s[2], s[3]) for s in sorted(subs)], bare


def _reorder_blocks(lines: list[str], added: set[int]) -> list[str]:
    """Re-emit each run of adjacent branch-added Decision blocks in descending number order."""
    starts = [i for i, text in enumerate(lines) if iter_decision_headings(text)]
    ends = starts[1:] + [len(lines)]
    blocks = [(lines[s:e], (s + 1) in added, int(iter_decision_headings(lines[s])[0].group(1))) for s, e in zip(starts, ends)]
    out = lines[: starts[0]]
    i = 0
    while i < len(blocks):
        if not blocks[i][1]:
            out.extend(blocks[i][0])
            i += 1
            continue
        j = i
        while j < len(blocks) and blocks[j][1]:
            j += 1
        for body, _flag, _number in sorted(blocks[i:j], key=lambda b: -b[2]):
            out.extend(body)
        i = j
    return out


def renumber(root: Path, onto: str = "origin/main", base: str | None = None) -> Result:
    """Move colliding branch-added numbers on the rebased working tree and rewrite recognised citations.

    `base` is the pre-rebase merge-base (rebase_resolve records it); default merge-base(onto, HEAD).
    """
    result = Result()
    base = base or _git(root, "merge-base", onto, "HEAD").stdout.strip()
    if not base:
        return result
    added = _added_lines(root, onto)
    live_text = _text(root, LIVE, None)
    live_numbers = _heading_numbers(live_text)
    corpus_numbers = live_numbers + _heading_numbers(_text(root, ARCHIVE, None))
    existing = numbers_at(root, base)
    colliding = [n for n in sorted({n for n in live_numbers if n not in existing}) if corpus_numbers.count(n) > 1]
    if not colliding:
        return result
    result.mapping = _ascending_map(colliding, max(corpus_numbers))
    for rel in sorted(added):
        path = root / rel
        lines = path.read_text(encoding="utf-8").split("\n")
        touched = False
        for line_no, _original in added[rel]:
            new_line, subs, bare = _rewrite_line(lines[line_no - 1], result.mapping, rel == LIVE)
            for token in bare:
                result.bare.append((rel, line_no, token))
            for old, new in subs:
                result.rewrites.append((rel, line_no, old, new))
            if new_line != lines[line_no - 1]:
                lines[line_no - 1] = new_line
                touched = True
        if rel == LIVE:
            reordered = _reorder_blocks(lines, {n for n, _t in added[rel]})
            touched = touched or reordered != lines
            lines = reordered
        if touched:
            path.write_text("\n".join(lines), encoding="utf-8")
            result.changed.append(rel)
    return result
