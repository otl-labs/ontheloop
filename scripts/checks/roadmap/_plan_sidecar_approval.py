"""validate_plan_documents' approval leg and its seam (rec-4769).

A plan whose approved-text sidecar the diff adds or changes needs the operator's GitHub approval or a grandfather. Kept
out of validate_plan_documents.py so the leg and the seam are covered from their own mirror test.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts.roadmap import approved_text_github, approved_words, review_page
from scripts.roadmap import approved_text_provenance as prov

_READ_ERRORS = (approved_words.ApprovedTextError, UnicodeDecodeError, OSError)


def sidecar_provenance(slug: str, root: Path, in_push: bool, deadline: prov.Deadline) -> tuple[Any, str, str | None]:
    """The core over the working-tree sidecar (the HEAD copy the PR proposes) with the GitHub lookup."""
    text = (root / approved_words.sidecar_rel(slug)).read_text(encoding="utf-8")
    sidecar = approved_words.parse_sidecar(text)
    return approved_text_github.resolve(slug, sidecar, root, in_push, deadline, informational=False)


def _head_sha(root: Path) -> str:
    path = os.environ.get("GITHUB_EVENT_PATH")
    if path:
        try:
            sha = json.loads(Path(path).read_text(encoding="utf-8"))["pull_request"]["head"]["sha"]
            if isinstance(sha, str) and sha:
                return sha
        except (OSError, ValueError, KeyError, TypeError):
            pass
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip() if result.returncode == 0 else "HEAD"


def _remedy(slug: str, root: Path) -> str:
    sidecar = approved_words.parse_sidecar((root / approved_words.sidecar_rel(slug)).read_text(encoding="utf-8"))
    approved = approved_words.approved_digest(sidecar)
    return (
        f"dispatch approved-text-approval.yml from main with slug={slug} sha={_head_sha(root)} digest={approved} "
        f"fingerprint={review_page.fingerprint(approved)} and have the operator approve it on the run page "
        "(git-ops.yaml approved_text_binding); dispatch at the PR's final head, and if the head moves before the tap, "
        "re-run pr-validate after it (the approval stands while the sidecar is unchanged)"
    )


def approval_findings(
    plan_name: str,
    slug: str,
    root: Path,
    changed: Callable[[], str | None],
    in_push: bool,
    deadline: prov.Deadline,
) -> tuple[list[str], list[str]]:
    """(failures, advisories). `changed` reads the digest validate_plan_documents already reads (None when the diff
    neither adds nor changes the sidecar); a malformed sidecar stays that check's wording gap, never an exception."""
    try:
        if changed() is None:
            return [], []
        verdict, mode, _ = sidecar_provenance(slug, root, in_push, deadline)
    except _READ_ERRORS:
        return [], []
    if verdict is None:
        print("  SKIP: approval lookup (on main)")
        return [], []
    if verdict.kind not in (prov.MISSING, prov.UNVERIFIED):
        return [], []
    message = f"{plan_name}: docs/plans/approved/{slug}.yaml approval {verdict.kind} ({verdict.detail})"
    if prov.is_failure(verdict, mode):
        return [f"{message}; {_remedy(slug, root)}"], []
    return [], [f"{verdict.kind} (advisory): {message}"]
