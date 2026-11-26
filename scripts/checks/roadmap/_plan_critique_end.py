"""When a plan's plan-critique series ends (planning SKILL.md Step 9; schema_version 7 adds the findings record)."""

from __future__ import annotations

from typing import Any

_DISPOSED_NITS = {"applied-wording", "build-note"}


def ended(critique: list[Any], schema_version: int) -> bool:
    last = critique[-1]
    if last.effective_choice == "accept-with-deferral":
        return True
    confirming = len(critique) > 1 and critique[-2].verdict == "PROCEED" and critique[-2].nits > 0
    if last.verdict != "PROCEED":
        return False
    if schema_version < 7:
        return last.nits == 0 or confirming
    findings = last.findings or []
    nit_items = [item for item in findings if item.severity == "nit"]
    disposed = len(nit_items) == last.nits and all(item.disposition in _DISPOSED_NITS for item in nit_items)
    return last.nits == 0 or confirming or disposed


def gap_message(name: str) -> str:
    return (
        f"{name}: gate_rounds must end with a plan-critique PROCEED that ends Step 9 (no nits, or the confirming "
        "round after a PROCEED listing nits) or an accept-with-deferral answer (planning SKILL.md Step 9); at "
        "schema_version 7 a PROCEED also ends it when every nit finding is applied-wording or build-note"
    )
