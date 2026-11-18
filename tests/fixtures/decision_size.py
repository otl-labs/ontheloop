"""Tmp-root builders for the Decision 272 size-check wiring tests (shared so the mirror file stays under its budget)."""

from __future__ import annotations

from pathlib import Path

import yaml

ENTRY = "## Decision 5: Persona entry (Decided)\n\n**Status:** Decided\n\n{}**Related:** none\n\n---\n\n"
OPTS = "**Options considered:** a -- rejected: b.\n\n"
CONTRACT_REL = "docs/contracts/decision-entry.yaml"
EXEMPT_REL = "config/decision_options_exemptions.yaml"


def gov(**keys: object) -> str:
    return yaml.safe_dump({"size_governance": {"per_entry_size_norm": {"cap_bytes": 6 * 1024}, **keys}})


FULL = gov(measured_size_norm={"cap_bytes": 5120}, options_considered={"cap_bytes": 1024})


def make_root(tmp_path: Path, body: str, contract: str | None = FULL, exempt: str | None = None) -> Path:
    """A root holding one Decision 5 entry whose body is `body`, plus the optional contract and exemption list."""
    docs = {
        "docs/DECISIONS.md": ENTRY.format(body),
        "docs/DECISIONS_ARCHIVE.md": "",
        CONTRACT_REL: contract,
        EXEMPT_REL: exempt,
    }
    for rel, text in docs.items():
        if text is not None:
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path
