"""The SIT and catalog-on-RDS Decisions (rec-5024): entries, envelopes, dated notes, account rule, roadmap items.

Numbers come from the headings, never hard-coded, so a renumber at merge keeps one source.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from tests.fixtures.decision_entry import authored_bytes, cap_bytes, decision_entry

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[3]
_SIT_HEADING = re.compile(r"^## Decision (\d+): SIT is stood up now", re.MULTILINE)
_RDS_HEADING = re.compile(r"^## Decision (\d+): The DuckLake catalog moves to RDS", re.MULTILINE)
_SIT_NOTED = (24, 73, 77, 126, 144, 202, 237, 242)
_RDS_NOTED = (107, 214)


def _decisions_text() -> str:
    return (_REPO / "docs/DECISIONS.md").read_text(encoding="utf-8")


def _numbers(text: str) -> tuple[int, int]:
    sit, rds = _SIT_HEADING.search(text), _RDS_HEADING.search(text)
    assert sit is not None and rds is not None, "both new Decision headings must exist"
    return int(sit.group(1)), int(rds.group(1))


def _envelope(entry: str) -> dict:
    return yaml.safe_load(re.search(r"```yaml\n(.*?)\n```", entry, re.DOTALL).group(1))


def _noting_paragraph(entry: str, phrase: str) -> str:
    """The one paragraph carrying the note, wherever later dated notes push it."""
    paragraphs = [p for p in entry.rstrip().removesuffix("---").rstrip().split("\n\n") if phrase in p]
    assert len(paragraphs) == 1, (phrase, len(paragraphs))
    return paragraphs[0]


def test_sit_notes_survive_a_later_note_on_the_same_entry() -> None:
    entry = "Body.\n\nDecision 9 fires here.\n\nA later dated note.\n\n---\n"
    assert _noting_paragraph(entry, "Decision 9 fires") == "Decision 9 fires here."
    with pytest.raises(AssertionError):
        _noting_paragraph(entry, "absent phrase")
    with pytest.raises(AssertionError):
        _noting_paragraph(entry + "\n\nDecision 9 fires again.", "Decision 9 fires")


def _rds_directly_above_sit(numbers: list[int], rds: int, sit: int) -> bool:
    """Newest-first heading order: RDS sits immediately above SIT, wherever later Decisions push the pair."""
    return rds in numbers and sit in numbers and numbers.index(rds) + 1 == numbers.index(sit)


def test_rds_directly_above_sit_survives_a_newer_decision() -> None:
    assert _rds_directly_above_sit([253, 252, 251], 253, 252)
    assert _rds_directly_above_sit([255, 254, 253, 252, 251], 253, 252)
    assert not _rds_directly_above_sit([253, 254, 252], 253, 252)
    assert not _rds_directly_above_sit([252, 253], 253, 252)
    assert not _rds_directly_above_sit([254, 251], 253, 252)


def test_sit_and_rds_decisions_and_notes() -> None:
    text = _decisions_text()
    sit, rds = _numbers(text)
    assert rds == sit + 1
    headings = re.findall(r"^## Decision (\d+):", text, re.MULTILINE)
    assert _rds_directly_above_sit([int(n) for n in headings], rds, sit)

    for number, amends, kind in ((sit, list(_SIT_NOTED), "direction"), (rds, list(_RDS_NOTED), "mechanism")):
        entry = decision_entry(text, number)
        assert authored_bytes(entry) <= cap_bytes()
        envelope = _envelope(entry)
        assert envelope["number"] == number and envelope["status"] == "Decided"
        assert envelope["amends"] == amends and envelope["kind"]["value"] == kind

    expected = {214: f"Amended by Decision {rds} (", 107: f"Amended by Decision {rds} (", 84: f"Decision {rds} moves"}
    expected |= dict.fromkeys(_SIT_NOTED, f"Amended by Decision {sit} (")
    expected[249] = f"Decision {sit} fires"
    for number, phrase in expected.items():
        note = _noting_paragraph(decision_entry(text, number), phrase)
        other = sit if str(rds) in phrase else rds
        assert not re.search(rf"Decision {other}\b", note), (number, "cites the other new Decision")


def test_account_rule_and_tier_gating() -> None:
    contract = yaml.safe_load((_REPO / "docs/contracts/environment-taxonomy.yaml").read_text(encoding="utf-8"))
    rule = " ".join(contract["single_account_rule"].split())
    assert "Only the SIT items of roadmap T2.57 and T2.58 author multi-account infrastructure" in rule
    assert "live_full is not a trigger" in rule
    for item in contract["general_conformance"]:
        assert "do not author multi-account" not in " ".join(str(item).split())
    axis = contract["axis_a"]
    assert "deterministic guard" in " ".join(axis["SIT"]["apply_gating"].split())
    assert " ".join(axis["PROD"]["apply_gating"].split()).startswith("promote-only")


def test_sit_roadmap_items() -> None:
    roadmap = yaml.safe_load((_REPO / "docs/ROADMAP-PLATFORM.yaml").read_text(encoding="utf-8"))
    items = {i["id"]: i for i in roadmap["tier_items"]}
    assert "T2.57" in items and "T2.58" in items
    assert "T2.57" in items["T2.58"]["depends_on"]
    for item in (items["T2.57"], items["T2.58"]):
        leads = [re.match(r"(rec-\d+):", c["text"]) for c in item["exit_criteria"]]
        assert all(leads), item["id"]
        ids = [m.group(1) for m in leads]
        assert len(ids) == len(set(ids)), item["id"]
    status = {c["id"]: c["status"] for c in items["T2.57"]["exit_criteria"]}
    assert status["c1"] == "met" and status["c2"] == "met"
