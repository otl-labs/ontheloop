"""Decision 177 clauses 1-4 as a pure comparison, plus the fast-tier WARN that applies it to a pull request's diff.

Re-lands the comparison of the retired validate_live_entry_immutability guard (a50ee8de) as a report surface: the
rule is a function of four {number -> block} maps and nothing else, so the post-hoc census over main
(append_only_census) and the --pre WARN share one implementation. Nothing here reads configuration, takes an allow or
skip input, or fails a run: Decision 177 clause 5 binds any merge-time surface unchanged, and this one only warns.

For every number present at the baseline (union of both corpus files) that is not already superseded there:
  embedding -- a live body must embed its prepared baseline lines in order, injectively, by exact equality;
               insertions anywhere are fine. Closed reversal-conditions stanza interiors are carved out with the
               fence grammar imported from scripts.preflight.decision_conditions (an unterminated fence exempts
               nothing); stanza existence is locked.
  stub     -- a body newly declaring Superseded must satisfy the strict stub shape.
  stanza   -- a baseline stanza that the current body (or its archive move) no longer carries.
  vanished -- a number absent from both files.
An archive move must embed the baseline lines with exactly two exemptions, the header and the Status marker line.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from scripts.checks import _common, registry
from scripts.checks.decisions import _baseline
from scripts.checks.decisions._baseline import BaselineBodyReaderFn
from scripts.decisions_md import iter_decision_sections, status_is_superseded
from scripts.preflight.decision_conditions import _FENCE_CLOSE_RE, _FENCE_OPEN_RE

_LIVE_REL_PATH = "docs/DECISIONS.md"
_ARCHIVE_REL_PATH = "docs/DECISIONS_ARCHIVE.md"

_STATUS_MARKER_RE = re.compile(r"^\*\*Status:\*\*")
_HEADING_RE = re.compile(r"^#{2,3}\s+Decision\s+\d+:")

_STUB_LINE_PATTERNS = (
    re.compile(r"^\*\*Status:\*\*\s*Superseded\s*$"),
    re.compile(r"^\*\*Date:\*\*\s*.+$"),
    re.compile(r"^\*\*Decision:\*\*\s*.+$"),
    re.compile(r"^\*\*Superseded by:\s*Decision\s+\d+\*\*\s*$"),
    re.compile(r"^---\s*$"),
)

Finding = tuple[int, str, Optional[str]]


def prepare_lines(block: str) -> list[str]:
    """Rstripped lines with CLOSED reversal-conditions stanza interiors (fences included) removed.

    An unterminated fence grants no exemption: its opening line and everything after it stay in the comparison.
    """
    lines = [raw.rstrip() for raw in block.splitlines()]
    excluded: set[int] = set()
    open_at: Optional[int] = None
    for i, line in enumerate(lines):
        if open_at is None:
            if _FENCE_OPEN_RE.match(line):
                open_at = i
            continue
        if _FENCE_CLOSE_RE.match(line):
            excluded.update(range(open_at, i + 1))
            open_at = None
    return [line for i, line in enumerate(lines) if i not in excluded]


def has_reversal_stanza(block: str) -> bool:
    return any(_FENCE_OPEN_RE.match(raw.rstrip()) for raw in block.splitlines())


def embeds_in_order(baseline: list[str], current: list[str], exempt: Optional[set[int]] = None) -> Optional[int]:
    """Index of the first baseline line with no ordered, injective exact match in current, or None."""
    skip = exempt or set()
    cursor = 0
    for i, line in enumerate(baseline):
        if i in skip:
            continue
        try:
            offset = current.index(line, cursor)
        except ValueError:
            return i
        cursor = offset + 1
    return None


def _stub_shape_violation(block: str) -> Optional[str]:
    lines = block.splitlines()
    if not lines or not _HEADING_RE.match(lines[0].rstrip()):
        return "missing '## Decision N:' header line"
    for raw in lines[1:]:
        line = raw.strip()
        if line and not any(pattern.match(line) for pattern in _STUB_LINE_PATTERNS):
            return line
    return None


def _archive_exempt_indices(baseline_lines: list[str]) -> set[int]:
    return {i for i, line in enumerate(baseline_lines) if _HEADING_RE.match(line) or _STATUS_MARKER_RE.match(line)}


def _embedding_findings(number: int, baseline_block: str, current_block: str, exempt_header_status: bool) -> list[Finding]:
    findings: list[Finding] = []
    baseline_lines = prepare_lines(baseline_block)
    current_lines = prepare_lines(current_block)
    exempt = _archive_exempt_indices(baseline_lines) if exempt_header_status else None
    missing = embeds_in_order(baseline_lines, current_lines, exempt)
    if missing is not None:
        findings.append((number, "embedding", baseline_lines[missing]))
    if has_reversal_stanza(baseline_block) and not has_reversal_stanza(current_block):
        findings.append((number, "stanza", None))
    return findings


def compare(
    baseline_live: dict[int, str],
    baseline_archive: dict[int, str],
    current_live: dict[int, str],
    current_archive: dict[int, str],
) -> list[Finding]:
    """One (decision, kind, first unmatched baseline line or None) per violation, ascending by number."""
    findings: list[Finding] = []
    for number in sorted(set(baseline_live) | set(baseline_archive)):
        baseline_block = baseline_live.get(number, baseline_archive.get(number, ""))
        if status_is_superseded(baseline_block):
            continue
        current_block = current_live.get(number)
        if current_block is None:
            archived = current_archive.get(number)
            if archived is None:
                findings.append((number, "vanished", None))
                continue
            findings.extend(_embedding_findings(number, baseline_block, archived, exempt_header_status=True))
            continue
        if status_is_superseded(current_block):
            violation = _stub_shape_violation(current_block)
            if violation is not None:
                findings.append((number, "stub", None))
            continue
        findings.extend(_embedding_findings(number, baseline_block, current_block, exempt_header_status=False))
    return findings


def _current_entries(root: Path, rel: str) -> dict[int, str]:
    path = root / rel
    if not path.exists():
        return {}
    content = path.read_text(encoding="utf-8", errors="replace")
    return {int(match.group(1)): block for match, block in iter_decision_sections(content)}


@registry.register("validate_decision_append_only", owner="platform")
def validate_decision_append_only(
    failed: list[str],
    root: Optional[Path] = None,
    baseline_body_reader: Optional[BaselineBodyReaderFn] = None,
) -> None:
    """Advisory WARN per decision whose ratified text a pull request's diff rewrites; never appends to failed.

    A branch behind main can WARN on a line main changed since; a rebase clears it.
    """
    print("\n=== Decision append-only WARN (Decision 177 cl.1-4, advisory) ===")
    root = root if root is not None else _common.ROOT
    reader = baseline_body_reader or _baseline.baseline_decision_bodies
    baseline = reader(root)
    if baseline is None:
        print("  SKIP: origin/main unreachable (advisory).")
        registry.skipped("origin/main unreachable")
        return
    baseline_live = baseline.get(_LIVE_REL_PATH, {})
    baseline_archive = baseline.get(_ARCHIVE_REL_PATH, {})
    findings = compare(
        baseline_live,
        baseline_archive,
        _current_entries(root, _LIVE_REL_PATH),
        _current_entries(root, _ARCHIVE_REL_PATH),
    )
    for number, kind, line in findings:
        detail = f" first unmatched baseline line: {line!r}" if line is not None else ""
        print(f"  WARN: Decision {number} {kind} violates the append-only dialect (Decision 177).{detail}")
    if not findings:
        print("  PASS: no ratified decision text is rewritten.")
    registry.examined(len(set(baseline_live) | set(baseline_archive)), unit="baseline_decision_bodies")
