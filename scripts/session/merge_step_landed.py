"""Premerge handling of approved-text files whose words landed in an earlier merge (approved_text.verify_landed).

Kept out of scripts.session.merge_step so that module stays covered by its own test file alone. Imports
scripts.roadmap.approved_text inside functions, as merge_step does.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any


def verify(sidecar: Any, slug: str, root: Path, base: str) -> tuple[list[str], dict[str, str]]:
    from scripts.roadmap import approved_text  # noqa: PLC0415

    return approved_text.verify_landed(sidecar, slug, root, base)


def print_landed(landed: dict[str, str]) -> None:
    for rel, sha in landed.items():
        print(f"PREMERGE APPROVED-TEXT LANDED {rel} {sha}")


def unlanded(sidecar: Any, landed: dict[str, str]) -> Any:
    """The sidecar's blocks for files that did not land (an object with `.blocks`; possibly empty)."""
    return SimpleNamespace(blocks=[b for b in sidecar.blocks if b.file not in landed])


def report_stale(sidecar: Any, slug: str, root: Path, landed: dict[str, str]) -> bool:
    """True when origin/main still resolves each landed file to the same landing commit."""
    if not landed:
        return True
    from scripts.roadmap import approved_text  # noqa: PLC0415

    scoped = sidecar.model_copy(update={"blocks": [b for b in sidecar.blocks if b.file in landed]})
    _, fresh = approved_text.verify_landed(scoped, slug, root, "origin/main")
    ok = True
    for rel, sha in landed.items():
        if fresh.get(rel) != sha:
            print(f"PREMERGE STALE BASE {rel}: rebase and re-run CI")
            ok = False
    return ok
