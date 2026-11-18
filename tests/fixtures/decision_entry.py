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


# Tmp-root builders for the Decision 272 size-check wiring tests (shared so the mirror file stays under its budget).
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
