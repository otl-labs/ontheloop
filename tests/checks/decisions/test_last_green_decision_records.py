"""The batched full tier and last-green ref Decision (rec-4874 build plan 1): entry, envelope, dated notes, taxonomy term.

Growth-safe by design: the number comes from the heading, and nothing pins heading order, the newest entry or a
note's position, so a later Decision or a later note on the same entries never turns this red (rec-5095's class).
Archiving one of the annotated entries under Decision 146 would, as it would test_sit_decision_records.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from tests.fixtures.decision_entry import authored_bytes, cap_bytes, decision_entry

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[3]
_HEADING = re.compile(r"^## Decision (\d+): The full tier runs post-merge on batches of main", re.MULTILINE)
_AMENDS = [72, 73, 159, 163, 201, 203, 205, 226, 244, 245, 249]
_FIRED = {245: "tap-promotion-lands", 249: "canary-model-decided"}
_CHILD_RECS = {"rec-5106", "rec-5107", "rec-5108", "rec-5109", "rec-5110"}


def _decisions_text() -> str:
    return (_REPO / "docs/DECISIONS.md").read_text(encoding="utf-8")


def _number(text: str) -> int:
    match = _HEADING.search(text)
    assert match is not None, "the batched full tier Decision heading must exist"
    return int(match.group(1))


def _paragraphs(entry: str, prefix: str) -> list[str]:
    return [p for p in entry.split("\n\n") if p.startswith(prefix)]


def test_decision_entry_and_envelope() -> None:
    text = _decisions_text()
    number = _number(text)
    entry = decision_entry(text, number)
    assert authored_bytes(entry) <= cap_bytes()
    envelope = yaml.safe_load(re.search(r"```yaml\n(.*?)\n```", entry, re.DOTALL).group(1))
    assert envelope["number"] == number and envelope["status"] == "Decided"
    assert envelope["amends"] == _AMENDS and envelope["kind"]["value"] == "direction"
    assert "{{" not in entry and not re.search(r"rec-P\d", entry)
    coverage = entry[entry.index("9. **Coverage") : entry.index("```yaml reversal-conditions")]
    assert set(re.findall(r"rec-\d+", coverage)) - {"rec-5019"} == _CHILD_RECS


def test_each_amended_entry_carries_one_dated_note() -> None:
    text = _decisions_text()
    number = _number(text)
    for amended in _AMENDS:
        notes = _paragraphs(decision_entry(text, amended), f"> **Amended by Decision {number} (")
        assert len(notes) == 1, (amended, len(notes))
        if amended in _FIRED:
            assert _FIRED[amended] in notes[0], amended
    sit = [p for p in _paragraphs(decision_entry(text, 252), "> **Update (") if f"Decision {number} " in p]
    assert len(sit) == 1 and "necessary condition" in sit[0] and "rec-5033" in sit[0]


def test_taxonomy_reserves_last_green_ref_apart_from_promotion() -> None:
    doc = yaml.safe_load((_REPO / "docs/contracts/environment-taxonomy.yaml").read_text(encoding="utf-8"))
    term = doc["vocabulary"]["last_green_ref"]
    assert "full tier" in term["reserved_meaning"] and "promotion" in term["do_not_use_for"]
    assert "deploy tier" in doc["vocabulary"]["promotion"]["reserved_meaning"]
