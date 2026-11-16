"""validate_plan_documents' two re-scout legs: the newest-version rule (rec-5102) and the re-scout barrier (rec-4490).

Kept out of validate_plan_documents.py so every new line is covered by this module's own mirror test.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from scripts.checks import _common
from scripts.checks.roadmap import _plan_rescout_barrier


def newest_schema_version() -> int:
    from scripts.roadmap import plan_document  # noqa: PLC0415

    return max(plan_document._SUPPORTED_VERSIONS)


def newest_version_findings(added_names: Collection[str], docs: Mapping[str, Any], floor: int) -> list[str]:
    newest = newest_schema_version()
    return [
        (
            f"{name}: newly-added plans must declare schema_version {newest}, the newest this tree supports "
            f"(got {doc.schema_version}): bump it, add cites, re-run the decision scout and record scout_input_fingerprint"
        )
        for name, doc in sorted(docs.items())
        if name in added_names and floor <= doc.schema_version < newest
    ]


def seam_names(plans_dir: Path | None, added: set[str] | None, modified: set[str] | None) -> dict[str, set[str]]:
    """The plans_dir test seam's name sets for run(): empty sets (never None) so no git path is reached."""
    if plans_dir is None:
        return {}
    return {"added_plan_names": added or set(), "modified_plan_names": modified or set()}


def run(
    *,
    docs: Mapping[str, Any],
    plan_paths: Collection[Path],
    root: Path,
    base: str | None,
    push_context: bool,
    event: str | None,
    floor: int,
    added_plan_names: Collection[str] | None = None,
    modified_plan_names: Collection[str] | None = None,
) -> tuple[list[str], list[str], str]:
    seam = added_plan_names is not None or modified_plan_names is not None
    loaded = {Path(path).name: Path(path) for path in plan_paths if Path(path).name in docs}
    if seam:
        added = set(added_plan_names or ())
        entries = [("A", str(loaded[n])) for n in sorted(added) if n in loaded]
        entries += [("M", str(loaded[n])) for n in sorted(set(modified_plan_names or ())) if n in loaded]
        diff_count = len(added) + len(set(modified_plan_names or ()))
    else:
        diff = _common.get_status_aware_diff(root)
        entries = [(status, path) for status, path in diff if status in ("A", "M", "??") and _common.PLAN_PATH_RE.match(path)]
        diff_count = sum(1 for status, _ in diff if status in ("A", "M", "D"))
        added = {Path(path).name for status, path in entries if status in ("A", "??")}
    result = _plan_rescout_barrier.barrier(
        entries, docs, root, base, push_context, diff_count=diff_count, seam=seam, event=event
    )
    findings = newest_version_findings(added, docs, floor)
    failures, warnings = list(result.failures), list(result.warnings)
    (warnings if push_context else failures).extend(findings)
    if result.examined:
        line = f" re-scout barrier: {result.examined} plan(s) examined, {result.compared} compared, {len(failures)} failing"
    else:
        line = " re-scout barrier: no added or modified plan in this diff"
    return failures, warnings, line
