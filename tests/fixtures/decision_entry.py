"""The size measure every test that pins one Decision entry uses (rec-4730).

docs/contracts/decision-entry.yaml size_governance caps an entry's span as authored and meters later annotations
WARN-only (Decision 151 sanctions them), so a pin measures the entry without its trailing run of dated annotations.
Growth of the entry's own text still counts.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.checks.decisions.entry_measure import authored, authored_bytes
from scripts.decisions_md import iter_decision_sections

__all__ = ["authored", "authored_bytes", "cap_bytes", "decision_entry"]

_REPO = Path(__file__).resolve().parents[2]


def cap_bytes() -> int:
    """per_entry_size_norm.cap_bytes, the norm the pinned entries landed under. An entry landed after Decision 272 is
    pinned against measured_size_norm instead (a whole-span pin against this cap can false-fail such an entry)."""
    contract = yaml.safe_load((_REPO / "docs/contracts/decision-entry.yaml").read_text(encoding="utf-8"))
    return int(contract["size_governance"]["per_entry_size_norm"]["cap_bytes"])


def decision_entry(text: str, number: int) -> str:
    blocks = [block for match, block in iter_decision_sections(text) if int(match.group(1)) == number]
    assert len(blocks) == 1, f"DECISIONS.md must carry exactly one Decision {number}"
    return blocks[0]
