"""Decision supersession-annotation invariant guard (DPI-01 + DPI-06 + DPI-03 extension,
audits/decision-log-premise-integrity-8fb581e.yaml, PLAN-decisions-supersession-guard).

A superseding Decision that ships without a forward pointer on its victim silently regrows the
"corpse pile" the decision-log-premise-integrity audit found: a reader of the victim entry has no
way to discover it was superseded. This check makes that a mechanical, standing regression guard;
it runs in --pre (its manifest Entry is the authority) and again in the full tier.

Parses docs/DECISIONS.md + docs/DECISIONS_ARCHIVE.md via the shared scripts.decisions_md grammar
(_iter_decision_sections, the '#{2,3}' regex from Decision 134's DAF-03 consolidation) -- never a
hand-rolled header regex, so this stays in lockstep with the ETL and the R1 ratification guard
(Decision 105). Three sub-checks:

  (a) Supersession forward-pointer (DPI-01): every textual "Supersedes/amends/partially
      supersedes Decision N" cross-reference must leave the superseder's number on the victim's
      block (plain "Decision {superseder}" or a "(Superseded by Decision {superseder})" header),
      unless the edge is listed in config/decision_supersession_waivers.yaml.
  (b) Duplicate-number detection (DPI-06 part a): the same decision number heading twice within
      one file is a FAIL; the same number appearing in both DECISIONS.md and DECISIONS_ARCHIVE.md
      is also a FAIL (PLAN-decision-renumber-at-merge; the live_archive_pair_allowlist waiver
      route is retired, Decision 163, and a waiver file that still carries the key FAILs).
  (c) Warehouse-ID conformance (DPI-03 extension): every "**Warehouse ID:** dec-NNN" line must
      equal dec-{header:03d} for its own block's header number.

Documented, spec-accepted regex limits: the edge regex matches only the singular "Supersedes
Decision N" form (the plural "Supersedes Decisions N, M" form is not matched), and the
block-owner=superseder heuristic can mis-attribute a cited edge (e.g. a rationale that quotes
another Decision's supersession sentence) -- both are handled by the waiver file, not by
regex cleverness. A waived edge that later becomes annotated is a WARN (stale waiver), never a
FAIL, to avoid waiver-rot build breaks.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml

from scripts.checks import _common, registry
from scripts.decisions_md import _iter_decision_sections, extract_entry_envelope

_LIVE_REL_PATH = "docs/DECISIONS.md"
_ARCHIVE_REL_PATH = "docs/DECISIONS_ARCHIVE.md"
_WAIVERS_REL_PATH = "config/decision_supersession_waivers.yaml"

_EDGE_RE = re.compile(r"(?:Supersedes|supersedes|amends|Amends|partially supersedes)\s+Decision\s+(\d+)")
_HEADER_SUPERSEDED_RE = re.compile(r"\(Superseded by Decision (\d+)\)")
_WAREHOUSE_ID_RE = re.compile(r"\*\*Warehouse ID:\*\*\s*(\S+)")

_GUARD_NAME = "Decision supersession-annotation guard"


def extract_supersession_edges(root: Path) -> list[tuple[int, int, str]]:
    """Pure extractor: (superseder, victim, file) for every textual Supersedes/amends/partially
    supersedes cross-reference in docs/DECISIONS.md + docs/DECISIONS_ARCHIVE.md, UNIONED with
    every envelope-borne amends/supersedes edge (PLAN-decision-entry-flow-governance, Decision
    167) -- an envelope-bearing entry that declares `amends: [N]` or `supersedes: [N]` must not
    silently escape this guard just because it carries no textual "amends Decision N" prose;
    _EDGE_RE matches only that textual form, which a typed envelope no longer emits.

    The containing block's own header number is the superseder; the referenced number is the
    victim. A self-reference (victim == superseder) is impossible by construction and skipped
    defensively. Deduped to unique (superseder, victim, file) triples -- a block's heading
    parenthetical and its body prose both legitimately say "amends Decision N" for the same real
    edge (e.g. Decision 145's header AND rationale both cite Decision 134), and that single edge
    must not be double-counted or produce duplicate FAIL/WARN lines. Sorted for deterministic,
    scannable output; this is the raw (waiver-unfiltered) signal the annotation sub-check and the
    VP non-vacuity assertion both consume.
    """
    edges: set[tuple[int, int, str]] = set()
    for rel_path in (_LIVE_REL_PATH, _ARCHIVE_REL_PATH):
        path = root / rel_path
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for heading_match, block in _iter_decision_sections(content):
            superseder = int(heading_match.group(1))
            for edge_match in _EDGE_RE.finditer(block):
                victim = int(edge_match.group(1))
                if victim == superseder:
                    continue
                edges.add((superseder, victim, rel_path))
            envelope = extract_entry_envelope(block)
            if envelope:
                for victim in list(envelope.get("amends") or []) + list(envelope.get("supersedes") or []):
                    if int(victim) != superseder:
                        edges.add((superseder, int(victim), rel_path))
    return sorted(edges)


def _combined_number_to_block(root: Path) -> dict[int, str]:
    """First-wins number->block across LIVE then ARCHIVE (mirrors parse_decisions_md's dedup: a
    DECISIONS.md entry wins over a DECISIONS_ARCHIVE.md entry sharing the same number)."""
    blocks: dict[int, str] = {}
    for rel_path in (_LIVE_REL_PATH, _ARCHIVE_REL_PATH):
        path = root / rel_path
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for heading_match, block in _iter_decision_sections(content):
            n = int(heading_match.group(1))
            if n not in blocks:
                blocks[n] = block
    return blocks


def _heading_count(root: Path) -> int:
    total = 0
    for rel_path in (_LIVE_REL_PATH, _ARCHIVE_REL_PATH):
        path = root / rel_path
        if path.exists():
            total += len(_iter_decision_sections(path.read_text(encoding="utf-8", errors="replace")))
    return total


def _is_annotated(superseder: int, victim_block: str) -> bool:
    if f"Decision {superseder}" in victim_block:
        return True
    header_match = _HEADER_SUPERSEDED_RE.search(victim_block)
    return bool(header_match and int(header_match.group(1)) == superseder)


def _load_waivers(root: Path, failed: list[str]) -> Optional[set[tuple[int, int]]]:
    path = root / _WAIVERS_REL_PATH
    if not path.exists():
        failed.append(f"{_GUARD_NAME}: {_WAIVERS_REL_PATH} not found")
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        failed.append(f"{_GUARD_NAME}: could not parse {_WAIVERS_REL_PATH}: {exc}")
        return None
    if not isinstance(data, dict):
        failed.append(f"{_GUARD_NAME}: {_WAIVERS_REL_PATH} has an unexpected top-level shape")
        return None
    if "live_archive_pair_allowlist" in data:
        failed.append(
            f"{_GUARD_NAME}: {_WAIVERS_REL_PATH} still carries live_archive_pair_allowlist, which is retired "
            "(a live/archive duplicate number is a FAIL with no waiver, Decision 163)"
        )
        return None
    return {(int(w["superseder"]), int(w["victim"])) for w in (data.get("waivers") or [])}


def _check_supersession_annotations(root: Path, waived: set[tuple[int, int]], issues: list[str]) -> tuple[bool, int]:
    edges = extract_supersession_edges(root)
    combined = _combined_number_to_block(root)
    any_fail = False
    for superseder, victim, fname in edges:
        victim_block = combined.get(victim, "")
        annotated = _is_annotated(superseder, victim_block)
        is_waived = (superseder, victim) in waived
        if annotated:
            if is_waived:
                issues.append(
                    f"  WARN: waiver {superseder}->{victim} is stale (Decision {victim} is now "
                    f"annotated) -- consider removing it from {_WAIVERS_REL_PATH}."
                )
            continue
        if is_waived:
            continue
        issues.append(
            f"  FAIL: Decision {superseder} supersedes/amends Decision {victim} ({fname}) with no "
            f"forward pointer on the victim's block, and no waiver in {_WAIVERS_REL_PATH}."
        )
        any_fail = True
    return any_fail, len(edges)


def _check_duplicate_numbers(root: Path, issues: list[str]) -> bool:
    any_fail = False
    numbers_by_file: dict[str, set[int]] = {}
    for rel_path in (_LIVE_REL_PATH, _ARCHIVE_REL_PATH):
        path = root / rel_path
        content = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        counts: dict[int, int] = {}
        for heading_match, _ in _iter_decision_sections(content):
            n = int(heading_match.group(1))
            counts[n] = counts.get(n, 0) + 1
        for n, count in sorted(counts.items()):
            if count > 1:
                issues.append(f"  FAIL: duplicate decision number {n} appears {count} times within {rel_path}.")
                any_fail = True
        numbers_by_file[rel_path] = set(counts)

    for n in sorted(numbers_by_file[_LIVE_REL_PATH] & numbers_by_file[_ARCHIVE_REL_PATH]):
        issues.append(f"  FAIL: Decision {n} appears in both {_LIVE_REL_PATH} and {_ARCHIVE_REL_PATH}.")
        any_fail = True
    return any_fail


def _check_warehouse_id_conformance(root: Path, issues: list[str]) -> bool:
    any_fail = False
    for rel_path in (_LIVE_REL_PATH, _ARCHIVE_REL_PATH):
        path = root / rel_path
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for heading_match, block in _iter_decision_sections(content):
            n = int(heading_match.group(1))
            wh_match = _WAREHOUSE_ID_RE.search(block)
            if not wh_match:
                continue
            value = wh_match.group(1)
            expected = f"dec-{n:03d}"
            if value != expected:
                issues.append(f"  FAIL: Decision {n} ({rel_path}) has Warehouse ID '{value}', expected '{expected}'.")
                any_fail = True
    return any_fail


@registry.register("validate_supersession_annotations", owner="platform")
def validate_supersession_annotations(failed: list[str], root: Path | None = None) -> None:
    """Enforce the supersession-annotation, duplicate-number, and Warehouse-ID invariants.

    root is a test/dogfood injection seam (mirrors validate_decision_entry_conformance) --
    defaults to _common.ROOT.
    """
    print(f"\n=== {_GUARD_NAME} (DPI-01/DPI-06/DPI-03-ext) ===")
    root = root if root is not None else _common.ROOT

    waived_edges = _load_waivers(root, failed)
    if waived_edges is None:
        registry.skipped("waiver file missing, unparseable or carrying the retired allowlist key")
        return
    registry.examined(_heading_count(root), unit="decisions")

    issues: list[str] = []
    annotation_fail, edge_count = _check_supersession_annotations(root, waived_edges, issues)
    duplicate_fail = _check_duplicate_numbers(root, issues)
    warehouse_fail = _check_warehouse_id_conformance(root, issues)

    for line in issues:
        print(line)

    if annotation_fail or duplicate_fail or warehouse_fail:
        failed.append(_GUARD_NAME)
    else:
        print(
            f"  PASS: {edge_count} supersession edge(s) enumerated; annotation, duplicate-number, "
            "and Warehouse-ID sub-checks green."
        )
