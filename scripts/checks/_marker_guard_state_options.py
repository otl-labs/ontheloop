"""Private option helpers for scripts/checks/_marker_guard.py (Decision 165, Decision 187 cl.7).

Every opt-in branch of the shared marker mechanism, and the unconditional kind rule (approval-routing.yaml
lane.kind_floor, PLAN-wording-approval-routing), live here, so scripts/checks/_marker_guard.py gains only
unconditional calls and stays measured at 100% by its own mirror. Measured through
tests/checks/test__marker_guard_state_options.py. Grammar and semantics: docs/contracts/marker-grammar.yaml
(state_diff_mechanics (5)-(8)). This module never imports _marker_guard at module level (no cycle).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from scripts.decisions_md import extract_entry_envelope, iter_decision_sections

if TYPE_CHECKING:
    from scripts.checks._marker_guard import MarkerEntry, RegistrySpec, StateEntry

_DECISIONS_REL = "docs/DECISIONS.md"
_ARCHIVE_REL = "docs/DECISIONS_ARCHIVE.md"
_MAP_REL = "docs/contracts/approval-routing.yaml"
_FLOORS: dict[str, tuple[int, str, int | None]] = {}


def mentions(body: str, candidates: list[str], bounded: bool) -> bool:
    """The substring test, or with bounded a candidate counts only where no [\\w-] character adjoins it."""
    if not bounded:
        return any(candidate in body for candidate in candidates)
    return any(re.search(rf"(?<![\w-]){re.escape(candidate)}(?![\w-])", body) for candidate in candidates)


def effective_marker(spec: RegistrySpec, entry: MarkerEntry | StateEntry) -> str | None:
    """The entry's marker, or None when spec.reason_required and its reason is None or blank (rec-3736),
    so a reasonless marker is no marker on base and head entries alike."""
    if spec.reason_required and not (entry.reason and entry.reason.strip()):
        return None
    return entry.marker


def bodies_from_text(text: str, into: dict[int, str]) -> None:
    """Merge one corpus file's Decision bodies into `into`, first-wins (DECISIONS.md over the archive)."""
    for heading, raw_block in iter_decision_sections(text):
        into.setdefault(int(heading.group(1)), raw_block)


def base_corpus(spec: RegistrySpec, reader: Callable[[str], str | None]) -> tuple[dict[int, str] | None, list[str]]:
    """(base Decision bodies, violations) for fresh_mention specs; (None, []) otherwise or when a read fails.

    None from either read prints one loud SKIP and adds no freshness violation. A DECISIONS.md that reads ""
    at a reachable base is a violation, never an empty corpus; an empty archive is an empty archive."""
    if not spec.fresh_mention:
        return None, []
    decisions = reader(_DECISIONS_REL)
    archive = reader(_ARCHIVE_REL)
    if decisions is None or archive is None:
        print("  SKIP: Decision corpus unreadable at the base (freshness unchecked; advisory locally).")
        return None, []
    if not decisions:
        return None, [f"{spec.rel_path}: Decision corpus absent at the base, so no change can be shown fresh."]
    bodies: dict[int, str] = {}
    bodies_from_text(decisions, bodies)
    bodies_from_text(archive, bodies)
    return bodies, []


def _added_lines(base_lines: list[str], head_lines: list[str]) -> list[str] | None:
    """Head lines that are pure additions to base, or None when a base line is missing or out of order."""
    matched = 0
    extra: list[str] = []
    for line in head_lines:
        if matched < len(base_lines) and line == base_lines[matched]:
            matched += 1
        else:
            extra.append(line)
    if matched < len(base_lines):
        return None
    seen = set(base_lines)
    return [line for line in extra if line not in seen]


def _fresh(spec: RegistrySpec, key: str, number: int, bodies: dict[int, str], base_bodies: dict[int, str]) -> bool:
    from scripts.checks._marker_guard import _expand_ancestor_candidates

    raw_targets = spec.mention_candidates(key) if spec.mention_candidates is not None else [key]
    candidates: list[str] = []
    for target in raw_targets:
        candidates.extend(_expand_ancestor_candidates(target))
    added = _added_lines(base_bodies.get(number, "").splitlines(), bodies.get(number, "").splitlines())
    return added is not None and mentions("\n".join(added), candidates, spec.boundary_match)


def transition_failure(
    spec: RegistrySpec,
    key: str,
    marker: str,
    unauthorized: bool,
    bodies: dict[int, str],
    base_bodies: dict[int, str] | None,
) -> str | None:
    """The gated transition's failure message, or None when authorized (and fresh, with fresh_mention)."""
    if unauthorized:
        return f"{key}: {spec.token} marker cites {marker}, but its body does not authorize this entry (mention `{key}`)."
    if base_bodies is None:
        return None
    if _fresh(spec, key, int(marker.removeprefix("dec-")), bodies, base_bodies):
        return None
    return (
        f"{key}: {spec.token} marker cites {marker}, but no text added to it in this change names this entry "
        "(a mention the base already carried is spent; an in-place rewrite never authorizes)."
    )


def _floor(root: Path) -> tuple[str, int | None]:
    """(state, kind_floor) of the working-tree map: absent | inert | unreadable | sampled, memoized per mtime_ns."""
    path = Path(root) / _MAP_REL
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return "absent", None
    cached = _FLOORS.get(str(root))
    if cached and cached[0] == stamp:
        return cached[1], cached[2]
    try:
        lane = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("lane")
    except (OSError, yaml.YAMLError, AttributeError):
        state, floor = "unreadable", None
    else:
        floor = lane.get("kind_floor") if isinstance(lane, dict) else None
        floor = floor if isinstance(floor, int) and not isinstance(floor, bool) else None
        state = "inert" if not isinstance(lane, dict) or lane.get("rung") != "sampled" else "sampled"
    _FLOORS[str(root)] = (stamp, state, floor)
    return state, floor


def kind_failure(number: int, body: str, root: Path) -> bool:
    """True when the cited entry may not authorize a marker under the map's lane.kind_floor.

    Reads root/docs/contracts/approval-routing.yaml from the working tree (callers pass _common.ROOT, so fixture-rooted
    callers read the real repository's map). Inert when the map is absent or not on sampled. An unreadable map, or a
    sampled map without an int kind_floor, refuses mechanism entries only. An entry with no kind reads as direction.
    """
    state, floor = _floor(root)
    if state in ("absent", "inert"):
        return False
    envelope = extract_entry_envelope(body) or {}
    kind = envelope.get("kind")
    value = kind.get("value") if isinstance(kind, dict) else None
    if state == "unreadable" or floor is None:
        refused = value == "mechanism"
    else:
        refused = number > floor and value not in (None, "direction")
    if refused:
        print(
            f"  kind rule: Decision {number} is above kind_floor {floor} and is not direction (approval-routing.yaml); "
            "cite a direction entry"
        )
    return refused
