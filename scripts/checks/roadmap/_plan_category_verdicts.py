"""validate_plan_documents' 12p leg: a drafting plan's last plan-critique row names every drafted Decision (rec-5261).

Kept out of validate_plan_documents.py so every branch is covered by this module's own mirror test. The trigger
(drafted_titles non-empty) is broader than plan-critique step 12p's own (a DECISIONS.md scope row or ratification
block); 0 of 31 drafting plans differ today.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from scripts.checks import _common

_SIDECAR_RE = re.compile(r"^docs/plans/approved/([^/]+)\.yaml$")
_MIN_SCHEMA_VERSION = 5
_SHAPE = (
    "category_consistency: [{entry: '<title>', verdict: PASS|REVISE}] (plan-critique SKILL.md step 12p; "
    "single-quote the title and double any ' inside it)"
)


def changed_sidecar_slugs(seam: set[str] | None, plans_dir: Path | None) -> set[str]:
    """Slugs whose docs/plans/approved/<slug>.yaml the diff adds, modifies or leaves untracked.

    The seam wins; under plans_dir the set is empty (as modified_plan_names), so fixtures never consult the real
    repo diff. One get_status_aware_diff read per run, not approved_text.sidecar_state's one git show per plan.
    """
    if seam is not None:
        return set(seam)
    if plans_dir is not None:
        return set()
    slugs: set[str] = set()
    for status, path in _common.get_status_aware_diff():
        match = _SIDECAR_RE.match(path)
        if match and status in ("A", "M", "??"):
            slugs.add(match.group(1))
    return slugs


def category_gaps(path: Path, doc: Any, root: Path) -> tuple[list[str], list[str]]:
    """(gaps, advisories) for a plan that drafts a Decision: the last critique row must name each drafted title once."""
    from scripts.roadmap import plan_decision_drafts  # noqa: PLC0415

    if doc.schema_version < _MIN_SCHEMA_VERSION:
        return [], []
    titles = set(plan_decision_drafts.drafted_titles(path, root))
    critique = [row for row in doc.gate_rounds if row.gate == "plan-critique"]
    if not titles or not critique:
        return [], []
    listed = ", ".join(repr(t) for t in sorted(titles))
    gaps: list[str] = []
    last = critique[-1]
    if last.category_consistency is None:
        gaps.append(
            f"{path.name}: the last plan-critique row has no category_consistency for the drafted entries [{listed}]: {_SHAPE}"
        )
    else:
        named = {item.entry.strip() for item in last.category_consistency}
        missing, unknown = sorted(titles - named), sorted(named - titles)
        if missing:
            gaps.append(f"{path.name}: the last plan-critique row's category_consistency omits drafted {missing!r}: {_SHAPE}")
        if unknown:
            gaps.append(
                f"{path.name}: the last plan-critique row's category_consistency names {unknown!r}, not a drafted title "
                f"(drafted: [{listed}]): {_SHAPE}"
            )
    advisories = [
        f"{path.name}: plan-critique round {row.round} has no category_consistency (step 12p records one per round)"
        for row in critique[:-1]
        if row.category_consistency is None
    ]
    return gaps, advisories


def run(path: Path, doc: Any, root: Path, added_names: set[str], sidecar_slugs: set[str]) -> tuple[list[str], list[str]]:
    if path.name not in added_names and doc.slug not in sidecar_slugs:
        return [], []
    return category_gaps(path, doc, root)
