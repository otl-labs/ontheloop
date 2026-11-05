"""Forge-neutral core of the approved-text provenance check (rec-4769; Decision 234).

A sidecar's approval is GitHub's record (read by scripts.roadmap.approved_text_github and injected as `lookup`),
or the sidecar is grandfathered: on main at GRANDFATHER_REF with the same approved-words digest and its plan not yet
implementation_declared there (git only, Decision 187). Imports only the standard library, yaml and approved_words.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import yaml

from scripts.roadmap import approved_words

GRANDFATHER_REF = "ee65bbd9d433d130c945ceb112c4eb91efa1a146"  # pragma: allowlist secret
DEADLINE_SECONDS = 30

APPROVED = "APPROVED"
GRANDFATHERED = "GRANDFATHERED"
MISSING = "MISSING"
UNVERIFIED = "UNVERIFIED"
ENFORCE, ADVISORY, SKIP = "enforce", "advisory", "skip"


class Verdict(NamedTuple):
    kind: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.kind} {self.detail}".rstrip()


class Deadline:
    """One shared time budget; created once per check before its plan loop and handed to every lookup."""

    def __init__(self, seconds: float) -> None:
        self._end = time.monotonic() + seconds

    def remaining(self) -> float:
        return self._end - time.monotonic()

    def expired(self) -> bool:
        return self.remaining() <= 0


Lookup = Callable[[str, str], Verdict]


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _plan_declared_at(plan_rel: str, root: Path, ref: str) -> bool:
    result = _git(root, "show", f"{ref}:{plan_rel}")
    if result.returncode != 0:
        return False
    try:
        data = yaml.safe_load(result.stdout)
    except yaml.YAMLError:
        return False
    return isinstance(data, dict) and bool(data.get("implementation_declared"))


def grandfather(slug: str, sidecar: approved_words.Sidecar, root: Path, ref: str | None = None) -> Verdict | None:
    """GRANDFATHERED, UNVERIFIED (ref absent from the clone), or None (not grandfathered)."""
    ref = ref or GRANDFATHER_REF
    if _git(root, "cat-file", "-e", f"{ref}^{{commit}}").returncode != 0:
        return Verdict(UNVERIFIED, f"GRANDFATHER_REF {ref[:12]} is not in this clone (shallow history?)")
    shown = _git(root, "show", f"{ref}:{approved_words.sidecar_rel(slug)}")
    if shown.returncode != 0:
        return None
    try:
        pinned = approved_words.parse_sidecar(shown.stdout)
    except approved_words.ApprovedTextError:
        return None
    if approved_words.approved_digest(pinned) != approved_words.approved_digest(sidecar):
        return None
    if _plan_declared_at(f"docs/plans/PLAN-{slug}.yaml", root, ref):
        return None
    return Verdict(GRANDFATHERED, f"at {ref[:12]}")


def provenance(slug: str, sidecar: approved_words.Sidecar, root: Path, lookup: Lookup, ref: str | None = None) -> Verdict:
    """APPROVED (run id, approver) | GRANDFATHERED | MISSING | UNVERIFIED(reason)."""
    pinned = grandfather(slug, sidecar, root, ref)
    if pinned is not None:
        return pinned
    return lookup(slug, approved_words.approved_digest(sidecar))


def mode(in_push: bool, on_pr: bool) -> str:
    """skip on a push (and the on-main schedule); enforce in pull-request CI; advisory everywhere else."""
    if in_push:
        return SKIP
    return ENFORCE if on_pr else ADVISORY


def is_failure(verdict: Verdict, check_mode: str) -> bool:
    return check_mode == ENFORCE and verdict.kind in (MISSING, UNVERIFIED)
