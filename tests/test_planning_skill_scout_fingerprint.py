"""rec-4490 acceptance: the planning skill and plan.md re-scout on the scout-input fingerprint, never on a judged change.

Guards the instruction text the sidecar does not bind (operator fork 3A) from being reworded away.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from scripts.roadmap import plan_document

_REPO = Path(__file__).resolve().parents[1]
_SKILL = (_REPO / ".claude/skills/planning/SKILL.md").read_text(encoding="utf-8")
_PLAN_MD = (_REPO / ".claude/commands/plan.md").read_text(encoding="utf-8")


def _step(text: str, number: int) -> str:
    match = re.search(rf"^## Step {number}:.*?(?=^## Step |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match is not None
    return match.group(0)


def test_planning_skill_rescouts_on_scout_input_fingerprint_change() -> None:
    gate = _SKILL[_SKILL.index("## Decision Scout Gate") : _SKILL.index("## Alignment Sign-off")]
    assert "scripts.roadmap.plan_fingerprint --plan [draft] --with-cites" in gate and "one dec:N per CITE id" in gate
    assert "print that command and its output" in gate
    gate_error = next(line for line in gate.splitlines() if line.startswith("- **Gate-error:**"))
    assert "fingerprint line" in gate_error and "FINGERPRINT-ERROR" in gate_error
    assert "re-dispatch after a BLOCK pivot or when the fingerprint moves" in gate and "never otherwise" in gate
    assert "the plan's `cites` holds the `dec:` tokens fed to `--with-cites`" in gate
    assert "only admin bypass merges it" in gate
    assert "re-run the Decision Scout if the fingerprint moved" in _SKILL
    for text in (_SKILL, _PLAN_MD):
        assert "material to decision alignment" not in text and "changes the proposed approach materially" not in text
    assert "scout_input_fingerprint=" in _step(_PLAN_MD, 6)
    assert "plan_fingerprint --plan docs/plans/PLAN-{slug}.yaml" in _step(_PLAN_MD, 9)
    assert "recompute `plan_fingerprint`" in _step(_PLAN_MD, 11)
    template = re.search(r"^schema_version: (\d+) #.*$", _SKILL, re.MULTILINE)
    assert template is not None and int(template.group(1)) == max(plan_document._SUPPORTED_VERSIONS)
    assert re.search(r"^cites: \[\]", _SKILL, re.MULTILINE)


def test_contracts_state_the_fingerprint_rule() -> None:
    alignment = yaml.safe_load((_REPO / "docs/contracts/project-alignment.yaml").read_text(encoding="utf-8"))
    sequencing = " ".join(alignment["task_sequencing"].split())
    assert "scripts/roadmap/plan_fingerprint.py" in sequencing and "re-scout required" in sequencing
    assert "material change" not in sequencing
    ledger = (_REPO / "docs/contracts/exit-criteria-ledger.yaml").read_text(encoding="utf-8")
    assert "_plan_rescout_barrier" in ledger and "until rec-4490's plan" not in " ".join(ledger.split())
