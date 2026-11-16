"""The BLOCK-always-fails Decision amending 83 (PLAN-plan-rescout-on-fingerprint): entry, envelope, and the dated note on 83.

Growth-safe: the number comes from the heading and nothing pins the newest entry or a count of Decisions.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from tests.fixtures.decision_entry import authored_bytes, cap_bytes, decision_entry

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[3]
_HEADING = re.compile(r"^## Decision (\d+): A plan PR whose last decision-scout row is BLOCK fails its check", re.MULTILINE)


def _text() -> str:
    return (_REPO / "docs/DECISIONS.md").read_text(encoding="utf-8")


def _number(text: str) -> int:
    matches = _HEADING.findall(text)
    assert len(matches) == 1, "exactly one Decision heading for the BLOCK rule must exist"
    return int(matches[0])


def test_decision_entry_and_envelope() -> None:
    text = _text()
    number = _number(text)
    entry = decision_entry(text, number)
    assert authored_bytes(entry) <= cap_bytes()
    envelope = yaml.safe_load(re.search(r"```yaml\n(.*?)\n```", entry, re.DOTALL).group(1))
    assert envelope["number"] == number and envelope["status"] == "Decided"
    assert envelope["amends"] == [83] and envelope["kind"]["value"] == "direction"
    assert "T4.32 c6" in entry and "admin bypass" in entry and "{{" not in entry


def test_decision_83_holds_exactly_one_dated_note_naming_it() -> None:
    text = _text()
    number = _number(text)
    entry = decision_entry(text, 83)
    notes = [p for p in entry.split("\n\n") if "PLAN-plan-rescout-on-fingerprint" in p]
    assert len(notes) == 1 and notes[0].startswith("> **Update (") and f"Decision {number} " in notes[0]
