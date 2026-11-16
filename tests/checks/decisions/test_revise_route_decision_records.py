"""The sandbox REVISE-route Decision (rec-5205): entry, envelope, reversal conditions, notes on 154 and 183, taxonomy text.

Scoped to the new Decision's own block, found by its header text; the number comes from the heading so later Decisions
never turn this red.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from tests.fixtures.decision_entry import decision_entry

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[3]
_HEADING = re.compile(
    r"^## Decision (\d+): A sandbox plan-reviewer REVISE on a push waits for the tf-gated-apply", re.MULTILINE
)
_PLAN = "PLAN-sandbox-reviewer-revise-route"


def _decisions_text() -> str:
    return (_REPO / "docs/DECISIONS.md").read_text(encoding="utf-8")


def _number(text: str) -> int:
    match = _HEADING.search(text)
    assert match is not None, "the REVISE-route Decision heading must exist"
    return int(match.group(1))


def test_decision_entry_envelope_and_reversal_conditions() -> None:
    text = _decisions_text()
    number = _number(text)
    entry = decision_entry(text, number)
    envelope = yaml.safe_load(re.search(r"```yaml\n(.*?)\n```", entry, re.DOTALL).group(1))
    assert envelope["number"] == number and envelope["amends"] == [154, 183]
    assert envelope["kind"]["value"] == "direction"
    assert "{{" not in entry
    reversal = entry[entry.index("```yaml reversal-conditions") :]
    for condition in ("rubber-stamp", "missed-defect", "rejected-reapplied"):
        assert f"id: {condition}" in reversal


@pytest.mark.parametrize("amended", [154, 183])
def test_amended_entries_carry_a_note_naming_the_plan(amended: int) -> None:
    text = _decisions_text()
    number = _number(text)
    notes = [p for p in decision_entry(text, amended).split("\n\n") if _PLAN in p]
    assert len(notes) == 1 and f"Decision {number}" in notes[0]


def test_taxonomy_names_the_push_revise_route() -> None:
    number = _number(_decisions_text())
    taxonomy = yaml.safe_load((_REPO / "docs/contracts/environment-taxonomy.yaml").read_text(encoding="utf-8"))
    sandbox = taxonomy["axis_a"]["sandbox"]
    assert "REVISE" in sandbox["apply_gating"] and f"Decision {number}" in sandbox["apply_gating"]
    gated = " ".join(str(taxonomy["axis_a"]["sandbox_gated_apply"]).split())
    assert "REVISE" in gated and "push path" in gated
