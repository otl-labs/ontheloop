"""The graduated-record known-red list only shrinks (rec-4831).

A change may remove a check_id from config/agent/verification_registry/known_red.yaml, never add one. The
base is the merge base of HEAD and origin/main (so a branch stays green when main removed a different id
after it branched), falling back to origin/main; the base list is read with git show (Decision 176 cl.4).
Skipped, never passed, when no base ref resolves or the base holds no list file (the introducing change).

Import closure is kept small on purpose (scripts.checks._common, scripts.checks.registry, yaml, subprocess):
importing scripts.verification_graduation would force validate_pre_glob_closure to demand its files in
pre_globs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from scripts.checks import _common, registry

KNOWN_RED_REL = "config/agent/verification_registry/known_red.yaml"


def ids_from_text(text: str) -> list[str]:
    """The check_ids listed in a known_red.yaml document; a malformed document yields no ids."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    return [e["check_id"] for e in entries if isinstance(e, dict) and isinstance(e.get("check_id"), str)]


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, encoding="utf-8")


def _base_ref(root: Path) -> str | None:
    merged = _git(root, "merge-base", "HEAD", "origin/main")
    if merged.returncode == 0 and merged.stdout.strip():
        return merged.stdout.strip()
    if _git(root, "rev-parse", "--verify", "-q", "origin/main").returncode == 0:
        return "origin/main"
    return None


@registry.register("validate_known_red_shrinks", owner="platform")
def validate_known_red_shrinks(failed: list[str], root: Path | None = None) -> None:
    """Fail when the known-red list at HEAD holds a check_id the base list lacks."""
    tree = Path(root) if root is not None else _common.ROOT
    base = _base_ref(tree)
    if base is None:
        registry.skipped("no base ref resolves (neither the merge base with origin/main nor origin/main)")
        return
    shown = _git(tree, "show", f"{base}:{KNOWN_RED_REL}")
    if shown.returncode != 0:
        registry.skipped(f"{KNOWN_RED_REL} absent at the base (the introducing change)")
        return
    path = tree / KNOWN_RED_REL
    head_ids = ids_from_text(path.read_text(encoding="utf-8")) if path.is_file() else []
    base_ids = set(ids_from_text(shown.stdout))
    added = sorted(set(head_ids) - base_ids)
    for check_id in added:
        failed.append(f"known-red list grew: {check_id} is not on the base list; fix the record, never list it")
    registry.examined(len(head_ids), unit="known_red_entries")
