"""The size measure every test that pins one Decision entry uses (rec-4730).

docs/contracts/decision-entry.yaml size_governance caps an entry's span as authored and meters later annotations
WARN-only (Decision 151 sanctions them), so a pin measures the entry without its trailing run of dated annotations.
Growth of the entry's own text still counts.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from scripts.decisions_md import iter_decision_sections

_REPO = Path(__file__).resolve().parents[2]
_DATE = r"\d{4}-\d{2}(?:-\d{2})?"
_BRACKET_OPENER = re.compile(rf"\[Amendment {_DATE}\b")
_BLOCKQUOTE = re.compile(rf"> \*\*[^*\n]*?{_DATE}[^*\n]*?:\*\*")
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")


def cap_bytes() -> int:
    contract = yaml.safe_load((_REPO / "docs/contracts/decision-entry.yaml").read_text(encoding="utf-8"))
    return int(contract["size_governance"]["per_entry_size_norm"]["cap_bytes"])


def decision_entry(text: str, number: int) -> str:
    blocks = [block for match, block in iter_decision_sections(text) if int(match.group(1)) == number]
    assert len(blocks) == 1, f"DECISIONS.md must carry exactly one Decision {number}"
    return blocks[0]


def _closes_at_end(paragraph: str) -> bool:
    depth = 0
    for index, char in enumerate(paragraph):
        depth += {"[": 1, "]": -1}.get(char, 0)
        if depth == 0:
            return index == len(paragraph) - 1
    return False


def _is_trailer(paragraph: str) -> bool:
    if _BRACKET_OPENER.match(paragraph):
        return _closes_at_end(paragraph)
    return _BLOCKQUOTE.match(paragraph) is not None and all(line.startswith(">") for line in paragraph.splitlines())


def authored(entry: str) -> str:
    """The entry without its trailing dated annotations, closing separator kept; an entry with none is unchanged."""
    core = entry.rstrip()
    if core.endswith("\n---"):
        core = core.removesuffix("---").rstrip()
    end = len(core)
    for brk in reversed(list(_PARAGRAPH_BREAK.finditer(core))):
        if not _is_trailer(core[brk.end() : end].strip()):
            break
        end = brk.start()
    return entry if end == len(core) else core[:end] + entry[len(core) :]


def authored_bytes(entry: str) -> int:
    return len(authored(entry).encode("utf-8"))
